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
MODEL_ID       = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6")

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

def chat_with_patient(conversation_history: list) -> dict:
    """
    Drive a patient intake conversation.

    `conversation_history` is a list of {"role": "user"|"assistant", "content": "..."}.

    Returns:
        {
            "response_text": str,          # next assistant message
            "extracted_data": {            # fields extracted so far; null if not yet mentioned
                "blood_group":   str | None,
                "hospital_name": str | None,
                "urgency":       "critical" | "urgent" | "standard" | None,
            }
        }
    """
    system = (
        "You are BloodLink, a compassionate blood donation coordinator. "
        "Your task is to collect three pieces of information from the person:\n"
        "1. blood_group — the patient's required blood group "
        "(e.g. 'O Positive', 'AB Negative')\n"
        "2. hospital_name — the name of the hospital\n"
        "3. urgency — one of: critical, urgent, standard\n\n"
        "Ask naturally, one question at a time. When a field is mentioned, remember it.\n"
        "Once all three fields are known, confirm the details and say you are "
        "finding a donor.\n\n"
        "ALWAYS respond with valid JSON in this exact shape:\n"
        '{"response_text": "<your next message>", "extracted_data": '
        '{"blood_group": <value or null>, "hospital_name": <value or null>, '
        '"urgency": <value or null>}}'
    )

    try:
        raw = _invoke(conversation_history, system=system)
        result = json.loads(raw)
        logger.info(f"chat_with_patient extracted: {result.get('extracted_data')}")
        return result
    except json.JSONDecodeError:
        logger.warning("chat_with_patient: could not parse JSON from Bedrock, returning raw text")
        return {"response_text": raw, "extracted_data": {}}  # type: ignore[name-defined]
    except Exception as exc:
        logger.error(f"chat_with_patient error: {exc}")
        return {
            "response_text": "I'm having trouble right now. Please try again in a moment.",
            "extracted_data": {},
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
