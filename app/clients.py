from __future__ import annotations

import logging
import ssl
import time
import uuid
from typing import Any

import httpx

from .config import Settings
from .prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, token: str, timeout: float = 20) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    async def call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/{method}", json=payload or {})
            response.raise_for_status()
            body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API returned ok=false for {method}")
        return body["result"]

    async def get_me(self) -> dict[str, Any]:
        return await self.call("getMe")

    async def send_chat_action(self, chat_id: int) -> None:
        await self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    async def send_reply(self, chat_id: int, message_id: int, text: str) -> None:
        await self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "reply_parameters": {
                    "message_id": message_id,
                    "allow_sending_without_reply": True,
                },
            },
        )


class GigaChatClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._access_token: str | None = None
        self._expires_at_ms = 0

    def _verify(self) -> bool | ssl.SSLContext:
        if self.settings.gigachat_ca_bundle:
            return ssl.create_default_context(cafile=self.settings.gigachat_ca_bundle)
        return self.settings.gigachat_verify_ssl

    async def _get_access_token(self, force: bool = False) -> str:
        now_ms = int(time.time() * 1000)
        if (
            not force
            and self._access_token
            and now_ms < self._expires_at_ms - 60_000
        ):
            return self._access_token

        headers = {
            "Authorization": f"Basic {self.settings.gigachat_auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(
            verify=self._verify(),
            timeout=self.settings.gigachat_timeout_seconds,
        ) as client:
            response = await client.post(
                self.settings.gigachat_oauth_url,
                headers=headers,
                data={"scope": self.settings.gigachat_scope},
            )
            response.raise_for_status()
            body = response.json()

        self._access_token = body["access_token"]
        self._expires_at_ms = int(body["expires_at"])
        return self._access_token

    async def ask(self, content: str) -> str:
        token = await self._get_access_token()
        payload = {
            "model": self.settings.gigachat_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0.1,
            "max_tokens": 220,
            "repetition_penalty": 1.05,
            "stream": False,
        }

        async with httpx.AsyncClient(
            verify=self._verify(),
            timeout=self.settings.gigachat_timeout_seconds,
        ) as client:
            response = await client.post(
                self.settings.gigachat_chat_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
            if response.status_code == 401:
                token = await self._get_access_token(force=True)
                response = await client.post(
                    self.settings.gigachat_chat_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=payload,
                )
            response.raise_for_status()
            body = response.json()

        return str(body["choices"][0]["message"]["content"]).strip()
