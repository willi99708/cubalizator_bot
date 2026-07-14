"""Оркестратор ответов по истории группы.

Разделение обязанностей (главное правило архитектуры из ТЗ):
  • Локальный код (history/retrieval/memory): хранение, индекс, поиск кандидатов,
    соседние сообщения, reply-цепочки, удаление дублей, сборка эпизодов.
  • GigaChat: понимание вопроса, расширение запроса, смысловые связи, вывод
    прозвищ, пересказ событий, финальная формулировка и оценка уверенности.

Поток:
  1) classify()     — GigaChat определяет режим (CHAT_REQUIRED / CHAT_PREFERRED /
                      GENERAL) и даёт строку поиска по истории.
  2) _retrieve()    — локально достаём эпизоды по строке поиска (+ подсказки памяти).
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

CLASSIFIER_SYSTEM = """
Ты — маршрутизатор вопросов бота закрытой Telegram-группы. Определи режим вопроса
и верни СТРОГО JSON без markdown и пояснений:
{
  "mode": "CHAT_REQUIRED" | "CHAT_PREFERRED" | "GENERAL",
  "chat_search_query": "строка для поиска по истории группы",
  "fallback_to_general": true | false
}

Режимы:
- CHAT_REQUIRED — ответ обязан опираться на историю группы. Признаки: «кто у нас»,
  «что Даня говорил», «кого в чате называют», «по нашей переписке», «с кем Илюха
  тусил», вопросы про конкретные события, суммы, прозвища и участников группы.
  Для него fallback_to_general = false.
- CHAT_PREFERRED — вопрос может относиться к истории, но имеет и обычный смысл:
  неоднозначные имена, прозвища, места, события. Примеры: «Что ты знаешь про
  Хриплую и Питер?», «Кто такой Корнишон?», «Что было с BMW Бена?».
  Для него fallback_to_general = true.
- GENERAL — обычный вопрос из общих знаний. Примеры: «Что лучше BMW F30 или
  Jetour T2?», «Кто такой Корнишон в биологии?», «Что посмотреть в Питере?»,
  «Какая столица России?». Для него fallback_to_general = true.

Если сомневаешься между CHAT_PREFERRED и GENERAL — выбирай CHAT_PREFERRED.
Клички, странные имена и сленг, которых нет в общих знаниях, рядом с людьми или
местами (например «Дрочер», «Корнишон», «Хриплая») — это про группу, ставь
CHAT_PREFERRED, а не GENERAL. Вопросы вида «кто такой X», «на чём ездит X»,
«что было с X» про участников группы — это история.
chat_search_query — короткая формулировка для поиска по переписке (имена, места,
темы из вопроса); для GENERAL можно повторить суть вопроса.
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
- ЗАПРЕЩЕНО писать мета-фразы вроде «фрагментов переписки нет», «судя по стилю
  общения» и любые догадки на пустом месте. Если данных для ответа нет — верни
  status с confidence «недостаточно» и ПУСТОЙ answer, не сочиняй.

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

def _heuristic_classify(question: str) -> dict[str, Any]:
    """Запасная классификация, если GigaChat недоступен. Не подменяет модель —
    лишь грубо разводит режимы по маркерам, склоняясь к CHAT_PREFERRED при сомнении."""
    from . import aliases

    n = history.normalize(question)
    required_markers = (
        "кто у нас", "у нас в чате", "по нашей переписке", "в нашем чате",
        "в чате называют", "кого в чате", "с кем", "что говорил", "что сказал",
    )
    person = aliases.resolve_query_person(question)
    if any(m in n for m in required_markers) or (
        person and any(w in n for w in ("говорил", "сказал", "премி", "премия", "тусил"))
    ):
        mode = "CHAT_REQUIRED"
    elif person or history.looks_like_history_question(question):
        mode = "CHAT_PREFERRED"
    else:
        mode = "GENERAL"
    return {
        "mode": mode,
        "chat_search_query": question,
        "fallback_to_general": mode != "CHAT_REQUIRED",
    }


_VALID_MODES = {"CHAT_REQUIRED", "CHAT_PREFERRED", "GENERAL"}


async def classify(question: str, llm: LLM) -> dict[str, Any]:
    try:
        raw = await llm.complete(
            CLASSIFIER_SYSTEM, f"Вопрос: {question}", temperature=0.0, max_tokens=200
        )
        data = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 — модель может отвалиться, деградируем мягко
        logger.warning("Классификатор GigaChat недоступен (%s) — беру эвристику", exc)
        data = None
    if not isinstance(data, dict) or data.get("mode") not in _VALID_MODES:
        return _heuristic_classify(question)
    data.setdefault("chat_search_query", question)
    # согласуем fallback с режимом по правилам ТЗ
    data["fallback_to_general"] = data.get("mode") != "CHAT_REQUIRED"
    return data


# --- локальная сборка эпизодов (без GigaChat) --------------------------------

