"""Оркестратор ответов по истории группы.

Разделение обязанностей (главное правило архитектуры из ТЗ):
  • Локальный код (history/retrieval/memory): хранение, индекс, поиск кандидатов,
    соседние сообщения, reply-цепочки, удаление дублей, сборка эпизодов.
  • GigaChat: понимание вопроса, расширение запроса, смысловые связи, вывод
    прозвищ, пересказ событий, финальная формулировка и оценка уверенности.

Поток:
  1) plan_search()  — GigaChat строит JSON-план поиска по вопросу.
  2) _retrieve()    — локально достаём эпизоды по плану (+ подсказки из памяти).
  3) analyze()      — GigaChat анализирует эпизоды и либо отвечает, либо просит
                      второй поиск.
  4) при необходимости — второй/третий проход (не больше max_passes).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from . import history, memory, retrieval

logger = logging.getLogger(__name__)


class LLM(Protocol):
    async def complete(self, system: str, user: str, **kwargs: Any) -> str: ...


# --- промпты -----------------------------------------------------------------

PLANNER_SYSTEM = """
Ты — планировщик поиска по переписке закрытой Telegram-группы. Тебе дают вопрос
пользователя. Верни СТРОГО JSON без пояснений и без markdown, с полями:
{
  "is_history_question": true|false,   // относится ли вопрос к истории этой группы
  "people": [],      // упомянутые люди и прозвища (как в вопросе)
  "places": [],      // места
  "events": [],      // события/темы
  "spellings": [],   // возможные варианты написания имён/прозвищ/мест
  "extra_queries": [],   // дополнительные поисковые формулировки
  "need_reply_chains": true|false,   // важны ли ответы/цепочки
  "need_neighbors": true|false       // важен ли соседний контекст
}
Если вопрос общий (сравнить машины, мнение, факт из общих знаний) —
is_history_question=false, остальные поля можно оставить пустыми.
""".strip()

ANALYZER_SYSTEM = """
Ты разбираешь фрагменты переписки закрытой пацанской группы и отвечаешь на вопрос
пользователя. Отвечай коротко, по делу, в характере группы.

Правила:
- Опирайся ТОЛЬКО на приведённые сообщения и подсказки памяти. Ничего не выдумывай.
- Прозвище определяй по контексту: кто автор сообщения-родителя, к кому обращаются,
  кто упомянут рядом, повторяется ли прозвище по отношению к одному человеку.
- Связывай несколько сообщений в одно событие, определяй участников.
- Обязательно помечай уверенность: «подтверждено», «вероятно по контексту» или
  «данных недостаточно».
- Если связь косвенная — так и скажи (пример: «Похоже, Корнишоном называют Ваню:
  прозвище несколько раз использовали в ответах ему, но прямого объяснения нет»).

Верни СТРОГО JSON без markdown:
{
  "status": "answer" | "need_more",
  "answer": "текст ответа для пользователя (если status=answer)",
  "confidence": "подтверждено" | "вероятно" | "недостаточно",
  "followup": { "queries": [], "people": [], "places": [] }  // если status=need_more
}
Проси need_more только если данных явно мало, а второй поиск реально поможет.
""".strip()


# --- разбор JSON от модели ---------------------------------------------------

def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # пытаемся вырезать первый JSON-объект
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


# --- планирование (GigaChat) -------------------------------------------------

def _heuristic_plan(question: str) -> dict[str, Any]:
    """Запасной план, если GigaChat недоступен или вернул мусор.
    Не подменяет смысловой анализ — только грубо подбирает поисковые термины."""
    from . import aliases

    person = aliases.resolve_query_person(question)
    return {
        "is_history_question": history.looks_like_history_question(question),
        "people": [person] if person else [],
        "places": [],
        "events": [],
        "spellings": [],
        "extra_queries": [],
        "need_reply_chains": True,
        "need_neighbors": True,
    }


async def plan_search(question: str, llm: LLM) -> dict[str, Any]:
    try:
        raw = await llm.complete(
            PLANNER_SYSTEM, f"Вопрос: {question}", temperature=0.0, max_tokens=400
        )
        plan = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 — модель может отвалиться, деградируем мягко
        logger.warning("Планировщик GigaChat недоступен (%s) — беру эвристику", exc)
        plan = None
    if not isinstance(plan, dict):
        return _heuristic_plan(question)
    plan.setdefault("is_history_question", history.looks_like_history_question(question))
    for key in ("people", "places", "events", "spellings", "extra_queries"):
        plan.setdefault(key, [])
    plan.setdefault("need_reply_chains", True)
    plan.setdefault("need_neighbors", True)
    return plan


# --- локальная сборка эпизодов (без GigaChat) --------------------------------

def _collect_terms(plan: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("people", "places", "events", "spellings", "extra_queries"):
        terms += [str(t) for t in plan.get(key, []) if str(t).strip()]
    # уникализируем, сохраняя порядок
    seen: set[str] = set()
    out = []
    for t in terms:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            out.append(t)
    return out


def _retrieve(
    question: str,
    plan: dict[str, Any],
    *,
    max_seeds: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Чисто локально: собираем seed-позиции по вопросу и терминам плана,
    затем эпизоды и подсказки памяти."""
    seeds: list[int] = []
    seen: set[int] = set()

    def add(idxs: list[int]) -> None:
        for i in idxs:
            if i not in seen:
                seen.add(i)
                seeds.append(i)

    add(history.search_indices(question, limit=8))
    for term in _collect_terms(plan):
        add(history.search_indices(term, limit=5, min_score=0.5))
    seeds = seeds[:max_seeds]

    if plan.get("need_neighbors", True):
        before = after = 7
    else:
        before = after = 2
    episodes = retrieval.gather_episodes(seeds, before=before, after=after)

    mem_query = " ".join([question] + _collect_terms(plan))
    mem_hits = memory.search_memory(mem_query)
    return episodes, mem_hits


