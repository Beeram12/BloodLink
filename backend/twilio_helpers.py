import os
import json
import logging
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
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
# Twilio config (lazy client init)
# ---------------------------------------------------------------------------

TWILIO_ACCOUNT_SID    = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN     = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_WHATSAPP  = os.environ.get("TWILIO_FROM_WHATSAPP", "whatsapp:+14155238886")
TWILIO_FROM_SMS       = os.environ.get("TWILIO_FROM_SMS", "")
API_BASE_URL          = os.environ.get("API_BASE_URL", "").rstrip("/")

_twilio_client = None

def _get_client():
    global _twilio_client
    if _twilio_client is None:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            raise EnvironmentError(
                "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set"
            )
        from twilio.rest import Client  # lazy import — not available locally
        logger.info("Initialising Twilio client")
        _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _twilio_client

# ---------------------------------------------------------------------------
# Core send functions
# ---------------------------------------------------------------------------

def send_whatsapp(to_number: str, message: str) -> dict:
    """
    Send a WhatsApp message via Twilio sandbox.
    `to_number` must be in E.164 format, e.g. +91XXXXXXXXXX.
    Returns {"success": True, "sid": "...", "channel": "whatsapp"}
          | {"success": False, "error": "...", "channel": "whatsapp"}
    """
    formatted_to = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
    logger.info(f"Sending WhatsApp to {formatted_to[:15]}...")
    try:
        msg = _get_client().messages.create(
            from_=TWILIO_FROM_WHATSAPP,
            to=formatted_to,
            body=message,
        )
        logger.info(f"WhatsApp sent, SID={msg.sid}")
        return {"success": True, "sid": msg.sid, "channel": "whatsapp"}
    except Exception as exc:
        logger.warning(f"WhatsApp send failed to {formatted_to[:15]}...: {exc}")
        return {"success": False, "error": str(exc), "channel": "whatsapp"}


def send_sms(to_number: str, message: str) -> dict:
    """
    Send an SMS via Twilio.
    Returns {"success": True, "sid": "...", "channel": "sms"}
          | {"success": False, "error": "...", "channel": "sms"}
    """
    if not TWILIO_FROM_SMS:
        logger.warning("TWILIO_FROM_SMS not set — skipping SMS fallback")
        return {"success": False, "error": "TWILIO_FROM_SMS not configured", "channel": "sms"}

    logger.info(f"Sending SMS to {to_number[:7]}...")
    try:
        msg = _get_client().messages.create(
            from_=TWILIO_FROM_SMS,
            to=to_number,
            body=message,
        )
        logger.info(f"SMS sent, SID={msg.sid}")
        return {"success": True, "sid": msg.sid, "channel": "sms"}
    except Exception as exc:
        logger.warning(f"SMS send failed to {to_number[:7]}...: {exc}")
        return {"success": False, "error": str(exc), "channel": "sms"}

# ---------------------------------------------------------------------------
# High-level donor outreach
# ---------------------------------------------------------------------------

def send_donor_outreach(donor: dict, request_id: str, message: str) -> dict:
    """
    Send an outreach message to a donor with confirm/decline URLs appended.

    Flow:
      1. Try WhatsApp
      2. If WhatsApp fails, fall back to SMS
      3. Return result dict with channel used

    Appends to message:
      ✅ Confirm: {API_BASE_URL}/confirm?request_id=X&donor_id=Y&action=yes
      ❌ Decline: {API_BASE_URL}/confirm?request_id=X&donor_id=Y&action=no
    """
    donor_id    = donor.get("donor_id", "")
    phone       = donor.get("phone_number", "") or donor.get("phone", "") or donor.get("mobile", "")
    donor_name  = donor.get("name", "Donor")

    if not phone:
        logger.warning(f"No phone number for donor {donor_id} ({donor_name}) — skipping outreach")
        return {"success": False, "error": "No phone number", "channel": "none", "donor_id": donor_id}

    confirm_url = f"{API_BASE_URL}/confirm?request_id={request_id}&donor_id={donor_id}&action=yes"
    decline_url = f"{API_BASE_URL}/confirm?request_id={request_id}&donor_id={donor_id}&action=no"
    full_message = (
        f"{message}\n\n"
        f"✅ I can donate: {confirm_url}\n"
        f"❌ I'm unavailable: {decline_url}"
    )

    result = send_whatsapp(phone, full_message)
    if not result["success"]:
        logger.info(f"WhatsApp failed for donor {donor_id}, falling back to SMS")
        result = send_sms(phone, full_message)

    result["donor_id"] = donor_id
    return result
