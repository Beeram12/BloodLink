import os
import json
import logging
import threading
from datetime import datetime, timezone
from urllib.parse import quote as _quote

import boto3

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level":     record.levelname,
            "module":    record.module,
            "message":   record.getMessage(),
        }
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log)

def _get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger

logger = _get_logger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TWILIO_ACCOUNT_SID   = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN    = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_WHATSAPP = os.environ.get("TWILIO_FROM_WHATSAPP", "whatsapp:+14155238886")
API_BASE_URL         = os.environ.get("API_BASE_URL", "").rstrip("/")
FRONTEND_URL         = os.environ.get("FRONTEND_URL", API_BASE_URL).rstrip("/")
DEMO_DONOR_PHONE     = os.environ.get("DEMO_DONOR_PHONE", "")
DEMO_DONOR_EMAIL     = os.environ.get("DEMO_DONOR_EMAIL", "")
SES_FROM_EMAIL       = os.environ.get("SES_FROM_EMAIL", "pranithreddy16.beeram@gmail.com")
SES_REGION           = os.environ.get("SES_REGION", "eu-north-1")

_twilio_client = None

def _get_twilio():
    global _twilio_client
    if _twilio_client is None:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            raise EnvironmentError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set")
        from twilio.rest import Client
        logger.info("Initialising Twilio client")
        _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _twilio_client

# ---------------------------------------------------------------------------
# WhatsApp (Twilio)
# ---------------------------------------------------------------------------

def send_whatsapp(to_number: str, message: str) -> dict:
    formatted = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
    logger.info(f"Sending WhatsApp to {formatted[:15]}...")
    try:
        msg = _get_twilio().messages.create(
            from_=TWILIO_FROM_WHATSAPP,
            to=formatted,
            body=message,
        )
        logger.info(f"WhatsApp sent SID={msg.sid}")
        return {"success": True, "sid": msg.sid, "channel": "whatsapp"}
    except Exception as exc:
        logger.warning(f"WhatsApp failed to {formatted[:15]}...: {exc}")
        return {"success": False, "error": str(exc), "channel": "whatsapp"}

# ---------------------------------------------------------------------------
# Email (AWS SES)
# ---------------------------------------------------------------------------

def send_email(to_email: str, subject: str, body_text: str, body_html: str = "") -> dict:
    logger.info(f"Sending SES email to {to_email[:20]}...")
    try:
        ses = boto3.client("sesv2", region_name=SES_REGION)
        content = {
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": body_text, "Charset": "UTF-8"},
                },
            }
        }
        if body_html:
            content["Simple"]["Body"]["Html"] = {"Data": body_html, "Charset": "UTF-8"}

        resp = ses.send_email(
            FromEmailAddress=SES_FROM_EMAIL,
            Destination={"ToAddresses": [to_email]},
            Content=content,
        )
        logger.info(f"SES email sent MessageId={resp.get('MessageId')}")
        return {"success": True, "message_id": resp.get("MessageId"), "channel": "email"}
    except Exception as exc:
        logger.warning(f"SES email failed to {to_email[:20]}...: {exc}")
        return {"success": False, "error": str(exc), "channel": "email"}

# ---------------------------------------------------------------------------
# High-level donor outreach — WhatsApp + Email in parallel
# ---------------------------------------------------------------------------

def send_donor_outreach(donor: dict, request_id: str, message: str) -> dict:
    donor_id   = donor.get("donor_id", "")
    donor_name = donor.get("name", "Donor")
    phone      = donor.get("phone_number", "") or donor.get("phone", "") or donor.get("mobile", "")
    email      = donor.get("email", "") or donor.get("email_address", "")

    # Demo fallbacks when dataset has no contact info
    if not phone and DEMO_DONOR_PHONE:
        logger.info(f"No phone for {donor_id} — using DEMO_DONOR_PHONE")
        phone = DEMO_DONOR_PHONE
    if not email and DEMO_DONOR_EMAIL:
        logger.info(f"No email for {donor_id} — using DEMO_DONOR_EMAIL")
        email = DEMO_DONOR_EMAIL

    if not phone and not email:
        logger.warning(f"No contact info for donor {donor_id} ({donor_name}) — skipping")
        return {"success": False, "error": "No contact info", "channel": "none", "donor_id": donor_id}

    safe_donor_id = _quote(donor_id, safe="")
    confirm_url   = f"{FRONTEND_URL}/confirm?request_id={request_id}&donor_id={safe_donor_id}"

    whatsapp_result = [None]
    email_result    = [None]

    # ── Thread 1: WhatsApp ──────────────────────────────────────────────────
    def _whatsapp():
        if not phone:
            return
        full_msg = f"{message}\n\nTap to respond: {confirm_url}"
        whatsapp_result[0] = send_whatsapp(phone, full_msg)

    # ── Thread 2: Email ─────────────────────────────────────────────────────
    def _email():
        if not email:
            return
        blood_group = donor.get("blood_group", "blood")
        subject     = f"Urgent: A patient needs {blood_group} blood — BloodLink"
        text_body   = (
            f"Dear {donor_name},\n\n"
            f"{message}\n\n"
            f"Please tap the link below to confirm or decline:\n{confirm_url}\n\n"
            f"Thank you for being a lifesaver.\n— BloodLink / Blood Warriors"
        )
        html_body = f"""
<div style="font-family:sans-serif;max-width:480px;margin:auto;padding:24px">
  <h2 style="color:#C41E3A;margin-bottom:4px">BloodLink</h2>
  <p style="color:#666;font-size:13px;margin-top:0">Blood Warriors Donor Network</p>
  <hr style="border:none;border-top:1px solid #eee;margin:16px 0"/>
  <p style="font-size:16px;color:#111">Dear <strong>{donor_name}</strong>,</p>
  <p style="font-size:15px;color:#333;line-height:1.6">{message}</p>
  <div style="margin:28px 0;text-align:center">
    <a href="{confirm_url}" style="background:#C41E3A;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-size:15px;font-weight:600">
      Respond Now
    </a>
  </div>
  <p style="font-size:12px;color:#999;text-align:center">
    BloodLink · Blood Warriors · Request {request_id}
  </p>
</div>"""
        email_result[0] = send_email(email, subject, text_body, html_body)

    threads = []
    if phone: threads.append(threading.Thread(target=_whatsapp, daemon=True))
    if email: threads.append(threading.Thread(target=_email,    daemon=True))

    for t in threads: t.start()
    for t in threads: t.join(timeout=8)

    # Return success if either channel worked
    wa  = whatsapp_result[0]
    em  = email_result[0]
    if wa and wa["success"]:
        result = wa
    elif em and em["success"]:
        result = em
    else:
        result = {"success": False, "error": "All channels failed", "channel": "none"}

    result["donor_id"] = donor_id
    result["email"]    = em
    return result
