"""Локальный слой сборки контекста для GigaChat.

Здесь НЕТ смысловых выводов. Задача модуля — только достать вокруг найденных
сообщений всё, что поможет модели понять эпизод: соседей по времени,
сообщение-родитель (на которое отвечали) и ответы на него, — а затем склеить
пересекающиеся окна в эпизоды и убрать дубли.

Все выводы (кто есть кто, что произошло) делает GigaChat, а не этот код.
"""
from __future__ import annotations

from typing import Any

from . import history


def _expand_one(index: Any, idx: int, before: int, after: int) -> set[int]:
    """Индексы вокруг одного попадания: окно по времени + reply-родитель + ответы."""
    picked: set[int] = set()
    lo = max(0, idx - before)
    hi = min(len(index.records) - 1, idx + after)
    picked.update(range(lo, hi + 1))

    rec = index.records[idx]
    parent_id = rec.get("reply_to")
    if parent_id is not None and parent_id in index.id_to_idx:
        picked.add(index.id_to_idx[parent_id])

    mid = rec.get("id")
    if mid is not None:
        picked.update(index.children.get(mid, []))
    return picked


def gather_episodes(
    seed_indices: list[int],
    *,
    before: int = 7,
    after: int = 7,
    max_episodes: int = 6,
) -> list[dict[str, Any]]:
    """Собирает эпизоды вокруг найденных сообщений.

    Возвращает список эпизодов; каждый — набор сообщений с автором, датой,
    message_id и reply_to_message_id, отсортированных по времени.
    Пересекающиеся окна склеиваются, дубли убираются.
    """
    index = history.get_index()
    if index is None or not seed_indices:
        return []

    # собираем все нужные индексы вокруг каждого сида
    all_idx: set[int] = set()
    for s in seed_indices:
        all_idx |= _expand_one(index, s, before, after)

    # склеиваем в непрерывные диапазоны -> эпизоды
    ordered = sorted(all_idx)
    episodes: list[list[int]] = []
    current: list[int] = []
    prev: int | None = None
    for i in ordered:
        if prev is None or i - prev <= 2:  # разрыв <=2 считаем тем же эпизодом
            current.append(i)
        else:
            episodes.append(current)
            current = [i]
        prev = i
    if current:
        episodes.append(current)

    # эпизоды, содержащие сид, важнее — сортируем по числу сидов внутри
    seed_set = set(seed_indices)
    episodes.sort(key=lambda ep: len(seed_set & set(ep)), reverse=True)

    out: list[dict[str, Any]] = []
    for ep in episodes[:max_episodes]:
        msgs = []
        for i in ep:
            r = index.records[i]
            msgs.append(
                {
                    "message_id": r.get("id"),
                    "reply_to_message_id": r.get("reply_to"),
                    "author": r["author"],
                    "date": r["date"],
                    "text": r["text"],
                }
            )
        out.append(
            {
                "start_date": msgs[0]["date"],
                "end_date": msgs[-1]["date"],
                "messages": msgs,
            }
        )
    return out


def format_episodes(episodes: list[dict[str, Any]]) -> str:
    """Текстовое представление эпизодов для передачи в GigaChat."""
    blocks: list[str] = []
    for n, ep in enumerate(episodes, 1):
        lines = [f"=== Эпизод {n} ({ep['start_date']} — {ep['end_date']}) ==="]
        for m in ep["messages"]:
            reply = f" (ответ на #{m['reply_to_message_id']})" if m["reply_to_message_id"] else ""
            lines.append(
                f"#{m['message_id']}{reply} [{m['date']}] {m['author']}: {m['text']}"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
