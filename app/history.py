"""Локальный поиск по истории Telegram-группы.

Без внешних сервисов и векторных баз — чистый Python, совместимо с Vercel Hobby.
Работает так:
  1. Один раз на экземпляр функции лениво читаем data/chat_history.json.
  2. Нормализуем сообщения (автор -> канон, дата, текст).
  3. Строим инвертированный индекс и считаем BM25 по запросу.
  4. Если в запросе есть человек — фильтруем по автору (с учётом алиасов).
  5. Если в запросе есть период ("в этом году", "за лето"...) — фильтруем по дате.
  6. Возвращаем только релевантные фрагменты и отдаём их GigaChat.
Ничего не выдумываем: нет попаданий — контекста нет.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime
from functools import lru_cache
from typing import Any

from . import aliases

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "chat_history.json"
)

# --- нормализация текста -----------------------------------------------------

_WORD_RE = re.compile(r"[0-9a-zA-Zа-яё]+", re.UNICODE)

# синонимы -> общий токен (и в индексе, и в запросе), чтобы Питер/СПб/Санкт-Петербург
# считались одним и тем же.
_SYNONYMS: dict[str, str] = {
    "спб": "питер",
    "питер": "питер",
    "санкт": "питер",
    "петербург": "питер",
    "петербурге": "питер",
    "премия": "преми",
    "премии": "преми",
    "премию": "преми",
    "премий": "преми",
    "бонус": "бонус",
    "бонуса": "бонус",
    "бонусы": "бонус",
    "энджелс": "ангелс",
    "ангелс": "ангелс",
    "angels": "ангелс",
    "энжелс": "ангелс",
}

_STEM_ENDINGS = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
    "ая", "яя", "ое", "ее", "ый", "ий", "ой", "ые", "ие", "ов", "ев",
    "ах", "ях", "ам", "ям", "ом", "ем", "ую", "юю", "ей",
    "а", "я", "о", "е", "у", "ю", "ы", "и", "ь",
)


def _stem(token: str) -> str:
    if token in _SYNONYMS:
        return _SYNONYMS[token]
    if len(token) <= 3:
        return token
    for end in _STEM_ENDINGS:
        if token.endswith(end) and len(token) - len(end) >= 3:
            base = token[: -len(end)]
            return _SYNONYMS.get(base, base)
    return token


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _WORD_RE.findall(normalize(text))]


# --- разбор дат --------------------------------------------------------------

_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def _year_bounds(year: int) -> tuple[int, int]:
    start = int(datetime(year, 1, 1).timestamp())
    end = int(datetime(year + 1, 1, 1).timestamp())
    return start, end


def parse_date_filter(query: str, now: datetime | None = None) -> tuple[int, int] | None:
    """Возвращает (start_ts, end_ts) для периода из запроса или None."""
    now = now or datetime.now()
    n = normalize(query)

    if "прошл" in n and "год" in n:
        return _year_bounds(now.year - 1)
    if "этом год" in n or "это год" in n or "в этом" in n and "год" in n:
        return _year_bounds(now.year)
    if "за лет" in n or "летом" in n:
        start = int(datetime(now.year, 6, 1).timestamp())
        end = int(datetime(now.year, 9, 1).timestamp())
        return start, end
    for stem, month in _MONTHS.items():
        if re.search(rf"\bв {stem}\w*", n):
            start = int(datetime(now.year, month, 1).timestamp())
            nxt = month % 12 + 1
            ny = now.year + (1 if nxt == 1 else 0)
            end = int(datetime(ny, nxt, 1).timestamp())
            return start, end
    return None


# --- разбор сумм -------------------------------------------------------------

_MONEY_RE = re.compile(
    r"(\d[\d\s.,]*)\s*"
    r"(млн|миллион\w*|тыс\w*|к\b|round|р\b|руб\w*|₽)?",
    re.IGNORECASE,
)


def extract_amounts(text: str) -> list[int]:
    """Достаёт денежные суммы из текста в рублях. '620 тыс' -> 620000."""
    out: list[int] = []
    n = normalize(text)
    for m in re.finditer(r"(\d[\d\s.,]*\d|\d)\s*(млн|миллион\w*|тыс\w*|к|руб\w*|р|₽)?", n):
        raw, unit = m.group(1), (m.group(2) or "")
        digits = raw.replace(" ", "").replace(",", ".")
        try:
            value = float(digits)
        except ValueError:
            continue
        if unit.startswith("млн") or unit.startswith("миллион"):
            value *= 1_000_000
        elif unit.startswith("тыс") or unit == "к":
            value *= 1_000
        if value >= 1000:  # отсекаем мелкие числа, не похожие на суммы
            out.append(int(value))
    return out


# --- загрузка и индекс -------------------------------------------------------

class _Index:
    def __init__(self, records: list[dict[str, Any]]):
        self.records = records
        self.doc_tokens: list[list[str]] = []
        self.df: dict[str, int] = {}
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.doc_len: list[int] = []
        self._build()

    def _build(self) -> None:
        for i, rec in enumerate(self.records):
            toks = tokenize(rec["text"])
            self.doc_tokens.append(toks)
            self.doc_len.append(len(toks))
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, c in tf.items():
                self.df[t] = self.df.get(t, 0) + 1
                self.postings.setdefault(t, []).append((i, c))
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.N = len(self.records)

        # карты для reply-цепочек и соседей (используются retrieval.py)
        self.id_to_idx: dict[int, int] = {}
        self.children: dict[int, list[int]] = {}
        for i, rec in enumerate(self.records):
            mid = rec.get("id")
            if mid is not None:
                self.id_to_idx[mid] = i
            parent = rec.get("reply_to")
            if parent is not None:
                self.children.setdefault(parent, []).append(i)

    def bm25(
        self,
        query_tokens: list[str],
        candidates: set[int] | None = None,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> dict[int, float]:
        scores: dict[int, float] = {}
        for t in set(query_tokens):
            postings = self.postings.get(t)
            if not postings:
                continue
            idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for doc_idx, tf in postings:
                if candidates is not None and doc_idx not in candidates:
                    continue
                dl = self.doc_len[doc_idx] or 1
                denom = tf + k1 * (1 - b + b * dl / (self.avgdl or 1))
                scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * (tf * (k1 + 1)) / denom
        return scores


@lru_cache(maxsize=1)
def _load_index() -> _Index | None:
    path = os.getenv("CHAT_HISTORY_PATH", DEFAULT_HISTORY_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        logger.error("История не найдена по пути %s — поиск по переписке отключён", path)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("История повреждена (%s): %s — поиск отключён", path, exc)
        return None

    records: list[dict[str, Any]] = []
    for m in raw:
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        records.append(
            {
                "id": m.get("id"),
                "author": aliases.canonical_for(m.get("from_id"), m.get("from")),
                "date": (m.get("date") or "")[:10],
                "ts": int(m.get("ts") or 0),
                "text": text,
                "reply_to": m.get("reply_to"),
            }
        )
    logger.info("История загружена: %s сообщений из %s", len(records), path)
    return _Index(records)


def history_available() -> bool:
    return _load_index() is not None


# --- публичный поиск ---------------------------------------------------------

def get_index() -> "_Index | None":
    return _load_index()


def search_indices(
    query: str,
    limit: int = 8,
    min_score: float = 0.3,
    *,
    extra_terms: list[str] | None = None,
    person: str | None = None,
    date_range: tuple[int, int] | None = None,
    literal: bool = False,
) -> list[int]:
    """Возвращает позиции релевантных сообщений. Локальный поисковый слой:
    фильтр по автору/дате + BM25 + буст сумм. Никаких выводов — только кандидаты.

    literal=True: ищем сами слова запроса (включая клички) по ВСЕМ авторам, без
    фильтра по автору и без выбрасывания алиасов из токенов. Нужно для вопросов
    вида «кто такой Дрочер», где важно найти сообщения СО словом, а не сообщения
    самого человека."""
    index = _load_index()
    if index is None:
        return []

    if not literal:
        if person is None:
            person = aliases.resolve_query_person(query)
    if date_range is None:
        date_range = parse_date_filter(query)

    candidates: set[int] | None = None
    if person is not None:
        candidates = {i for i, r in enumerate(index.records) if r["author"] == person}
    if date_range is not None:
        start, end = date_range
        in_range = {i for i, r in enumerate(index.records) if start <= r["ts"] < end}
        candidates = in_range if candidates is None else (candidates & in_range)

    stop = _STOPWORDS if literal else (_STOPWORDS | _PERSON_STOP)
    q_tokens = [t for t in tokenize(query) if t not in stop]
    for term in extra_terms or []:
        q_tokens.extend(t for t in tokenize(term) if t not in _STOPWORDS)
    scores = index.bm25(q_tokens, candidates=candidates)

    if any(w in normalize(query) for w in _MONEY_MARKERS):
        for doc_idx in list(scores):
            if extract_amounts(index.records[doc_idx]["text"]):
                scores[doc_idx] += 3.0

    if not scores and candidates:
        ranked = sorted(candidates, key=lambda i: index.records[i]["ts"], reverse=True)
        return ranked[:limit]

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [i for i, s in ranked if s >= min_score][:limit]


def search(query: str, limit: int = 8, min_score: float = 0.3) -> list[dict[str, Any]]:
    index = _load_index()
    if index is None:
        return []
    idxs = search_indices(query, limit=limit, min_score=min_score)
    return [index.records[i] for i in idxs]


# частые слова-связки, которые только зашумляют ранжирование запроса
_MONEY_MARKERS = (
    "сумм", "преми", "бонус", "сколько", "заплат", "выплат", "получил",
    "зп", "зарплат", "деньг", "стоит", "цена", "к получил",
)

_STOPWORDS = {
    _stem(w) for w in (
        "что", "как", "где", "когда", "кто", "кого", "кому", "чем", "про",
        "это", "был", "была", "было", "были", "есть", "для", "или", "тот",
        "там", "так", "вот", "уже", "ещё", "еще", "нам", "они", "она", "оно",
        "говорил", "говорила", "говорили", "сказал", "писал", "какой", "какая",
        "сумму", "получил", "тусил", "рассказывал", "предлагал",
        # временные слова уже отрабатывает фильтр по дате — в скоринге не нужны
        "этом", "году", "год", "прошлом", "прошлый", "лете", "летом",
        "месяц", "месяце", "раз", "последний", "последнее", "какую", "на",
    )
}

# токены-имена не должны участвовать в текстовом BM25 (мы уже отфильтровали по автору)
_PERSON_STOP = {
    _stem(normalize(a)) for names in aliases.ALIASES.values() for a in names if " " not in a
}


def looks_like_history_question(query: str) -> bool:
    """Грубая эвристика: похоже ли, что вопрос про историю группы."""
    if aliases.resolve_query_person(query) is not None:
        return True
    n = normalize(query)
    markers = (
        "переписк", "в чате", "в группе", "говорил", "писал", "обсужда",
        "премии", "премию", "премий", "когда", "кто предлаг", "с кем",
    )
    return any(m in n for m in markers)


def build_context(query: str, limit: int = 8) -> str | None:
    """Готовый блок для GigaChat с найденными фрагментами. None — если пусто."""
    if not history_available():
        if looks_like_history_question(query):
            return "__HISTORY_UNAVAILABLE__"
        return None

    hits = search(query, limit=limit)
    if not hits:
        return None

    blocks = []
    for i, r in enumerate(hits, 1):
        blocks.append(
            f"[Сообщение {i}]\n"
            f"Автор: {r['author']}\n"
            f"Дата: {r['date']}\n"
            f"Текст: {r['text']}"
        )
    return "\n\n".join(blocks)
