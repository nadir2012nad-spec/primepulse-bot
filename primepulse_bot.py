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
RUN_ONCE = os.getenv("RUN_ONCE", "true").lower() in ("1", "true", "yes", "y")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
REQUIRE_SOCIAL = os.getenv("REQUIRE_SOCIAL", "false").lower() in ("1", "true", "yes", "y")

DEX_CHAINS = ["solana", "ethereum", "base", "bsc", "polygon"]
GECKO_NETWORKS = ["solana", "eth", "base", "bsc", "polygon_pos"]
seen_fingerprints = set()

STABLE_OR_WRAPPED = ["usdt","tether","usdc","usd coin","dai","busd","fdusd","tusd","usde","paypal usd","stablecoin","wrapped","weth","wbtc","wsol","wmatic","binance-peg","bridged","wormhole","staked","restaked","liquid staking","synthetic usd","usd₮"]
SPAM_TERMS = ["test token","testtoken","lp token","liquidity pool token","fake usdt","fake usdc","claim rewards","airdrop claim","visit to claim","reward token","free claim","official airdrop","presale claim","bonus claim"]
BAD_EMAIL_DOMAINS = {"example.com","domain.com","email.com","test.com","localhost.com"}
CONTACT_PATHS = [
    "/contact", "/contact-us", "/contacts", "/about", "/about-us",
    "/team", "/docs", "/whitepaper", "/partners", "/community"
]

NOISE_LINK_KEYWORDS = [
    "dexscreener.com", "geckoterminal.com", "dextools.io",
    "etherscan.io", "basescan.org", "bscscan.com", "polygonscan.com", "solscan.io",
    "coingecko.com", "coinmarketcap.com"
]


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
    discord: str = ""
    github: str = ""
    logo_url: str = ""
    banner_url: str = ""
    dexscreener_url: str = ""
    source_url: str = ""
    quality_score: int = 0
    risk_score: int = 0
    contact_priority: str = "NONE"
    contact_reason: str = ""
    outreach_route: str = "CLAIM_ONLY"
    analysis_notes: str = ""
    short_description: str = "Early token detected by Cryptalysts."

def clean_text(v: Any) -> str: return "" if v is None else str(v).strip()
def clean_float(v: Any) -> float:
    try: return 0.0 if v is None or v == "" else float(v)
    except Exception: return 0.0
def normalize_url(url: str) -> str:
    url = clean_text(url)
    if not url: return ""
    if url.startswith("mailto:"): return url
    if url.startswith("//"): return "https:" + url
    if not url.startswith(("http://","https://")): return "https://" + url
    return url
def valid_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http","https") and bool(p.netloc) and "." in p.netloc
    except Exception: return False
def now_ms() -> int: return int(datetime.now(timezone.utc).timestamp() * 1000)
def age_minutes_from_ms(created_ms: Any) -> Optional[float]:
    try:
        if not created_ms: return None
        return (now_ms() - int(created_ms)) / 60000
    except Exception: return None
def chain_display(chain: str) -> str:
    return {"eth":"ethereum","ethereum":"ethereum","base":"base","bsc":"bsc","bnb":"bsc","bnb-chain":"bsc","polygon":"polygon","polygon_pos":"polygon","matic":"polygon","sol":"solana","solana":"solana"}.get(clean_text(chain).lower(), clean_text(chain).lower())
def fingerprint(t: TokenCandidate) -> str:
    return hashlib.sha256(f"{t.chain.lower()}::{t.contract_address.lower()}::{t.pair_address.lower()}".encode()).hexdigest()

def http_get_json(url: str, headers=None, timeout=20):
    try:
        r = requests.get(url, timeout=timeout, headers=headers or {"Accept":"application/json","User-Agent":"CryptalystsPrimePulse/4.0"})
        if not r.ok:
            print(f"[GET JSON ERROR] {r.status_code} {url} :: {r.text[:250]}")
            return None
        return r.json()
    except Exception as e:
        print(f"[GET JSON EXCEPTION] {url} :: {e}")
        return None
