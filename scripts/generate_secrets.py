import secrets

print("TELEGRAM_WEBHOOK_SECRET=" + secrets.token_urlsafe(32).replace("=", ""))
print("WEBHOOK_PATH_SECRET=" + secrets.token_urlsafe(24).replace("=", ""))
