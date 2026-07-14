"""Тесты трёхрежимного роутера (CHAT_REQUIRED / CHAT_PREFERRED / GENERAL).

GigaChat замокан: классификатор и аналитик заданы по скрипту. Проверяем
маршрутизацию, фолбэк, двухпроходный поиск, reply-цепочки, память и деградацию.
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
    return {"id": mid, "from": name, "from_id": uid,
            "date": "2026-03-10T12:00:00", "ts": ts, "text": text, "reply_to": reply_to}


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
    def __init__(self, classification, analyses=None, raise_on_classify=False):
        self.classification = classification
        self.analyses = list(analyses or [])
        self.analyze_inputs = []
        self.classify_calls = 0
        self.raise_on_classify = raise_on_classify

    async def complete(self, system, user, **kwargs):
        if system == analysis.CLASSIFIER_SYSTEM:
            self.classify_calls += 1
            if self.raise_on_classify:
                raise RuntimeError("giga down")
            return json.dumps(self.classification, ensure_ascii=False)
        self.analyze_inputs.append(user)
        nxt = self.analyses.pop(0) if self.analyses else {
            "status": "answer", "answer": "ок", "confidence": "вероятно"}
        return json.dumps(nxt, ensure_ascii=False)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_kornishon_via_reply_chains():
    _write_history([
        _msg(1, "vanya", "пацаны я купил новую тачку", 1000),
        _msg(2, "roma", "Корнишон ты чего опять", 1001, reply_to=1),
        _msg(3, "dania", "ахах Корнишон в деле", 1002, reply_to=1),
        _msg(4, "gorshok", "да норм всё", 1003),
    ])
    _reset_memory_empty()
    llm = FakeLLM({"mode": "CHAT_PREFERRED", "chat_search_query": "Корнишон"},
                  [{"status": "answer", "answer": "Похоже, Корнишон — это Ваня.",
                    "confidence": "вероятно"}])
    ans = run(analysis.route_answer("Кто такой Корнишон?", llm))
    assert ans == "Похоже, Корнишон — это Ваня."
    joined = "\n".join(llm.analyze_inputs)
    assert "Корнишон" in joined and "купил новую тачку" in joined


def test_hriplaya_piter_two_pass():
    _write_history([
        _msg(1, "ben", "кто такая Хриплая вообще", 1000),
        _msg(2, "gorshok", "это с которой Илюха в феврале виделся", 1001, reply_to=1),
        _msg(3, "dania", "болтовня", 2000), _msg(4, "dania", "ещё", 2001),
        _msg(5, "dania", "и ещё", 2002), _msg(6, "dania", "снова", 2003),
        _msg(7, "gorshok", "в Питер ездил в феврале, тусили круто", 2004),
        _msg(8, "roma", "с кем", 2005, reply_to=7),
    ])
    _reset_memory_empty()
    llm = FakeLLM({"mode": "CHAT_PREFERRED", "chat_search_query": "Хриплая Питер"},
        [{"status": "need_more", "confidence": "недостаточно",
          "followup": {"queries": ["Питер"], "people": ["Илюха"]}},
         {"status": "answer", "answer": "Хриплая связана с поездкой Илюхи в Питер.",
          "confidence": "вероятно"}])
    ans = run(analysis.route_answer("Что ты знаешь про Хриплую и Питер?", llm))
    assert "Питер" in ans
    assert len(llm.analyze_inputs) == 2
    assert "Хриплая" in llm.analyze_inputs[1] and "Питер" in llm.analyze_inputs[1]


def test_uses_chat_memory():
    _write_history([_msg(1, "ben", "да было дело", 1000), _msg(2, "roma", "помню", 1001)])
    _write_memory({"people": {}, "aliases": [],
        "events": [{"description": "поездка на Кубу зимой 2026",
                    "participants": ["Дон Биткоин"], "dates": ["2026-01"],
                    "evidence": [1], "confidence": "подтверждено"}],
        "inside_jokes": [], "relationships": []})
    llm = FakeLLM({"mode": "CHAT_REQUIRED", "chat_search_query": "Куба"},
                  [{"status": "answer", "answer": "На Кубу ездил Бен.",
                    "confidence": "подтверждено"}])
    ans = run(analysis.route_answer("Кто у нас ездил на Кубу?", llm))
    assert ans == "На Кубу ездил Бен."
    assert "поездка на Кубу" in llm.analyze_inputs[0]
    _reset_memory_empty()


def test_general_returns_none():
    _write_history([_msg(1, "dania", "привет", 1000)])
    _reset_memory_empty()
    llm = FakeLLM({"mode": "GENERAL", "chat_search_query": "bmw jetour"})
    ans = run(analysis.route_answer("Что лучше BMW F30 или Jetour T2?", llm))
    assert ans is None
    assert llm.analyze_inputs == []


def test_chat_required_not_found():
    _write_history([_msg(1, "dania", "ничего по теме", 1000)])
    _reset_memory_empty()
    llm = FakeLLM({"mode": "CHAT_REQUIRED", "chat_search_query": "зулькарнайн флюгегехаймен"})
    ans = run(analysis.route_answer("Что у нас говорили про зулькарнайн?", llm))
    assert ans == analysis.NOT_FOUND


def test_chat_preferred_falls_back_to_general():
    _write_history([_msg(1, "roma", "какой-то корнишон мелькал", 1000),
                    _msg(2, "dania", "хз", 1001)])
    _reset_memory_empty()
    llm = FakeLLM({"mode": "CHAT_PREFERRED", "chat_search_query": "корнишон"},
                  [{"status": "answer", "answer": "Точно сказать не могу.",
                    "confidence": "недостаточно"}])
    ans = run(analysis.route_answer("Кто такой Корнишон?", llm))
    assert ans is None


def test_chat_required_returns_history_answer():
    _write_history([_msg(1, "ben", "премия 460 к пришла", 1000),
                    _msg(2, "roma", "ого", 1001)])
    _reset_memory_empty()
    llm = FakeLLM({"mode": "CHAT_REQUIRED", "chat_search_query": "премия Бен"},
                  [{"status": "answer", "answer": "По переписке — 460 к.",
                    "confidence": "вероятно"}])
    ans = run(analysis.route_answer("На какую сумму Бен получил премию?", llm))
    assert ans == "По переписке — 460 к."


def test_history_unavailable_required_vs_preferred():
    os.environ["CHAT_HISTORY_PATH"] = "/nonexistent/hist.json"
    history._load_index.cache_clear()
    _reset_memory_empty()
    req = FakeLLM({"mode": "CHAT_REQUIRED", "chat_search_query": "x"})
    assert run(analysis.route_answer("что у нас говорили про BMW", req)) == analysis.NOT_FOUND
    pref = FakeLLM({"mode": "CHAT_PREFERRED", "chat_search_query": "x"})
    assert run(analysis.route_answer("что было с BMW Бена", pref)) is None


def test_classification_fields_and_fallback_rules():
    llm = FakeLLM({"mode": "CHAT_REQUIRED", "chat_search_query": "q", "fallback_to_general": True})
    cls = run(analysis.classify("что Даня говорил", llm))
    assert set(cls) >= {"mode", "chat_search_query", "fallback_to_general"}
    assert cls["mode"] == "CHAT_REQUIRED"
    assert cls["fallback_to_general"] is False
    llm2 = FakeLLM({"mode": "CHAT_PREFERRED", "chat_search_query": "q"})
    cls2 = run(analysis.classify("кто такой Корнишон", llm2))
    assert cls2["fallback_to_general"] is True


def test_heuristic_classify_on_llm_failure():
    llm = FakeLLM({"mode": "GENERAL", "chat_search_query": "q"}, raise_on_classify=True)
    cls = run(analysis.classify("что Даня говорил про BMW", llm))
    assert cls["mode"] == "CHAT_REQUIRED"
    assert cls["fallback_to_general"] is False


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    os.environ.pop("CHAT_HISTORY_PATH", None)
    os.environ.pop("CHAT_MEMORY_PATH", None)
    history._load_index.cache_clear()
    memory._load.cache_clear()