def _merge_episodes(
    a: list[dict[str, Any]], b: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Объединяет два набора эпизодов, убирая дубли по набору message_id."""
    def sig(ep: dict[str, Any]) -> frozenset:
        return frozenset(m["message_id"] for m in ep["messages"])

    out = list(a)
    known = {sig(e) for e in a}
    for ep in b:
        s = sig(ep)
        if s not in known:
            known.add(s)
            out.append(ep)
    return out


# --- смысловой анализ (GigaChat) + двухпроходный цикл ------------------------

async def analyze_once(
    question: str,
    episodes: list[dict[str, Any]],
    mem_hits: list[dict[str, Any]],
    llm: LLM,
) -> dict[str, Any] | None:
    ctx_parts = []
    mem_text = memory.format_memory(mem_hits)
    if mem_text:
        ctx_parts.append(mem_text)
    ctx_parts.append(retrieval.format_episodes(episodes) or "(эпизодов не найдено)")
    user = f"Вопрос: {question}\n\nНайденные фрагменты:\n\n" + "\n\n".join(ctx_parts)
    raw = await llm.complete(ANALYZER_SYSTEM, user, temperature=0.2, max_tokens=600)
    return _parse_json(raw)


async def answer_history(
    question: str,
    llm: LLM,
    *,
    max_passes: int = 2,
) -> str | None:
    """Возвращает финальный ответ по истории или None, если вопрос не про историю.
    None означает: обрабатывай вопрос обычным путём (общие знания GigaChat)."""
    if not history.history_available():
        if history.looks_like_history_question(question):
            return "Не могу сейчас поднять переписку — история недоступна."
        return None

    plan = await plan_search(question, llm)
    if not plan.get("is_history_question", False):
        return None

    episodes, mem_hits = _retrieve(question, plan)

    last: dict[str, Any] | None = None
    for pass_no in range(1, max_passes + 1):
        result = await analyze_once(question, episodes, mem_hits, llm)
        last = result or last
        if not isinstance(result, dict):
            break

        status = result.get("status", "answer")
        if status == "answer" or pass_no == max_passes:
            answer = (result.get("answer") or "").strip()
            if answer:
                return answer
            break

        # status == need_more: второй проход по follow-up
        followup = result.get("followup") or {}
        fu_plan = {
            "people": followup.get("people", []),
            "places": followup.get("places", []),
            "events": [],
            "spellings": [],
            "extra_queries": followup.get("queries", []),
            "need_neighbors": True,
            "need_reply_chains": True,
        }
        logger.info("История: второй поиск pass=%s followup=%s", pass_no + 1, fu_plan)
        more_eps, more_mem = _retrieve(question, fu_plan)
        episodes = _merge_episodes(episodes, more_eps)
        # мемори-подсказки объединяем по описанию
        seen_desc = {m.get("description") for m in mem_hits}
        mem_hits += [m for m in more_mem if m.get("description") not in seen_desc]

    if isinstance(last, dict) and (last.get("answer") or "").strip():
        return last["answer"].strip()
    return "В переписке этого не нашёл."
