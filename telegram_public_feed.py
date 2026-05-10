import os, random, requests

WP_BASE = os.getenv("WP_BASE", "https://cryptalysts.com").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID", "")
MAX_PUBLIC_FEED_POSTS = int(os.getenv("MAX_PUBLIC_FEED_POSTS", "2"))
PROMO_EVERY_RUN = os.getenv("PROMO_EVERY_RUN", "true").lower() == "true"

def require_env():
    missing = [k for k, v in {
        "WP_USERNAME": WP_USERNAME,
        "WP_APP_PASSWORD": WP_APP_PASSWORD,
        "BOT_TOKEN": BOT_TOKEN,
        "PUBLIC_CHANNEL_ID": PUBLIC_CHANNEL_ID,
    }.items() if not v]
    if missing:
        raise SystemExit("Missing env/secrets: " + ", ".join(missing))

def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def tg_send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": PUBLIC_CHANNEL_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=30
    )
    print("[TG SEND]", r.status_code, r.text[:500])
    r.raise_for_status()

def wp_get_items():
    r = requests.get(
        f"{WP_BASE}/wp-json/cryptalysts/v1/public-feed-items",
        params={"limit": MAX_PUBLIC_FEED_POSTS},
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        timeout=30
    )
    print("[WP GET]", r.status_code, r.text[:600])
    r.raise_for_status()
    return r.json().get("items", [])

def wp_mark(post_id):
    r = requests.post(
        f"{WP_BASE}/wp-json/cryptalysts/v1/public-feed-mark",
        json={"post_id": post_id},
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        timeout=30
    )
    print("[WP MARK]", post_id, r.status_code, r.text[:300])

def token_post(item):
    title = esc(item.get("title", "New Token"))
    symbol = esc(item.get("symbol", ""))
    chain = esc(item.get("chain", "Unknown"))
    category = esc(item.get("category", "Early Token"))
    ai = int(item.get("ai_visibility_score") or 0)
    vis = int(item.get("visibility_score") or 0)
    trend = int(item.get("trending_score") or 0)
    listing = item.get("listing_url", "")
    claim = item.get("claim_url", "")
    name_line = f"{title} ({symbol})" if symbol else title
    signal = "Early visibility"
    if trend >= 75: signal = "Trending signal detected"
    elif ai >= 75: signal = "Strong visibility signal"
    elif vis >= 60: signal = "Visibility building"
    return f"""🚨 <b>NEW TOKEN DETECTED</b>

<b>{name_line}</b>

• Chain: <b>{chain}</b>
• Category: <b>{category}</b>
• Signal: <b>{signal}</b>
• AI Visibility: <b>{ai}/100</b>

This token has been indexed on Cryptalysts.

🔗 <b>Live listing:</b>
{listing}

⚡ <b>Project owner?</b>
Claim your listing for free:
{claim}

#Crypto #NewToken #EarlySignal #Cryptalysts"""

def promo_post():
    return random.choice([
"""⚡ <b>Visibility starts before momentum.</b>

Cryptalysts tracks early-stage crypto projects and indexes new tokens before the crowd notices.

• Live token discovery
• Public listings
• Ranking signals
• Claim tools
• Featured visibility

🔗 https://cryptalysts.com

#Crypto #TokenDiscovery #EarlySignal""",
"""🚀 <b>Builders: your token needs visibility before the chart moves.</b>

Cryptalysts gives early projects a public listing, ranking signal, claim path and visibility layer.

Claim your listing. Build signal. Get seen.

🔗 https://cryptalysts.com

#Web3 #CryptoMarketing #NewTokens""",
"""📡 <b>PrimePulse visibility layer is live.</b>

Early projects can request:
• homepage placement
• featured visibility
• ranking boost
• market attention
• exposure campaigns

🔗 https://cryptalysts.com/feature-your-token/

#PrimePulse #CryptoVisibility #TokenLaunch"""
])

def main():
    require_env()
    sent = 0
    for item in wp_get_items():
        try:
            tg_send(token_post(item))
            wp_mark(int(item["post_id"]))
            sent += 1
        except Exception as e:
            print("[TOKEN POST ERROR]", item.get("post_id"), repr(e))
    if PROMO_EVERY_RUN:
        try:
            tg_send(promo_post())
            sent += 1
        except Exception as e:
            print("[PROMO ERROR]", repr(e))
    print(f"Done. Public feed messages sent: {sent}")

if __name__ == "__main__":
    main()
