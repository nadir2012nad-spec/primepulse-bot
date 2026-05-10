import os, random, requests, textwrap, tempfile, io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WP_BASE=os.getenv("WP_BASE","https://cryptalysts.com").rstrip("/")
WP_USERNAME=os.getenv("WP_USERNAME","")
WP_APP_PASSWORD=os.getenv("WP_APP_PASSWORD","")
BOT_TOKEN=os.getenv("BOT_TOKEN","")
PUBLIC_CHANNEL_ID=os.getenv("PUBLIC_CHANNEL_ID","")
MAX_PUBLIC_FEED_POSTS=int(os.getenv("MAX_PUBLIC_FEED_POSTS","4"))
MIN_SIGNAL_SCORE=int(os.getenv("MIN_SIGNAL_SCORE","45"))
PROMO_EVERY_RUN=os.getenv("PROMO_EVERY_RUN","true").lower()=="true"

BRAND="CRYPTALYSTS LIVE SIGNAL"
PRIMEPULSE="Visibility tools powered by PrimePulseOps.com"

def require_env():
    missing=[k for k,v in {"WP_USERNAME":WP_USERNAME,"WP_APP_PASSWORD":WP_APP_PASSWORD,"BOT_TOKEN":BOT_TOKEN,"PUBLIC_CHANNEL_ID":PUBLIC_CHANNEL_ID}.items() if not v]
    if missing:
        raise SystemExit("Missing env/secrets: "+", ".join(missing))

def esc(s):
    return str(s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def smart_hashtags(chain,category):
    tags=["#Crypto","#NewToken","#EarlySignal","#Cryptalysts"]
    c=str(chain or "").lower(); cat=str(category or "").lower()
    for k,v in {"solana":"#Solana","base":"#Base","ethereum":"#Ethereum","eth":"#Ethereum","polygon":"#Polygon","bsc":"#BSC","binance":"#BSC","arbitrum":"#Arbitrum"}.items():
        if k in c:
            tags.insert(1,v); break
    if "meme" in cat: tags.append("#MemeCoin")
    if "ai" in cat: tags.append("#AI")
    if "defi" in cat: tags.append("#DeFi")
    if "gaming" in cat: tags.append("#Gaming")
    return " ".join(dict.fromkeys(tags))

def wp_get_items():
    fetch_limit=max(MAX_PUBLIC_FEED_POSTS*5,20)
    r=requests.get(f"{WP_BASE}/wp-json/cryptalysts/v1/public-feed-items",params={"limit":fetch_limit},auth=(WP_USERNAME,WP_APP_PASSWORD),timeout=30)
    print("[WP GET]",r.status_code,r.text[:900]); r.raise_for_status()
    raw_items=r.json().get("items",[])
    filtered=[]
    for item in raw_items:
        score=int(item.get("signal_score") or item.get("ai_visibility_score") or item.get("visibility_score") or 0)
        if score < MIN_SIGNAL_SCORE:
            print("[FILTERED LOW SCORE]", item.get("title"), score); continue
        has_social_fields=any(k in item for k in ("telegram","website","x","twitter","discord"))
        if has_social_fields:
            socials=str(item.get("telegram","")).strip() or str(item.get("website","")).strip() or str(item.get("x","")).strip() or str(item.get("twitter","")).strip() or str(item.get("discord","")).strip()
            if not socials:
                print("[FILTERED NO SOCIAL]", item.get("title")); continue
        filtered.append(item)
        if len(filtered)>=MAX_PUBLIC_FEED_POSTS: break
    return filtered

def wp_mark(post_id):
    r=requests.post(f"{WP_BASE}/wp-json/cryptalysts/v1/public-feed-mark",json={"post_id":post_id},auth=(WP_USERNAME,WP_APP_PASSWORD),timeout=30)
    print("[WP MARK]",post_id,r.status_code,r.text[:300])

def get_font(size,bold=False):
    p="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(p,size) if Path(p).exists() else ImageFont.load_default()

def download_logo(url):
    if not url: return None
    try:
        r=requests.get(url,timeout=12,headers={"User-Agent":"CryptalystsPublicFeed/4.0"}); r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception as e:
        print("[LOGO ERROR]",repr(e)); return None

def signal_status(score):
    if score >= 85: return "TOP TRENDING SIGNAL", "#ff3b30"
    if score >= 70: return "TRENDING SIGNAL", "#f97316"
    if score >= 55: return "SIGNAL BUILDING", "#8cff00"
    return "EARLY SIGNAL", "#facc15"

def make_card(item):
    W,H=1200,675
    bg=Image.new("RGB",(W,H),"#05070b"); draw=ImageDraw.Draw(bg)
    draw.rounded_rectangle((36,36,1164,639),radius=34,fill="#080b12",outline="#1f2937",width=3)
    draw.ellipse((-260,-300,520,480),fill="#10220a")
    draw.rounded_rectangle((54,54,1146,621),radius=28,outline="#8cff00",width=2)
    f_brand=get_font(24,True); f_title=get_font(52,True); f_label=get_font(22,True); f_value=get_font(28,True); f_small=get_font(22,False); f_score=get_font(72,True)
    draw.text((86,82),BRAND,font=f_brand,fill="#8cff00")
    title=str(item.get("title") or "New Token"); symbol=str(item.get("symbol") or "")
    name=f"{title} ({symbol})" if symbol else title
    y=132
    for line in textwrap.wrap(name,width=26)[:2]:
        draw.text((86,y),line,font=f_title,fill="white"); y+=58
    chain=str(item.get("chain") or "Unknown"); category=str(item.get("category") or "Early Token")
    score=int(item.get("signal_score") or item.get("ai_visibility_score") or 0)
    badge_text,badge_color=signal_status(score)
    draw.rounded_rectangle((86,285,760,475),radius=22,fill="#0d111b",outline="#263244",width=2)
    yy=315
    for label,value in [("CHAIN",chain),("CATEGORY",category),("FEATURED","2 SLOTS LEFT")]:
        draw.text((116,yy),label,font=f_label,fill="#8cff00"); draw.text((330,yy),value,font=f_value,fill="white"); yy+=48
    draw.rounded_rectangle((800,285,1088,475),radius=24,fill="#101827",outline=badge_color,width=3)
    draw.text((838,315),"LIVE SIGNAL",font=f_label,fill="#cbd5e1")
    draw.text((838,350),f"{score}/100",font=f_score,fill="#8cff00")
    draw.text((838,430),badge_text,font=f_label,fill=badge_color)
    logo=download_logo(item.get("logo_url"))
    if logo:
        logo.thumbnail((150,150)); mask=Image.new("L",logo.size,0); md=ImageDraw.Draw(mask); md.rounded_rectangle((0,0,logo.size[0],logo.size[1]),radius=22,fill=255); bg.paste(logo,(935,108),mask)
    else:
        draw.rounded_rectangle((935,108,1085,258),radius=24,fill="#101827",outline="#263244",width=2); draw.text((970,158),"CLX",font=get_font(40,True),fill="#8cff00")
    draw.text((86,540),"Indexed on Cryptalysts • Claim your listing for free",font=f_value,fill="white")
    draw.text((86,585),PRIMEPULSE,font=f_small,fill="#8cff00")
    out=tempfile.NamedTemporaryFile(delete=False,suffix=".png"); bg.save(out.name,"PNG"); return out.name

def token_caption(item):
    title=esc(item.get("title","New Token")); symbol=esc(item.get("symbol","")); chain=esc(item.get("chain","Unknown")); category=esc(item.get("category","Early Token"))
    score=int(item.get("signal_score") or item.get("ai_visibility_score") or 0)
    listing=item.get("listing_url",""); claim=item.get("claim_url","")
    name_line=f"{title} ({symbol})" if symbol else title
    signal_line,_=signal_status(score)
    return f'''🚨 <b>NEW TOKEN DETECTED</b>

<b>{name_line}</b>

• Chain: <b>{chain}</b>
• Category: <b>{category}</b>
• LIVE SIGNAL SCORE: <b>{score}/100</b>
• Status: <b>{signal_line}</b>
• FEATURED: <b>2 SLOTS LEFT</b>

🔗 <b>Live listing:</b>
{listing}

⚡ <b>Project owner?</b>
Claim your listing for free:
{claim}

<i>{PRIMEPULSE}</i>

{smart_hashtags(chain,category)}'''

def tg_send_photo(image_path,caption):
    with open(image_path,"rb") as f:
        r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",data={"chat_id":PUBLIC_CHANNEL_ID,"caption":caption,"parse_mode":"HTML"},files={"photo":f},timeout=60)
    print("[TG PHOTO]",r.status_code,r.text[:500]); r.raise_for_status()

def tg_send(text):
    r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":PUBLIC_CHANNEL_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":False},timeout=30)
    print("[TG SEND]",r.status_code,r.text[:500]); r.raise_for_status()

