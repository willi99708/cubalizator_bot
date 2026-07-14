from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _clean(name: str, default: str) -> str:
    """Читает переменную и срезает пробелы/переносы строк по краям.
    Спасает от невидимого '\\n', который прилетает при копировании
    значения в поле Vercel и роняет httpx при сборке URL/заголовков."""
    return (os.getenv(name) or default).strip()


def _req(name: str) -> str:
    """Обязательная переменная + та же чистка от пробелов/переносов."""
    return os.environ[name].strip()


def _model(name: str, default: str) -> str:
    """Читает имя модели, отсекая типовые ошибки настройки:
    пустое значение или случайно вписанное имя самой переменной
    (например GIGACHAT_MODEL=GIGACHAT_MODEL) -> берём дефолт."""
    value = (os.getenv(name) or "").strip()
    if not value or value.upper() == name.upper():
        return default
    return value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    bot_username: str
    telegram_webhook_secret: str
    webhook_path_secret: str
    allow_reply_to_bot: bool

    gigachat_auth_key: str
    gigachat_scope: str
    gigachat_model: str
    gigachat_oauth_url: str
    gigachat_chat_url: str
    gigachat_verify_ssl: bool
    gigachat_ca_bundle: str | None
    gigachat_timeout_seconds: float

    max_answer_chars: int
    chat_history_path: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_bot_token=_req("TELEGRAM_BOT_TOKEN"),
            bot_username=_clean("BOT_USERNAME", "cubalizator_bot").lstrip("@"),
            telegram_webhook_secret=_req("TELEGRAM_WEBHOOK_SECRET"),
            webhook_path_secret=_req("WEBHOOK_PATH_SECRET"),
            allow_reply_to_bot=_bool("ALLOW_REPLY_TO_BOT", False),
            gigachat_auth_key=_req("GIGACHAT_AUTH_KEY"),
            gigachat_scope=_clean("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            gigachat_model=_model("GIGACHAT_MODEL", "GigaChat-2-Max"),
            gigachat_oauth_url=_clean(
                "GIGACHAT_OAUTH_URL",
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            ),
            gigachat_chat_url=_clean(
                "GIGACHAT_CHAT_URL",
                "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
            ),
            gigachat_verify_ssl=_bool("GIGACHAT_VERIFY_SSL", False),
            gigachat_ca_bundle=os.getenv("GIGACHAT_CA_BUNDLE") or None,
            gigachat_timeout_seconds=float(os.getenv("GIGACHAT_TIMEOUT_SECONDS", "60")),
            max_answer_chars=int(os.getenv("MAX_ANSWER_CHARS", "700")),
            chat_history_path=os.getenv("CHAT_HISTORY_PATH") or None,
        )
