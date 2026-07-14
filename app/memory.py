"""Долговременная структурированная память по истории группы.

Файл data/chat_memory.json строится ОДИН РАЗ из всей переписки пакетами через
GigaChat (см. scripts/build_memory.py) — не регулярками. Здесь только загрузка
и локальный поиск по памяти: она помогает быстро найти нужный эпизод, а
подтверждают ответ всегда исходные сообщения из истории.

Схема:
{
  "people": { "<канон>": {"aliases": [...], "description": "..."} },
  "aliases": [ {"alias": "...", "person": "...", "confidence": "..."} ],
  "events": [ {"description","participants","dates","evidence","confidence"} ],
  "inside_jokes": [ {...} ],
  "relationships": [ {...} ]
}
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from . import history

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "chat_memory.json"
)

_EMPTY: dict[str, Any] = {
    "people": {},
    "aliases": [],
    "events": [],
    "inside_jokes": [],
    "relationships": [],
}


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    path = os.getenv("CHAT_MEMORY_PATH", DEFAULT_MEMORY_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("Память %s не найдена — работаем без неё", path)
        return dict(_EMPTY)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Память повреждена (%s): %s — работаем без неё", path, exc)
        return dict(_EMPTY)
    for k, v in _EMPTY.items():
        data.setdefault(k, v)
    return data


def memory_available() -> bool:
    d = _load()
    return bool(d["events"] or d["inside_jokes"] or d["relationships"])


def _entry_text(entry: dict[str, Any]) -> str:
    parts = [str(entry.get("description", ""))]
    parts += [str(x) for x in entry.get("participants", [])]
    parts += [str(x) for x in entry.get("dates", [])]
    parts.append(str(entry.get("alias", "")))
    parts.append(str(entry.get("person", "")))
    return " ".join(parts)


def search_memory(query: str, limit: int = 6) -> list[dict[str, Any]]:
    """Ищет релевантные записи памяти простым пересечением токенов запроса.
    Память маленькая, тяжёлый индекс не нужен."""
    data = _load()
    q_tokens = set(history.tokenize(query))
    if not q_tokens:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for kind in ("events", "inside_jokes", "relationships", "aliases"):
        for entry in data.get(kind, []):
            toks = set(history.tokenize(_entry_text(entry)))
            overlap = len(q_tokens & toks)
            if overlap:
                item = dict(entry)
                item["kind"] = kind
                scored.append((float(overlap), item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:limit]]


def format_memory(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    lines = ["Подсказки из структурированной памяти (требуют подтверждения сообщениями):"]
    for e in entries:
        if e.get("kind") == "aliases" and e.get("alias") and e.get("person"):
            desc = f"«{e['alias']}» — это {e['person']}"
        else:
            desc = e.get("description") or e.get("alias") or ""
        conf = e.get("confidence", "")
        ev = e.get("evidence") or []
        ev_s = f" [сообщения: {', '.join('#'+str(x) for x in ev)}]" if ev else ""
        conf_s = f" ({conf})" if conf else ""
        lines.append(f"- {desc}{conf_s}{ev_s}")
    return "\n".join(lines)