def promo_post():
    return random.choice([
'''⚡ <b>Visibility starts before momentum.</b>

Cryptalysts indexes early-stage crypto projects before the crowd notices.

• Live token discovery
• Ranking signals
• Claim tools
• Featured placements
• PrimePulseOps.com visibility layer

🔗 https://cryptalysts.com

#Crypto #TokenDiscovery #EarlySignal''',
'''🚀 <b>Only 2 featured slots left.</b>

Early projects can push visibility with:
• homepage placement
• ranking boost
• Telegram feed exposure
• public listing authority
• PrimePulseOps.com campaign access

🔗 https://cryptalysts.com/feature-your-token/

#PrimePulseOps #CryptoVisibility #TokenLaunch''',
'''📡 <b>Builders need signal before the chart moves.</b>

Cryptalysts gives new tokens a public listing, claim path and visibility layer.

Claim. Build signal. Get seen.

🔗 https://cryptalysts.com

Powered by PrimePulseOps.com

#Web3 #NewTokens #CryptoMarketing''',
'''🔥 <b>Top signal projects get seen first.</b>

Cryptalysts turns early listings into discoverable visibility assets with public signal, claim tools and featured exposure.

🔗 https://cryptalysts.com/feature-your-token/

#CryptoGrowth #TokenVisibility #PrimePulseOps'''])

def main():
    require_env(); sent=0
    items=wp_get_items()
    if not items: print("No eligible public feed items after quality filter.")
    for item in items:
        try:
            card=make_card(item); tg_send_photo(card,token_caption(item)); wp_mark(int(item["post_id"])); sent+=1
        except Exception as e: print("[TOKEN POST ERROR]",item.get("post_id"),repr(e))
    if PROMO_EVERY_RUN:
        try: tg_send(promo_post()); sent+=1
        except Exception as e: print("[PROMO ERROR]",repr(e))
    print(f"Done. Public feed messages sent: {sent}")

if __name__=="__main__": main()
