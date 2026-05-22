import os
import re
import time
import html
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFont

WP_BASE = os.getenv("WP_BASE", "https://cryptalysts.com").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID", os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID", ""))

MAX_PUBLIC_FEED_POSTS = int(os.getenv("MAX_PUBLIC_FEED_POSTS", "20"))
PUBLIC_FEED_MIN_SCORE = int(os.getenv("PUBLIC_FEED_MIN_SCORE", "35"))
SLEEP_BETWEEN_POSTS = int(os.getenv("SLEEP_BETWEEN_POSTS", "8"))

UA = "CryptalystsPublicTelegramFeed/5.0"

GET_ENDPOINTS = [
    "/wp-json/cryptalysts/v1/public-feed",
    "/wp-json/cryptalysts/v1/pf-feed",
    "/wp-json/cryptalysts/v1/feed-public",
]

UPDATE_ENDPOINTS = [
    "/wp-json/cryptalysts/v1/public-feed-update",
    "/wp-json/cryptalysts/v1/pf-update",
    "/wp-json/cryptalysts/v1/feed-public-update",
]

CHAIN_TAGS = {
    "solana": ["#Solana", "#SOL"],
    "eth": ["#Ethereum", "#ETH"],
    "ethereum": ["#Ethereum", "#ETH"],
    "base": ["#Base"],
    "bsc": ["#BSC", "#BNBChain"],
    "bnb": ["#BSC", "#BNBChain"],
    "polygon": ["#Polygon", "#MATIC"],
    "arbitrum": ["#Arbitrum"],
    "optimism": ["#Optimism"],
}

CATEGORY_TAGS = {
    "ai": "#AI",
    "meme": "#MemeCoin",
    "defi": "#DeFi",
    "gaming": "#GameFi",
    "infrastructure": "#CryptoInfra",
    "trading": "#Trading",
    "launchpad": "#Launchpad",
}

def require_env():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not PUBLIC_CHANNEL_ID:
        missing.append("PUBLIC_CHANNEL_ID / TELEGRAM_PUBLIC_CHANNEL_ID")
    if not WP_USERNAME:
        missing.append("WP_USERNAME")
    if not WP_APP_PASSWORD:
        missing.append("WP_APP_PASSWORD")
    if missing:
        raise SystemExit("Missing env/secrets: " + ", ".join(missing))

def safe_get(d, keys, default=""):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d.get(k)
    return default

def strip_control(s):
    return "".join(ch for ch in str(s or "") if unicodedata.category(ch)[0] != "C").strip()

def clean_symbol(symbol):
    s = strip_control(symbol).upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s[:16]

def broken_name(name):
    name = strip_control(name)
    if not name or len(name) < 2:
        return True
    letters = [c for c in name if c.isalpha()]
    if not letters:
        return True
    ascii_letters = [c for c in letters if ord(c) < 128]
    non_ascii_ratio = 1 - (len(ascii_letters) / max(1, len(letters)))
    return non_ascii_ratio > 0.45

def clean_token_name(name, symbol):
    name = html.unescape(strip_control(name))
    name = re.sub(r"\s+", " ", name).strip()
    symbol = clean_symbol(symbol)

    if broken_name(name):
        return symbol or "TOKEN"

    if len(name) > 34:
        name = name[:31].rstrip() + "..."

    return name

def cashtag(symbol):
    s = clean_symbol(symbol)
    return f"${s}" if s else "$TOKEN"

def normalize_listing_url(item):
    url = safe_get(item, ["listing_url", "url", "permalink", "link"], "")
    if url:
        return str(url).replace("\\/", "/")
    slug = safe_get(item, ["slug"], "")
    if slug:
        return f"{WP_BASE}/listing/{slug}/"
    title = safe_get(item, ["title", "name"], "")
    slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")[:60]
    return f"{WP_BASE}/listing/{slug}/" if slug else WP_BASE

def get_signal_score(item):
    for k in ["ai_visibility_score", "signal_score", "visibility_score", "lead_score", "score", "quality_score"]:
        try:
            v = int(float(item.get(k, 0)))
            if v > 0:
                return max(0, min(100, v))
        except Exception:
            pass
    return 0

def get_chain(item):
    return strip_control(safe_get(item, ["chain", "network"], "crypto")).lower()

def get_category(item):
    return strip_control(safe_get(item, ["project_category", "category"], "Unclassified"))

def should_post(item):
    score = get_signal_score(item)
    if score < PUBLIC_FEED_MIN_SCORE:
        return False, f"score below threshold {score}<{PUBLIC_FEED_MIN_SCORE}"
    return True, "ok"

