import os
import json
import logging
from datetime import datetime, timezone

from db_helpers import update_request, get_request
from matching import rank_donors
from bedrock_helpers import generate_outreach_message
from twilio_helpers import send_donor_outreach, send_sms

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

COORDINATOR_PHONE = os.environ.get("COORDINATOR_PHONE", "")

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_escalation(request: dict, all_eligible_donors: list) -> dict:
    """
    Run the appropriate escalation level for a request.
    Returns the updated request dict.
    """
    request_id = request.get("request_id", "UNKNOWN")
    level      = int(request.get("escalation_level", 1))
    status     = request.get("status", "PENDING")

    logger.info(f"Running escalation for {request_id} at level {level} (status={status})")

    if status == "CONFIRMED":
        logger.info(f"{request_id} already CONFIRMED — skipping escalation")
        return request

    dispatch = {
        1: _level_1,
        2: _level_2,
        3: _level_3,
        4: _level_4,
        5: _level_5,
    }
    handler = dispatch.get(level, _level_5)

    try:
        updated = handler(request, all_eligible_donors)
        logger.info(f"Escalation level {level} complete for {request_id}")
        return updated
    except Exception as exc:
        logger.error(f"Escalation level {level} failed for {request_id}: {exc}")
        raise

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_outreach_message(request: dict) -> str:
    """Generate (or reuse) the outreach message for this request."""
    existing = request.get("outreach_message", "")
    if existing:
        return existing
    return generate_outreach_message(
        request.get("blood_group", ""),
        request.get("hospital_name", ""),
        request.get("urgency", "urgent"),
    )

def _already_contacted(request: dict) -> set:
    return set(request.get("contacted_donor_ids", []))

def _contact_donors(donors: list, request: dict, contacted: set) -> list:
    """Send outreach to donors not yet contacted. Returns list of newly contacted IDs."""
    message     = _get_outreach_message(request)
    request_id  = request.get("request_id", "")
    newly_contacted = []

    for donor in donors:
        donor_id = donor.get("donor_id", "")
        if not donor_id or donor_id in contacted:
            continue
        result = send_donor_outreach(donor, request_id, message)
        if result.get("success"):
            newly_contacted.append(donor_id)
            logger.info(f"Outreach sent to donor {donor_id} via {result.get('channel')}")
        else:
            logger.warning(f"Outreach failed for donor {donor_id}: {result.get('error')}")

    return newly_contacted

def _persist_escalation(request_id: str, contacted_ids: list, new_level: int, status: str = "PENDING") -> dict:
    updates = {
        "contacted_donor_ids": contacted_ids,
        "escalation_level":    new_level,
        "status":              status,
    }
    return update_request(request_id, updates)

# ---------------------------------------------------------------------------
# Level handlers
# ---------------------------------------------------------------------------

def _level_1(request: dict, all_eligible_donors: list) -> dict:
    """Contact top 5 exact-match donors via WhatsApp."""
    request_id  = request.get("request_id", "")
    blood_group = request.get("blood_group", "")
    logger.info(f"Level 1: contacting top-5 exact donors for {request_id}")

    ranked    = rank_donors(all_eligible_donors, blood_group, exact_only=True)
    top5      = ranked[:5]
    contacted = _already_contacted(request)
    newly     = _contact_donors(top5, request, contacted)

    all_contacted = list(contacted | set(newly))
    _persist_escalation(request_id, all_contacted, new_level=2)

    return {**request, "contacted_donor_ids": all_contacted, "escalation_level": 2}


def _level_2(request: dict, all_eligible_donors: list) -> dict:
    """Contact donors 5-10 via WhatsApp, and send SMS backup to original 5."""
    request_id  = request.get("request_id", "")
    blood_group = request.get("blood_group", "")
    logger.info(f"Level 2: contacting donors 5-10 + SMS backup for {request_id}")

    ranked    = rank_donors(all_eligible_donors, blood_group, exact_only=True)
    next5     = ranked[5:10]
    contacted = _already_contacted(request)
    newly     = _contact_donors(next5, request, contacted)

    # SMS backup to originally contacted donors
    message = _get_outreach_message(request)
    original_ids = list(contacted)[:5]
    original_donors = [d for d in all_eligible_donors if d.get("donor_id", "") in original_ids]
    for donor in original_donors:
        phone = donor.get("phone_number", "") or donor.get("phone", "") or donor.get("mobile", "")
        if phone:
            send_sms(phone, f"[REMINDER] {message}")

    all_contacted = list(contacted | set(newly))
    _persist_escalation(request_id, all_contacted, new_level=3)

    return {**request, "contacted_donor_ids": all_contacted, "escalation_level": 3}


def _level_3(request: dict, all_eligible_donors: list) -> dict:
    """Expand to all compatible blood groups; contact top-10 not yet reached."""
    request_id  = request.get("request_id", "")
    blood_group = request.get("blood_group", "")
    logger.info(f"Level 3: expanding to compatible blood groups for {request_id}")

    ranked    = rank_donors(all_eligible_donors, blood_group, exact_only=False)
    contacted = _already_contacted(request)
    uncontacted = [d for d in ranked if d.get("donor_id", "") not in contacted]
    top10    = uncontacted[:10]
    newly    = _contact_donors(top10, request, contacted)

    all_contacted = list(contacted | set(newly))
    _persist_escalation(request_id, all_contacted, new_level=4)

    return {**request, "contacted_donor_ids": all_contacted, "escalation_level": 4}


def _level_4(request: dict, all_eligible_donors: list) -> dict:
    """Flag as NEEDS_HUMAN and alert the coordinator."""
    request_id = request.get("request_id", "")
    logger.warning(f"Level 4: escalating {request_id} to NEEDS_HUMAN — alerting coordinator")

    alert = (
        f"[BloodLink ALERT] Request {request_id} needs human intervention. "
        f"Blood group: {request.get('blood_group')}, "
        f"Hospital: {request.get('hospital_name')}, "
        f"Urgency: {request.get('urgency')}. "
        "Automated outreach exhausted."
    )
    if COORDINATOR_PHONE:
        send_sms(COORDINATOR_PHONE, alert)
    else:
        logger.warning("COORDINATOR_PHONE not set — coordinator alert not sent")

    contacted = list(_already_contacted(request))
    _persist_escalation(request_id, contacted, new_level=5, status="NEEDS_HUMAN")

    return {**request, "status": "NEEDS_HUMAN", "escalation_level": 5}


def _level_5(request: dict, all_eligible_donors: list) -> dict:
    """Emergency broadcast to ALL eligible donors in the same city."""
    request_id    = request.get("request_id", "")
    request_city  = (request.get("city", "") or "").strip().lower()
    logger.warning(f"Level 5: emergency broadcast for {request_id} in city='{request_city}'")

    if request_city:
        city_donors = [
            d for d in all_eligible_donors
            if (d.get("city", "") or "").strip().lower() == request_city
        ]
    else:
        city_donors = all_eligible_donors
        logger.warning(f"No city on request {request_id} — broadcasting to ALL eligible donors")

    logger.info(f"Broadcasting to {len(city_donors)} donors")
    contacted = _already_contacted(request)
    newly     = _contact_donors(city_donors, request, contacted)

    all_contacted = list(contacted | set(newly))
    update_request(request_id, {
        "contacted_donor_ids": all_contacted,
        "escalation_level":    5,
        "status":              "NEEDS_HUMAN",
    })

    return {**request, "contacted_donor_ids": all_contacted, "escalation_level": 5}
