import os
import time
import asyncio
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, ChatWriteForbiddenError
from telethon.tl.types import Channel, Chat, User

WP_BASE = os.getenv("WP_BASE", "https://cryptalysts.com").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "")

MAX_SENDS = int(os.getenv("MAX_TELEGRAM_SENDS", "3"))
PRIORITY = os.getenv("OUTREACH_PRIORITY", "ALL")
SLEEP_BETWEEN_SENDS = int(os.getenv("SLEEP_BETWEEN_SENDS", "35"))
UA = "CryptalystsOutreachDispatcher/1.2"

def wp_get_leads():
    url = f"{WP_BASE}/wp-json/cryptalysts/v1/outreach-leads"
    r = requests.get(
        url,
        params={"limit": MAX_SENDS, "priority": PRIORITY},
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=30,
    )
    print("[WP GET]", r.status_code, r.text[:800])
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
    print("[WP UPDATE]", post_id, status, r.status_code, r.text[:500])
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

def build_claim_message(lead):
    title = lead.get("title", "your project")
    listing_url = lead.get("listing_url", "").strip()

    return f"""Hi team — {title} has been detected and indexed on Cryptalysts as an early-stage token listing.

Your live listing:
{listing_url}

You can now claim and upgrade this listing for free to unlock:
• visibility boosts
• featured placement access
• ranking improvements
• traffic exposure tools

Claim your listing for free here:
{listing_url}?claim=1
"""

def entity_kind(entity):
    if isinstance(entity, User):
        if getattr(entity, "bot", False):
            return "bot"
        return "user"
    if isinstance(entity, Channel):
        if getattr(entity, "broadcast", False):
            return "channel"
        if getattr(entity, "megagroup", False):
            return "group"
        return "channel_or_group"
    if isinstance(entity, Chat):
        return "group"
    return type(entity).__name__

async def main():
    validate_env()
    leads = wp_get_leads()

    if not leads:
        print("No Telegram leads to contact.")
        return

    client = TelegramClient(StringSession(TELEGRAM_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    sent = 0
    skipped = 0
    failed = 0

    async with client:
        me = await client.get_me()
        print(f"Telegram connected as: {getattr(me, 'username', None) or me.id}")

        for lead in leads:
            if sent >= MAX_SENDS:
                break

            post_id = lead["post_id"]
            tg = lead["telegram"]
            title = lead["title"]
            message = build_claim_message(lead)

            print(f"[SEND TRY] {title} -> {tg}")

            try:
                entity = await client.get_entity(tg)
                kind = entity_kind(entity)
                print(f"[ENTITY] {tg} kind={kind}")

                if kind in ("channel", "group", "channel_or_group"):
                    reason = f"Telegram target is a {kind}, not a direct DM user. Skipped for manual/public contact."
                    print("[SKIPPED]", reason)
                    wp_update(post_id, "SKIPPED", sent_to=tg, error=reason)
                    skipped += 1
                    continue

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
                error = f"{type(e).__name__}: target cannot be messaged directly. Skipped for manual/public contact."
                print("[SKIPPED FORBIDDEN]", error)
                wp_update(post_id, "SKIPPED", sent_to=tg, error=error)
                skipped += 1
                continue

            except Exception as e:
                error = f"{type(e).__name__}: {str(e)}"
                print("[ERROR]", error)
                wp_update(post_id, "FAILED", sent_to=tg, error=error)
                failed += 1
                continue

    print(f"Done. Sent {sent} Telegram messages. Skipped {skipped}. Failed {failed}.")

if __name__ == "__main__":
    asyncio.run(main())
