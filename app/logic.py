from __future__ import annotations

import re
from typing import Any


def message_text(message: dict[str, Any] | None) -> str:
    if not message:
        return ""
    return str(message.get("text") or message.get("caption") or "").strip()


def is_mentioned(text: str, bot_username: str) -> bool:
    if not text:
        return False
    pattern = rf"(?<![\w])@{re.escape(bot_username)}\b"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def strip_mention(text: str, bot_username: str) -> str:
    pattern = rf"(?<![\w])@{re.escape(bot_username)}\b"
    cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" ,:;—-\n\t")


def replied_to_bot(message: dict[str, Any], bot_id: int | None) -> bool:
    if bot_id is None:
        return False
    reply = message.get("reply_to_message") or {}
    sender = reply.get("from") or {}
    return sender.get("id") == bot_id


def should_answer(
    message: dict[str, Any],
    *,
    bot_username: str,
    bot_id: int | None,
    allow_reply_to_bot: bool,
) -> bool:
    text = message_text(message)
    return is_mentioned(text, bot_username) or (
        allow_reply_to_bot and replied_to_bot(message, bot_id)
    )


def build_model_input(message: dict[str, Any], bot_username: str) -> str:
    current = message_text(message)
    question = strip_mention(current, bot_username)

    reply = message.get("reply_to_message") or {}
    replied_text = message_text(reply)

    quote = message.get("quote") or {}
    quote_text = str(quote.get("text") or "").strip()
    checked_text = quote_text or replied_text

    reply_sender = reply.get("from") or {}
    reply_name = (
        reply_sender.get("username")
        or reply_sender.get("first_name")
        or "участник группы"
    )

    parts: list[str] = []
    if checked_text:
        parts.append(
            f"Проверяемое сообщение от {reply_name}:\n{checked_text}"
        )

    if question:
        parts.append(f"Вопрос пользователя:\n{question}")
    elif checked_text:
        parts.append("Задача: проверь процитированный тезис по фактам.")
    else:
        parts.append("Пользователь не указал проверяемый тезис.")

    return "\n\n".join(parts)


def truncate_answer(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{shortened}…"
