import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.utils import formataddr

WP_BASE=os.getenv("WP_BASE","https://cryptalysts.com").rstrip("/")
WP_USERNAME=os.getenv("WP_USERNAME","")
WP_APP_PASSWORD=os.getenv("WP_APP_PASSWORD","")
SMTP_HOST=os.getenv("SMTP_HOST","")
SMTP_PORT=int(os.getenv("SMTP_PORT","587"))
SMTP_USERNAME=os.getenv("SMTP_USERNAME","")
SMTP_PASSWORD=os.getenv("SMTP_PASSWORD","")
SMTP_FROM_EMAIL=os.getenv("SMTP_FROM_EMAIL",SMTP_USERNAME)
SMTP_FROM_NAME=os.getenv("SMTP_FROM_NAME","Cryptalysts")
SMTP_USE_TLS=os.getenv("SMTP_USE_TLS","true").lower() in ("1","true","yes","y")
MAX_EMAIL_SENDS=int(os.getenv("MAX_EMAIL_SENDS","10"))
UA="Mozilla/5.0 CryptalystsEmailOutreach/1.1"

def require_env():
    missing=[k for k,v in {
        "WP_USERNAME":WP_USERNAME,
        "WP_APP_PASSWORD":WP_APP_PASSWORD,
        "SMTP_HOST":SMTP_HOST,
        "SMTP_USERNAME":SMTP_USERNAME,
        "SMTP_PASSWORD":SMTP_PASSWORD,
        "SMTP_FROM_EMAIL":SMTP_FROM_EMAIL
    }.items() if not v]
    if missing:
        raise SystemExit("Missing env/secrets: "+", ".join(missing))

def wp_get_leads():
    r=requests.get(
        f"{WP_BASE}/wp-json/cryptalysts/v1/mail-queue",
        params={"limit":MAX_EMAIL_SENDS},
        auth=(WP_USERNAME,WP_APP_PASSWORD),
        headers={"User-Agent":UA,"Accept":"application/json","Cache-Control":"no-cache"},
        timeout=30
    )
    print("[WP GET]",r.status_code,r.text[:900])
    r.raise_for_status()
    return r.json().get("items",[])

def wp_update(post_id,status,sent_to="",error=""):
    r=requests.post(
        f"{WP_BASE}/wp-json/cryptalysts/v1/mail-update",
        json={"post_id":post_id,"status":status,"sent_to":sent_to,"error":error[:900]},
        auth=(WP_USERNAME,WP_APP_PASSWORD),
        headers={"User-Agent":UA,"Accept":"application/json","Content-Type":"application/json","Cache-Control":"no-cache"},
        timeout=30
    )
    print("[WP UPDATE]",post_id,status,r.status_code,r.text[:500])
    return r.ok

def send_email(to_email,subject,body):
    msg=MIMEText(body,"plain","utf-8")
    msg["Subject"]=subject
    msg["From"]=formataddr((SMTP_FROM_NAME,SMTP_FROM_EMAIL))
    msg["To"]=to_email
    msg["Reply-To"]=SMTP_FROM_EMAIL
    with smtplib.SMTP(SMTP_HOST,SMTP_PORT,timeout=30) as server:
        if SMTP_USE_TLS:
            server.starttls()
        server.login(SMTP_USERNAME,SMTP_PASSWORD)
        server.sendmail(
    SMTP_FROM_EMAIL,
    [to_email, SMTP_FROM_EMAIL],
    msg.as_string())

def main():
    require_env()
    leads=wp_get_leads()
    if not leads:
        print("No email leads to contact.")
        return

    sent=0
    failed=0

    for lead in leads:
        post_id=lead["post_id"]
        to_email=lead["email"]
        subject=lead["subject"]
        body=lead["body"]
        title=lead.get("title","")

        print(f"[EMAIL TRY] {title} -> {to_email}")

        try:
            send_email(to_email,subject,body)
            wp_update(post_id,"EMAIL_SENT",sent_to=to_email)
            sent+=1
            print(f"[EMAIL SENT] {title} -> {to_email}")
        except Exception as e:
            err=f"{type(e).__name__}: {str(e)}"
            print("[EMAIL ERROR]",err)
            wp_update(post_id,"EMAIL_FAILED",sent_to=to_email,error=err)
            failed+=1

    print(f"Done. Sent {sent}. Failed {failed}.")

if __name__=="__main__":
    main()
