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
ENRICHMENT_MAX_PAGES = int(os.getenv("ENRICHMENT_MAX_PAGES", "6"))
ENRICHMENT_TIMEOUT = int(os.getenv("ENRICHMENT_TIMEOUT", "12"))
CONTACT_FORM_SCAN = os.getenv("CONTACT_FORM_SCAN", "true").lower() in ("1", "true", "yes", "y")

DEX_CHAINS = ["solana", "ethereum", "base", "bsc", "polygon"]
GECKO_NETWORKS = ["solana", "eth", "base", "bsc", "polygon_pos"]
seen_fingerprints = set()

STABLE_OR_WRAPPED = ["usdt","tether","usdc","usd coin","dai","busd","fdusd","tusd","usde","stablecoin","wrapped","weth","wbtc","wsol","wmatic","binance-peg","bridged","wormhole","staked","restaked"]
SPAM_TERMS = ["test token","testtoken","lp token","liquidity pool token","fake usdt","fake usdc","claim rewards","airdrop claim","visit to claim","reward token"]
BAD_EMAIL_DOMAINS = {"example.com","domain.com","email.com","test.com","localhost.com","sentry.io","schema.org","wixpress.com"}
BAD_EMAIL_LOCALS = {"noreply","no-reply","donotreply","do-not-reply","privacy","legal","abuse","security","admin","webmaster"}
EMAIL_PRIORITY_WORDS = [
    "partnership", "partnerships", "business", "bizdev", "marketing", "growth", "founder", "ceo",
    "team", "hello", "contact", "support", "listing", "press", "media", "info"
]
CONTACT_PATHS = [
    "/contact", "/contact-us", "/contacts", "/support", "/help", "/about", "/about-us", "/team",
    "/community", "/partnership", "/partnerships", "/partners", "/business", "/press", "/media",
    "/docs", "/documentation", "/developers", "/dev", "/whitepaper", "/litepaper", "/links"
]
CONTACT_HINTS = ["contact", "support", "help", "team", "about", "business", "partnership", "partner", "press", "media", "docs", "whitepaper", "litepaper", "community"]
FORM_PLATFORMS = ["typeform.com", "tally.so", "forms.gle", "docs.google.com/forms", "formspree.io", "hubspot", "hsforms", "airtable.com", "notion.site", "jotform.com", "paperform.co"]
PROFILE_HUBS = ["linktr.ee", "linktree", "beacons.ai", "bio.link", "carrd.co", "taplink.cc", "solo.to"]
BLOCKED_WEBSITE_DOMAINS = {
    "x.com","twitter.com","t.me","telegram.me","discord.gg","discord.com","discordapp.com",
    "dexscreener.com","dextools.io","pump.fun","birdeye.so","geckoterminal.com",
    "solscan.io","etherscan.io","bscscan.com","basescan.org","polygonscan.com",
    "coingecko.com","coinmarketcap.com","youtube.com","youtu.be","instagram.com","facebook.com",
    "reddit.com","medium.com"
}

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
    lead_score: int = 0
    trending_score: int = 0
    visibility_score: int = 0
    branding_score: int = 0
    growth_score: int = 0
    claim_status: str = "Unclaimed"
    outreach_status: str = "Not contacted"
    outreach_subject: str = ""
    outreach_email: str = ""
    outreach_x: str = ""
    outreach_telegram: str = ""
    outreach_next_action: str = ""
    indexed_keywords: str = ""
    auto_tags: str = ""
    project_category: str = "Unclassified"
    ai_visibility_score: int = 0
    contact_form_url: str = ""
    docs_url: str = ""
    linktree_url: str = ""
    medium_url: str = ""
    reddit_url: str = ""
    discovered_emails: str = ""
    enrichment_pages_scanned: int = 0
    enrichment_sources: str = ""
    contact_reachability_score: int = 0
    best_contact_route: str = ""

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

def domain_of(url: str) -> str:
    try:
        return urlparse(normalize_url(url)).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def is_blocked_website(url: str) -> bool:
    d = domain_of(url)
    if not d:
        return True
    return any(d == b or d.endswith("." + b) for b in BLOCKED_WEBSITE_DOMAINS)

def valid_project_website(url: str) -> bool:
    url = normalize_url(url)
    return valid_url(url) and not is_blocked_website(url)

