import os
import json
import logging
import time
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
# User-facing error message (never exposes internals)
# ---------------------------------------------------------------------------

USER_ERROR_MSG = (
    "We are experiencing a brief technical issue. Your request is important to us. "
    "Please try sending your message again in a moment."
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are BloodLink Coordinator, a compassionate and warm AI assistant for Blood Warriors, an organisation that connects blood donors with Thalassemia patients across India.

PERSONALITY: Warm, gentle, and reassuring. Never make anyone feel bad for not having information. Speak simply. Never use bullet points, asterisks, hashtags, dashes, or any markdown. Plain conversational sentences only.

CORE RULES:
1. Ask ONE question at a time.
2. Never invent or guess any information.
3. Never double-verify or re-ask something the user has already clearly answered.
4. If the user says they do not have something or cannot provide it, accept that immediately and move on warmly. Do NOT repeat the request.

TWO USER TYPES:
- If user wants to donate blood: Follow the DONOR FLOW below.
- If user needs blood for a patient: Follow the PATIENT FLOW below exactly.

DONOR FLOW:
Step D1: Thank them warmly. Then ask: That is wonderful, thank you so much. Could you tell me which city you are in? I will find the nearest Blood Warriors centre for you.
Step D2: Once city is provided, respond with the nearest centre for that city. Use only these known centres — if the city is not listed, say the nearest known city below:
  - Hyderabad: Blood Warriors Centre, Banjara Hills, Hyderabad. Call: 040-12345678
  - Delhi: Blood Warriors Centre, Connaught Place, New Delhi. Call: 011-12345678
  - Mumbai: Blood Warriors Centre, Dadar, Mumbai. Call: 022-12345678
  - Chennai: Blood Warriors Centre, Anna Nagar, Chennai. Call: 044-12345678
  - Bangalore: Blood Warriors Centre, Indiranagar, Bangalore. Call: 080-12345678
  - Kolkata: Blood Warriors Centre, Salt Lake, Kolkata. Call: 033-12345678
  For any other city say: The nearest Blood Warriors centre to you is in {closest major city above}. You can also register online or call our national helpline at 1800-XXX-XXXX.
Say: Please visit them or call to complete your registration. Your donation will directly save a Thalassemia patient's life. Thank you for being a hero.

PATIENT FLOW:

Step 1: Ask for the patient user ID. Say: Of course, I am here to help. Could you please share the patient user ID? It is the registration code given by Blood Warriors.

IMPORTANT — If the user says they do not have the ID, do not know it, forgot it, or cannot find it: immediately move to Step 1b without any further mention of the user ID. Say warmly: That is perfectly fine, do not worry at all. I can still get things started for you. Could you please tell me the patient's blood group? ##SHOW_BLOOD_BUTTONS##

Step 1b: Once blood group is provided (no user ID path), continue to Step 2.

Step 2: Ask for the patient's current location or city. Say: Thank you. Which city or area is the patient in right now? This helps us find the nearest available donors.

Step 3: Once location is provided, ask for urgency. Say exactly: Thank you. How urgently is the blood needed? ##SHOW_URGENCY_BUTTONS## Type 1 for Critical, needed within a few hours. Type 2 for Urgent, needed within 24 hours. Type 3 for Standard, needed within 3 days.

Step 4: Once urgency is confirmed, say exactly this: Thank you. We have sent an urgent message to nearby donors. We will notify you as soon as a donor confirms. Please stay close to your phone. Then include REQUEST_READY|{user_id_or_NO_USER_ID}|{urgency} on its own line at the end of response_text and set ready=true. Use NO_USER_ID literally if no ID was given.

VALIDATION — only apply these when user has NOT said they lack the ID:
- A valid user ID is a long hex string, often starting with a backslash and x. Example: \\xa7287517...
- If something clearly wrong is given as a user ID (like a single word or name), gently ask once to double check.
- Urgency: critical, urgent, or standard only.
- Blood group: O Positive, O Negative, A Positive, A Negative, B Positive, B Negative, AB Positive, AB Negative only.

OUT OF SCOPE: For anything unrelated to blood requests say: I am only here to help with blood donation. How can I help you today?

ALWAYS respond with valid JSON only:
{"response_text": "<plain text, no markdown>", "extracted_data": {"user_id": <str|null>, "urgency": <str|null>, "blood_group": <str|null>, "city": <str|null>}, "ready": <true|false>}"""

# ---------------------------------------------------------------------------
# Internal invoke helper with retry
# ---------------------------------------------------------------------------

def _invoke(messages: list, system: str = "", retries: int = 1) -> str:
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens":        500,
        "messages":          messages,
    }
    if system:
        payload["system"] = system

    logger.info(f"Invoking Bedrock model {MODEL_ID} with {len(messages)} message(s)")

    for attempt in range(retries + 1):
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
            code = exc.response["Error"]["Code"]
            msg  = exc.response["Error"]["Message"]
            logger.error(f"Bedrock ClientError (attempt {attempt+1}): {code} — {msg}")
            if attempt < retries:
                time.sleep(1)
                continue
            raise
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.error(f"Unexpected Bedrock response format: {exc}")
            raise
        except Exception as exc:
            logger.error(f"Bedrock unexpected error (attempt {attempt+1}): {exc}")
            if attempt < retries:
                time.sleep(1)
                continue
            raise

# ---------------------------------------------------------------------------
# Trim conversation history to avoid token limits
# ---------------------------------------------------------------------------

def _trim_history(messages: list, max_messages: int = 20) -> list:
    if len(messages) <= max_messages:
        return messages
    # Keep first summary-style message then last (max_messages - 1) messages
    logger.info(f"Trimming conversation history from {len(messages)} to {max_messages}")
    return messages[-(max_messages):]

# ---------------------------------------------------------------------------
# Public: chat_with_patient
# ---------------------------------------------------------------------------

def chat_with_patient(conversation_history: list, bridge_context: dict = None, known_profile: dict = None) -> dict:
    """
    Patient intake chat. Collects user_id and urgency, then triggers request creation.
    Returns: {"response_text": str, "extracted_data": dict, "ready": bool}
    """
    trimmed = _trim_history(conversation_history)

    try:
        raw = _invoke(trimmed, system=SYSTEM_PROMPT, retries=1)

        # Parse JSON response
        cleaned = raw.strip()
        if "```" in cleaned:
            for part in cleaned.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("{"):
                    cleaned = part
                    break
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start != -1 and end > start:
            cleaned = cleaned[start:end]

        result = json.loads(cleaned)
        result.setdefault("ready", False)
        result.setdefault("extracted_data", {})

        # Clean response_text — strip any leaked JSON
        rt = result.get("response_text", "")
        if "{" in rt and "extracted_data" in rt:
            rt = rt[:rt.find("{")].strip()
            result["response_text"] = rt

        logger.info(f"chat extracted: {result.get('extracted_data')} ready={result.get('ready')}")
        return result

    except json.JSONDecodeError:
        logger.warning("chat_with_patient: could not parse JSON from Bedrock — returning raw text")
        text = raw.strip() if 'raw' in dir() else ""
        if "{" in text:
            text = text[:text.find("{")].strip()
        return {"response_text": text or USER_ERROR_MSG, "extracted_data": {}, "ready": False}

    except Exception as exc:
        logger.error(f"chat_with_patient error: {exc}", exc_info=True)
        return {
            "response_text": USER_ERROR_MSG,
            "extracted_data": {},
            "ready": False,
        }

# ---------------------------------------------------------------------------
# Public: generate_outreach_message
# ---------------------------------------------------------------------------

def generate_outreach_message(blood_group: str, hospital_name: str, urgency: str) -> str:
    prompt = (
        f"Write a short, compassionate WhatsApp message to a blood donor. "
        f"The patient needs {blood_group} blood at {hospital_name}. "
        f"Urgency level: {urgency}. "
        "Keep it under 160 characters. Be warm but brief. "
        "Do NOT include any URLs — those will be appended separately. "
        "Return only the message text, no quotes or formatting."
    )
    try:
        msg = _invoke([{"role": "user", "content": prompt}], retries=1).strip()
        logger.info(f"Outreach message generated ({len(msg)} chars)")
        return msg
    except Exception as exc:
        logger.error(f"generate_outreach_message error: {exc}")
        return f"Urgent: A patient at {hospital_name} needs {blood_group} blood ({urgency}). Can you help?"

# ---------------------------------------------------------------------------
# Public: score_donor_narrative
# ---------------------------------------------------------------------------

def score_donor_narrative(donor_profile: dict) -> str:
    name        = donor_profile.get("name", "This donor")
    blood_group = donor_profile.get("blood_group", "unknown blood group")
    donations   = donor_profile.get("donations_till_date", "0")
    score       = donor_profile.get("_score", 0)
    last_donated= donor_profile.get("last_donation_date", "unknown date")

    prompt = (
        f"A blood donor named {name} has been selected. "
        f"Blood group: {blood_group}. Total donations: {donations}. "
        f"Last donated: {last_donated}. Compatibility score: {score}/100. "
        "Write 1-2 sentences explaining why this donor is a good match. "
        "Be positive and specific. Return only the explanation."
    )
    try:
        narrative = _invoke([{"role": "user", "content": prompt}], retries=1).strip()
        logger.info(f"Donor narrative generated for donor_id={donor_profile.get('donor_id')}")
        return narrative
    except Exception as exc:
        logger.error(f"score_donor_narrative error: {exc}")
        return f"{name} is a strong match with a compatibility score of {score}/100."
