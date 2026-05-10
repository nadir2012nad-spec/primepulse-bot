import os
import time
import asyncio
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, ChatWriteForbiddenError

WP_BASE = os.getenv("WP_BASE", "https://cryptalysts.com").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "")

MAX_SENDS = int(os.getenv("MAX_TELEGRAM_SENDS", "3"))
PRIORITY = os.getenv("OUTREACH_PRIORITY", "HIGH")
SLEEP_BETWEEN_SENDS = int(os.getenv("SLEEP_BETWEEN_SENDS", "35"))
UA = "CryptalystsOutreachDispatcher/1.0"

def wp_get_leads():
    url = f"{WP_BASE}/wp-json/cryptalysts/v1/outreach-leads"
    r = requests.get(
        url,
        params={"limit": MAX_SENDS, "priority": PRIORITY},
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=30,
    )
    print("[WP GET]", r.status_code, r.text[:500])
    r.raise_for_status()
    return r.json().get("items", [])

def wp_update(post_id, status, sent_to="", error=""):
    url = f"{WP_BASE}/wp-json/cryptalysts/v1/outreach-update"
    payload = {"post_id": post_id, "status": status, "sent_to": sent_to, "error": error[:900]}
    r = requests.post(
        url,
        json=payload,
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    print("[WP UPDATE]", post_id, status, r.status_code, r.text[:300])
    return r.ok

def validate_env():
    missing = []
    for k, v in {
        "WP_USERNAME": WP_USERNAME,
        "WP_APP_PASSWORD": WP_APP_PASSWORD,
        "TELEGRAM_API_ID": TELEGRAM_API_ID,
        "TELEGRAM_API_HASH": TELEGRAM_API_HASH,
        "TELEGRAM_SESSION": TELEGRAM_SESSION,
    }.items():
        if not v:
            missing.append(k)
    if missing:
        raise SystemExit("Missing secrets/env: " + ", ".join(missing))

async def main():
    validate_env()
    leads = wp_get_leads()

    if not leads:
        print("No Telegram leads to contact.")
        return

    client = TelegramClient(StringSession(TELEGRAM_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    sent = 0

    async with client:
        me = await client.get_me()
        print(f"Telegram connected as: {getattr(me, 'username', None) or me.id}")

        for lead in leads:
            if sent >= MAX_SENDS:
                break

            post_id = lead["post_id"]
            tg = lead["telegram"]
            title = lead["title"]
            message = lead["message"]

            print(f"[SEND TRY] {title} -> {tg}")

            try:
                entity = await client.get_entity(tg)
                await client.send_message(entity, message, link_preview=False)
                wp_update(post_id, "CONTACTED", sent_to=tg)
                sent += 1
                print(f"[SENT] {title} -> {tg}")
                time.sleep(SLEEP_BETWEEN_SENDS)

            except FloodWaitError as e:
                error = f"FloodWait {e.seconds}s"
                print("[FLOOD]", error)
                wp_update(post_id, "WAITING", sent_to=tg, error=error)
                break

            except (UserPrivacyRestrictedError, ChatWriteForbiddenError) as e:
                error = f"Cannot message this Telegram target: {type(e).__name__}"
                print("[FORBIDDEN]", error)
                wp_update(post_id, "FAILED", sent_to=tg, error=error)

            except Exception as e:
                error = f"{type(e).__name__}: {str(e)}"
                print("[ERROR]", error)
                wp_update(post_id, "FAILED", sent_to=tg, error=error)

    print(f"Done. Sent {sent} Telegram messages.")

if __name__ == "__main__":
    asyncio.run(main())
