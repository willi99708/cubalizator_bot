from __future__ import annotations

import os

import httpx


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    public_url = os.environ["PUBLIC_URL"].rstrip("/")
    path_secret = os.environ["WEBHOOK_PATH_SECRET"]
    webhook_secret = os.environ["TELEGRAM_WEBHOOK_SECRET"]

    url = f"{public_url}/telegram/webhook/{path_secret}"
    response = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": url,
            "secret_token": webhook_secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    print(response.json())
    print(f"Webhook: {url}")


if __name__ == "__main__":
    main()
