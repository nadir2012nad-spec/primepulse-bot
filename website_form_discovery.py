import os
import re
import requests
from html import unescape
from urllib.parse import urljoin, urlparse

WP_BASE = os.getenv("WP_BASE", "https://cryptalysts.com").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
MAX_FORM_DISCOVERY = int(os.getenv("MAX_FORM_DISCOVERY", "30"))
UA = "Mozilla/5.0 CryptalystsFormDiscovery/1.3"

CONTACT_PATHS = ["", "/contact", "/contact-us", "/contacts", "/support", "/help", "/about", "/team", "/links"]

FORM_PLATFORMS = [
    "typeform.com", "tally.so", "forms.gle", "docs.google.com/forms",
    "airtable.com", "formspree.io", "hubspot", "hsforms", "jotform.com",
    "notion.site", "paperform.co"
]

BLOCKED_DOMAINS = [
    "x.com", "twitter.com", "t.me", "telegram.me", "discord.gg", "discord.com",
    "dexscreener.com", "dextools.io", "pump.fun", "birdeye.so",
    "solscan.io", "etherscan.io", "bscscan.com", "basescan.org", "polygonscan.com",
    "geckoterminal.com", "coingecko.com", "coinmarketcap.com",
    "medium.com", "youtube.com", "youtu.be", "instagram.com", "facebook.com",
    "reddit.com", "github.com", "linktr.ee", "beacons.ai"
]

def norm(u):
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if not u.startswith(("http://", "https://")):
        return "https://" + u
    return u

def domain_of(u):
    try:
        return urlparse(norm(u)).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def is_blocked_url(u):
    d = domain_of(u)
    if not d:
        return True
    return any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)

def valid_project_site(u):
    u = norm(u)
    try:
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            return False
        if not p.netloc or "." not in p.netloc:
            return False
        if is_blocked_url(u):
            return False
        return True
    except Exception:
        return False

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[TELEGRAM SKIPPED] Missing BOT_TOKEN or CHAT_ID")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        print("[TELEGRAM]", r.status_code, r.text[:300])
    except Exception as e:
        print("[TELEGRAM ERROR]", repr(e))

def notify_form_found(item, form_url, form_type):
    title = item.get("title", "Unknown Token")
    listing_url = item.get("listing_url", "")
    website = item.get("website", "")
    msg = f"""🧾 <b>WEBSITE CONTACT FORM FOUND</b>

<b>Token:</b> {title}
<b>Type:</b> {form_type}

🌐 <b>Website:</b>
{website}

📝 <b>Form:</b>
{form_url}

🔗 <b>Listing:</b>
{listing_url}

<b>Action:</b>
Open Website Form Queue → Copy Message → Submit → Mark Submitted.
"""
    send_telegram(msg)

def wp_targets():
    r = requests.get(
        f"{WP_BASE}/wp-json/cryptalysts/v1/form-discovery-targets",
        params={"limit": MAX_FORM_DISCOVERY},
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=30,
    )
    print("[WP TARGETS]", r.status_code, r.text[:600])
    r.raise_for_status()
    return r.json().get("items", [])

def wp_update(post_id, form_url="", form_type="", status="", notes=""):
    r = requests.post(
        f"{WP_BASE}/wp-json/cryptalysts/v1/form-discovery-update",
        json={"post_id": post_id, "form_url": form_url, "form_type": form_type, "status": status, "notes": notes[:600]},
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    print("[WP UPDATE]", post_id, r.status_code, r.text[:400])
    return r.ok

def get_html(u):
    if not valid_project_site(u):
        return "", u
    try:
        r = requests.get(
            u, timeout=15, allow_redirects=True,
            headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"},
        )
        ct = r.headers.get("content-type", "").lower()
        final = r.url
        if is_blocked_url(final):
            return "", final
        if not r.ok or ("text/html" not in ct and "application/xhtml" not in ct):
            return "", final
        return r.text[:500000], final
    except Exception as e:
        print("[HTML ERROR]", u, repr(e))
        return "", u

def links(html, base):
    out = []
    hrefs = re.findall(r\"href=[\\\"']([^\\\"']+)[\\\"']\", html, re.I)
    srcs = re.findall(r\"src=[\\\"']([^\\\"']+)[\\\"']\", html, re.I)
    for raw in hrefs + srcs:
        u = unescape(raw.strip())
        if not u or u.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        full = norm(urljoin(base, u))
        if is_blocked_url(full):
            continue
        out.append(full)
    return list(dict.fromkeys(out))

def detect_form(html, page):
    low = html.lower()
    if re.search(r"<form\\b", html, re.I) and any(x in low for x in ["contact", "message", "email", "name", "submit"]):
        return page, "HTML_FORM"
    for u in links(html, page):
        lu = u.lower()
        if any(p in lu for p in FORM_PLATFORMS):
            return u, "FORM_PLATFORM"
    return "", ""

def candidate_pages(site):
    site = norm(site)
    if not valid_project_site(site):
        return []
    p = urlparse(site)
    root = f"{p.scheme}://{p.netloc}"
    return list(dict.fromkeys([site] + [root + x for x in CONTACT_PATHS]))

def mark_found(item, form_url, form_type, notes):
    post_id = item["post_id"]
    ok = wp_update(post_id, form_url=form_url, form_type=form_type, status="FOUND", notes=notes)
    if ok:
        notify_form_found(item, form_url, form_type)
    return ok

def discover(item):
    post_id = item["post_id"]
    website = item.get("website", "")
    if not valid_project_site(website):
        print(f"[SKIP INVALID WEBSITE] {item.get('title')} :: {website}")
        return wp_update(post_id, status="NO_FORM", notes="Skipped: website is social/dex/explorer or invalid project domain")

    found = []
    for page in candidate_pages(website):
        html, final = get_html(page)
        if not html:
            continue
        form, typ = detect_form(html, final)
        if form and not is_blocked_url(form):
            return mark_found(item, form, typ, f"Form found on {final}")
        for u in links(html, final):
            lu = u.lower()
            if any(x in lu for x in ["/contact", "/contact-us", "/support", "/help"]) and valid_project_site(u):
                found.append(u)

    for u in list(dict.fromkeys(found))[:6]:
        html, final = get_html(u)
        if not html:
            continue
        form, typ = detect_form(html, final)
        if form and not is_blocked_url(form):
            return mark_found(item, form, typ, f"Form found on linked page {final}")

    return wp_update(post_id, status="NO_FORM", notes="No contact form found after scan")

def main():
    if not WP_USERNAME or not WP_APP_PASSWORD:
        raise SystemExit("Missing WP credentials")
    items = wp_targets()
    if not items:
        print("No websites to scan for forms.")
        return
    updates = 0
    for it in items:
        print("[SCAN]", it.get("title"), it.get("website"))
        if discover(it):
            updates += 1
    print(f"Done. Scanned {len(items)}. Updates {updates}.")

if __name__ == "__main__":
    main()
