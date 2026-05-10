import os, random, requests, textwrap, tempfile, io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WP_BASE = os.getenv("WP_BASE", "https://cryptalysts.com").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID", "")
MAX_PUBLIC_FEED_POSTS = int(os.getenv("MAX_PUBLIC_FEED_POSTS", "2"))
PROMO_EVERY_RUN = os.getenv("PROMO_EVERY_RUN", "true").lower() == "true"
BRAND = "CRYPTALYSTS LIVE SIGNAL"
PRIMEPULSE = "Visibility tools powered by PrimePulse"

def require_env():
    missing = [k for k, v in {"WP_USERNAME": WP_USERNAME, "WP_APP_PASSWORD": WP_APP_PASSWORD, "BOT_TOKEN": BOT_TOKEN, "PUBLIC_CHANNEL_ID": PUBLIC_CHANNEL_ID}.items() if not v]
    if missing:
        raise SystemExit("Missing env/secrets: " + ", ".join(missing))

def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def smart_hashtags(chain, category):
    tags = ["#Crypto", "#NewToken", "#EarlySignal", "#Cryptalysts"]
    c = str(chain or "").lower()
    cat = str(category or "").lower()
    for k, v in {"solana":"#Solana","base":"#Base","ethereum":"#Ethereum","eth":"#Ethereum","polygon":"#Polygon","bsc":"#BSC","binance":"#BSC","arbitrum":"#Arbitrum"}.items():
        if k in c:
            tags.insert(1, v)
            break
    if "meme" in cat: tags.append("#MemeCoin")
    if "ai" in cat: tags.append("#AI")
    if "defi" in cat: tags.append("#DeFi")
    if "gaming" in cat: tags.append("#Gaming")
    return " ".join(dict.fromkeys(tags))

def wp_get_items():
    r = requests.get(f"{WP_BASE}/wp-json/cryptalysts/v1/public-feed-items", params={"limit": MAX_PUBLIC_FEED_POSTS}, auth=(WP_USERNAME, WP_APP_PASSWORD), timeout=30)
    print("[WP GET]", r.status_code, r.text[:700])
    r.raise_for_status()
    return r.json().get("items", [])

def wp_mark(post_id):
    r = requests.post(f"{WP_BASE}/wp-json/cryptalysts/v1/public-feed-mark", json={"post_id": post_id}, auth=(WP_USERNAME, WP_APP_PASSWORD), timeout=30)
    print("[WP MARK]", post_id, r.status_code, r.text[:300])

def get_font(size, bold=False):
    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def download_logo(url):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent":"CryptalystsPublicFeed/2.0"})
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception as e:
        print("[LOGO ERROR]", repr(e))
        return None

