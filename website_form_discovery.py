import os
import re
import requests
from html import unescape
from urllib.parse import urljoin, urlparse

WP_BASE=os.getenv("WP_BASE","https://cryptalysts.com").rstrip("/")
WP_USERNAME=os.getenv("WP_USERNAME","")
WP_APP_PASSWORD=os.getenv("WP_APP_PASSWORD","")
BOT_TOKEN=os.getenv("BOT_TOKEN","")
CHAT_ID=os.getenv("CHAT_ID","")
MAX_FORM_DISCOVERY=int(os.getenv("MAX_FORM_DISCOVERY","30"))
UA="Mozilla/5.0 CryptalystsFormDiscovery/2.0-STRICT"

CONTACT_PATHS=["/contact","/contact-us","/contacts","/support","/help","/business","/partnership","/partners","/inquiry"]
CONTACT_HINTS=["contact","contact-us","support","help","business","partnership","partner","inquiry","enquiry","request"]

BLOCKED_DOMAINS=["x.com","twitter.com","t.me","telegram.me","discord.gg","discord.com","dexscreener.com","dextools.io","pump.fun","birdeye.so","solscan.io","etherscan.io","bscscan.com","basescan.org","polygonscan.com","geckoterminal.com","coingecko.com","coinmarketcap.com","medium.com","youtube.com","youtu.be","instagram.com","facebook.com","reddit.com","github.com","linktr.ee","beacons.ai"]
BAD_HINTS=["newsletter","subscribe","signup","sign-up","sign_up","waitlist","updates","notify","early-access","early_access","mailchimp","klaviyo","privy","omnisend"]
FORM_PLATFORMS=["typeform.com","tally.so","docs.google.com/forms","airtable.com","formspree.io","hubspot","hsforms","jotform.com","paperform.co"]

def norm(u):
    u=(u or "").strip()
    if not u:return ""
    if u.startswith("//"):return "https:"+u
    if not u.startswith(("http://","https://")):return "https://"+u
    return u

def domain_of(u):
    try:return urlparse(norm(u)).netloc.lower().replace("www.","")
    except Exception:return ""

def is_blocked(u):
    d=domain_of(u)
    if not d:return True
    return any(d==b or d.endswith("."+b) for b in BLOCKED_DOMAINS)

def is_project_site(u):
    u=norm(u)
    try:
        p=urlparse(u)
        return p.scheme in ("http","https") and bool(p.netloc) and "." in p.netloc and not is_blocked(u)
    except Exception:return False

def is_contact_url(u):
    low=(u or "").lower()
    return any(x in low for x in CONTACT_HINTS)

def is_bad_url_or_html(u,html=""):
    low=((u or "")+" "+(html or "")[:20000]).lower()
    return any(x in low for x in BAD_HINTS)

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[TG SKIP] missing BOT_TOKEN or CHAT_ID");return
    try:
        r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":True},timeout=20)
        print("[TG]",r.status_code,r.text[:250])
    except Exception as e:print("[TG ERROR]",repr(e))

def notify(item,form,typ):
    msg=f"""🧾 <b>STRICT WEBSITE CONTACT FORM FOUND</b>

<b>Token:</b> {item.get('title','Unknown')}
<b>Type:</b> {typ}

🌐 <b>Website:</b>
{item.get('website','')}

📝 <b>Form:</b>
{form}

🔗 <b>Listing:</b>
{item.get('listing_url','')}

<b>Action:</b>
Website Form Queue → Copy Message → Submit → Mark Submitted.
"""
    send_tg(msg)

def wp_targets():
    r=requests.get(f"{WP_BASE}/wp-json/cryptalysts/v1/form-discovery-targets",params={"limit":MAX_FORM_DISCOVERY},auth=(WP_USERNAME,WP_APP_PASSWORD),headers={"User-Agent":UA,"Accept":"application/json"},timeout=30)
    print("[WP TARGETS]",r.status_code,r.text[:600]);r.raise_for_status();return r.json().get("items",[])

def wp_update(post_id,form_url="",form_type="",status="",notes=""):
    r=requests.post(f"{WP_BASE}/wp-json/cryptalysts/v1/form-discovery-update",json={"post_id":post_id,"form_url":form_url,"form_type":form_type,"status":status,"notes":notes[:600]},auth=(WP_USERNAME,WP_APP_PASSWORD),headers={"User-Agent":UA,"Accept":"application/json","Content-Type":"application/json"},timeout=30)
    print("[WP UPDATE]",post_id,r.status_code,r.text[:400]);return r.ok

