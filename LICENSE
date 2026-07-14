# Деплой в Yandex Cloud

## Что установить на компьютер

1. Git.
2. Docker Desktop.
3. Yandex Cloud CLI (`yc`).
4. Python 3.12 — только для установки webhook и тестов.

## Что загрузить на GitHub

Загрузить весь репозиторий, кроме `.env`.

## Создание Docker-образа

```powershell
yc init
yc container registry configure-docker
yc container registry create --name cubalizator-registry
$REGISTRY_ID = yc container registry get cubalizator-registry --format json | ConvertFrom-Json | Select-Object -ExpandProperty id

docker build -t "cr.yandex/$REGISTRY_ID/cubalizator:latest" .
docker push "cr.yandex/$REGISTRY_ID/cubalizator:latest"
```

## Создание Serverless Container

```powershell
yc serverless container create --name cubalizator
```

В консоли Yandex Cloud:

1. Открыть **Serverless Containers → cubalizator → Редактор**.
2. Образ: `cr.yandex/<REGISTRY_ID>/cubalizator:latest`.
3. Память: 256 MB.
4. Таймаут: 70 секунд.
5. Создать ревизию с переменными ниже.
6. На вкладке **Обзор** включить **Публичный контейнер**.

## Переменные контейнера

```text
TELEGRAM_BOT_TOKEN=<новый токен BotFather>
BOT_USERNAME=cubalizator_bot
TELEGRAM_WEBHOOK_SECRET=<случайный секрет>
WEBHOOK_PATH_SECRET=<случайный секрет для URL>
ALLOW_REPLY_TO_BOT=false
GIGACHAT_AUTH_KEY=<Authorization Key GigaChat>
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-2-Max
GIGACHAT_VERIFY_SSL=false
GIGACHAT_TIMEOUT_SECONDS=60
MAX_ANSWER_CHARS=700
```

Секреты сгенерировать:

```powershell
python scripts/generate_secrets.py
```

## Установка webhook

После создания ревизии скопировать публичный URL контейнера.

В PowerShell локально:

```powershell
$env:TELEGRAM_BOT_TOKEN="<новый токен>"
$env:PUBLIC_URL="https://<id>.containers.yandexcloud.net"
$env:WEBHOOK_PATH_SECRET="<тот же WEBHOOK_PATH_SECRET>"
$env:TELEGRAM_WEBHOOK_SECRET="<тот же TELEGRAM_WEBHOOK_SECRET>"
python scripts/set_webhook.py
```


## BotFather

1. `/setjoingroups` → Enable.
2. Privacy Mode можно оставить включённым: бот получает упоминания и ответы на свои сообщения.
3. Если требуется, чтобы бот видел все групповые сообщения, `/setprivacy` → Disable, затем удалить и повторно добавить бота в группу.
