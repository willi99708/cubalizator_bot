from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import history
from .clients import GigaChatClient, TelegramClient
from .config import Settings
from .logic import (
    build_model_input,
    extract_question,
    message_text,
    should_answer,
    truncate_answer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("cubalizator")

settings = Settings.from_env()
telegram = TelegramClient(settings.telegram_bot_token)
gigachat = GigaChatClient(settings)

try:
    bot_id: int | None = int(settings.telegram_bot_token.split(":", 1)[0])
except (ValueError, IndexError):
    bot_id = None

if settings.chat_history_path:
    os.environ.setdefault("CHAT_HISTORY_PATH", settings.chat_history_path)

app = FastAPI(title="CubaLizator Bot")

logger.info(
    "Application loaded username=%s bot_id=%s model=%s",
    settings.bot_username,
    bot_id,
    settings.gigachat_model,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def extract_message(update: dict[str, Any]) -> dict[str, Any] | None:
    for key in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "business_message",
        "edited_business_message",
    ):
        value = update.get(key)
        if isinstance(value, dict):
            return value
    return None


def is_chatid_command(text: str) -> bool:
    if not text:
        return False
    first_token = text.strip().split(maxsplit=1)[0]
    return first_token.split("@", 1)[0].lower() == "/chatid"


@app.post("/telegram/webhook/{path_secret}")
async def telegram_webhook(
    path_secret: str,
    request: Request,
) -> JSONResponse:
    if path_secret != settings.webhook_path_secret:
        raise HTTPException(status_code=404, detail="Not found")

    update: dict[str, Any] = await request.json()
    message = extract_message(update)

    logger.info(
        "Telegram update id=%s keys=%s has_message=%s",
        update.get("update_id"),
        list(update.keys()),
        message is not None,
    )

    if not message:
        return JSONResponse({"ok": True})

    sender = message.get("from") or {}
    if sender.get("is_bot"):
        return JSONResponse({"ok": True})

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return JSONResponse({"ok": True})

    text = message_text(message)
    logger.info(
        "Message chat_id=%s message_id=%s text=%r",
        chat_id,
        message_id,
        text[:300],
    )

    try:
        if is_chatid_command(text):
            await telegram.send_reply(chat_id, message_id, f"ID чата: {chat_id}")
            return JSONResponse({"ok": True})

        if not should_answer(
            message,
            bot_username=settings.bot_username,
            bot_id=bot_id,
            allow_reply_to_bot=settings.allow_reply_to_bot,
            is_private=chat.get("type") == "private",
        ):
            logger.info("Ignored message without mention/reply")
            return JSONResponse({"ok": True})

        model_input = build_model_input(message, settings.bot_username)

        question = extract_question(message, settings.bot_username)
        history_ctx = history.build_context(question)
        if history_ctx == "__HISTORY_UNAVAILABLE__":
            model_input = (
                "История группы сейчас недоступна.\n\n" + model_input
            )
        elif history_ctx:
            model_input = (
                f"Фрагменты переписки (используй их как источник):\n\n"
                f"{history_ctx}\n\n---\n\n{model_input}"
            )
            logger.info("History context attached for question=%r", question[:120])

        await telegram.send_chat_action(chat_id)
        answer = await gigachat.ask(model_input)
        answer = truncate_answer(answer, settings.max_answer_chars)
        await telegram.send_reply(chat_id, message_id, answer)

    except httpx.TimeoutException:
        logger.exception("External API timeout")
        await telegram.send_reply(chat_id, message_id, "Завис. Повтори чуть позже.")
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "External API error status=%s body=%s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        await telegram.send_reply(chat_id, message_id, "API прилегло. Попробуй позже.")
    except Exception:
        logger.exception("Unexpected update processing error")
        try:
            await telegram.send_reply(
                chat_id,
                message_id,
                "Технически отъехал. Повтори позже.",
            )
        except Exception:
            logger.exception("Could not send fallback response")

    return JSONResponse({"ok": True})
