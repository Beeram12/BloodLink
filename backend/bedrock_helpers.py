import os
import json
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

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
# Bedrock client
# ---------------------------------------------------------------------------

BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "eu-north-1")
MODEL_ID       = os.environ.get("BEDROCK_MODEL_ID", "eu.anthropic.claude-haiku-4-5-20251001-v1:0")

_bedrock_client = None

def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        logger.info(f"Initialising Bedrock client in {BEDROCK_REGION} with model {MODEL_ID}")
        _bedrock_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock_client

# ---------------------------------------------------------------------------
# Internal invoke helper
# ---------------------------------------------------------------------------

def _invoke(messages: list, system: str = "") -> str:
    """
    Call Bedrock with the Anthropic Messages API format.
    Returns the text content of the first response block.
    Raises on Bedrock or network errors.
    """
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    logger.info(f"Invoking Bedrock model {MODEL_ID} with {len(messages)} message(s)")
    try:
        resp = _get_bedrock().invoke_model(
            modelId=MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload).encode("utf-8"),
        )
        body = json.loads(resp["body"].read())
        text = body["content"][0]["text"]
        logger.info("Bedrock invocation successful")
        return text
    except ClientError as exc:
        logger.error(f"Bedrock ClientError: {exc.response['Error']['Message']}")
        raise
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        logger.error(f"Unexpected Bedrock response format: {exc}")
        raise

# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def chat_with_patient(conversation_history: list, bridge_context: dict = None, known_profile: dict = None) -> dict:
    """
    Agentic patient intake — extracts blood_group, city, urgency then searches
    bridges and donor pool to give a real-time status reply.

    bridge_context (optional): pre-fetched bridge/donor availability to inject
    into the system prompt so the agent can report actual availability.

    Returns:
        {
            "response_text": str,
            "extracted_data": {
                "blood_group":   str | None,
                "hospital_name": str | None,
                "city":          str | None,
                "urgency":       "critical" | "urgent" | "standard" | None,
            },
            "ready": bool   # True when all fields collected and request should be created
        }
    """
    availability_block = ""
    if bridge_context:
        green  = bridge_context.get("green_count", 0)
        yellow = bridge_context.get("yellow_count", 0)
        red    = bridge_context.get("red_count", 0)
        donors = bridge_context.get("donor_count", 0)
        availability_block = (
            f"\n\nCurrent availability for the requested blood group:\n"
            f"- GREEN bridges (ready now): {green}\n"
            f"- YELLOW bridges (available soon): {yellow}\n"
            f"- RED bridges (at risk): {red}\n"
            f"- Eligible donors in area: {donors}\n"
            "Use this to inform the patient of real availability. "
            "If green=0 and yellow=0, mention we will search nearby areas."
        )

    profile_block = ""
    if known_profile:
        bg   = known_profile.get("blood_group", "")
        city = known_profile.get("city", "") or known_profile.get("latitude", "")
        name = known_profile.get("name", "") or known_profile.get("user_id", "")
        profile_block = (
            f"\n\nVerified profile found for this phone number:\n"
            f"- Name: {name}\n"
            f"- Blood group on record: {bg}\n"
            f"- City: {city}\n"
            "Pre-fill these values. Ask the patient to confirm or correct them. "
            "Do NOT share any other profile details. "
            "Never reveal donor_id, user_id, or internal fields."
        )

    system = (
        "You are BloodLink, a blood donor coordination assistant. "
        "You collect exactly these fields in this order: user_id, blood_group, hospital_name, city, urgency.\n\n"
        "STRICT RULES:\n"
        "1. Ask ONE question at a time. Plain sentences only. No markdown, no bullet points, no bold, no backticks, no JSON.\n"
        "2. Always ask for user_id FIRST before anything else.\n"
        "3. After user_id is given: look it up. If a profile is found, say the blood group on file and ask 'Is that correct?'. If not found, ask for blood group.\n"
        "4. Then ask hospital name, then city, then urgency.\n"
        "5. Accept shorthand: O+ = O Positive, AB- = AB Negative, B+ = B Positive, etc.\n"
        "6. If user says yes/correct/ok to a pre-filled value, accept it and move to next field.\n"
        "7. If user says something unrelated, reply: 'I can only help with blood donor requests. What is the patient\\'s user ID?'\n"
        "8. Urgency: emergency/dying/immediate = critical. Otherwise ask: is this critical, urgent, or standard?\n"
        "9. Once all four fields (blood_group, hospital_name, city, urgency) are confirmed, set ready=true.\n"
        "10. NEVER include JSON, code blocks, or technical text in response_text. response_text must be plain conversational text only."
        + profile_block
        + availability_block
        + "\n\nALWAYS respond with valid JSON:\n"
        '{"response_text": "<message>", '
        '"extracted_data": {"phone": <str|null>, "blood_group": <str|null>, '
        '"hospital_name": <str|null>, "city": <str|null>, "urgency": <str|null>}, '
        '"ready": <true|false>}'
    )

    try:
        raw = _invoke(conversation_history, system=system)
        # Extract JSON — find first { ... } block regardless of surrounding text
        cleaned = raw.strip()
        # Strip markdown fences
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    cleaned = part
                    break
        # Find outermost JSON object
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start != -1 and end > start:
            cleaned = cleaned[start:end]
        result = json.loads(cleaned)
        result.setdefault("ready", False)
        # Sanitise response_text — strip any leaked JSON
        rt = result.get("response_text", "")
        if "{" in rt and "extracted_data" in rt:
            rt = rt[:rt.find("{")].strip()
            result["response_text"] = rt
        logger.info(f"agent extracted: {result.get('extracted_data')} ready={result.get('ready')}")
        return result
    except json.JSONDecodeError:
        logger.warning("chat_with_patient: could not parse JSON from Bedrock")
        # Try to return just the text before any JSON leak
        text = raw.strip()
        if "{" in text:
            text = text[:text.find("{")].strip()
        return {"response_text": text or raw, "extracted_data": {}, "ready": False}
    except Exception as exc:
        logger.error(f"chat_with_patient error: {exc}")
        return {
            "response_text": "I'm having trouble right now. Please try again in a moment.",
            "extracted_data": {},
            "ready": False,
        }