def http_get_html(url: str, timeout=12):
    url = normalize_url(url)
    if not valid_url(url): return "", url
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True, headers={"Accept":"text/html,application/xhtml+xml,*/*","User-Agent":"Mozilla/5.0 (compatible; CryptalystsBot/4.0; +https://cryptalysts.com)"})
        ctype = r.headers.get("content-type","").lower()
        if not r.ok or ("text/html" not in ctype and "application/xhtml" not in ctype): return "", r.url
        return r.text[:650000], r.url
    except Exception as e:
        print(f"[HTML EXCEPTION] {url} :: {e}")
        return "", url
def http_post_json(url: str, payload: Dict[str, Any], auth: Tuple[str,str]):
    try:
        r = requests.post(url, json=payload, auth=auth, timeout=35, headers={"Accept":"application/json","Content-Type":"application/json","User-Agent":"CryptalystsPrimePulse/4.0"})
        if not r.ok:
            print(f"[POST ERROR] {r.status_code} :: {r.text[:800]}")
            return None
        return r.json()
    except Exception as e:
        print(f"[POST EXCEPTION] {e}")
        return None
def send_telegram(message: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("[TELEGRAM SKIPPED] Missing credentials")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":CHAT_ID,"text":message,"parse_mode":"HTML","disable_web_page_preview":True}, timeout=15)
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")

def meta_content(html: str, keys: List[str]) -> str:
    for key in keys:
        for p in [rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']', rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']', rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(key)}["\']', rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(key)}["\']']:
            m = re.search(p, html, re.I)
            if m: return unescape(m.group(1).strip())
    return ""
def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""
def extract_links(html: str, base_url: str) -> List[str]:
    out = []
    for raw in re.findall(r'(?:href|src)=["\']([^"\']+)["\']', html, re.I):
        u = unescape(raw.strip())
        if u and not u.startswith(("javascript:","#")): out.append(normalize_url(urljoin(base_url, u)))
    return out
def first_matching_link(links: List[str], needles: List[str]) -> str:
    for u in links:
        low = u.lower()
        if any(noise in low for noise in NOISE_LINK_KEYWORDS):
            continue
        if any(n in low for n in needles):
            return u.split("?")[0].rstrip("/")
    return ""
def extract_email(html: str) -> str:
    emails = re.findall(r"mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", html, re.I)
    emails += re.findall(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b", html, re.I)
    cleaned, seen = [], set()
    for e in emails:
        e = e.lower().strip().strip(".,;:()[]{}<>\"'")
        if e in seen: continue
        seen.add(e)
        domain = e.split("@")[-1]
        if domain in BAD_EMAIL_DOMAINS: continue
        if any(e.endswith(x) for x in [".png",".jpg",".jpeg",".webp",".gif",".svg",".css",".js"]): continue
        if any(x in e for x in ["sentry","wixpress","wordpress","schema.org","cloudflare"]): continue
        cleaned.append(e)
    preferred = [e for e in cleaned if any(x in e for x in ["contact","team","hello","info","support","business","partner","marketing"])]
    return (preferred or cleaned or [""])[0]

def deobfuscate_emails(text: str) -> str:
    if not text:
        return text
    text = unescape(text)
    replacements = [
        (" [at] ", "@"), (" (at) ", "@"), ("{at}", "@"), ("[at]", "@"), ("(at)", "@"),
        (" at ", "@"), (" [dot] ", "."), (" (dot) ", "."), ("{dot}", "."), ("[dot]", "."), ("(dot)", "."),
        (" dot ", ".")
    ]
    for a, b in replacements:
        text = text.replace(a, b)
    return text

def extract_email_deep(html: str) -> str:
    direct = extract_email(html)
    if direct:
        return direct
    return extract_email(deobfuscate_emails(html))

def extract_favicon(html: str, base_url: str) -> str:
    m = re.search(r'<link[^>]+rel=["\'][^"\']*(?:icon|shortcut icon|apple-touch-icon)[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
    if m: return normalize_url(urljoin(base_url, unescape(m.group(1).strip())))
    p = urlparse(base_url)
    return f"{p.scheme}://{p.netloc}/favicon.ico" if p.scheme and p.netloc else ""

def same_domain_url(base_url: str, path: str) -> str:
    p = urlparse(base_url)
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}{path}"

def enrich_from_contact_pages(t: TokenCandidate, base_url: str) -> TokenCandidate:
    # Visit a few likely contact/about/team pages to improve email/social discovery.
    for path in CONTACT_PATHS:
        if t.email and t.twitter and t.telegram:
            break

        url = same_domain_url(base_url, path)
        if not url:
            continue

        html, final_url = http_get_html(url, timeout=8)
        if not html:
            continue

        links = extract_links(html, final_url)

        if not t.twitter:
            t.twitter = first_matching_link(links, ["twitter.com/", "x.com/"])
        if not t.telegram:
            t.telegram = first_matching_link(links, ["t.me/", "telegram.me/"])
        if not t.discord:
            t.discord = first_matching_link(links, ["discord.gg/", "discord.com/invite"])
        if not t.github:
            t.github = first_matching_link(links, ["github.com/"])
        if not t.email:
            t.email = extract_email_deep(html)

        if t.email or t.twitter or t.telegram:
            t.analysis_notes += f" Extra contact signals found from {path}."

        time.sleep(0.15)

    return t

def build_outreach_hint(t: TokenCandidate) -> str:
    if t.outreach_route in ("EMAIL", "EMAIL_PLUS_SOCIAL"):
        return "Send concise email: your token is already indexed on Cryptalysts; claim/update listing; optional visibility boost."
    if t.outreach_route == "TELEGRAM":
        return "Use public Telegram context only: short non-spam message directing team to claim listing."
    if t.outreach_route == "TELEGRAM_PLUS_X":
        return "Prioritize Telegram announcement/channel plus public X mention, avoid mass DM."
    if t.outreach_route == "X_PUBLIC":
        return "Use public X reply/mention angle: indexed early on Cryptalysts, claim listing for updates."
    if t.outreach_route == "WEBSITE_CONTACT_CHECK":
        return "Manual website/contact-form check recommended."
    return "No outreach route. Leave as SEO/claim-only listing."

def enrich_from_website(t: TokenCandidate) -> TokenCandidate:
    if not t.website or not valid_url(normalize_url(t.website)): return t
    html, final_url = http_get_html(t.website)
    if not html: return t
    t.website = final_url or t.website
    links = extract_links(html, t.website)
    if not t.twitter: t.twitter = first_matching_link(links, ["twitter.com/","x.com/"])
    if not t.telegram: t.telegram = first_matching_link(links, ["t.me/","telegram.me/"])
    if not t.discord: t.discord = first_matching_link(links, ["discord.gg/","discord.com/invite"])
    if not t.github: t.github = first_matching_link(links, ["github.com/"])
    if not t.email: t.email = extract_email_deep(html)
    og_img = meta_content(html, ["og:image","twitter:image","twitter:image:src"])
    if og_img: og_img = normalize_url(urljoin(t.website, og_img))
    if og_img and not t.banner_url: t.banner_url = og_img
    if og_img and not t.logo_url: t.logo_url = og_img
    if not t.logo_url: t.logo_url = extract_favicon(html, t.website)
    desc = meta_content(html, ["description","og:description","twitter:description"])
    title = page_title(html)
    if desc: t.analysis_notes += f" Website metadata: {desc[:220]}"
    elif title: t.analysis_notes += f" Website title detected: {title[:160]}"

    t = enrich_from_contact_pages(t, t.website)
    return t

def dex_profile_links(profile: Dict[str, Any]) -> Dict[str,str]:
    out = {"website":"","twitter":"","telegram":"","discord":"","email":"","banner":""}
    for link in profile.get("links", []) or []:
        url = normalize_url(clean_text(link.get("url")))
        typ = clean_text(link.get("type")).lower()
        label = clean_text(link.get("label")).lower()
        low = url.lower()
        if not url: continue
        if not out["twitter"] and (typ in ("twitter","x") or "twitter" in label or "twitter.com/" in low or "x.com/" in low): out["twitter"] = url
        elif not out["telegram"] and (typ == "telegram" or "telegram" in label or "t.me/" in low): out["telegram"] = url
        elif not out["discord"] and ("discord" in typ or "discord" in label or "discord.gg/" in low or "discord.com/invite" in low): out["discord"] = url
        elif not out["email"] and url.startswith("mailto:"): out["email"] = url.replace("mailto:","").split("?")[0].strip()
        elif not out["website"] and valid_url(url): out["website"] = url
    out["banner"] = normalize_url(clean_text(profile.get("header") or profile.get("banner") or ""))
    return out
def dex_pair_links(pair: Dict[str,Any]) -> Dict[str,str]:
    info = pair.get("info") or {}
    out = {"website":"","twitter":"","telegram":"","discord":"","email":"","logo":normalize_url(clean_text(info.get("imageUrl") or "")),"banner":normalize_url(clean_text(info.get("header") or ""))}
    combined = []
    combined.extend(info.get("websites") or [])
    combined.extend(info.get("socials") or [])
    for item in combined:
        url = normalize_url(clean_text(item.get("url")))
        typ = clean_text(item.get("type")).lower()
        low = url.lower()
        if not url: continue
        if not out["twitter"] and (typ in ("twitter","x") or "twitter.com/" in low or "x.com/" in low): out["twitter"] = url
        elif not out["telegram"] and (typ == "telegram" or "t.me/" in low or "telegram.me/" in low): out["telegram"] = url
        elif not out["discord"] and ("discord" in typ or "discord.gg/" in low or "discord.com/invite" in low): out["discord"] = url
        elif not out["email"] and url.startswith("mailto:"): out["email"] = url.replace("mailto:","").split("?")[0].strip()
        elif not out["website"] and valid_url(url): out["website"] = url
    return out
def dex_fetch_pairs(addresses: List[str]) -> List[Dict[str,Any]]:
    pairs = []
    for i in range(0, len(addresses), 30):
        data = http_get_json(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(addresses[i:i+30])}")
        if isinstance(data, dict): pairs.extend(data.get("pairs") or [])
        time.sleep(0.25)
    return pairs
def dex_candidate(pair: Dict[str,Any], pmap: Dict[str,Dict[str,Any]], source: str) -> Optional[TokenCandidate]:
    chain = chain_display(pair.get("chainId"))
    if chain not in DEX_CHAINS: return None
    base = pair.get("baseToken") or {}
    contract = clean_text(base.get("address"))
    if not contract: return None
    prof = pmap.get(contract, {})
    pl, il = dex_profile_links(prof), dex_pair_links(pair)
    market_url = clean_text(pair.get("url") or prof.get("url") or "")
    return TokenCandidate(
        source=source, chain=chain, name=clean_text(base.get("name") or prof.get("name") or "Unknown Token"),
        symbol=clean_text(base.get("symbol") or prof.get("symbol") or ""), contract_address=contract,
        pair_address=clean_text(pair.get("pairAddress")), dex=clean_text(pair.get("dexId")).upper(),
        liquidity_usd=clean_float((pair.get("liquidity") or {}).get("usd")),
        volume_h1=clean_float((pair.get("volume") or {}).get("h1")), age_minutes=age_minutes_from_ms(pair.get("pairCreatedAt")),
        website=il["website"] or pl["website"], twitter=il["twitter"] or pl["twitter"], telegram=il["telegram"] or pl["telegram"],
        discord=il["discord"] or pl["discord"], email=il["email"] or pl["email"], logo_url=il["logo"] or normalize_url(clean_text(prof.get("icon"))),
        banner_url=il["banner"] or pl["banner"], dexscreener_url=market_url, source_url=market_url
    )
def source_dex_profiles() -> List[TokenCandidate]:
    profiles = http_get_json("https://api.dexscreener.com/token-profiles/latest/v1")
    if not isinstance(profiles, list): return []
    addrs, pmap = [], {}
    for p in profiles:
        chain, addr = chain_display(p.get("chainId")), clean_text(p.get("tokenAddress"))
        if chain in DEX_CHAINS and addr: addrs.append(addr); pmap[addr] = p
    return [c for c in (dex_candidate(p, pmap, "dexscreener_profiles") for p in dex_fetch_pairs(addrs)) if c]
def source_dex_boosts() -> List[TokenCandidate]:
    profs = []
    for url in ["https://api.dexscreener.com/token-boosts/latest/v1","https://api.dexscreener.com/token-boosts/top/v1"]:
        data = http_get_json(url)
        if isinstance(data, list): profs.extend(data)
        time.sleep(0.25)
    addrs, pmap = [], {}
    for p in profs:
        chain, addr = chain_display(p.get("chainId")), clean_text(p.get("tokenAddress"))
        if chain in DEX_CHAINS and addr: addrs.append(addr); pmap[addr] = p
    return [c for c in (dex_candidate(p, pmap, "dexscreener_boosts") for p in dex_fetch_pairs(list(dict.fromkeys(addrs)))) if c]

def gecko_map(included: List[Dict[str,Any]]) -> Dict[str,Dict[str,Any]]:
    out = {}
    for item in included or []:
        if clean_text(item.get("type")) and clean_text(item.get("id")): out[f"{clean_text(item.get('type'))}:{clean_text(item.get('id'))}"] = item
    return out
def nested_number(d: Dict[str,Any], path: List[str]) -> float:
    cur = d
    for p in path:
        if not isinstance(cur, dict): return 0.0
        cur = cur.get(p)
    return clean_float(cur)
def gecko_candidate(pool: Dict[str,Any], inc: Dict[str,Dict[str,Any]], network: str) -> Optional[TokenCandidate]:
    attrs, rel = pool.get("attributes") or {}, pool.get("relationships") or {}
    base_ref = ((rel.get("base_token") or {}).get("data") or {})
    ba = (inc.get(f"token:{clean_text(base_ref.get('id'))}", {}).get("attributes") or {})
    contract = clean_text(ba.get("address"))
    if not contract: return None
    age = None
    created = clean_text(attrs.get("pool_created_at"))
    if created:
        try: age = (datetime.now(timezone.utc) - datetime.fromisoformat(created.replace("Z","+00:00"))).total_seconds() / 60
        except Exception: age = None
    pool_id = clean_text(pool.get("id"))
    return TokenCandidate(
        source="geckoterminal", chain=chain_display(network), name=clean_text(ba.get("name") or "Unknown Token"),
        symbol=clean_text(ba.get("symbol") or ""), contract_address=contract, pair_address=pool_id.split("_")[-1] if "_" in pool_id else pool_id,
        dex=clean_text(attrs.get("dex_id")).upper(), liquidity_usd=clean_float(attrs.get("reserve_in_usd")),
        volume_h1=nested_number(attrs, ["volume_usd","h1"]), age_minutes=age, website=normalize_url(clean_text(attrs.get("url"))),
        logo_url=normalize_url(clean_text(ba.get("image_url"))), source_url=normalize_url(clean_text(attrs.get("url")))
    )
def source_gecko_new_pools() -> List[TokenCandidate]:
    out = []
    headers = {"Accept":"application/json;version=20230302","User-Agent":"CryptalystsPrimePulse/4.0"}
    for network in GECKO_NETWORKS:
        data = http_get_json(f"https://api.geckoterminal.com/api/v2/networks/{network}/new_pools?include=base_token,quote_token", headers=headers)
        if isinstance(data, dict):
            inc = gecko_map(data.get("included") or [])
            for pool in data.get("data") or []:
                c = gecko_candidate(pool, inc, network)
                if c: out.append(c)
        time.sleep(0.5)
    return out

def stable_or_wrapped(name: str, symbol: str) -> bool:
    text = f"{name} {symbol}".lower()
    compact = symbol.lower().replace("$","").strip()
    if compact in {"usdt","usdc","dai","busd","fdusd","tusd","usde","weth","wbtc","wsol","wmatic","eth","btc","bnb","sol","matic"}: return True
    return any(k in text for k in STABLE_OR_WRAPPED)
def spam_name(name: str, symbol: str) -> bool:
    return any(k in f"{name} {symbol}".lower() for k in SPAM_TERMS)
def contact_priority(t: TokenCandidate) -> TokenCandidate:
    if t.email and (t.twitter or t.telegram):
        t.contact_priority, t.outreach_route, t.contact_reason = "HIGH","EMAIL_PLUS_SOCIAL","Email plus at least one public social/community channel found."
    elif t.email:
        t.contact_priority, t.outreach_route, t.contact_reason = "HIGH","EMAIL","Public email found."
    elif t.telegram and t.twitter:
        t.contact_priority, t.outreach_route, t.contact_reason = "HIGH","TELEGRAM_PLUS_X","Telegram and X/Twitter found."
    elif t.telegram:
        t.contact_priority, t.outreach_route, t.contact_reason = "MEDIUM","TELEGRAM","Telegram community/contact found."
    elif t.twitter:
        t.contact_priority, t.outreach_route, t.contact_reason = "MEDIUM","X_PUBLIC","X/Twitter profile found."
    elif t.discord:
        t.contact_priority, t.outreach_route, t.contact_reason = "MEDIUM","DISCORD_PUBLIC","Discord community invite found."
    elif t.github:
        t.contact_priority, t.outreach_route, t.contact_reason = "LOW","GITHUB_RESEARCH","GitHub found; manual project research possible."
    elif t.website:
        t.contact_priority, t.outreach_route, t.contact_reason = "LOW","WEBSITE_CONTACT_CHECK","Website found but no direct email/social contact detected."
    else:
        t.contact_priority, t.outreach_route, t.contact_reason = "NONE","CLAIM_ONLY","No direct public contact found during first enrichment."

    hint = build_outreach_hint(t)
    if hint and hint not in t.analysis_notes:
        t.analysis_notes += f" Outreach hint: {hint}"
    return t

def score(t: TokenCandidate) -> TokenCandidate:
    t = contact_priority(t)
    q, r, notes = 0, 0, []
    if t.age_minutes is None: r += 8; notes.append("Pair age unavailable.")
    elif t.age_minutes <= 30: q += 18; notes.append("Very fresh pair detected.")
    elif t.age_minutes <= 180: q += 12; notes.append("Recent pair detected.")
    else: q += 3; r += 10; notes.append("Older than preferred discovery window.")
    if t.liquidity_usd >= 25000: q += 20; notes.append("Liquidity above early-signal threshold.")
    elif t.liquidity_usd >= 10000: q += 15; notes.append("Acceptable early liquidity.")
    elif t.liquidity_usd >= MIN_LIQUIDITY_USD: q += 8; r += 8; notes.append("Liquidity present but thin.")
    else: r += 35; notes.append("Liquidity below threshold.")
    if t.volume_h1 >= 25000: q += 18; notes.append("Strong 1h activity.")
    elif t.volume_h1 >= 5000: q += 12; notes.append("Visible 1h activity.")
    elif t.volume_h1 > 0: q += 5; notes.append("Low but visible 1h activity.")
    else: r += 8; notes.append("1h volume unavailable or very low.")
    if t.website: q += 10
    else: r += 8
    if t.twitter: q += 12
    else: r += 5
    if t.telegram: q += 8
    if t.email: q += 7; notes.append("Public email found.")
    if t.discord: q += 5
    if t.github: q += 3
    if t.contact_priority == 'HIGH': q += 8
    elif t.contact_priority == 'MEDIUM': q += 4
    if t.logo_url: q += 7
    if t.banner_url: q += 5
    if stable_or_wrapped(t.name, t.symbol): q -= 30; r += 80; notes.append("Stablecoin/wrapped/official asset pattern.")
    if spam_name(t.name, t.symbol): q -= 15; r += 40; notes.append("Spam-like naming pattern.")
    if not (t.website or t.twitter or t.telegram or t.email): r += 15; notes.append("No public contact channel found.")
    t.quality_score, t.risk_score = max(0,min(100,q)), max(0,min(100,r))
    t.analysis_notes = " ".join(notes + ([t.analysis_notes.strip()] if t.analysis_notes.strip() else []))
    return t
def passes(t: TokenCandidate) -> Tuple[bool,str]:
    if not t.contract_address: return False, "missing contract"
    if not t.name or t.name.lower() in {"unknown","n/a","unknown token"}: return False, "missing name"
    if stable_or_wrapped(t.name, t.symbol): return False, "stable/wrapped/official filtered"
    if spam_name(t.name, t.symbol): return False, "spam name filtered"
    if t.liquidity_usd < MIN_LIQUIDITY_USD: return False, f"low liquidity {t.liquidity_usd}"
    if t.volume_h1 < MIN_VOLUME_H1_USD: return False, f"low h1 volume {t.volume_h1}"
    if t.age_minutes is not None and t.age_minutes > MAX_AGE_MINUTES: return False, f"too old {t.age_minutes:.1f}m"
    if REQUIRE_SOCIAL and not (t.website or t.twitter or t.telegram or t.email): return False, "missing contact/social"
    if t.quality_score < MIN_QUALITY_SCORE: return False, f"quality too low {t.quality_score}"
    if t.risk_score >= 80: return False, f"risk too high {t.risk_score}"
    return True, "ok"
def merge(cands: List[TokenCandidate]) -> List[TokenCandidate]:
    merged = {}
    for c in cands:
        if not c.contract_address: continue
        key = f"{c.chain.lower()}::{c.contract_address.lower()}"
        if key not in merged: merged[key] = c; continue
        e = merged[key]
        if c.liquidity_usd > e.liquidity_usd: e.liquidity_usd = c.liquidity_usd
        if c.volume_h1 > e.volume_h1: e.volume_h1 = c.volume_h1
        if e.age_minutes is None or (c.age_minutes is not None and c.age_minutes < e.age_minutes): e.age_minutes = c.age_minutes
        for f in ["website","twitter","telegram","email","discord","github","logo_url","banner_url","dexscreener_url","source_url","pair_address","dex"]:
            if not getattr(e, f) and getattr(c, f): setattr(e, f, getattr(c, f))
        if c.source not in e.source: e.source += "+" + c.source
    return list(merged.values())
def publish(t: TokenCandidate) -> Tuple[bool,str]:
    if not WP_USERNAME or not WP_APP_PASSWORD: return False, "Missing WordPress credentials"
    payload = {
        "name":t.name,"symbol":t.symbol,"chain":t.chain,"contract_address":t.contract_address,"pair_address":t.pair_address,"dex":t.dex,
        "website":t.website,"twitter":t.twitter,"telegram":t.telegram,"email":t.email,"discord":t.discord,"github":t.github,
        "logo_url":t.logo_url,"banner_url":t.banner_url,"dexscreener_url":t.dexscreener_url,"source_url":t.source_url,"source":t.source,
        "liquidity_usd":round(t.liquidity_usd,2),"volume_h1":round(t.volume_h1,2),"age_minutes":round(t.age_minutes,2) if t.age_minutes is not None else "",
        "quality_score":t.quality_score,"risk_score":t.risk_score,"contact_priority":t.contact_priority,"contact_reason":t.contact_reason,
        "outreach_route":t.outreach_route,"analysis_notes":t.analysis_notes,"short_description":t.short_description,"promoted":False,
    }
    res = http_post_json(WP_API_URL, payload, auth=(WP_USERNAME, WP_APP_PASSWORD))
    if not res: return False, "No response from WordPress"
    if res.get("ok"): return True, ("duplicate: " if res.get("duplicate") else "created: ") + clean_text(res.get("url"))
    return False, json.dumps(res)[:700]
def fmt_money(v): return f"${v:,.0f}" if v else "N/A"
def telegram_message(t: TokenCandidate, wp_status: str) -> str:
    age = f"{t.age_minutes:.1f} min" if t.age_minutes is not None else "N/A"
    contacts = []
    if t.website: contacts.append(f"🌐 <b>Website:</b> {t.website}")
    if t.twitter: contacts.append(f"🐦 <b>X:</b> {t.twitter}")
    if t.telegram: contacts.append(f"💬 <b>Telegram:</b> {t.telegram}")
    if t.email: contacts.append(f"✉️ <b>Email:</b> {t.email}")
    if t.discord: contacts.append(f"🟣 <b>Discord:</b> {t.discord}")
    if t.github: contacts.append(f"🐙 <b>GitHub:</b> {t.github}")
    contact_text = "\\n".join(contacts) if contacts else "No public contact channel found yet"
    return (f"🚨 <b>QUALIFIED TOKEN DETECTED</b>\\n━━━━━━━━━━━━━━━━━━\\n🪙 <b>Token:</b> {t.name} ({t.symbol})\\n⛓ <b>Chain:</b> {t.chain}\\n🏦 <b>DEX:</b> {t.dex or 'N/A'}\\n⏱ <b>Age:</b> {age}\\n💧 <b>Liquidity:</b> {fmt_money(t.liquidity_usd)}\\n📊 <b>Volume 1h:</b> {fmt_money(t.volume_h1)}\\n🧠 <b>Quality:</b> {t.quality_score}/100\\n⚠️ <b>Risk:</b> {t.risk_score}/100\\n📬 <b>Contact Priority:</b> {t.contact_priority}\\n🎯 <b>Outreach Route:</b> {t.outreach_route}\\n📌 <b>Reason:</b> {t.contact_reason}\\n📄 <b>Contract:</b> <code>{t.contract_address}</code>\\n🔎 <b>Source:</b> {t.source}\\n{contact_text}\\n🔗 <b>Market:</b> {t.dexscreener_url or t.source_url or 'N/A'}\\n🧩 <b>Cryptalysts:</b> {wp_status}")
def collect():
    allc = []
    for name, fn in [("DexScreener latest profiles", source_dex_profiles), ("DexScreener boosts", source_dex_boosts), ("GeckoTerminal new pools", source_gecko_new_pools)]:
        try:
            print(f"[SOURCE] {name}")
            items = fn()
            print(f"[SOURCE] {name}: {len(items)}")
            allc.extend(items)
        except Exception as e: print(f"[SOURCE ERROR] {name}: {e}")
    return merge(allc)
def process(candidates):
    print(f"[PROCESS] merged candidates: {len(candidates)}")
    published, skipped = 0, 0
    for t in candidates:
        fp = fingerprint(t)
        if fp in seen_fingerprints: skipped += 1; continue
        if t.website: t = enrich_from_website(t)
        t = score(t)
        ok, reason = passes(t)
        if not ok:
            print(f"[SKIP] {t.chain} {t.symbol} {t.contract_address} :: {reason}")
            skipped += 1
            continue
        seen_fingerprints.add(fp)
        wp_ok, wp_status = publish(t)
        if wp_ok: published += 1; print(f"[PUBLISHED] {t.chain} {t.symbol} :: {wp_status}")
        else: print(f"[WP FAILED] {t.chain} {t.symbol} :: {wp_status}")
        send_telegram(telegram_message(t, wp_status))
        time.sleep(1)
    print(f"[SUMMARY] published={published} skipped={skipped}")
def run_once():
    print("============================================================")
    print("PrimePulse / Cryptalysts Discovery Engine v0.4")
    print(f"UTC: {datetime.now(timezone.utc).isoformat()}")
    print(f"Min liquidity: ${MIN_LIQUIDITY_USD:,.0f}")
    print(f"Max age: {MAX_AGE_MINUTES} minutes")
    print(f"Min quality: {MIN_QUALITY_SCORE}/100")
    print("Sources: DexScreener + GeckoTerminal + Deep Website Contact Enrichment")
    print("============================================================")
    process(collect())
def main():
    if RUN_ONCE: run_once(); return
    send_telegram("✅ <b>PrimePulse Discovery Engine v0.4 ACTIVE</b>")
    while True:
        try: run_once()
        except Exception as e: print(f"[LOOP ERROR] {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)
if __name__ == "__main__":
    main()