def make_card(item):
    W, H = 1200, 1200
    bg = Image.new("RGB", (W, H), "#05070b")
    draw = ImageDraw.Draw(bg)
    draw.rounded_rectangle((70, 70, 1130, 1130), radius=42, outline="#1f2937", width=3, fill="#080b12")
    draw.rounded_rectangle((90, 90, 1110, 1110), radius=36, outline="#8cff00", width=2)

    f12 = get_font(28, True); f16 = get_font(34, True); f28 = get_font(68, True); f40 = get_font(96, True); fsmall = get_font(26, False)
    draw.text((120, 125), BRAND, font=f12, fill="#8cff00")

    title = str(item.get("title") or "New Token")
    symbol = str(item.get("symbol") or "")
    name = f"{title} ({symbol})" if symbol else title
    y = 190
    for line in textwrap.wrap(name, width=20)[:3]:
        draw.text((120, y), line, font=f28, fill="white")
        y += 78

    logo = download_logo(item.get("logo_url"))
    if logo:
        logo.thumbnail((300, 300))
        mask = Image.new("L", logo.size, 0)
        md = ImageDraw.Draw(mask)
        md.rounded_rectangle((0, 0, logo.size[0], logo.size[1]), radius=36, fill=255)
        bg.paste(logo, (780, 210), mask)
    else:
        draw.rounded_rectangle((780, 210, 1030, 460), radius=36, fill="#101827", outline="#263244", width=2)
        draw.text((835, 310), "CLX", font=f40, fill="#8cff00")

    chain = str(item.get("chain") or "Unknown")
    category = str(item.get("category") or "Early Token")
    score = int(item.get("signal_score") or item.get("ai_visibility_score") or 0)

    draw.rounded_rectangle((120, 530, 1080, 820), radius=28, fill="#0d111b", outline="#1f2937", width=2)
    yy = 570
    for label, value in [("CHAIN", chain), ("CATEGORY", category), ("LIVE SIGNAL SCORE", f"{score}/100"), ("FEATURED", "AVAILABLE")]:
        draw.text((160, yy), label, font=fsmall, fill="#8cff00")
        draw.text((540, yy), value, font=f16, fill="white")
        yy += 58

    draw.text((120, 900), "Indexed on Cryptalysts", font=f16, fill="white")
    draw.text((120, 955), "Claim your listing for free and unlock visibility tools.", font=fsmall, fill="#cbd5e1")
    draw.text((120, 1040), PRIMEPULSE, font=fsmall, fill="#8cff00")
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    bg.save(out.name, "PNG")
    return out.name

def token_caption(item):
    title = esc(item.get("title", "New Token")); symbol = esc(item.get("symbol", ""))
    chain = esc(item.get("chain", "Unknown")); category = esc(item.get("category", "Early Token"))
    score = int(item.get("signal_score") or item.get("ai_visibility_score") or 0)
    listing = item.get("listing_url", ""); claim = item.get("claim_url", "")
    name_line = f"{title} ({symbol})" if symbol else title
    return f"""🚨 <b>NEW TOKEN DETECTED</b>

<b>{name_line}</b>

• Chain: <b>{chain}</b>
• Category: <b>{category}</b>
• LIVE SIGNAL SCORE: <b>{score}/100</b>
• FEATURED: <b>AVAILABLE</b>

🔗 <b>Live listing:</b>
{listing}

⚡ <b>Project owner?</b>
Claim your listing for free:
{claim}

<i>{PRIMEPULSE}</i>

{smart_hashtags(chain, category)}"""

def tg_send_photo(image_path, caption):
    with open(image_path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"chat_id": PUBLIC_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, files={"photo": f}, timeout=60)
    print("[TG PHOTO]", r.status_code, r.text[:500])
    r.raise_for_status()

def tg_send(text):
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": PUBLIC_CHANNEL_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}, timeout=30)
    print("[TG SEND]", r.status_code, r.text[:500])
    r.raise_for_status()

def promo_post():
    return random.choice([
"""⚡ <b>Visibility starts before momentum.</b>

Cryptalysts indexes early-stage crypto projects before the crowd notices.

• Live token discovery
• Ranking signals
• Claim tools
• Featured placements
• PrimePulse visibility layer

🔗 https://cryptalysts.com

#Crypto #TokenDiscovery #EarlySignal""",
"""🚀 <b>Featured slots are open.</b>

Early projects can push visibility with:
• homepage placement
• ranking boost
• Telegram feed exposure
• public listing authority
• PrimePulse campaign access

🔗 https://cryptalysts.com/feature-your-token/

#PrimePulse #CryptoVisibility #TokenLaunch""",
"""📡 <b>Builders need signal before the chart moves.</b>

Cryptalysts gives new tokens a public listing, claim path and visibility layer.

Claim. Build signal. Get seen.

🔗 https://cryptalysts.com

#Web3 #NewTokens #CryptoMarketing"""
])

def main():
    require_env()
    sent = 0
    for item in wp_get_items():
        try:
            card = make_card(item)
            tg_send_photo(card, token_caption(item))
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
