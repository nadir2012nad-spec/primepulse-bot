import os
import re
import time
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
WP_USERNAME = os.getenv("WP_USERNAME", "").strip()
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "").strip()
WP_API_URL = os.getenv("WP_API_URL", "https://cryptalysts.com/wp-json/cryptalysts/v1/token").strip()

MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "3000"))
MAX_AGE_MINUTES = float(os.getenv("MAX_AGE_MINUTES", "180"))
MIN_VOLUME_H1_USD = float(os.getenv("MIN_VOLUME_H1_USD", "0"))
MIN_QUALITY_SCORE = int(os.getenv("MIN_QUALITY_SCORE", "35"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
RUN_ONCE = os.getenv("RUN_ONCE", "true").lower() in ("1", "true", "yes", "y")
REQUIRE_SOCIAL = os.getenv("REQUIRE_SOCIAL", "false").lower() in ("1", "true", "yes", "y")

DEX_CHAINS = ["solana", "ethereum", "base", "bsc", "polygon"]
GECKO_NETWORKS = ["solana", "eth", "base", "bsc", "polygon_pos"]
seen_fingerprints = set()

STABLE_OR_WRAPPED_KEYWORDS = [
    "usdt", "tether", "usdc", "usd coin", "dai", "busd", "fdusd", "tusd", "usde",
    "stablecoin", "stable coin", "wrapped", "weth", "wbtc", "wsol", "wmatic",
    "binance-peg", "bridged", "wormhole", "layerzero", "aave interest bearing",
    "compound", "staked", "liquid staking", "restaked", "wrapped ether",
]
SPAM_NAME_KEYWORDS = [
    "test token", "testtoken", "lp token", "liquidity pool token",
    "fake usdt", "fake usdc", "claim rewards", "airdrop claim",
]
BAD_EMAIL_DOMAINS = ["example.com", "domain.com", "email.com", "test.com"]

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
    email: str = ""
    logo_url: str = ""
    banner_url: str = ""
    dexscreener_url: str = ""
    source_url: str = ""
    quality_score: int = 0
    risk_score: int = 0
    analysis_notes: str = ""
    short_description: str = ""
    raw: Optional[Dict[str, Any]] = None

def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()

def clean_float(value: Any) -> float:
    try:
        return 0.0 if value is None or value == "" else float(value)
    except Exception:
        return 0.0

def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)

def age_minutes_from_ms(created_ms: Optional[int]) -> Optional[float]:
    if not created_ms:
        return None
    try:
        return (now_ms() - int(created_ms)) / 60000
    except Exception:
        return None

def chain_display(chain: str) -> str:
    c = clean_text(chain).lower()
    return {
        "eth": "ethereum", "ethereum": "ethereum", "base": "base",
        "bsc": "bsc", "bnb": "bsc", "bnb-chain": "bsc",
        "polygon": "polygon", "polygon_pos": "polygon", "matic": "polygon",
        "sol": "solana", "solana": "solana",
    }.get(c, c)

def fingerprint_token(t: TokenCandidate) -> str:
    base = f"{t.chain.lower()}::{t.contract_address.lower()}::{t.pair_address.lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def is_probably_stable_wrapped_or_official(name: str, symbol: str) -> bool:
    text = f"{name} {symbol}".lower()
    compact_symbol = symbol.lower().replace("$", "").strip()
    if compact_symbol in {"usdt", "usdc", "dai", "busd", "fdusd", "tusd", "usde", "weth", "wbtc", "wsol", "wmatic", "eth", "btc", "bnb", "sol", "matic"}:
        return True
    return any(k in text for k in STABLE_OR_WRAPPED_KEYWORDS)

def is_spammy_name(name: str, symbol: str) -> bool:
    text = f"{name} {symbol}".lower()
    return any(k in text for k in SPAM_NAME_KEYWORDS)

def normalize_url(url: str) -> str:
    url = clean_text(url)
    if not url:
        return ""
    if url.startswith("mailto:"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url

def is_valid_public_url(url: str) -> bool:
    if not url or url.startswith("mailto:"):
        return False
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc) and "." in p.netloc
    except Exception:
        return False

def http_get_json(url: str, timeout: int = 20, headers: Optional[Dict[str, str]] = None) -> Any:
    try:
        r = requests.get(url, timeout=timeout, headers=headers or {"Accept": "application/json", "User-Agent": "CryptalystsPrimePulse/2.0"})
        if not r.ok:
            print(f"[GET ERROR] {r.status_code} {url} :: {r.text[:250]}")
            return None
        return r.json()
    except Exception as e:
        print(f"[GET EXCEPTION] {url} :: {e}")
        return None

def http_get_text(url: str, timeout: int = 12) -> str:
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True, headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "User-Agent": "Mozilla/5.0 (compatible; CryptalystsBot/2.0; +https://cryptalysts.com)"})
        ctype = r.headers.get("content-type", "").lower()
        if not r.ok or ("text/html" not in ctype and "application/xhtml" not in ctype):
            return ""
        return r.text[:600000]
    except Exception as e:
        print(f"[HTML EXCEPTION] {url} :: {e}")
        return ""

