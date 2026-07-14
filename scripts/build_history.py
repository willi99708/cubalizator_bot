"""Пересборка data/chat_history.json из сырого экспорта Telegram Desktop.

Экспорт (result.json) весит десятки МБ и тащит фото, реакции, сущности —
всё это боту не нужно. Скрипт оставляет только автора, дату и текст,
уменьшая файл примерно вдвое и ускоряя холодный старт.

Запуск:
    python scripts/build_history.py path/to/result.json
    # по умолчанию читает result.json в корне, пишет data/chat_history.json
"""
from __future__ import annotations

import json
import os
import sys


def _flatten(text: object) -> str:
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts = []
        for p in text:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(str(p.get("text", "")))
        return "".join(parts)
    return ""


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else "result.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join("data", "chat_history.json")

    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    out = []
    for m in data.get("messages", []):
        if m.get("type") != "message":
            continue
        text = _flatten(m.get("text", "")).strip()
        if not text:
            continue
        out.append(
            {
                "id": m.get("id"),
                "from_id": m.get("from_id"),
                "from": m.get("from"),
                "date": m.get("date"),
                "ts": int(m.get("date_unixtime", 0)),
                "text": text,
                "reply_to": m.get("reply_to_message_id"),
            }
        )

    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    size_mb = os.path.getsize(dst) / 1e6
    print(f"Готово: {len(out)} сообщений -> {dst} ({size_mb:.1f} МБ)")


if __name__ == "__main__":
    main()
