from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .clients import GigaChatClient, TelegramClient
from .config import Settings
from .logic import build_model_input, message_text, should_answer, truncate_answer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("cubalizator")

settings = Settings.from_env()
telegram = TelegramClient(settings.telegram_bot_token)
gigachat = GigaChatClient(settings)
bot_id: int | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global bot_id
    me = await telegram.get_me()
    bot_id = int(me["id"])
    actual_username = str(me.get("username") or "")
    logger.info(
        "Bot initialized username=%s id=%s allowed_chat_id=%s model=%s",
        actual_username,
        bot_id,
        settings.allowed_chat_id,
        settings.gigachat_model,
    )
    yield


app = FastAPI(title="CubaLizator Bot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/telegram/webhook/{path_secret}")
async def telegram_webhook(
    path_secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    if path_secret != settings.webhook_path_secret:
        raise HTTPException(status_code=404, detail="Not found")
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = await request.json()
    message: dict[str, Any] | None = update.get("message")
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

    # Временная команда, чтобы узнать ID группы до фиксации ALLOWED_CHAT_ID.
    if text.lower().startswith("/chatid"):
        await telegram.send_reply(chat_id, message_id, f"ID чата: {chat_id}")
        return JSONResponse({"ok": True})

    if settings.allowed_chat_id is not None and chat_id != settings.allowed_chat_id:
        return JSONResponse({"ok": True})

    if not should_answer(
        message,
        bot_username=settings.bot_username,
        bot_id=bot_id,
        allow_reply_to_bot=settings.allow_reply_to_bot,
    ):
        return JSONResponse({"ok": True})

    model_input = build_model_input(message, settings.bot_username)

    try:
        await telegram.send_chat_action(chat_id)
        answer = await gigachat.ask(model_input)
        answer = truncate_answer(answer, settings.max_answer_chars)
        await telegram.send_reply(chat_id, message_id, answer)
    except httpx.TimeoutException:
        logger.exception("GigaChat timeout")
        await telegram.send_reply(chat_id, message_id, "Завис. Повтори чуть позже.")
    except httpx.HTTPStatusError as exc:
        logger.exception("External API error status=%s", exc.response.status_code)
        await telegram.send_reply(chat_id, message_id, "API прилегло. Попробуй позже.")
    except Exception:
        logger.exception("Unexpected update processing error")
        await telegram.send_reply(chat_id, message_id, "Технически отъехал. Повтори позже.")

    return JSONResponse({"ok": True})
