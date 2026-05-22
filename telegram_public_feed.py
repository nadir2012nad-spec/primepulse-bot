import os
import re
import time
import html
import requests
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("PUBLIC_CHANNEL_ID")

MAX_POSTS = 20
MIN_SCORE = 35

# =========================
# CLEAN TOKEN
# =========================

def clean_symbol(symbol):
    if not symbol:
        return "TOKEN"
    symbol = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    return symbol[:12]

def cashtag(symbol):
    return f"${clean_symbol(symbol)}"

def clean_name(name, symbol):
    if not name or len(name) < 2:
        return clean_symbol(symbol)
    try:
        name.encode("utf-8")
        return name[:30]
    except:
        return clean_symbol(symbol)

# =========================
# IMAGE
# =========================

def create_image(name, symbol, score):
    W, H = 900, 320
    img = Image.new("RGB", (W, H), (5, 10, 15))
    d = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 18)
    except:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    tag = cashtag(symbol)

    d.text((40, 40), f"{tag} {name}", fill=(255,255,255), font=font_big)
    d.text((40, 120), f"SCORE: {score}/100", fill=(140,255,0), font=font_small)
    d.text((40, 160), "EARLY SIGNAL", fill=(255,255,255), font=font_small)

    path = f"/tmp/{symbol}.jpg"
    img.save(path)
    return path

# =========================
# MESSAGE
# =========================

def build_message(name, symbol, score, url):
    tag = cashtag(symbol)

    return f"""
🚨 {tag} JUST DETECTED

{name} {tag}

• LIVE SIGNAL SCORE: {score}/100
• STATUS: EARLY SIGNAL
• FEATURED: AVAILABLE

🔎 Listing:
{url}

⚡ Claim:
{url}?claim=1

Powered by PrimePulseOps.com

{tag} {name}
#{symbol} #Crypto #EarlySignal
"""

# =========================
# SEND
# =========================

def send(photo, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(photo, "rb") as f:
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "caption": caption
        }, files={"photo": f})

# =========================
# MAIN
# =========================

def main():
    # SIMULATION (remplace par ton fetch réel)
    tokens = [
        {"name":"Test Token", "symbol":"TEST", "score":48, "url":"https://cryptalysts.com/listing/test"}
    ]

    count = 0

    for t in tokens:
        if count >= MAX_POSTS:
            break

        if t["score"] < MIN_SCORE:
            continue

        name = clean_name(t["name"], t["symbol"])
        symbol = clean_symbol(t["symbol"])

        img = create_image(name, symbol, t["score"])
        msg = build_message(name, symbol, t["score"], t["url"])

        send(img, msg)

        count += 1
        time.sleep(5)

if __name__ == "__main__":
    main()