def same_domain_or_hub(url: str, root_domain: str) -> bool:
    d = domain_of(url)
    if not d:
        return False
    if d == root_domain or d.endswith("." + root_domain):
        return True
    return any(h in d for h in PROFILE_HUBS) or d in {"github.com"}
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
        r = requests.get(url, timeout=timeout, headers=headers or {"Accept":"application/json","User-Agent":"CryptalystsPrimePulse/9.0"})
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
        r = requests.get(url, timeout=timeout, allow_redirects=True, headers={"Accept":"text/html,application/xhtml+xml,*/*","User-Agent":"Mozilla/5.0 (compatible; CryptalystsBot/9.0; +https://cryptalysts.com)"})
        ctype = r.headers.get("content-type","").lower()
        if not r.ok or ("text/html" not in ctype and "application/xhtml" not in ctype): return "", r.url
        return r.text[:650000], r.url
    except Exception as e:
        print(f"[HTML EXCEPTION] {url} :: {e}")
        return "", url
def http_post_json(url: str, payload: Dict[str, Any], auth: Tuple[str,str]):
    try:
        r = requests.post(url, json=payload, auth=auth, timeout=45, headers={"Accept":"application/json","Content-Type":"application/json","User-Agent":"CryptalystsPrimePulse/9.0"})
        if not r.ok:
            print(f"[POST ERROR] {r.status_code} :: {r.text[:1000]}")
            return {"ok": False, "error": f"HTTP {r.status_code}", "body": r.text[:1000]}
        try:
            return r.json()
        except Exception:
            print(f"[POST INVALID JSON] {r.text[:1000]}")
            return {"ok": False, "error": "Invalid JSON/empty WordPress response", "body": r.text[:1000]}
    except Exception as e:
        print(f"[POST EXCEPTION] {e}")
        return {"ok": False, "error": str(e), "body": ""}

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
    for pattern in [r'href="([^"]+)"', r"href='([^']+)'", r'src="([^"]+)"', r"src='([^']+)'"]:
        for raw in re.findall(pattern, html, re.I):
            u = unescape(raw.strip())
            if not u or u.startswith(("javascript:", "#", "mailto:", "tel:")):
                continue
            full = normalize_url(urljoin(base_url, u))
            if valid_url(full):
                out.append(full)
    return unique_keep_order(out)
def first_matching_link(links: List[str], needles: List[str]) -> str:
    for u in links:
        low = u.lower()
        if any(n in low for n in needles): return u
    return ""
def clean_email_value(e: str) -> str:
    e = unescape(e or "").lower().strip().strip(".,;:()[]{}<>\"'")
    e = re.sub(r"\s+", "", e)
    e = e.replace("[at]", "@").replace("(at)", "@").replace("{at}", "@").replace(" at ", "@")
    e = e.replace("[dot]", ".").replace("(dot)", ".").replace("{dot}", ".").replace(" dot ", ".")
    e = e.replace("&#64;", "@").replace("%40", "@")
    return e

def valid_email(e: str) -> bool:
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", e or ""):
        return False
    local, domain = e.split("@", 1)
    if domain in BAD_EMAIL_DOMAINS:
        return False
    if local in BAD_EMAIL_LOCALS:
        return False
    if any(e.endswith(x) for x in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".css", ".js"]):
        return False
    if any(x in e for x in ["sentry", "wixpress", "wordpress", "schema.org", "cloudflare", "example", "email@"]):
        return False
    return True