def wp_get_feed():
    last_error = ""
    for endpoint in GET_ENDPOINTS:
        try:
            r = requests.get(
                f"{WP_BASE}{endpoint}",
                params={"limit": MAX_PUBLIC_FEED_POSTS, "min_score": PUBLIC_FEED_MIN_SCORE, "priority": "ALL"},
                auth=(WP_USERNAME, WP_APP_PASSWORD),
                headers={"User-Agent": UA, "Accept": "application/json", "Cache-Control": "no-cache"},
                timeout=35,
            )
            print("[WP GET]", endpoint, r.status_code, r.text[:700])
            if r.ok:
                data = r.json()
                return data.get("items", []) if isinstance(data, dict) else []
            last_error = f"{endpoint} HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            last_error = f"{endpoint} {type(e).__name__}: {e}"
            print("[WP GET ERROR]", last_error)
    raise RuntimeError("Cannot fetch public feed: " + last_error)

def wp_update(post_id, status="POSTED", error=""):
    if not post_id:
        return False

    payload = {"post_id": post_id, "status": status, "error": error[:500]}

    for endpoint in UPDATE_ENDPOINTS:
        try:
            r = requests.post(
                f"{WP_BASE}{endpoint}",
                json=payload,
                auth=(WP_USERNAME, WP_APP_PASSWORD),
                headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"},
                timeout=30,
            )
            print("[WP UPDATE]", endpoint, post_id, status, r.status_code, r.text[:300])
            if r.ok:
                return True
        except Exception as e:
            print("[WP UPDATE ERROR]", endpoint, repr(e))

    return False

def font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()

def text_fit(draw, text, font_obj, max_width):
    text = str(text or "")
    if draw.textlength(text, font=font_obj) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=font_obj) > max_width:
        text = text[:-1]
    return text.rstrip() + "..."

def score_color(score):
    if score >= 75:
        return (140, 255, 0)
    if score >= 55:
        return (255, 220, 80)
    return (255, 135, 70)

def safe_filename(s):
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", str(s or "token"))[:40]

def create_card(item):
    title_raw = safe_get(item, ["title", "name"], "")
    symbol_raw = safe_get(item, ["symbol"], "")
    sym = clean_symbol(symbol_raw)
    name = clean_token_name(title_raw, sym)
    ctag = cashtag(sym)
    chain = get_chain(item)
    category = get_category(item)
    score = get_signal_score(item)

    W, H = 900, 320
    img = Image.new("RGB", (W, H), (4, 8, 14))
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, H], fill=(4, 8, 14))
    d.ellipse([-120, -180, 420, 360], fill=(13, 45, 20))
    d.ellipse([600, -220, 1050, 330], fill=(20, 10, 38))
    d.rectangle([18, 18, W-18, H-18], outline=(115, 255, 0), width=2)
    d.rectangle([22, 22, W-22, H-22], outline=(33, 55, 41), width=1)

    f_kicker = font(18, True)
    f_title = font(38, True)
    f_small = font(18, False)
    f_tiny = font(15, False)
    f_score = font(44, True)

    d.text((42, 34), "CRYPTALYSTS LIVE SIGNAL", fill=(140, 255, 0), font=f_kicker)
    d.text((42, 74), text_fit(d, f"{ctag}  {name}", f_title, 570), fill=(255, 255, 255), font=f_title)

    d.text((42, 134), "CHAIN", fill=(140, 255, 0), font=f_tiny)
    d.text((160, 134), chain.upper(), fill=(235, 245, 255), font=f_small)

    d.text((42, 166), "CATEGORY", fill=(140, 255, 0), font=f_tiny)
    d.text((160, 166), category.upper()[:28], fill=(235, 245, 255), font=f_small)

    d.text((42, 198), "STATUS", fill=(140, 255, 0), font=f_tiny)
    d.text((160, 198), "EARLY SIGNAL", fill=(235, 245, 255), font=f_small)

    d.text((42, 230), "FEATURED", fill=(140, 255, 0), font=f_tiny)
    d.text((160, 230), "AVAILABLE", fill=(235, 245, 255), font=f_small)

    sx, sy, sw, sh = 652, 60, 196, 150
    d.rounded_rectangle([sx, sy, sx+sw, sy+sh], radius=18, outline=(140, 255, 0), width=2, fill=(6, 16, 22))
    d.text((sx+28, sy+18), "LIVE SIGNAL", fill=(255, 255, 255), font=f_tiny)
    d.text((sx+32, sy+52), f"{score}/100", fill=score_color(score), font=f_score)
    d.text((sx+38, sy+112), "EARLY VISIBILITY", fill=(160, 170, 185), font=f_tiny)

    d.rectangle([42, 274, W-42, 292], fill=(10, 28, 18))
    d.text((52, 271), "Indexed on Cryptalysts · Claim your listing for free", fill=(235, 255, 230), font=f_tiny)

    path = Path(tempfile.gettempdir()) / f"cryptalysts_signal_{safe_filename(sym or name)}_{int(time.time())}.jpg"
    img.save(path, "JPEG", quality=92)
    return str(path)

