"""Тесты оркестратора истории (пункт 7 нового ТЗ).

GigaChat замокан: реальную модель из тестов не дёргаем. Проверяем, что локальный
слой правильно собирает эпизоды/reply-цепочки и что двухпроходный цикл, память
и деградация работают. Смысловые выводы — за моделью, поэтому в фейке они заданы.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from app import analysis, history, memory

U = {
    "vanya": ("Ваня Авоськин", "user1008691840"),
    "roma": ("Рома", "user929563641"),
    "dania": ("Daniil", "user636876688"),
    "gorshok": ("Gorshok", "user398728095"),
    "ben": ("Дон Биткоин", "user548381092"),
}


def _msg(mid, who, text, ts, reply_to=None):
    name, uid = U[who]
    return {
        "id": mid, "from": name, "from_id": uid,
        "date": "2026-03-10T12:00:00", "ts": ts, "text": text, "reply_to": reply_to,
    }


def _write_history(msgs):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False)
    os.environ["CHAT_HISTORY_PATH"] = path
    history._load_index.cache_clear()
    return path


def _write_memory(data):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.environ["CHAT_MEMORY_PATH"] = path
    memory._load.cache_clear()
    return path


def _reset_memory_empty():
    os.environ.pop("CHAT_MEMORY_PATH", None)
    memory._load.cache_clear()


class FakeLLM:
    """Планировщик и аналитик по скрипту. Пишет, что ему передали."""

    def __init__(self, plan, analyses):
        self.plan = plan
        self.analyses = list(analyses)
        self.analyze_inputs: list[str] = []
        self.plan_calls = 0

    async def complete(self, system, user, **kwargs):
        if system == analysis.PLANNER_SYSTEM:
            self.plan_calls += 1
            return json.dumps(self.plan, ensure_ascii=False)
        # аналитик
        self.analyze_inputs.append(user)
        nxt = self.analyses.pop(0) if self.analyses else {"status": "answer", "answer": "ок", "confidence": "вероятно"}
        return json.dumps(nxt, ensure_ascii=False)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- 1. Корнишон определяется по reply-цепочкам ------------------------------
def test_kornishon_via_reply_chains():
    msgs = [
        _msg(1, "vanya", "пацаны я купил новую тачку", 1000),
        _msg(2, "roma", "Корнишон ты чего опять", 1001, reply_to=1),
        _msg(3, "dania", "ахах Корнишон в деле", 1002, reply_to=1),
        _msg(4, "gorshok", "да норм всё", 1003),
    ]
    _write_history(msgs)
    _reset_memory_empty()

    plan = {"is_history_question": True, "people": ["Корнишон"],
            "spellings": ["Корнишон", "Корнишону"], "places": [], "events": [],
            "extra_queries": [], "need_reply_chains": True, "need_neighbors": True}
    llm = FakeLLM(plan, [{"status": "answer",
                          "answer": "Похоже, Корнишон — это Ваня.",
                          "confidence": "вероятно"}])

    ans = run(analysis.answer_history("Кого называют Корнишон?", llm))
    assert ans == "Похоже, Корнишон — это Ваня."
    # в аналитик должна попасть reply-цепочка: сообщение-родитель от Вани (#1)
    joined = "\n".join(llm.analyze_inputs)
    assert "Корнишон" in joined
    assert "Ваня" in joined and "купил новую тачку" in joined


# --- 2 & 3. Хриплая + Питер: объединение эпизодов через второй проход --------
def test_hriplaya_piter_two_pass_merge():
    msgs = [
        _msg(1, "ben", "кто такая Хриплая вообще", 1000),
        _msg(2, "gorshok", "это с которой Илюха в феврале виделся", 1001, reply_to=1),
        # отдельный эпизод про Питер, далеко по времени/индексам
        _msg(3, "dania", "болтовня про погоду", 2000),
        _msg(4, "dania", "ещё болтовня", 2001),
        _msg(5, "dania", "и ещё", 2002),
        _msg(6, "dania", "и ещё раз", 2003),
        _msg(7, "gorshok", "в Питер ездил в феврале, тусили круто", 2004),
        _msg(8, "roma", "с кем", 2005, reply_to=7),
    ]
    _write_history(msgs)
    _reset_memory_empty()

    plan = {"is_history_question": True, "people": ["Хриплая", "Илюха"],
            "spellings": ["Хриплая", "Хриплой"], "places": ["Питер"], "events": [],
            "extra_queries": [], "need_reply_chains": True, "need_neighbors": True}
    llm = FakeLLM(plan, [
        {"status": "need_more", "confidence": "недостаточно",
         "followup": {"queries": ["Питер"], "people": ["Илюха"], "places": ["Питер"]}},
        {"status": "answer",
         "answer": "Хриплая связана с поездкой Илюхи в Питер в феврале.",
         "confidence": "вероятно"},
    ])

    ans = run(analysis.answer_history("Что ты знаешь про Хриплую и Питер?", llm))
    assert "Питер" in ans
    # было два прохода аналитика
    assert len(llm.analyze_inputs) == 2
    # во втором проходе присутствуют оба эпизода (Хриплая и Питер)
    second = llm.analyze_inputs[1]
    assert "Хриплая" in second and "Питер" in second


# --- 4. Использование chat_memory.json --------------------------------------
def test_uses_chat_memory():
    msgs = [_msg(1, "ben", "да было дело", 1000), _msg(2, "roma", "помню", 1001)]
    _write_history(msgs)
    _write_memory({
        "people": {}, "aliases": [],
        "events": [{"description": "поездка на Кубу зимой 2026",
                    "participants": ["Дон Биткоин"], "dates": ["2026-01"],
                    "evidence": [1], "confidence": "подтверждено"}],
        "inside_jokes": [], "relationships": [],
    })
    plan = {"is_history_question": True, "people": [], "places": ["Куба"],
            "spellings": [], "events": ["Куба"], "extra_queries": ["Куба"],
            "need_reply_chains": True, "need_neighbors": True}
    llm = FakeLLM(plan, [{"status": "answer", "answer": "На Кубу ездил Бен.",
                          "confidence": "подтверждено"}])

    ans = run(analysis.answer_history("Кто ездил на Кубу?", llm))
    assert ans == "На Кубу ездил Бен."
    assert "поездка на Кубу" in llm.analyze_inputs[0]  # подсказка памяти дошла до модели
    _reset_memory_empty()


# --- 5. Низкая уверенность при неоднозначном контексте ----------------------
def test_low_confidence_answer():
    msgs = [_msg(1, "roma", "какой-то Корнишон", 1000), _msg(2, "dania", "хз кто это", 1001)]
    _write_history(msgs)
    _reset_memory_empty()
    plan = {"is_history_question": True, "people": ["Корнишон"], "spellings": [],
            "places": [], "events": [], "extra_queries": [],
            "need_reply_chains": True, "need_neighbors": True}
    llm = FakeLLM(plan, [
        {"status": "need_more", "followup": {"queries": ["Корнишон"]}},
        {"status": "answer", "confidence": "недостаточно",
         "answer": "Данных недостаточно: кто такой Корнишон, из переписки не ясно."},
    ])
    ans = run(analysis.answer_history("Кто такой Корнишон?", llm))
    assert "недостаточно" in ans.lower()


# --- планировщик решает, что вопрос не про историю --------------------------
def test_non_history_returns_none():
    msgs = [_msg(1, "dania", "привет", 1000)]
    _write_history(msgs)
    _reset_memory_empty()
    plan = {"is_history_question": False}
    llm = FakeLLM(plan, [])
    # общий вопрос-сравнение — оркестратор должен вернуть None (обычный путь)
    ans = run(analysis.answer_history("Что лучше BMW F30 или Jetour T2?", llm))
    assert ans is None


# --- деградация: истории нет, но вопрос исторический -------------------------
def test_history_unavailable_message():
    os.environ["CHAT_HISTORY_PATH"] = "/nonexistent/path/hist.json"
    history._load_index.cache_clear()
    _reset_memory_empty()
    llm = FakeLLM({"is_history_question": True}, [])
    ans = run(analysis.answer_history("что Даня говорил про BMW", llm))
    assert ans is not None and "недоступна" in ans.lower()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    os.environ.pop("CHAT_HISTORY_PATH", None)
    os.environ.pop("CHAT_MEMORY_PATH", None)
    history._load_index.cache_clear()
    memory._load.cache_clear()