def extract_emails(html: str) -> List[str]:
    raw = []
    raw += re.findall(r"mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", html, re.I)
    raw += re.findall(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b", html, re.I)
    raw += re.findall(r"\b[a-zA-Z0-9._%+\-]+\s*(?:\[at\]|\(at\)|\{at\}| at )\s*[a-zA-Z0-9.\-]+\s*(?:\[dot\]|\(dot\)|\{dot\}| dot )\s*[a-zA-Z]{2,}\b", html, re.I)
    raw += re.findall(r"[a-zA-Z0-9._%+\-]+\s*\\u0040\s*[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", html, re.I)

    out, seen = [], set()
    for e in raw:
        ce = clean_email_value(e.replace("\\u0040", "@"))
        if ce in seen or not valid_email(ce):
            continue
        seen.add(ce)
        out.append(ce)
    return out

def pick_best_email(emails: List[str]) -> str:
    if not emails:
        return ""
    scored = []
    for e in emails:
        local = e.split("@")[0]
        score = 0
        for i, w in enumerate(EMAIL_PRIORITY_WORDS):
            if w in local:
                score += 300 - (i * 8)
        if any(x in local for x in ["partnership", "business", "marketing", "growth", "founder", "bizdev"]):
            score += 120
        if any(x in local for x in ["support", "hello", "contact", "team"]):
            score += 60
        if any(x in local for x in ["noreply", "no-reply", "privacy", "legal", "abuse"]):
            score -= 500
        scored.append((score, e))
    scored.sort(reverse=True)
    return scored[0][1]

def extract_email(html: str) -> str:
    return pick_best_email(extract_emails(html))

def contact_like_url(url: str) -> bool:
    low = (url or "").lower()
    return any(k in low for k in ["contact", "support", "help", "business", "partnership", "partner", "inquiry", "enquiry"])

def bad_form_context(url: str, html: str = "") -> bool:
    low = ((url or "") + " " + (html or "")[:25000]).lower()
    return any(k in low for k in ["newsletter", "subscribe", "signup", "sign-up", "waitlist", "early-access", "notify me", "updates"])

def detect_contact_form(html: str, page_url: str, links: List[str]) -> Tuple[str, str]:
    if not CONTACT_FORM_SCAN:
        return "", ""
    low = html.lower()
    if contact_like_url(page_url) and not bad_form_context(page_url, html):
        if "<form" in low and any(k in low for k in ["message", "your message", "contact us", "get in touch", "business inquiries", "support request", "partnership"]):
            m = re.search(r"<form[^>]+action=[\"']([^\"']+)[\"']", html, re.I)
            action = normalize_url(urljoin(page_url, unescape(m.group(1).strip()))) if m else page_url
            if contact_like_url(action) or contact_like_url(page_url):
                return action, "strict_html_contact_form"
    for u in links:
        lu = u.lower()
        if bad_form_context(lu):
            continue
        if contact_like_url(lu) and any(platform in lu for platform in FORM_PLATFORMS):
            return u, "strict_form_platform"
    return "", ""
    low = html.lower()
    if "<form" in low and any(k in low for k in ["contact", "message", "email", "name", "submit", "inquiry", "partnership"]):
        m = re.search(r"<form[^>]+action=[\"']([^\"']+)[\"']", html, re.I)
        action = normalize_url(urljoin(page_url, unescape(m.group(1).strip()))) if m else page_url
        return action, "html_form"
    for u in links:
        lu = u.lower()
        for platform in FORM_PLATFORMS:
            if platform in lu:
                return u, platform
    return "", ""

def unique_keep_order(items: List[str]) -> List[str]:
    out, seen = [], set()
    for x in items:
        x = clean_text(x)
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out

def candidate_enrichment_urls(home_url: str, homepage_html: str, homepage_links: List[str]) -> List[str]:
    base = normalize_url(home_url)
    parsed = urlparse(base)
    root = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else base
    root_domain = parsed.netloc.lower().replace("www.", "")

    urls = [base]
    urls += [normalize_url(urljoin(root, p)) for p in CONTACT_PATHS]

    for u in homepage_links:
        lu = u.lower()
        if any(n in lu for n in CONTACT_HINTS) or any(h in lu for h in PROFILE_HUBS):
            if same_domain_or_hub(u, root_domain):
                urls.append(u)

    for raw in re.findall(r'https?://[^\s\\"\'<>]+', homepage_html, re.I):
        u = normalize_url(raw.strip().rstrip(".,);]"))
        lu = u.lower()
        if valid_url(u) and not is_blocked_website(u) and (same_domain_or_hub(u, root_domain) or any(n in lu for n in CONTACT_HINTS)):
            urls.append(u)

    return unique_keep_order(urls)[:max(1, ENRICHMENT_MAX_PAGES)]

def extract_favicon(html: str, base_url: str) -> str:
    m = re.search(r'<link[^>]+rel=["\'][^"\']*(?:icon|shortcut icon|apple-touch-icon)[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
    if m: return normalize_url(urljoin(base_url, unescape(m.group(1).strip())))
    p = urlparse(base_url)
    return f"{p.scheme}://{p.netloc}/favicon.ico" if p.scheme and p.netloc else ""
def enrich_from_website(t: TokenCandidate) -> TokenCandidate:
    if not t.website or not valid_project_website(normalize_url(t.website)):
        return t

    home_html, final_url = http_get_html(t.website, timeout=ENRICHMENT_TIMEOUT)
    if not home_html:
        return t

    t.website = final_url or t.website
    home_links = extract_links(home_html, t.website)
    urls = candidate_enrichment_urls(t.website, home_html, home_links)

    all_links: List[str] = []
    all_emails: List[str] = []
    pages_scanned = 0
    sources = []

    for page_url in urls:
        html, final = http_get_html(page_url, timeout=ENRICHMENT_TIMEOUT)
        if not html:
            continue
        pages_scanned += 1
        sources.append(final or page_url)
        links = extract_links(html, final or page_url)
        all_links.extend(links)
        all_emails.extend(extract_emails(html))

        if not t.contact_form_url:
            form_url, form_platform = detect_contact_form(html, final or page_url, links)
            if form_url:
                t.contact_form_url = form_url
                if form_platform:
                    t.enrichment_sources += f" form:{form_platform}"

        og_img = meta_content(html, ["og:image", "twitter:image", "twitter:image:src"])
        if og_img:
            og_img = normalize_url(urljoin(final or page_url, og_img))
            if og_img and not t.banner_url: t.banner_url = og_img
            if og_img and not t.logo_url: t.logo_url = og_img

        desc = meta_content(html, ["description", "og:description", "twitter:description"])
        title = page_title(html)
        if desc and "Website metadata:" not in t.analysis_notes:
            t.analysis_notes += f" Website metadata: {desc[:220]}"
        elif title and "Website title detected:" not in t.analysis_notes:
            t.analysis_notes += f" Website title detected: {title[:160]}"

        time.sleep(0.15)

    preliminary_links = unique_keep_order(home_links + all_links)
    hub_links = [u for u in preliminary_links if any(h in u.lower() for h in PROFILE_HUBS)]
    for hub in hub_links[:2]:
        html, final = http_get_html(hub, timeout=ENRICHMENT_TIMEOUT)
        if not html:
            continue
        pages_scanned += 1
        sources.append(final or hub)
        hub_extracted = extract_links(html, final or hub)
        all_links.extend(hub_extracted)
        all_emails.extend(extract_emails(html))
        time.sleep(0.15)

    links = unique_keep_order(home_links + all_links)
    all_emails = unique_keep_order(all_emails)

    if not t.twitter: t.twitter = first_matching_link(links, ["twitter.com/", "x.com/"])
    if not t.telegram: t.telegram = first_matching_link(links, ["t.me/", "telegram.me/"])
    if not t.discord: t.discord = first_matching_link(links, ["discord.gg/", "discord.com/invite"])
    if not t.github: t.github = first_matching_link(links, ["github.com/"])
    if not t.docs_url: t.docs_url = first_matching_link(links, ["docs.", "/docs", "whitepaper", "gitbook", "mirror.xyz"])
    if not t.linktree_url: t.linktree_url = first_matching_link(links, ["linktr.ee/", "linktree", "bio.link/", "beacons.ai/"])
    if not t.medium_url: t.medium_url = first_matching_link(links, ["medium.com/", "mirror.xyz/"])
    if not t.reddit_url: t.reddit_url = first_matching_link(links, ["reddit.com/r/"])

    if not t.email:
        t.email = pick_best_email(all_emails)
    if all_emails:
        t.discovered_emails = ", ".join(all_emails[:8])

    if not t.logo_url:
        t.logo_url = extract_favicon(home_html, t.website)

    t.enrichment_pages_scanned = pages_scanned
    t.enrichment_sources = ", ".join(sources[:8])
    if pages_scanned:
        t.analysis_notes += f" Enrichment scanned {pages_scanned} website page(s)."
    if t.contact_form_url:
        t.analysis_notes += " Contact form detected."
    if t.email:
        t.analysis_notes += " Outreach email detected."
    if t.discord:
        t.analysis_notes += " Discord route detected."

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
    headers = {"Accept":"application/json;version=20230302","User-Agent":"CryptalystsPrimePulse/9.0"}
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
    if t.email and (t.twitter or t.telegram or t.discord):
        t.contact_priority, t.outreach_route, t.contact_reason = "HIGH", "EMAIL_PLUS_SOCIAL", "Email plus at least one public social/community channel found."
    elif t.email:
        t.contact_priority, t.outreach_route, t.contact_reason = "HIGH", "EMAIL", "Public outreach email found."
    elif t.contact_form_url and (t.twitter or t.telegram or t.discord):
        t.contact_priority, t.outreach_route, t.contact_reason = "HIGH", "FORM_PLUS_SOCIAL", "Contact form plus public social/community route found."
    elif t.contact_form_url:
        t.contact_priority, t.outreach_route, t.contact_reason = "MEDIUM", "WEBSITE_FORM", "Website contact form found."
    elif t.discord and (t.twitter or t.telegram):
        t.contact_priority, t.outreach_route, t.contact_reason = "MEDIUM", "DISCORD_PLUS_SOCIAL", "Discord plus another community route found."
    elif t.telegram and t.twitter:
        t.contact_priority, t.outreach_route, t.contact_reason = "HIGH", "TELEGRAM_PLUS_X", "Telegram and X/Twitter found."
    elif t.telegram:
        t.contact_priority, t.outreach_route, t.contact_reason = "MEDIUM", "TELEGRAM", "Telegram community/contact found."
    elif t.discord:
        t.contact_priority, t.outreach_route, t.contact_reason = "MEDIUM", "DISCORD", "Discord community route found."
    elif t.twitter:
        t.contact_priority, t.outreach_route, t.contact_reason = "MEDIUM", "X_PUBLIC", "X/Twitter profile found."
    elif t.website:
        t.contact_priority, t.outreach_route, t.contact_reason = "LOW", "WEBSITE_CONTACT_CHECK", "Website found but no direct email/social contact detected."
    else:
        t.contact_priority, t.outreach_route, t.contact_reason = "NONE", "CLAIM_ONLY", "No direct public contact found during enrichment."
    return t

def score(t: TokenCandidate) -> TokenCandidate:
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
    if t.email: q += 12; notes.append("Public email found.")
    if t.contact_form_url: q += 8; notes.append("Contact form found.")
    if t.discord: q += 6
    if t.github: q += 3
    if t.docs_url: q += 4
    if t.linktree_url: q += 3
    if t.logo_url: q += 7
    if t.banner_url: q += 5
    if stable_or_wrapped(t.name, t.symbol): q -= 30; r += 80; notes.append("Stablecoin/wrapped/official asset pattern.")
    if spam_name(t.name, t.symbol): q -= 15; r += 40; notes.append("Spam-like naming pattern.")
    if not (t.website or t.twitter or t.telegram or t.email or t.discord or t.contact_form_url): r += 15; notes.append("No public contact channel found.")
    t.quality_score, t.risk_score = max(0,min(100,q)), max(0,min(100,r))
    t.analysis_notes = " ".join(notes + ([t.analysis_notes.strip()] if t.analysis_notes.strip() else []))
    return contact_priority(t)
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

def safe_symbol(t: TokenCandidate) -> str:
    s = (t.symbol or "").strip()
    return f" ({s})" if s else ""

def build_claim_url(t: TokenCandidate) -> str:
    return "https://cryptalysts.com/submit-listing/"

def build_feature_url(t: TokenCandidate) -> str:
    return "https://cryptalysts.com/feature-your-token/"

def build_indexed_keywords(t: TokenCandidate) -> str:
    parts = [
        t.name, t.symbol, t.chain, t.dex, t.contract_address,
        f"{t.name} token", f"{t.name} crypto", f"{t.symbol} token" if t.symbol else "",
        f"{t.chain} new token", "early token discovery", "claim token listing"
    ]
    return ", ".join([p for p in parts if p])

def build_auto_tags(t: TokenCandidate) -> str:
    tags = ["auto-indexed", "early-token", t.chain]
    if t.contact_priority == "HIGH":
        tags.append("contactable")
    if t.quality_score >= 60:
        tags.append("promising-signal")
    if t.liquidity_usd >= 25000:
        tags.append("strong-liquidity")
    if t.twitter:
        tags.append("x-found")
    if t.telegram:
        tags.append("telegram-found")
    if t.email:
        tags.append("email-found")
    if t.contact_form_url:
        tags.append("contact-form-found")
    if t.discord:
        tags.append("discord-found")
    if t.docs_url:
        tags.append("docs-found")
    return ", ".join([x for x in tags if x])


def classify_project_category(t: TokenCandidate) -> str:
    text = " ".join([t.name, t.symbol, t.website, t.analysis_notes]).lower()
    rules = [
        ("AI", ["ai", "agent", "gpt", "neural", "intelligence", "bot"]),
        ("DeFi", ["defi", "swap", "yield", "staking", "farm", "liquidity", "dao"]),
        ("Meme", ["doge", "pepe", "inu", "cat", "frog", "meme", "wojak", "bonk"]),
        ("Gaming", ["game", "gaming", "play", "nft", "metaverse"]),
        ("Infrastructure", ["chain", "layer", "protocol", "node", "rollup", "bridge", "infra"]),
        ("Trading", ["trade", "trading", "signals", "perp", "bot"]),
        ("Launchpad", ["launch", "presale", "fairlaunch", "ido"]),
        ("NSFW", ["onlyfans", "nsfw", "adult"]),
    ]
    for label, keys in rules:
        if any(k in text for k in keys):
            return label
    return "Unclassified"

def compute_ai_visibility_score(t: TokenCandidate) -> int:
    base = int((t.quality_score * 0.25) + (t.trending_score * 0.25) + (t.visibility_score * 0.25) + (t.lead_score * 0.25))
    if t.project_category in ("AI", "Meme", "DeFi"):
        base += 5
    if t.contact_priority == "HIGH":
        base += 8
    elif t.contact_priority == "MEDIUM":
        base += 4
    if t.logo_url and t.banner_url:
        base += 4
    if t.email:
        base += 5
    if t.contact_form_url:
        base += 3
    return max(0, min(100, base))

def compute_contact_reachability(t: TokenCandidate) -> int:
    score = 0
    if t.email:
        local = t.email.split("@")[0].lower()
        if any(x in local for x in ["partnership", "business", "marketing", "growth", "founder", "bizdev"]):
            score += 45
        elif any(x in local for x in ["hello", "contact", "team"]):
            score += 35
        elif "support" in local:
            score += 25
        else:
            score += 20
    if t.contact_form_url:
        score += 20
    if t.telegram:
        score += 15
    if t.discord:
        score += 12
    if t.twitter:
        score += 8
    if t.linktree_url:
        score += 5
    if t.docs_url:
        score += 4
    if t.github:
        score += 3
    return max(0, min(100, score))

def compute_business_scores(t: TokenCandidate) -> TokenCandidate:
    # Lead score = commercial contact + seriousness.
    lead = 0
    if t.contact_priority == "HIGH": lead += 45
    elif t.contact_priority == "MEDIUM": lead += 30
    elif t.contact_priority == "LOW": lead += 15
    if t.quality_score: lead += min(30, int(t.quality_score * 0.30))
    if t.liquidity_usd >= 25000: lead += 12
    elif t.liquidity_usd >= 10000: lead += 8
    if t.volume_h1 >= 25000: lead += 10
    elif t.volume_h1 >= 5000: lead += 6
    if t.website: lead += 4
    if t.email: lead += 12
    if t.contact_form_url: lead += 8
    if t.discord: lead += 5
    if t.logo_url or t.banner_url: lead += 4
    t.lead_score = max(0, min(100, lead))

    # Trending score = market pulse + freshness + votes later on WP side.
    trending = 0
    if t.age_minutes is not None:
        if t.age_minutes <= 30: trending += 28
        elif t.age_minutes <= 180: trending += 18
        else: trending += 5
    if t.volume_h1 >= 50000: trending += 30
    elif t.volume_h1 >= 10000: trending += 20
    elif t.volume_h1 > 0: trending += 8
    if t.liquidity_usd >= 50000: trending += 20
    elif t.liquidity_usd >= 10000: trending += 12
    if t.twitter or t.telegram: trending += 10
    t.trending_score = max(0, min(100, trending))

    # Visibility score = public presentation readiness.
    visibility = 0
    if t.website: visibility += 20
    if t.twitter: visibility += 20
    if t.telegram: visibility += 15
    if t.email: visibility += 15
    if t.contact_form_url: visibility += 10
    if t.discord: visibility += 7
    if t.logo_url: visibility += 10
    if t.banner_url: visibility += 10
    if t.quality_score >= 60: visibility += 10
    t.visibility_score = max(0, min(100, visibility))

    t.branding_score = max(0, min(100, (20 if t.website else 0) + (20 if t.logo_url else 0) + (20 if t.banner_url else 0) + (20 if t.twitter else 0) + (20 if t.telegram else 0)))
    t.growth_score = max(0, min(100, int((t.volume_h1 / 1000) + (t.liquidity_usd / 5000))))

    t.project_category = classify_project_category(t)
    t.ai_visibility_score = compute_ai_visibility_score(t)
    t.indexed_keywords = build_indexed_keywords(t)
    t.contact_reachability_score = compute_contact_reachability(t)
    t.best_contact_route = t.outreach_route
    t.auto_tags = build_auto_tags(t) + ", " + t.project_category.lower()
    return t

def build_outreach_templates(t: TokenCandidate) -> TokenCandidate:
    claim_url = build_claim_url(t)
    feature_url = build_feature_url(t)
    title = f"{t.name}{safe_symbol(t)}"

    t.outreach_subject = f"{t.name} is already indexed on Cryptalysts"

    t.outreach_email = (
        f"Hi {t.name} team,\n\n"
        f"Your token {title} was detected and indexed on Cryptalysts as an early-stage token listing.\n\n"
        f"You can claim your listing for free and unlock visibility tools here:\n{claim_url}\n\n"
        f"If you want stronger exposure, featured placement and PrimePulseOps visibility campaigns are available here:\n{feature_url}\n\n"
        f"Cryptalysts tracks early token visibility, ranking signals and public discovery for new crypto projects.\n"
    )

    t.outreach_x = (
        f"{t.name} was detected early and indexed on Cryptalysts. "
        f"Project team can claim the listing for free and unlock visibility tools: {claim_url}"
    )

    t.outreach_telegram = (
        f"Hi team — {t.name} was detected by Cryptalysts and indexed as an early token listing. "
        f"You can claim your listing for free here: {claim_url}. Featured visibility is available via {feature_url}."
    )

    if t.contact_priority == "HIGH":
        t.outreach_next_action = "Contact now using email/contact form plus the strongest public social route."
    elif t.contact_priority == "MEDIUM":
        t.outreach_next_action = "Use Telegram/Discord/X or website form carefully; avoid spam or mass DM."
    elif t.contact_priority == "LOW":
        t.outreach_next_action = "Review website manually and search for contact page/form before outreach."
    else:
        t.outreach_next_action = "No direct outreach. Let SEO + claim CTA + public feed work."

    return t


def publish(t: TokenCandidate) -> Tuple[bool,str]:
    if not WP_USERNAME or not WP_APP_PASSWORD: return False, "Missing WordPress credentials"
    payload = {
        "name":t.name,"symbol":t.symbol,"chain":t.chain,"contract_address":t.contract_address,"pair_address":t.pair_address,"dex":t.dex,
        "website":t.website,"twitter":t.twitter,"telegram":t.telegram,"email":t.email,"discord":t.discord,"github":t.github,
        "logo_url":t.logo_url,"banner_url":t.banner_url,"dexscreener_url":t.dexscreener_url,"source_url":t.source_url,"source":t.source,
        "liquidity_usd":round(t.liquidity_usd,2),"volume_h1":round(t.volume_h1,2),"age_minutes":round(t.age_minutes,2) if t.age_minutes is not None else "",
        "quality_score":t.quality_score,"risk_score":t.risk_score,"contact_priority":t.contact_priority,"contact_reason":t.contact_reason,
        "outreach_route":t.outreach_route,"analysis_notes":t.analysis_notes,"short_description":t.short_description,"promoted":False,
        "lead_score":t.lead_score,
        "trending_score":t.trending_score,
        "visibility_score":t.visibility_score,
        "branding_score":t.branding_score,
        "growth_score":t.growth_score,
        "claim_status":t.claim_status,
        "outreach_status":t.outreach_status,
        "outreach_subject":t.outreach_subject,
        "outreach_email":t.outreach_email,
        "outreach_x":t.outreach_x,
        "outreach_telegram":t.outreach_telegram,
        "outreach_next_action":t.outreach_next_action,
        "first_seen":datetime.now(timezone.utc).isoformat(),
        "last_seen":datetime.now(timezone.utc).isoformat(),
        "indexed_keywords":t.indexed_keywords,
        "auto_tags":t.auto_tags,
        "project_category":t.project_category,
        "ai_visibility_score":t.ai_visibility_score,
        "contact_form_url":t.contact_form_url,
        "docs_url":t.docs_url,
        "linktree_url":t.linktree_url,
        "medium_url":t.medium_url,
        "reddit_url":t.reddit_url,
        "discovered_emails":t.discovered_emails,
        "enrichment_pages_scanned":t.enrichment_pages_scanned,
        "enrichment_sources":t.enrichment_sources,
        "contact_reachability_score":t.contact_reachability_score,
        "best_contact_route":t.best_contact_route,
    }
    res = http_post_json(WP_API_URL, payload, auth=(WP_USERNAME, WP_APP_PASSWORD))
    if not res:
        return False, "WordPress empty response"
    if res.get("ok"):
        if res.get("duplicate"):
            return True, "duplicate_updated: " + clean_text(res.get("url"))
        return True, "created: " + clean_text(res.get("url"))
    return False, "WordPress error: " + json.dumps(res)[:700]
def fmt_money(v): return f"${v:,.0f}" if v else "N/A"
def telegram_message(t: TokenCandidate, wp_status: str) -> str:
    age = f"{t.age_minutes:.1f} min" if t.age_minutes is not None else "N/A"
    contacts = []
    if t.website: contacts.append(f"🌐 <b>Website:</b> {t.website}")
    if t.twitter: contacts.append(f"🐦 <b>X:</b> {t.twitter}")
    if t.telegram: contacts.append(f"💬 <b>Telegram:</b> {t.telegram}")
    if t.email: contacts.append(f"✉️ <b>Email:</b> {t.email}")
    if t.discord: contacts.append(f"🟣 <b>Discord:</b> {t.discord}")
    if t.contact_form_url: contacts.append(f"📝 <b>Contact Form:</b> {t.contact_form_url}")
    if t.docs_url: contacts.append(f"📚 <b>Docs:</b> {t.docs_url}")
    if t.linktree_url: contacts.append(f"🔗 <b>Link Hub:</b> {t.linktree_url}")
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
        t = compute_business_scores(t)
        t = build_outreach_templates(t)
        ok, reason = passes(t)
        if not ok:
            print(f"[SKIP] {t.chain} {t.symbol} {t.contract_address} :: {reason}")
            skipped += 1
            continue
        seen_fingerprints.add(fp)
        wp_ok, wp_status = publish(t)
        if wp_ok:
            if wp_status.startswith("duplicate_updated:"):
                print(f"[DUPLICATE UPDATED - NO TELEGRAM] {t.chain} {t.symbol} :: {wp_status}")
                skipped += 1
                time.sleep(0.5)
                continue
            published += 1
            print(f"[PUBLISHED] {t.chain} {t.symbol} :: {wp_status}")
        else:
            print(f"[WP FAILED] {t.chain} {t.symbol} :: {wp_status}")
        send_telegram(telegram_message(t, wp_status))
        time.sleep(1)
    print(f"[SUMMARY] published={published} skipped={skipped}")
def run_once():
    print("============================================================")
    print("PrimePulse / Cryptalysts Discovery Engine v0.9 Real Contact Discovery")
    print(f"UTC: {datetime.now(timezone.utc).isoformat()}")
    print(f"Min liquidity: ${MIN_LIQUIDITY_USD:,.0f}")
    print(f"Max age: {MAX_AGE_MINUTES} minutes")
    print(f"Min quality: {MIN_QUALITY_SCORE}/100")
    print("Sources: DexScreener + GeckoTerminal + Real Contact Discovery v1 + CRM scoring")
    print("============================================================")
    process(collect())
def main():
    if RUN_ONCE: run_once(); return
    send_telegram("✅ <b>PrimePulse Discovery Engine v0.9 ACTIVE</b>")
    while True:
        try: run_once()
        except Exception as e: print(f"[LOOP ERROR] {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)
if __name__ == "__main__":
    main()
