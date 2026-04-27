import requests
import time
from datetime import datetime, timezone

# ── CONFIGURATION ──────────────────────────────────────────
BOT_TOKEN = "8462196837:AAF8me80EJ-3Qg9_gZ87NtC8WTok-CHpq40"
CHAT_ID = 8362599550
MIN_LIQUIDITY_USD = 5000
MAX_AGE_MINUTES = 5
CHECK_INTERVAL_SECONDS = 20
# ───────────────────────────────────────────────────────────

seen_pairs = set()

ENDPOINTS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/latest/dex/search?q=solana&order=pairCreatedAt",
]

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            print(f"Telegram error: {r.text}")
    except Exception as e:
        print(f"Telegram exception: {e}")

def fetch_pairs_from_search():
    """Fetch recent Solana pairs via search endpoint"""
    url = "https://api.dexscreener.com/latest/dex/search?q=solana"
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        return data.get("pairs", [])
    except Exception as e:
        print(f"Search endpoint error: {e}")
        return []

def fetch_new_token_profiles():
    """Fetch latest token profiles — gives newest tokens"""
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    try:
        r = requests.get(url, timeout=15)
        tokens = r.json()
        if not isinstance(tokens, list):
            return []
        # Only Solana tokens
        sol_tokens = [t for t in tokens if t.get("chainId") == "solana"]
        addresses = [t.get("tokenAddress") for t in sol_tokens if t.get("tokenAddress")]
        return addresses, sol_tokens
    except Exception as e:
        print(f"Token profiles error: {e}")
        return [], []

def fetch_pairs_for_addresses(addresses):
    """Batch fetch pair data for a list of token addresses"""
    if not addresses:
        return []
    # DexScreener allows comma-separated addresses (max 30)
    chunk = addresses[:30]
    joined = ",".join(chunk)
    url = f"https://api.dexscreener.com/latest/dex/tokens/{joined}"
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        return data.get("pairs", [])
    except Exception as e:
        print(f"Pairs fetch error: {e}")
        return []

def extract_social(pair, token_profiles_map={}):
    info = pair.get("info", {})
    socials = info.get("socials", [])
    websites = info.get("websites", [])

    twitter = None
    telegram = None

    for s in socials:
        stype = s.get("type", "").lower()
        surl = s.get("url", "")
        if stype in ("twitter", "x") and not twitter:
            twitter = surl
        if stype == "telegram" and not telegram:
            telegram = surl

    for w in websites:
        wurl = w.get("url", "")
        if ("twitter.com" in wurl or "x.com" in wurl) and not twitter:
            twitter = wurl
        if "t.me" in wurl and not telegram:
            telegram = wurl

    # Also check token profile
    base_addr = pair.get("baseToken", {}).get("address", "")
    if base_addr in token_profiles_map:
        profile = token_profiles_map[base_addr]
        for s in profile.get("links", []):
            stype = s.get("type", "").lower()
            surl = s.get("url", "")
            if stype in ("twitter", "x") and not twitter:
                twitter = surl
            if stype == "telegram" and not telegram:
                telegram = surl

    return twitter, telegram

def get_age_minutes(pair):
    created_at = pair.get("pairCreatedAt")
    if not created_at:
        return None
    try:
        created_ms = int(created_at)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return (now_ms - created_ms) / 60000
    except:
        return None

def get_liquidity(pair):
    liq = pair.get("liquidity", {})
    return liq.get("usd", 0) or 0

def process_pairs(pairs, token_profiles_map={}):
    now = datetime.now(timezone.utc)
    for pair in pairs:
        if pair.get("chainId") != "solana":
            continue

        pair_address = pair.get("pairAddress", "")
        if not pair_address or pair_address in seen_pairs:
            continue

        age = get_age_minutes(pair)
        if age is None or age > MAX_AGE_MINUTES:
            continue

        liquidity = get_liquidity(pair)
        if liquidity < MIN_LIQUIDITY_USD:
            continue

        twitter, telegram = extract_social(pair, token_profiles_map)
        if not twitter:
            continue

        seen_pairs.add(pair_address)

        base = pair.get("baseToken", {})
        token_name = base.get("name", "N/A")
        token_symbol = base.get("symbol", "N/A")
        contract = base.get("address", "N/A")
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
        print(f"[{now.strftime('%H:%M:%S')}] ✅ {token_symbol} | Liq: {liq_str} | Âge: {age_str}")

def check_pairs():
    # Méthode 1 : token profiles (les plus récents)
    addresses, profiles = fetch_new_token_profiles()
    token_profiles_map = {p.get("tokenAddress"): p for p in profiles}
    if addresses:
        pairs = fetch_pairs_for_addresses(addresses)
        process_pairs(pairs, token_profiles_map)

    # Méthode 2 : search classique
    pairs2 = fetch_pairs_from_search()
    process_pairs(pairs2, token_profiles_map)

def main():
    print("✅ PrimePulse Monitor v2 démarré")
    print(f"   Liquidité min : ${MIN_LIQUIDITY_USD:,}")
    print(f"   Âge max       : {MAX_AGE_MINUTES} minutes")
    print(f"   Intervalle    : {CHECK_INTERVAL_SECONDS}s\n")

    send_telegram(
        "✅ <b>PrimePulse Monitor v2 ACTIF</b>\n"
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
