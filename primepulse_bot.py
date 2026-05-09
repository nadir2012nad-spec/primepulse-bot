import os
import time
import json
import base64
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# PRIMEPULSE / CRYPTALYSTS DISCOVERY ENGINE
# Multi-source free discovery:
# - DexScreener
# - GeckoTerminal
#
# Output:
# - Telegram notification
# - Automatic WordPress / HivePress listing creation
#
# Required GitHub Actions Secrets:
# - BOT_TOKEN
# - CHAT_ID
# - WP_USERNAME
# - WP_APP_PASSWORD
#
# Optional GitHub Actions Variables / Secrets:
# - WP_API_URL
# - MIN_LIQUIDITY_USD
# - MAX_AGE_MINUTES
# - CHECK_INTERVAL_SECONDS
# - RUN_ONCE
# - REQUIRE_SOCIAL
# ============================================================


# ----------------------------
# CONFIG
# ----------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

WP_USERNAME = os.getenv("WP_USERNAME", "").strip()
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "").strip()
WP_API_URL = os.getenv(
    "WP_API_URL",
    "https://cryptalysts.com/wp-json/cryptalysts/v1/token"
).strip()

MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "3000"))
MAX_AGE_MINUTES = float(os.getenv("MAX_AGE_MINUTES", "180"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

# GitHub Actions should normally run once on schedule, not infinite loop.
RUN_ONCE = os.getenv("RUN_ONCE", "true").lower() in ("1", "true", "yes", "y")

# If true: token must have at least Twitter/X, Telegram, or Website.
# For cash/claim system, I recommend true after testing.
REQUIRE_SOCIAL = os.getenv("REQUIRE_SOCIAL", "false").lower() in ("1", "true", "yes", "y")

# Chains we care about now.
# DexScreener chain ids and GeckoTerminal network ids are not always identical.
DEX_CHAINS = ["solana", "ethereum", "base", "bsc", "polygon"]
GECKO_NETWORKS = ["solana", "eth", "base", "bsc", "polygon_pos"]

# Prevent very weak garbage.
MIN_VOLUME_H1_USD = float(os.getenv("MIN_VOLUME_H1_USD", "0"))

# In-memory seen for long-running mode.
seen_fingerprints = set()


# ----------------------------
# DATA MODEL
# ----------------------------

@dataclass
class TokenCandidate:
    source: str
    chain: str
    name: str
    symbol: str
    contract_address: str
    pair_address: str = ""
    dex: str = ""
    liquidity_usd: float = 0.0
    volume_h1: float = 0.0
    age_minutes: Optional[float] = None
    website: str = ""
    twitter: str = ""
    telegram: str = ""
    logo_url: str = ""
    dexscreener_url: str = ""
    source_url: str = ""
    short_description: str = ""
    raw: Optional[Dict[str, Any]] = None


# ----------------------------
# HTTP HELPERS
# ----------------------------

def http_get_json(url: str, timeout: int = 20, headers: Optional[Dict[str, str]] = None) -> Any:
    try:
        r = requests.get(
            url,
            timeout=timeout,
            headers=headers or {
                "Accept": "application/json",
                "User-Agent": "CryptalystsPrimePulse/1.0"
            },
        )
        if not r.ok:
            print(f"[GET ERROR] {r.status_code} {url} :: {r.text[:250]}")
            return None
        return r.json()
    except Exception as e:
        print(f"[GET EXCEPTION] {url} :: {e}")
        return None


def http_post_json(url: str, payload: Dict[str, Any], auth: Optional[Tuple[str, str]] = None) -> Any:
    try:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CryptalystsPrimePulse/1.0"
        }
        r = requests.post(url, json=payload, headers=headers, auth=auth, timeout=30)
        if not r.ok:
            print(f"[POST ERROR] {r.status_code} {url} :: {r.text[:500]}")
            return None
        return r.json()
    except Exception as e:
        print(f"[POST EXCEPTION] {url} :: {e}")
        return None


# ----------------------------
# TELEGRAM
# ----------------------------

def send_telegram(message: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("[TELEGRAM SKIPPED] Missing BOT_TOKEN or CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        if not r.ok:
            print(f"[TELEGRAM ERROR] {r.text[:500]}")
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")


# ----------------------------
# NORMALIZATION
# ----------------------------

def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def age_minutes_from_ms(created_ms: Optional[int]) -> Optional[float]:
    if not created_ms:
        return None
    try:
        return (now_ms() - int(created_ms)) / 60000
    except Exception:
        return None


def clean_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def chain_display(chain: str) -> str:
    c = clean_text(chain).lower()

    mapping = {
        "eth": "ethereum",
        "ethereum": "ethereum",
        "base": "base",
        "bsc": "bsc",
        "bnb": "bsc",
        "bnb-chain": "bsc",
        "polygon": "polygon",
        "polygon_pos": "polygon",
        "matic": "polygon",
        "sol": "solana",
        "solana": "solana",
    }

    return mapping.get(c, c)


def fingerprint_token(t: TokenCandidate) -> str:
    base = f"{t.chain.lower()}::{t.contract_address.lower()}::{t.pair_address.lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def has_social_or_site(t: TokenCandidate) -> bool:
    return bool(t.website or t.twitter or t.telegram)


def candidate_passes_filters(t: TokenCandidate) -> Tuple[bool, str]:
    if not t.contract_address:
        return False, "missing contract"

    if not t.name or t.name.lower() in ("unknown", "n/a"):
        return False, "missing name"

    if t.liquidity_usd < MIN_LIQUIDITY_USD:
        return False, f"low liquidity {t.liquidity_usd}"

    if t.volume_h1 < MIN_VOLUME_H1_USD:
        return False, f"low h1 volume {t.volume_h1}"

    if t.age_minutes is not None and t.age_minutes > MAX_AGE_MINUTES:
        return False, f"too old {t.age_minutes:.1f}m"

    if REQUIRE_SOCIAL and not has_social_or_site(t):
        return False, "missing social/site"

    return True, "ok"


# ----------------------------
# DEXSCREENER SOURCE
# ----------------------------

def dex_extract_links_from_profile(profile: Dict[str, Any]) -> Tuple[str, str, str]:
    website = ""
    twitter = ""
    telegram = ""

    for link in profile.get("links", []) or []:
        url = clean_text(link.get("url"))
        typ = clean_text(link.get("type")).lower()
        label = clean_text(link.get("label")).lower()

        if not url:
            continue

        if not website and ("website" in typ or "website" in label or typ == ""):
            if "twitter.com" not in url and "x.com" not in url and "t.me" not in url:
                website = url

        if not twitter and (typ in ("twitter", "x") or "twitter.com" in url or "x.com" in url):
            twitter = url

        if not telegram and (typ == "telegram" or "t.me" in url):
            telegram = url

    return website, twitter, telegram


def dex_extract_links_from_pair(pair: Dict[str, Any]) -> Tuple[str, str, str, str]:
    info = pair.get("info") or {}
    websites = info.get("websites") or []
    socials = info.get("socials") or []

    website = ""
    twitter = ""
    telegram = ""
    logo_url = clean_text(info.get("imageUrl") or "")

    for w in websites:
        url = clean_text(w.get("url"))
        if not url:
            continue

        if not website and "twitter.com" not in url and "x.com" not in url and "t.me" not in url:
            website = url

        if not twitter and ("twitter.com" in url or "x.com" in url):
            twitter = url

        if not telegram and "t.me" in url:
            telegram = url

    for s in socials:
        url = clean_text(s.get("url"))
        typ = clean_text(s.get("type")).lower()

        if not url:
            continue

        if not twitter and (typ in ("twitter", "x") or "twitter.com" in url or "x.com" in url):
            twitter = url

        if not telegram and (typ == "telegram" or "t.me" in url):
            telegram = url

    return website, twitter, telegram, logo_url


def dex_fetch_pairs_for_addresses(addresses: List[str]) -> List[Dict[str, Any]]:
    if not addresses:
        return []

    pairs: List[Dict[str, Any]] = []

    for i in range(0, len(addresses), 30):
        chunk = addresses[i:i + 30]
        joined = ",".join(chunk)
        url = f"https://api.dexscreener.com/latest/dex/tokens/{joined}"
        data = http_get_json(url)
        if isinstance(data, dict):
            pairs.extend(data.get("pairs") or [])
        time.sleep(0.25)

    return pairs


def dex_candidate_from_pair(pair: Dict[str, Any], profile_map: Dict[str, Dict[str, Any]]) -> Optional[TokenCandidate]:
    chain = chain_display(pair.get("chainId"))
    if chain not in DEX_CHAINS:
        return None

    base = pair.get("baseToken") or {}
    contract = clean_text(base.get("address"))
    if not contract:
        return None

    profile = profile_map.get(contract, {})

    pair_website, pair_twitter, pair_telegram, pair_logo = dex_extract_links_from_pair(pair)
    prof_website, prof_twitter, prof_telegram = dex_extract_links_from_profile(profile)

    created_at = pair.get("pairCreatedAt")
    age = age_minutes_from_ms(created_at)

    liquidity = clean_float((pair.get("liquidity") or {}).get("usd"))
    volume_h1 = clean_float((pair.get("volume") or {}).get("h1"))

    token_name = clean_text(base.get("name") or profile.get("name") or "Unknown Token")
    token_symbol = clean_text(base.get("symbol") or profile.get("symbol") or "")

    dex_url = clean_text(pair.get("url") or profile.get("url") or "")

    return TokenCandidate(
        source="dexscreener",
        chain=chain,
        name=token_name,
        symbol=token_symbol,
        contract_address=contract,
        pair_address=clean_text(pair.get("pairAddress")),
        dex=clean_text(pair.get("dexId")).upper(),
        liquidity_usd=liquidity,
        volume_h1=volume_h1,
        age_minutes=age,
        website=pair_website or prof_website,
        twitter=pair_twitter or prof_twitter,
        telegram=pair_telegram or prof_telegram,
        logo_url=pair_logo or clean_text(profile.get("icon")),
        dexscreener_url=dex_url,
        source_url=dex_url,
        short_description="Early token detected by the Cryptalysts multi-source discovery engine.",
        raw={"pair": pair, "profile": profile},
    )


def source_dexscreener_latest_profiles() -> List[TokenCandidate]:
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    profiles = http_get_json(url)

    if not isinstance(profiles, list):
        return []

    filtered_profiles = []
    addresses = []

    for p in profiles:
        chain = chain_display(p.get("chainId"))
        addr = clean_text(p.get("tokenAddress"))

        if chain in DEX_CHAINS and addr:
            filtered_profiles.append(p)
            addresses.append(addr)

    profile_map = {clean_text(p.get("tokenAddress")): p for p in filtered_profiles}
    pairs = dex_fetch_pairs_for_addresses(addresses)

    candidates = []
    for pair in pairs:
        c = dex_candidate_from_pair(pair, profile_map)
        if c:
            candidates.append(c)

    return candidates


def source_dexscreener_boosts() -> List[TokenCandidate]:
    urls = [
        "https://api.dexscreener.com/token-boosts/latest/v1",
        "https://api.dexscreener.com/token-boosts/top/v1",
    ]

    raw_profiles = []
    for url in urls:
        data = http_get_json(url)
        if isinstance(data, list):
            raw_profiles.extend(data)
        time.sleep(0.25)

    addresses = []
    profile_map = {}

    for p in raw_profiles:
        chain = chain_display(p.get("chainId"))
        addr = clean_text(p.get("tokenAddress"))

        if chain in DEX_CHAINS and addr:
            addresses.append(addr)
            profile_map[addr] = p

    pairs = dex_fetch_pairs_for_addresses(list(dict.fromkeys(addresses)))

    candidates = []
    for pair in pairs:
        c = dex_candidate_from_pair(pair, profile_map)
        if c:
            c.source = "dexscreener_boosts"
            candidates.append(c)

    return candidates


# ----------------------------
# GECKOTERMINAL SOURCE
# ----------------------------

def gecko_network_to_chain(network: str) -> str:
    mapping = {
        "eth": "ethereum",
        "ethereum": "ethereum",
        "base": "base",
        "bsc": "bsc",
        "polygon_pos": "polygon",
        "polygon": "polygon",
        "solana": "solana",
    }
    return mapping.get(network, network)


def gecko_extract_included_map(included: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result = {}
    for item in included or []:
        if not isinstance(item, dict):
            continue
        item_id = clean_text(item.get("id"))
        item_type = clean_text(item.get("type"))
        if item_id:
            result[f"{item_type}:{item_id}"] = item
    return result


def gecko_attr_number(attrs: Dict[str, Any], path: List[str]) -> float:
    cur: Any = attrs
    for p in path:
        if not isinstance(cur, dict):
            return 0.0
        cur = cur.get(p)
    return clean_float(cur)


def gecko_candidate_from_pool(pool: Dict[str, Any], included_map: Dict[str, Dict[str, Any]], network: str) -> Optional[TokenCandidate]:
    attrs = pool.get("attributes") or {}
    rel = pool.get("relationships") or {}

    base_ref = ((rel.get("base_token") or {}).get("data") or {})
    base_id = clean_text(base_ref.get("id"))

    base_token = included_map.get(f"token:{base_id}", {})
    base_attrs = base_token.get("attributes") or {}

    contract = clean_text(base_attrs.get("address"))
    name = clean_text(base_attrs.get("name"))
    symbol = clean_text(base_attrs.get("symbol"))

    # Fallback: Gecko pool id often looks like network_pooladdress
    pool_id = clean_text(pool.get("id"))
    pool_address = pool_id.split("_")[-1] if "_" in pool_id else pool_id

    created_at_raw = clean_text(attrs.get("pool_created_at"))
    age = None
    if created_at_raw:
        try:
            created_dt = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - created_dt).total_seconds() / 60
        except Exception:
            age = None

    liquidity = clean_float(attrs.get("reserve_in_usd"))
    volume_h1 = gecko_attr_number(attrs, ["volume_usd", "h1"])

    # GeckoTerminal usually does not expose socials directly in new pools.
    # We still publish if filters pass; DexScreener can enrich later.
    gecko_url = clean_text(attrs.get("url"))

    return TokenCandidate(
        source="geckoterminal",
        chain=gecko_network_to_chain(network),
        name=name or "Unknown Token",
        symbol=symbol,
        contract_address=contract,
        pair_address=pool_address,
        dex=clean_text(attrs.get("dex_id")).upper(),
        liquidity_usd=liquidity,
        volume_h1=volume_h1,
        age_minutes=age,
        website=gecko_url,
        twitter="",
        telegram="",
        logo_url=clean_text(base_attrs.get("image_url")),
        dexscreener_url="",
        source_url=gecko_url,
        short_description="Early pool detected by the Cryptalysts multi-source discovery engine.",
        raw={"pool": pool, "base_token": base_token},
    )


def source_geckoterminal_new_pools() -> List[TokenCandidate]:
    candidates = []

    headers = {
        "Accept": "application/json;version=20230302",
        "User-Agent": "CryptalystsPrimePulse/1.0",
    }

    for network in GECKO_NETWORKS:
        url = f"https://api.geckoterminal.com/api/v2/networks/{network}/new_pools?include=base_token,quote_token"
        data = http_get_json(url, headers=headers)

        if not isinstance(data, dict):
            continue

        included_map = gecko_extract_included_map(data.get("included") or [])
        pools = data.get("data") or []

        for pool in pools:
            c = gecko_candidate_from_pool(pool, included_map, network)
            if c:
                candidates.append(c)

        time.sleep(0.5)

    return candidates


# ----------------------------
# DEDUPLICATION / MERGE
# ----------------------------

def merge_candidates(candidates: List[TokenCandidate]) -> List[TokenCandidate]:
    merged: Dict[str, TokenCandidate] = {}

    for c in candidates:
        key = f"{c.chain.lower()}::{c.contract_address.lower()}"
        if not c.contract_address:
            continue

        if key not in merged:
            merged[key] = c
            continue

        existing = merged[key]

        # Prefer richer data.
        existing.sources = None if False else None  # no-op placeholder to keep simple

        if c.liquidity_usd > existing.liquidity_usd:
            existing.liquidity_usd = c.liquidity_usd

        if c.volume_h1 > existing.volume_h1:
            existing.volume_h1 = c.volume_h1

        if existing.age_minutes is None or (c.age_minutes is not None and c.age_minutes < existing.age_minutes):
            existing.age_minutes = c.age_minutes

        for field in ["website", "twitter", "telegram", "logo_url", "dexscreener_url", "source_url", "pair_address", "dex"]:
            if not getattr(existing, field) and getattr(c, field):
                setattr(existing, field, getattr(c, field))

        if existing.source != c.source:
            existing.source = existing.source + "+" + c.source

    return list(merged.values())


# ----------------------------
# WORDPRESS PUBLISHER
# ----------------------------

def build_listing_description(t: TokenCandidate) -> str:
    liquidity = f"${t.liquidity_usd:,.0f}" if t.liquidity_usd else "Not available"
    volume = f"${t.volume_h1:,.0f}" if t.volume_h1 else "Not available"
    age = f"{t.age_minutes:.1f} minutes" if t.age_minutes is not None else "Not available"

    lines = [
        f"{t.name} ({t.symbol}) was detected by the Cryptalysts early token discovery engine.",
        "",
        "Early market snapshot:",
        f"- Chain: {t.chain}",
        f"- Contract: {t.contract_address}",
        f"- Liquidity at detection: {liquidity}",
        f"- 1h volume at detection: {volume}",
        f"- Pair age at detection: {age}",
        f"- Source: {t.source}",
        "",
        "Visibility status:",
        "This listing was automatically generated to help the market discover new token activity early.",
        "",
        "Project owners can claim this listing, update branding, add official links, and request stronger visibility through PrimePulseOps.",
    ]

    if t.source_url:
        lines.append("")
        lines.append(f"Market source: {t.source_url}")

    return "\n".join(lines)


def publish_to_wordpress(t: TokenCandidate) -> Tuple[bool, str]:
    if not WP_USERNAME or not WP_APP_PASSWORD:
        return False, "Missing WP_USERNAME or WP_APP_PASSWORD"

    payload = {
        "name": t.name,
        "symbol": t.symbol,
        "chain": t.chain,
        "contract_address": t.contract_address,
        "pair_address": t.pair_address,
        "website": t.website or t.source_url,
        "twitter": t.twitter,
        "telegram": t.telegram,
        "logo_url": t.logo_url,
        "dexscreener_url": t.dexscreener_url or t.source_url,
        "liquidity_usd": round(t.liquidity_usd, 2),
        "volume_h1": round(t.volume_h1, 2),
        "age_minutes": round(t.age_minutes, 2) if t.age_minutes is not None else "",
        "short_description": t.short_description,
        "description": build_listing_description(t),
        "promoted": False,
    }

    response = http_post_json(
        WP_API_URL,
        payload,
        auth=(WP_USERNAME, WP_APP_PASSWORD),
    )

    if not response:
        return False, "No response from WordPress"

    if response.get("ok"):
        url = response.get("url", "")
        duplicate = response.get("duplicate", False)
        status = "duplicate" if duplicate else "created"
        return True, f"{status}: {url}"

    return False, json.dumps(response)[:500]


# ----------------------------
# NOTIFICATION FORMAT
# ----------------------------

def format_token_message(t: TokenCandidate, wp_status: str) -> str:
    age = f"{t.age_minutes:.1f} min" if t.age_minutes is not None else "N/A"
    liquidity = f"${t.liquidity_usd:,.0f}" if t.liquidity_usd else "N/A"
    volume = f"${t.volume_h1:,.0f}" if t.volume_h1 else "N/A"

    socials = []
    if t.website:
        socials.append(f"🌐 <b>Website:</b> {t.website}")
    if t.twitter:
        socials.append(f"🐦 <b>X:</b> {t.twitter}")
    if t.telegram:
        socials.append(f"💬 <b>Telegram:</b> {t.telegram}")

    socials_text = "\n".join(socials) if socials else "No socials found yet"

    return (
        f"🚨 <b>NEW TOKEN DETECTED</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>Token:</b> {t.name} ({t.symbol})\n"
        f"⛓ <b>Chain:</b> {t.chain}\n"
        f"🏦 <b>DEX:</b> {t.dex or 'N/A'}\n"
        f"⏱ <b>Age:</b> {age}\n"
        f"💧 <b>Liquidity:</b> {liquidity}\n"
        f"📊 <b>Volume 1h:</b> {volume}\n"
        f"📄 <b>Contract:</b> <code>{t.contract_address}</code>\n"
        f"🔎 <b>Source:</b> {t.source}\n"
        f"{socials_text}\n"
        f"🔗 <b>Market:</b> {t.dexscreener_url or t.source_url or 'N/A'}\n"
        f"🧩 <b>Cryptalysts:</b> {wp_status}"
    )


# ----------------------------
# DISCOVERY RUN
# ----------------------------

def collect_all_sources() -> List[TokenCandidate]:
    all_candidates: List[TokenCandidate] = []

    sources = [
        ("DexScreener latest profiles", source_dexscreener_latest_profiles),
        ("DexScreener boosts", source_dexscreener_boosts),
        ("GeckoTerminal new pools", source_geckoterminal_new_pools),
    ]

    for name, fn in sources:
        try:
            print(f"[SOURCE] {name}")
            items = fn()
            print(f"[SOURCE] {name}: {len(items)} candidates")
            all_candidates.extend(items)
        except Exception as e:
            print(f"[SOURCE ERROR] {name}: {e}")

    return merge_candidates(all_candidates)


def process_candidates(candidates: List[TokenCandidate]) -> None:
    print(f"[PROCESS] merged candidates: {len(candidates)}")

    published = 0
    skipped = 0

    for t in candidates:
        fp = fingerprint_token(t)

        if fp in seen_fingerprints:
            skipped += 1
            continue

        ok, reason = candidate_passes_filters(t)
        if not ok:
            print(f"[SKIP] {t.chain} {t.symbol} {t.contract_address} :: {reason}")
            skipped += 1
            continue

        seen_fingerprints.add(fp)

        wp_ok, wp_status = publish_to_wordpress(t)

        if wp_ok:
            published += 1
            print(f"[PUBLISHED] {t.chain} {t.symbol} :: {wp_status}")
        else:
            print(f"[WP FAILED] {t.chain} {t.symbol} :: {wp_status}")

        send_telegram(format_token_message(t, wp_status))

        # Avoid hammering WordPress / Telegram.
        time.sleep(1)

    print(f"[SUMMARY] published={published} skipped={skipped}")


def run_once() -> None:
    print("============================================================")
    print("PrimePulse / Cryptalysts Discovery Engine")
    print(f"Time UTC: {datetime.now(timezone.utc).isoformat()}")
    print(f"Min liquidity: ${MIN_LIQUIDITY_USD:,.0f}")
    print(f"Max age: {MAX_AGE_MINUTES} minutes")
    print(f"Require social/site: {REQUIRE_SOCIAL}")
    print("Sources: DexScreener + GeckoTerminal")
    print("============================================================")

    candidates = collect_all_sources()
    process_candidates(candidates)


def main() -> None:
    if not BOT_TOKEN:
        print("[WARN] BOT_TOKEN missing. Telegram disabled.")
    if not CHAT_ID:
        print("[WARN] CHAT_ID missing. Telegram disabled.")
    if not WP_USERNAME or not WP_APP_PASSWORD:
        print("[WARN] WordPress credentials missing. Publishing disabled.")

    if RUN_ONCE:
        run_once()
        return

    send_telegram("✅ <b>PrimePulse Discovery Engine ACTIVE</b>\nMulti-source monitoring started.")

    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[FATAL LOOP ERROR] {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