def get_html(u):
    if not is_project_site(u):return "",u
    try:
        r=requests.get(u,timeout=15,allow_redirects=True,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,*/*"})
        final=r.url;ct=r.headers.get("content-type","").lower()
        if is_blocked(final):return "",final
        if not r.ok or ("text/html" not in ct and "application/xhtml" not in ct):return "",final
        return r.text[:500000],final
    except Exception as e:
        print("[HTML ERROR]",u,repr(e));return "",u

def extract_links(html,base):
    out=[]
    for pattern in [r'href="([^"]+)"',r"href='([^']+)'"]:
        for raw in re.findall(pattern,html,re.I):
            u=unescape(raw.strip())
            if not u or u.startswith(("javascript:","#","mailto:","tel:")):continue
            full=norm(urljoin(base,u))
            if not is_blocked(full):out.append(full)
    return list(dict.fromkeys(out))

def page_has_contact_form(html,url):
    if not is_contact_url(url):return False
    if is_bad_url_or_html(url,html):return False
    low=html.lower()
    has_form=bool(re.search(r"<form\\b",html,re.I))
    has_contact_words=any(x in low for x in ["contact us","get in touch","send message","send us a message","business inquiries","partnership","support request","your message"])
    has_message_field=any(x in low for x in ["textarea","message","your message"])
    return has_form and has_contact_words and has_message_field

def find_external_contact_form(html,page):
    if not is_contact_url(page) or is_bad_url_or_html(page,html):return "",""
    for u in extract_links(html,page):
        lu=u.lower()
        if is_bad_url_or_html(lu):continue
        if any(p in lu for p in FORM_PLATFORMS) and is_contact_url(lu):
            return u,"STRICT_CONTACT_FORM_PLATFORM"
    return "",""

def candidates(site):
    site=norm(site)
    if not is_project_site(site):return []
    p=urlparse(site);root=f"{p.scheme}://{p.netloc}"
    return [root+x for x in CONTACT_PATHS]

def discover(item):
    post_id=item["post_id"];site=item.get("website","")
    if not is_project_site(site):
        print("[SKIP INVALID WEBSITE]",item.get("title"),site)
        return wp_update(post_id,status="NO_VALID_CONTACT_FORM",notes="Skipped social/dex/explorer/invalid site")

    pages=candidates(site)
    linked=[]
    for page in pages:
        html,final=get_html(page)
        if not html:continue
        if page_has_contact_form(html,final):
            ok=wp_update(post_id,form_url=final,form_type="STRICT_HTML_CONTACT_FORM",status="FOUND",notes=f"Strict contact form found on {final}")
            if ok:notify(item,final,"STRICT_HTML_CONTACT_FORM")
            return ok
        ext,typ=find_external_contact_form(html,final)
        if ext:
            ok=wp_update(post_id,form_url=ext,form_type=typ,status="FOUND",notes=f"Strict external contact form found on {final}")
            if ok:notify(item,ext,typ)
            return ok
        for u in extract_links(html,final):
            if is_contact_url(u) and is_project_site(u) and not is_bad_url_or_html(u):linked.append(u)

    for page in list(dict.fromkeys(linked))[:8]:
        html,final=get_html(page)
        if not html:continue
        if page_has_contact_form(html,final):
            ok=wp_update(post_id,form_url=final,form_type="STRICT_HTML_CONTACT_FORM",status="FOUND",notes=f"Strict contact form found on linked page {final}")
            if ok:notify(item,final,"STRICT_HTML_CONTACT_FORM")
            return ok
        ext,typ=find_external_contact_form(html,final)
        if ext:
            ok=wp_update(post_id,form_url=ext,form_type=typ,status="FOUND",notes=f"Strict external contact form found on linked page {final}")
            if ok:notify(item,ext,typ)
            return ok

    return wp_update(post_id,status="NO_VALID_CONTACT_FORM",notes="No strict contact/support/business form found")

def main():
    if not WP_USERNAME or not WP_APP_PASSWORD:raise SystemExit("Missing WP credentials")
    items=wp_targets()
    if not items:
        print("No websites to scan for forms.");return
    n=0
    for it in items:
        print("[SCAN]",it.get("title"),it.get("website"))
        if discover(it):n+=1
    print(f"Done. Scanned {len(items)}. Updates {n}.")

if __name__=="__main__":
    main()
