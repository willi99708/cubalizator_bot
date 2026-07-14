# cubalizator_bot

Telegram-фактчекер на GigaChat. Отвечает только при упоминании `@cubalizator_bot`; при упоминании в ответе на чужое сообщение передаёт цитату в GigaChat.

## Локальная проверка

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Для локального webhook нужен публичный HTTPS URL. Основной вариант развёртывания — Yandex Cloud Serverless Containers.

## Переменные окружения

Скопируйте `.env.example` в `.env` только для локальной работы. `.env` не коммитить.

`ALLOW_REPLY_TO_BOT=false` означает: бот отвечает только при `@упоминании`. Если поставить `true`, он также продолжит разговор при обычном ответе на сообщение самого бота.

## Сборка Docker

```bash
docker build -t cubalizator:latest .
```

## Webhook

После публикации контейнера задайте локально переменные `TELEGRAM_BOT_TOKEN`, `PUBLIC_URL`, `WEBHOOK_PATH_SECRET`, `TELEGRAM_WEBHOOK_SECRET`, затем выполните:

```bash
python scripts/set_webhook.py
```

URL будет таким:

```text
https://<public-container-url>/telegram/webhook/<WEBHOOK_PATH_SECRET>
```

## Получение ID группы

Пока `ALLOWED_CHAT_ID` пустой, добавьте бота в группу и отправьте:

```text
/chatid@cubalizator_bot
```

После получения отрицательного ID добавьте его в переменную окружения `ALLOWED_CHAT_ID` и создайте новую ревизию контейнера.