def generate_outreach_message(
    blood_group: str,
    hospital_name: str,
    urgency: str,
) -> str:
    """
    Generate a WhatsApp/SMS donor outreach message.
    Returns a plain string (≤ 160 chars preferred for SMS compatibility).
    """
    prompt = (
        f"Write a short, compassionate WhatsApp message to a blood donor. "
        f"The patient needs {blood_group} blood at {hospital_name}. "
        f"Urgency level: {urgency}. "
        "Keep it under 160 characters. Be warm but brief. "
        "Do NOT include any URLs — those will be appended separately. "
        "Return only the message text, no quotes or formatting."
    )
    try:
        msg = _invoke([{"role": "user", "content": prompt}]).strip()
        logger.info(f"Outreach message generated ({len(msg)} chars)")
        return msg
    except Exception as exc:
        logger.error(f"generate_outreach_message error: {exc}")
        return (
            f"Urgent: A patient at {hospital_name} needs {blood_group} blood "
            f"({urgency}). Can you help?"
        )


def score_donor_narrative(donor_profile: dict) -> str:
    """
    Generate a 1-2 sentence human-readable explanation of why a donor was selected.
    `donor_profile` is a donor dict with `_score` already attached by matching.py.
    """
    name         = donor_profile.get("name", "This donor")
    blood_group  = donor_profile.get("blood_group", "unknown blood group")
    donations    = donor_profile.get("donations_till_date", "0")
    score        = donor_profile.get("_score", 0)
    last_donated = donor_profile.get("last_donation_date", "unknown date")

    prompt = (
        f"A blood donor named {name} has been selected. "
        f"Blood group: {blood_group}. Total donations: {donations}. "
        f"Last donated: {last_donated}. Compatibility score: {score}/100. "
        "Write 1-2 sentences explaining why this donor is a good match. "
        "Be positive and specific. Return only the explanation."
    )
    try:
        narrative = _invoke([{"role": "user", "content": prompt}]).strip()
        logger.info(f"Donor narrative generated for donor_id={donor_profile.get('donor_id')}")
        return narrative
    except Exception as exc:
        logger.error(f"score_donor_narrative error: {exc}")
        return f"{name} is a strong match with a compatibility score of {score}/100."