def _retrieve(
    query: str,
    extra_terms: list[str] | None = None,
    *,
    max_seeds: int = 20,
    neighbors: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Чисто локально: собираем seed-позиции по строке поиска (и доп. терминам),
    затем эпизоды и подсказки памяти."""
    seeds: list[int] = []
    seen: set[int] = set()

    def add(idxs: list[int]) -> None:
        for i in idxs:
            if i not in seen:
                seen.add(i)
                seeds.append(i)

    add(history.search_indices(query, limit=8, literal=True))   # слова-клички по всем
    add(history.search_indices(query, limit=6))                  # версия с автор-фильтром
    for term in extra_terms or []:
        if str(term).strip():
            add(history.search_indices(str(term), limit=5, min_score=0.3, literal=True))
    seeds = seeds[:max_seeds]

    mem_query = " ".join([query] + [str(t) for t in (extra_terms or [])])
    mem_hits = memory.search_memory(mem_query)

    # память помогает найти эпизод: подтягиваем исходные сообщения по evidence
    index = history.get_index()
    if index is not None:
        for hit in mem_hits:
            for mid in hit.get("evidence", []) or []:
                idx = index.id_to_idx.get(mid)
                if idx is not None and idx not in seen:
                    seen.add(idx)
                    seeds.append(idx)

    before = after = 7 if neighbors else 2
    episodes = retrieval.gather_episodes(seeds, before=before, after=after)
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


def _insufficient(confidence: str) -> bool:
    return "недостаточ" in (confidence or "").lower()


async def _run_history(
    question: str,
    query: str,
    llm: LLM,
    *,
    max_passes: int = 2,
) -> dict[str, Any]:
    """Прогоняет историю через двухпроходный анализ.
    Возвращает {answer, confidence, found}: found=False — убедительных данных нет."""
    episodes, mem_hits = _retrieve(query)
    if not episodes and not mem_hits:
        return {"answer": "", "confidence": "недостаточно", "found": False}

    last: dict[str, Any] | None = None
    for pass_no in range(1, max_passes + 1):
        result = await analyze_once(question, episodes, mem_hits, llm)
        last = result or last
        if not isinstance(result, dict):
            break

        status = result.get("status", "answer")
        if status == "answer" or pass_no == max_passes:
            break

        followup = result.get("followup") or {}
        terms = (
            list(followup.get("queries", []))
            + list(followup.get("people", []))
            + list(followup.get("places", []))
        )
        logger.info("История: второй поиск pass=%s terms=%s", pass_no + 1, terms)
        more_eps, more_mem = _retrieve(query, terms)
        episodes = _merge_episodes(episodes, more_eps)
        seen_desc = {m.get("description") for m in mem_hits}
        mem_hits += [m for m in more_mem if m.get("description") not in seen_desc]

    if isinstance(last, dict):
        answer = (last.get("answer") or "").strip()
        confidence = str(last.get("confidence") or "")
        return {"answer": answer, "confidence": confidence, "found": bool(answer)}
    return {"answer": "", "confidence": "недостаточно", "found": False}


NOT_FOUND = "В переписке этого не нашёл."


async def route_answer(
    question: str,
    llm: LLM,
    *,
    max_passes: int = 2,
) -> str | None:
    """Трёхрежимная маршрутизация.

    Возвращает:
      • строку — готовый ответ по истории группы (отправить пользователю);
      • None   — вопрос надо обработать обычным путём (общие знания GigaChat).

    Режимы:
      GENERAL        -> сразу None (обычный ответ, без блокировки историей).
      CHAT_REQUIRED  -> только история; нет данных -> «В переписке этого не нашёл».
      CHAT_PREFERRED -> сначала история; нет убедительных данных -> None (фолбэк).
    """
    cls = await classify(question, llm)
    mode = cls.get("mode", "GENERAL")
    query = cls.get("chat_search_query") or question

    # Подстраховка от слабого классификатора (Lite): если в вопросе есть известный
    # участник группы или явные маркеры истории, не отдаём его в GENERAL —
    # минимум проверяем переписку (CHAT_PREFERRED, с фолбэком в обычный ответ).
    from . import aliases
    if mode == "GENERAL" and (
        aliases.resolve_query_person(question) is not None
        or history.looks_like_history_question(question)
    ):
        mode = "CHAT_PREFERRED"
        logger.info("Router override: GENERAL -> CHAT_PREFERRED (известный алиас/маркер)")

    logger.info("Router mode=%s query=%r", mode, query[:120])

    if mode == "GENERAL":
        return None

    if not history.history_available():
        return NOT_FOUND if mode == "CHAT_REQUIRED" else None

    result = await _run_history(question, query, llm, max_passes=max_passes)
    good = result["found"] and not _insufficient(result["confidence"])

    if mode == "CHAT_REQUIRED":
        # неуверенный/пустой ответ в REQUIRED = «не нашёл», без фантазий модели
        return result["answer"] if good else NOT_FOUND

    # CHAT_PREFERRED: нет убедительных данных -> обычный вопрос (не «не нашёл»)
    return result["answer"] if good else None


# обратная совместимость со старым именем
answer_history = route_answer