def build_hashtags(item):
    symbol = clean_symbol(safe_get(item, ["symbol"], ""))
    chain = get_chain(item)
    category = get_category(item).lower()

    tags = []
    if symbol:
        tags.append("#" + re.sub(r"[^A-Z0-9]", "", symbol.upper()))
    tags += CHAIN_TAGS.get(chain, [f"#{chain.upper()}" if chain else "#Crypto"])
    tags.append(CATEGORY_TAGS.get(category, f"#{re.sub(r'[^A-Za-z0-9]', '', category.title())}" if category else "#Crypto"))
    tags += ["#NewToken", "#EarlySignal", "#Cryptalysts"]

    out = []
    seen = set()
    for t in tags:
        if t and t not in seen and len(t) > 1:
            seen.add(t)
            out.append(t)
    return " ".join(out[:8])

def build_caption(item):
    title_raw = safe_get(item, ["title", "name"], "")
    symbol_raw = safe_get(item, ["symbol"], "")
    sym = clean_symbol(symbol_raw)
    ctag = cashtag(sym)
    name = clean_token_name(title_raw, sym)

    chain = get_chain(item).upper()
    category = get_category(item)
    score = get_signal_score(item)
    listing = normalize_listing_url(item)
    claim_url = listing + ("?claim=1" if "?" not in listing else "&claim=1")
    hashtags = build_hashtags(item)

    return (
        f"🚨 <b>{ctag} JUST DETECTED</b>\n\n"
        f"<b>{html.escape(name)}</b> {ctag}\n\n"
        f"• Chain: <b>{html.escape(chain)}</b>\n"
        f"• Category: <b>{html.escape(category)}</b>\n"
        f"• LIVE SIGNAL SCORE: <b>{score}/100</b>\n"
        f"• Status: <b>EARLY SIGNAL</b>\n"
        f"• Featured: <b>AVAILABLE</b>\n\n"
        f"🔎 <b>Live listing:</b>\n"
        f"{html.escape(listing)}\n\n"
        f"⚡ <b>Project owner?</b>\n"
        f"Claim your {ctag} listing for free:\n"
        f"{html.escape(claim_url)}\n\n"
        f"Visibility tools powered by <b>PrimePulseOps.com</b>\n\n"
        f"{ctag} {html.escape(name)}\n"
        f"{hashtags}"
    )

def send_photo(path, caption):
    with open(path, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": PUBLIC_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": f},
            timeout=45,
        )
    print("[TG PHOTO]", r.status_code, r.text[:500])
    r.raise_for_status()
    return r.json()

def send_text(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": PUBLIC_CHANNEL_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=30,
    )
    print("[TG TEXT]", r.status_code, r.text[:500])
    r.raise_for_status()
    return r.json()

def promo_message():
    return (
        "🔥 <b>Early token visibility matters before the market wakes up.</b>\n\n"
        "Cryptalysts indexes early-stage tokens with public signal, live ranking, claim tools and featured visibility.\n\n"
        "🔗 https://cryptalysts.com/feature-your-token/\n\n"
        "#CryptoGrowth #TokenVisibility #PrimePulseOps"
    )

def main():
    require_env()

    items = wp_get_feed()
    if not items:
        print("No public feed items.")
        if os.getenv("POST_PROMO_WHEN_EMPTY", "false").lower() in ("1", "true", "yes"):
            send_text(promo_message())
        return

    sent = 0
    skipped = 0

    for item in items:
        if sent >= MAX_PUBLIC_FEED_POSTS:
            break

        post_id = safe_get(item, ["post_id", "id", "ID"], "")
        ok, reason = should_post(item)
        if not ok:
            print("[SKIP]", safe_get(item, ["title", "name"], ""), reason)
            skipped += 1
            continue

        try:
            card = create_card(item)
            caption = build_caption(item)
            send_photo(card, caption)
            wp_update(post_id, "POSTED")
            sent += 1
            time.sleep(SLEEP_BETWEEN_POSTS)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print("[POST ERROR]", err)
            wp_update(post_id, "FAILED", err)
            skipped += 1

    print(f"Done. Sent {sent}. Skipped {skipped}.")

    if sent > 0 and os.getenv("POST_PROMO_AFTER_BATCH", "true").lower() in ("1", "true", "yes"):
        try:
            time.sleep(3)
            send_text(promo_message())
        except Exception as e:
            print("[PROMO ERROR]", repr(e))

if __name__ == "__main__":
    main()
