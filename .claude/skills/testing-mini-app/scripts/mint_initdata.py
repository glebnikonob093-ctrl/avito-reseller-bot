"""Mint a valid Telegram WebApp initData string signed with our BOT_TOKEN.
Prints the URL-encoded querystring on stdout.

Usage:
    BOT_TOKEN="$BOT_TOKEN" python3 mint_initdata.py > /tmp/init_data.txt
"""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

BOT_TOKEN = os.environ["BOT_TOKEN"]
TG_USER_ID = 8675309

user = {
    "id": TG_USER_ID,
    "first_name": "Test",
    "last_name": "User",
    "username": "tester",
    "language_code": "ru",
    "allows_write_to_pm": True,
}

auth_date = int(time.time())
fields = {
    "query_id": "AAEqHelloWorld",
    "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    "auth_date": str(auth_date),
}

# Canonical data_check_string: fields sorted by key, joined with \n.
data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
sig = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

# Build a querystring matching what Telegram's web client emits:
# values are URL-encoded so JSON braces become %7B / %7D.
parts = [f"{k}={quote(fields[k], safe='')}" for k in fields] + [f"hash={sig}"]
print("&".join(parts))
