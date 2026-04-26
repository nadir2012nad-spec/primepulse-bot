import requests
import time
import json
from datetime import datetime, timezone

# ── CONFIGURATION ──────────────────────────────────────────
BOT_TOKEN = "8462196837:AAF8me80EJ-3Qg9_gZ87NtC8WTok-CHpq40"
CHAT_ID = "8362599550"
MIN_LIQUIDITY_USD = 5000
MAX_AGE_MINUTES = 5
CHECK_INTERVAL_SECONDS = 30
# ───────────────────────────────────────────────────────────

seen_pairs = set()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_new_solana_pairs():
    url = "https://api.dexscreener.com/latest/dex/search?q=SOL"
    try:
        response = requests.get(url, timeout=15)
        data = response.json()
        return data.get("pairs", [])
    except Exception as e:
        print(f"DexScreener error: {e}")
        return []

def extract_social(pair):
    info = pair.get("info", {})
    socials = info.get("socials", [])
    websites = info.get("websites", [])
    
    twitter = None
    telegram = None

    for s in socials:
        stype = s.get("type", "").lower()
        url = s.get("url", "")
        if stype == "twitter" and not twitter:
            twitter = url
        if stype == "telegram" and not telegram:
            telegram = url

    # Fallback dans websites
    for w in websites:
        url = w.get("url", "")
        if "twitter.com" in url or "x.com" in url:
            if not twitter:
                twitter = url
        if "t.me" in url:
            if not telegram:
                telegram = url

    return twitter, telegram

def get_age_minutes(pair):
    created_at = pair.get("pairCreatedAt")
    if not created_at:
        return None
    try:
        created_ms = int(created_at)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        age_minutes = (now_ms - created_ms) / 60000
        return age_minutes
    except:
        return None

def get_liquidity(pair):
    liq = pair.get("liquidity", {})
    return liq.get("usd", 0) or 0

def check_pairs():
    pairs = get_new_solana_pairs()
    now = datetime.now(timezone.utc)

    for pair in pairs:
        # Filtre : Solana uniquement
        if pair.get("chainId") != "solana":
            continue

        pair_address = pair.get("pairAddress", "")
        if not pair_address or pair_address in seen_pairs:
            continue

        # Filtre : âge
        age = get_age_minutes(pair)
        if age is None or age > MAX_AGE_MINUTES:
            continue

        # Filtre : liquidité
        liquidity = get_liquidity(pair)
        if liquidity < MIN_LIQUIDITY_USD:
            continue

        # Filtre : Twitter obligatoire
        twitter, telegram = extract_social(pair)
        if not twitter:
            continue

        # Tout est OK — on envoie la notif
        seen_pairs.add(pair_address)

        base = pair.get("baseToken", {})
        token_name = base.get("name", "N/A")
        token_symbol = base.get("symbol", "N/A")
        contract = base.get("address", "N/A")

        price_usd = pair.get("priceUsd", "N/A")
        volume_h1 = pair.get("volume", {}).get("h1", 0)
        dex = pair.get("dexId", "N/A").upper()
        pair_url = pair.get("url", "")

        age_str = f"{age:.1f} min"
        liq_str = f"${liquidity:,.0f}"
        vol_str = f"${volume_h1:,.0f}" if volume_h1 else "N/A"

        tg_line = f"\n💬 <b>Telegram :</b> {telegram}" if telegram else ""

        message = (
            f"🚨 <b>NOUVELLE PAIRE SOLANA</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Token :</b> {token_name} (${token_symbol})\n"
            f"⏱ <b>Âge :</b> {age_str}\n"
            f"💧 <b>Liquidité :</b> {liq_str}\n"
            f"📊 <b>Volume 1h :</b> {vol_str}\n"
            f"🏦 <b>DEX :</b> {dex}\n"
            f"📄 <b>Contrat :</b> <code>{contract}</code>\n"
            f"🐦 <b>Twitter :</b> {twitter}"
            f"{tg_line}\n"
            f"🔗 <b>DexScreener :</b> {pair_url}"
        )

        send_telegram(message)
        print(f"[{now.strftime('%H:%M:%S')}] Notif envoyée : {token_symbol} | Liq: {liq_str} | Âge: {age_str}")

def main():
    print("✅ PrimePulse Monitor démarré")
    print(f"   Liquidité min : ${MIN_LIQUIDITY_USD:,}")
    print(f"   Âge max : {MAX_AGE_MINUTES} minutes")
    print(f"   Intervalle : {CHECK_INTERVAL_SECONDS}s\n")

    send_telegram(
        "✅ <b>PrimePulse Monitor ACTIF</b>\n"
        f"Surveillance Solana lancée.\n"
        f"Liquidité min : ${MIN_LIQUIDITY_USD:,} | Âge max : {MAX_AGE_MINUTES}min"
    )

    while True:
        try:
            check_pairs()
        except Exception as e:
            print(f"Erreur générale : {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