def http_post_json(url: str, payload: Dict[str, Any], auth: Optional[Tuple[str, str]] = None) -> Any:
    try:
        r = requests.post(url, json=payload, auth=auth, timeout=35, headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "CryptalystsPrimePulse/2.0"})
        if not r.ok:
            print(f"[POST ERROR] {r.status_code} {url} :: {r.text[:800]}")
            return None
        return r.json()
    except Exception as e:
        print(f"[POST EXCEPTION] {url} :: {e}")
        return None

def send_telegram(message: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("[TELEGRAM SKIPPED] Missing BOT_TOKEN or CHAT_ID")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
        if not r.ok:
            print(f"[TELEGRAM ERROR] {r.text[:500]}")
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")

def extract_meta_content(html: str, keys: List[str]) -> str:
    if not html:
        return ""
    for key in keys:
        patterns = [
            rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(key)}["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(key)}["\']',
        ]
        for p in patterns:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return unescape(m.group(1).strip())
    return ""

def extract_first_url_matching(html: str, patterns: List[str], base_url: str = "") -> str:
    if not html:
        return ""
    urls = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    urls += re.findall(r'src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for raw in urls:
        url = unescape(raw.strip())
        if base_url:
            url = urljoin(base_url, url)
        low = url.lower()
        if any(p in low for p in patterns):
            return normalize_url(url)
    return ""

def extract_email(html: str) -> str:
    if not html:
        return ""
    candidates = re.findall(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', html, re.IGNORECASE)
    candidates += re.findall(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b', html, re.IGNORECASE)
    seen, cleaned = set(), []
    for e in candidates:
        e = e.lower().strip().strip(".,;:()[]{}<>")
        if e in seen:
            continue
        seen.add(e)
        domain = e.split("@")[-1]
        if domain in BAD_EMAIL_DOMAINS or any(e.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"]):
            continue
        if "sentry" in e or "wixpress" in e or "wordpress" in e:
            continue
        cleaned.append(e)
    preferred = [e for e in cleaned if any(x in e for x in ["contact", "team", "hello", "info", "support", "business", "partnership", "marketing"])]
    return (preferred or cleaned or [""])[0]

def enrich_from_website(t: TokenCandidate) -> TokenCandidate:
    site = normalize_url(t.website)
    if not is_valid_public_url(site):
        return t
    html = http_get_text(site)
    if not html:
        return t
    if not t.email:
        t.email = extract_email(html)
    if not t.twitter:
        t.twitter = extract_first_url_matching(html, ["twitter.com", "x.com/"], site)
    if not t.telegram:
        t.telegram = extract_first_url_matching(html, ["t.me/", "telegram.me/"], site)
    og_image = extract_meta_content(html, ["og:image", "twitter:image", "twitter:image:src"])
    if og_image:
        og_image = normalize_url(urljoin(site, og_image))
    if og_image and not t.banner_url:
        t.banner_url = og_image
    if og_image and not t.logo_url:
        t.logo_url = og_image
    return t

def dex_extract_links_from_profile(profile: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    website = twitter = telegram = email = banner = ""
    for link in profile.get("links", []) or []:
        raw = clean_text(link.get("url"))
        url = normalize_url(raw)
        typ = clean_text(link.get("type")).lower()
        label = clean_text(link.get("label")).lower()
        if not url:
            continue
        if not twitter and (typ in ("twitter", "x") or "twitter" in label or "twitter.com" in url or "x.com/" in url):
            twitter = url
        elif not telegram and (typ == "telegram" or "telegram" in label or "t.me/" in url):
            telegram = url
        elif not email and raw.startswith("mailto:"):
            email = raw.replace("mailto:", "").split("?")[0].strip()
        elif not website and is_valid_public_url(url):
            website = url
    banner = normalize_url(clean_text(profile.get("header") or profile.get("banner") or ""))
    return website, twitter, telegram, email, banner

def dex_extract_links_from_pair(pair: Dict[str, Any]) -> Tuple[str, str, str, str, str, str]:
    info = pair.get("info") or {}
    website = twitter = telegram = email = ""
    logo_url = normalize_url(clean_text(info.get("imageUrl") or ""))
    banner_url = normalize_url(clean_text(info.get("header") or ""))
    for w in info.get("websites") or []:
        raw = clean_text(w.get("url")); url = normalize_url(raw)
        if not url: continue
        if not twitter and ("twitter.com" in url or "x.com/" in url): twitter = url
        elif not telegram and ("t.me/" in url or "telegram.me/" in url): telegram = url
        elif not email and raw.startswith("mailto:"): email = raw.replace("mailto:", "").split("?")[0].strip()
        elif not website and is_valid_public_url(url): website = url
    for s in info.get("socials") or []:
        raw = clean_text(s.get("url")); url = normalize_url(raw); typ = clean_text(s.get("type")).lower()
        if not url: continue
        if not twitter and (typ in ("twitter", "x") or "twitter.com" in url or "x.com/" in url): twitter = url
        elif not telegram and (typ == "telegram" or "t.me/" in url or "telegram.me/" in url): telegram = url
        elif not email and raw.startswith("mailto:"): email = raw.replace("mailto:", "").split("?")[0].strip()
    return website, twitter, telegram, email, logo_url, banner_url

def dex_fetch_pairs_for_addresses(addresses: List[str]) -> List[Dict[str, Any]]:
    pairs = []
    for i in range(0, len(addresses), 30):
        data = http_get_json(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(addresses[i:i+30])}")
        if isinstance(data, dict):
            pairs.extend(data.get("pairs") or [])
        time.sleep(0.25)
    return pairs

def dex_candidate_from_pair(pair: Dict[str, Any], profile_map: Dict[str, Dict[str, Any]], source_name: str) -> Optional[TokenCandidate]:
    chain = chain_display(pair.get("chainId"))
    if chain not in DEX_CHAINS:
        return None
    base = pair.get("baseToken") or {}
    contract = clean_text(base.get("address"))
    if not contract:
        return None
    profile = profile_map.get(contract, {})
    pw, pt, pg, pe, plogo, pbanner = dex_extract_links_from_pair(pair)
    fw, ft, fg, fe, fbanner = dex_extract_links_from_profile(profile)
    return TokenCandidate(
        source=source_name,
        chain=chain,
        name=clean_text(base.get("name") or profile.get("name") or "Unknown Token"),
        symbol=clean_text(base.get("symbol") or profile.get("symbol") or ""),
        contract_address=contract,
        pair_address=clean_text(pair.get("pairAddress")),
        dex=clean_text(pair.get("dexId")).upper(),
        liquidity_usd=clean_float((pair.get("liquidity") or {}).get("usd")),
        volume_h1=clean_float((pair.get("volume") or {}).get("h1")),
        age_minutes=age_minutes_from_ms(pair.get("pairCreatedAt")),
        website=pw or fw,
        twitter=pt or ft,
        telegram=pg or fg,
        email=pe or fe,
        logo_url=plogo or normalize_url(clean_text(profile.get("icon"))),
        banner_url=pbanner or fbanner,
        dexscreener_url=clean_text(pair.get("url") or profile.get("url") or ""),
        source_url=clean_text(pair.get("url") or profile.get("url") or ""),
        short_description="Early token detected by the Cryptalysts multi-source discovery engine.",
        raw={"pair": pair, "profile": profile},
    )

def source_dexscreener_latest_profiles() -> List[TokenCandidate]:
    profiles = http_get_json("https://api.dexscreener.com/token-profiles/latest/v1")
    if not isinstance(profiles, list):
        return []
    addresses, profile_map = [], {}
    for p in profiles:
        chain = chain_display(p.get("chainId")); addr = clean_text(p.get("tokenAddress"))
        if chain in DEX_CHAINS and addr:
            addresses.append(addr); profile_map[addr] = p
    return [c for pair in dex_fetch_pairs_for_addresses(addresses) if (c := dex_candidate_from_pair(pair, profile_map, "dexscreener_profiles"))]

def source_dexscreener_boosts() -> List[TokenCandidate]:
    raw_profiles = []
    for url in ["https://api.dexscreener.com/token-boosts/latest/v1", "https://api.dexscreener.com/token-boosts/top/v1"]:
        data = http_get_json(url)
        if isinstance(data, list): raw_profiles.extend(data)
        time.sleep(0.25)
    addresses, profile_map = [], {}
    for p in raw_profiles:
        chain = chain_display(p.get("chainId")); addr = clean_text(p.get("tokenAddress"))
        if chain in DEX_CHAINS and addr:
            addresses.append(addr); profile_map[addr] = p
    return [c for pair in dex_fetch_pairs_for_addresses(list(dict.fromkeys(addresses))) if (c := dex_candidate_from_pair(pair, profile_map, "dexscreener_boosts"))]

def gecko_network_to_chain(network: str) -> str:
    return {"eth": "ethereum", "base": "base", "bsc": "bsc", "polygon_pos": "polygon", "solana": "solana"}.get(network, network)

def gecko_extract_included_map(included: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {f"{clean_text(i.get('type'))}:{clean_text(i.get('id'))}": i for i in (included or []) if clean_text(i.get("id"))}

def gecko_attr_number(attrs: Dict[str, Any], path: List[str]) -> float:
    cur = attrs
    for p in path:
        if not isinstance(cur, dict): return 0.0
        cur = cur.get(p)
    return clean_float(cur)

def gecko_candidate_from_pool(pool: Dict[str, Any], included_map: Dict[str, Dict[str, Any]], network: str) -> Optional[TokenCandidate]:
    attrs = pool.get("attributes") or {}; rel = pool.get("relationships") or {}
    base_ref = ((rel.get("base_token") or {}).get("data") or {})
    base_token = included_map.get(f"token:{clean_text(base_ref.get('id'))}", {})
    base_attrs = base_token.get("attributes") or {}
    created_raw = clean_text(attrs.get("pool_created_at")); age = None
    if created_raw:
        try: age = (datetime.now(timezone.utc) - datetime.fromisoformat(created_raw.replace("Z", "+00:00"))).total_seconds() / 60
        except Exception: age = None
    pool_id = clean_text(pool.get("id")); pool_address = pool_id.split("_")[-1] if "_" in pool_id else pool_id
    gecko_url = normalize_url(clean_text(attrs.get("url")))
    return TokenCandidate(
        source="geckoterminal", chain=gecko_network_to_chain(network),
        name=clean_text(base_attrs.get("name")) or "Unknown Token", symbol=clean_text(base_attrs.get("symbol")),
        contract_address=clean_text(base_attrs.get("address")), pair_address=pool_address,
        dex=clean_text(attrs.get("dex_id")).upper(), liquidity_usd=clean_float(attrs.get("reserve_in_usd")),
        volume_h1=gecko_attr_number(attrs, ["volume_usd", "h1"]), age_minutes=age,
        website=gecko_url, logo_url=normalize_url(clean_text(base_attrs.get("image_url"))),
        source_url=gecko_url, short_description="Early pool detected by the Cryptalysts multi-source discovery engine.",
        raw={"pool": pool, "base_token": base_token},
    )

def source_geckoterminal_new_pools() -> List[TokenCandidate]:
    candidates = []
    headers = {"Accept": "application/json;version=20230302", "User-Agent": "CryptalystsPrimePulse/2.0"}
    for network in GECKO_NETWORKS:
        data = http_get_json(f"https://api.geckoterminal.com/api/v2/networks/{network}/new_pools?include=base_token,quote_token", headers=headers)
        if isinstance(data, dict):
            included_map = gecko_extract_included_map(data.get("included") or [])
            for pool in data.get("data") or []:
                c = gecko_candidate_from_pool(pool, included_map, network)
                if c: candidates.append(c)
        time.sleep(0.5)
    return candidates

def score_candidate(t: TokenCandidate) -> TokenCandidate:
    score, risk, notes = 0, 0, []
    if t.age_minutes is not None:
        if t.age_minutes <= 30: score += 18; notes.append("Very fresh pair detected.")
        elif t.age_minutes <= 180: score += 12; notes.append("Recent pair detected.")
        else: score += 3; risk += 10; notes.append("Pair is older than preferred early-discovery window.")
    else: risk += 8; notes.append("Pair age unavailable.")
    if t.liquidity_usd >= 25000: score += 20; notes.append("Liquidity is above minimum early-signal threshold.")
    elif t.liquidity_usd >= 10000: score += 15; notes.append("Liquidity is acceptable for early tracking.")
    elif t.liquidity_usd >= MIN_LIQUIDITY_USD: score += 8; risk += 8; notes.append("Liquidity is present but still thin.")
    else: risk += 35; notes.append("Liquidity is below threshold.")
    if t.volume_h1 >= 25000: score += 18; notes.append("Strong short-term trading activity found.")
    elif t.volume_h1 >= 5000: score += 12; notes.append("Visible short-term trading activity found.")
    elif t.volume_h1 > 0: score += 5; notes.append("Low but visible short-term activity.")
    else: risk += 8; notes.append("1h volume is unavailable or very low.")
    if t.website: score += 10
    else: risk += 8
    if t.twitter: score += 12
    else: risk += 5
    if t.telegram: score += 8
    if t.email: score += 5; notes.append("Public email/contact channel discovered.")
    if t.logo_url: score += 7
    if t.banner_url: score += 5
    if is_probably_stable_wrapped_or_official(t.name, t.symbol): risk += 80; score -= 30; notes.append("Stablecoin, wrapped asset, or official large asset pattern detected.")
    if is_spammy_name(t.name, t.symbol): risk += 40; score -= 15; notes.append("Spam-like naming pattern detected.")
    if not (t.website or t.twitter or t.telegram or t.email): risk += 15; notes.append("No public contact channel discovered during first pass.")
    t.quality_score = max(0, min(100, score)); t.risk_score = max(0, min(100, risk)); t.analysis_notes = " ".join(notes)
    return t

def candidate_passes_filters(t: TokenCandidate) -> Tuple[bool, str]:
    if not t.contract_address: return False, "missing contract"
    if not t.name or t.name.lower() in ("unknown", "n/a", "unknown token"): return False, "missing name"
    if is_probably_stable_wrapped_or_official(t.name, t.symbol): return False, "stable/wrapped/official asset filtered"
    if is_spammy_name(t.name, t.symbol): return False, "spammy name filtered"
    if t.liquidity_usd < MIN_LIQUIDITY_USD: return False, f"low liquidity {t.liquidity_usd}"
    if t.volume_h1 < MIN_VOLUME_H1_USD: return False, f"low h1 volume {t.volume_h1}"
    if t.age_minutes is not None and t.age_minutes > MAX_AGE_MINUTES: return False, f"too old {t.age_minutes:.1f}m"
    if REQUIRE_SOCIAL and not (t.website or t.twitter or t.telegram or t.email): return False, "missing social/contact"
    if t.quality_score < MIN_QUALITY_SCORE: return False, f"quality score too low {t.quality_score}"
    if t.risk_score >= 80: return False, f"risk score too high {t.risk_score}"
    return True, "ok"

def merge_candidates(candidates: List[TokenCandidate]) -> List[TokenCandidate]:
    merged = {}
    for c in candidates:
        if not c.contract_address: continue
        key = f"{c.chain.lower()}::{c.contract_address.lower()}"
        if key not in merged:
            merged[key] = c; continue
        e = merged[key]
        if c.liquidity_usd > e.liquidity_usd: e.liquidity_usd = c.liquidity_usd
        if c.volume_h1 > e.volume_h1: e.volume_h1 = c.volume_h1
        if e.age_minutes is None or (c.age_minutes is not None and c.age_minutes < e.age_minutes): e.age_minutes = c.age_minutes
        for field in ["website", "twitter", "telegram", "email", "logo_url", "banner_url", "dexscreener_url", "source_url", "pair_address", "dex"]:
            if not getattr(e, field) and getattr(c, field): setattr(e, field, getattr(c, field))
        if c.source not in e.source: e.source = e.source + "+" + c.source
    return list(merged.values())

def publish_to_wordpress(t: TokenCandidate) -> Tuple[bool, str]:
    if not WP_USERNAME or not WP_APP_PASSWORD:
        return False, "Missing WP_USERNAME or WP_APP_PASSWORD"
    payload = {
        "name": t.name, "symbol": t.symbol, "chain": t.chain, "contract_address": t.contract_address,
        "pair_address": t.pair_address, "dex": t.dex, "website": t.website, "twitter": t.twitter,
        "telegram": t.telegram, "email": t.email, "logo_url": t.logo_url, "banner_url": t.banner_url,
        "dexscreener_url": t.dexscreener_url, "source_url": t.source_url, "source": t.source,
        "liquidity_usd": round(t.liquidity_usd, 2), "volume_h1": round(t.volume_h1, 2),
        "age_minutes": round(t.age_minutes, 2) if t.age_minutes is not None else "",
        "quality_score": t.quality_score, "risk_score": t.risk_score, "analysis_notes": t.analysis_notes,
        "short_description": t.short_description, "promoted": False,
    }
    response = http_post_json(WP_API_URL, payload, auth=(WP_USERNAME, WP_APP_PASSWORD))
    if not response: return False, "No response from WordPress"
    if response.get("ok"):
        return True, ("duplicate: " if response.get("duplicate") else "created: ") + response.get("url", "")
    return False, json.dumps(response)[:700]

def format_token_message(t: TokenCandidate, wp_status: str) -> str:
    age = f"{t.age_minutes:.1f} min" if t.age_minutes is not None else "N/A"
    liquidity = f"${t.liquidity_usd:,.0f}" if t.liquidity_usd else "N/A"
    volume = f"${t.volume_h1:,.0f}" if t.volume_h1 else "N/A"
    contacts = []
    if t.website: contacts.append(f"🌐 <b>Website:</b> {t.website}")
    if t.twitter: contacts.append(f"🐦 <b>X:</b> {t.twitter}")
    if t.telegram: contacts.append(f"💬 <b>Telegram:</b> {t.telegram}")
    if t.email: contacts.append(f"✉️ <b>Email:</b> {t.email}")
    return (
        f"🚨 <b>QUALIFIED TOKEN DETECTED</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>Token:</b> {t.name} ({t.symbol})\n⛓ <b>Chain:</b> {t.chain}\n🏦 <b>DEX:</b> {t.dex or 'N/A'}\n"
        f"⏱ <b>Age:</b> {age}\n💧 <b>Liquidity:</b> {liquidity}\n📊 <b>Volume 1h:</b> {volume}\n"
        f"🧠 <b>Quality:</b> {t.quality_score}/100\n⚠️ <b>Risk:</b> {t.risk_score}/100\n📄 <b>Contract:</b> <code>{t.contract_address}</code>\n"
        f"🔎 <b>Source:</b> {t.source}\n{chr(10).join(contacts) if contacts else 'No public contact channel found yet'}\n"
        f"🔗 <b>Market:</b> {t.dexscreener_url or t.source_url or 'N/A'}\n🧩 <b>Cryptalysts:</b> {wp_status}"
    )

def collect_all_sources() -> List[TokenCandidate]:
    all_candidates = []
    for name, fn in [("DexScreener latest profiles", source_dexscreener_latest_profiles), ("DexScreener boosts", source_dexscreener_boosts), ("GeckoTerminal new pools", source_geckoterminal_new_pools)]:
        try:
            print(f"[SOURCE] {name}")
            items = fn(); print(f"[SOURCE] {name}: {len(items)} candidates")
            all_candidates.extend(items)
        except Exception as e:
            print(f"[SOURCE ERROR] {name}: {e}")
    return merge_candidates(all_candidates)

def process_candidates(candidates: List[TokenCandidate]) -> None:
    print(f"[PROCESS] merged candidates: {len(candidates)}")
    published = skipped = 0
    for t in candidates:
        fp = fingerprint_token(t)
        if fp in seen_fingerprints:
            skipped += 1; continue
        if t.website: t = enrich_from_website(t)
        t = score_candidate(t)
        ok, reason = candidate_passes_filters(t)
        if not ok:
            print(f"[SKIP] {t.chain} {t.symbol} {t.contract_address} :: {reason}")
            skipped += 1; continue
        seen_fingerprints.add(fp)
        wp_ok, wp_status = publish_to_wordpress(t)
        if wp_ok:
            published += 1; print(f"[PUBLISHED] {t.chain} {t.symbol} :: {wp_status}")
        else:
            print(f"[WP FAILED] {t.chain} {t.symbol} :: {wp_status}")
        send_telegram(format_token_message(t, wp_status))
        time.sleep(1)
    print(f"[SUMMARY] published={published} skipped={skipped}")

def run_once() -> None:
    print("============================================================")
    print("PrimePulse / Cryptalysts Discovery Engine v0.2")
    print(f"Time UTC: {datetime.now(timezone.utc).isoformat()}")
    print(f"Min liquidity: ${MIN_LIQUIDITY_USD:,.0f}")
    print(f"Max age: {MAX_AGE_MINUTES} minutes")
    print(f"Min quality score: {MIN_QUALITY_SCORE}/100")
    print(f"Require social/contact: {REQUIRE_SOCIAL}")
    print("Sources: DexScreener + GeckoTerminal")
    print("============================================================")
    process_candidates(collect_all_sources())

def main() -> None:
    if not BOT_TOKEN: print("[WARN] BOT_TOKEN missing. Telegram disabled.")
    if not CHAT_ID: print("[WARN] CHAT_ID missing. Telegram disabled.")
    if not WP_USERNAME or not WP_APP_PASSWORD: print("[WARN] WordPress credentials missing. Publishing disabled.")
    if RUN_ONCE:
        run_once(); return
    send_telegram("✅ <b>PrimePulse Discovery Engine v0.2 ACTIVE</b>\nMulti-source monitoring started.")
    while True:
        try: run_once()
        except Exception as e: print(f"[FATAL LOOP ERROR] {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
