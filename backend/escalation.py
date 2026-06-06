import os
import json
import logging
from datetime import datetime, timezone

from db_helpers import update_request, get_request
from matching import prioritise_donors
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
# Helpers
# ---------------------------------------------------------------------------

def _get_outreach_message(request: dict) -> str:
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

def _declined(request: dict) -> set:
    return set(request.get("declined_donor_ids", []))

def _ref_coords(request: dict):
    """Extract hospital lat/lon from request record if stored."""
    try:
        lat = float(request.get("latitude") or 0) or None
        lon = float(request.get("longitude") or 0) or None
        return lat, lon
    except Exception:
        return None, None

def _contact_donors(donors: list, request: dict, contacted: set) -> list:
    """Send outreach to donors not yet contacted. Returns list of newly contacted IDs."""
    message    = _get_outreach_message(request)
    request_id = request.get("request_id", "")
    newly      = []

    for donor in donors:
        donor_id = donor.get("donor_id", "")
        if not donor_id or donor_id in contacted:
            continue
        result = send_donor_outreach(donor, request_id, message)
        if result.get("success"):
            newly.append(donor_id)
            logger.info(f"Outreach sent to {donor_id} via {result.get('channel')} tier={donor.get('_tier')}")
        else:
            logger.warning(f"Outreach failed for {donor_id}: {result.get('error')}")

    return newly

def _persist(request_id: str, contacted: list, level: int, status: str = "PENDING") -> dict:
    return update_request(request_id, {
        "contacted_donor_ids": contacted,
        "escalation_level":    level,
        "status":              status,
    })

def _reping_pending(request: dict, all_donors: list, batch_ids: list) -> None:
    """Re-send to donors in batch who haven't confirmed or declined yet."""
    confirmed  = request.get("confirmed_donor_id", "")
    declined   = _declined(request)
    message    = _get_outreach_message(request)
    request_id = request.get("request_id", "")

    pending = [d for d in batch_ids if d != confirmed and d not in declined]
    if not pending:
        return

    donor_map = {d.get("donor_id"): d for d in all_donors}
    for donor_id in pending:
        donor = donor_map.get(donor_id)
        if not donor:
            continue
        result = send_donor_outreach(donor, request_id, f"[REMINDER] {message}")
        logger.info(f"Re-ping {'sent' if result.get('success') else 'failed'} for {donor_id}")

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_escalation(request: dict, all_eligible_donors: list) -> dict:
    request_id = request.get("request_id", "UNKNOWN")
    level      = int(request.get("escalation_level", 1))
    status     = request.get("status", "PENDING")

    logger.info(f"Running escalation for {request_id} at level {level} (status={status})")

    if status == "CONFIRMED":
        logger.info(f"{request_id} already CONFIRMED — skipping")
        return request

    dispatch = {1: _level_1, 2: _level_2, 3: _level_3, 4: _level_4, 5: _level_5}
    handler  = dispatch.get(level, _level_5)

    try:
        updated = handler(request, all_eligible_donors)
        logger.info(f"Escalation level {level} complete for {request_id}")
        return updated
    except Exception as exc:
        logger.error(f"Escalation level {level} failed for {request_id}: {exc}", exc_info=True)
        raise

# ---------------------------------------------------------------------------
# Level handlers
# ---------------------------------------------------------------------------

def _level_1(request: dict, all_eligible_donors: list) -> dict:
    """Contact top 5 exact-match donors using tiered priority."""
    request_id  = request.get("request_id", "")
    blood_group = request.get("blood_group", "")
    contacted   = _already_contacted(request)
    declined    = _declined(request)
    ref_lat, ref_lon = _ref_coords(request)

    ranked = prioritise_donors(
        all_eligible_donors, blood_group,
        declined_donor_ids=list(declined),
        ref_lat=ref_lat, ref_lon=ref_lon,
    )
    # Exact blood group only at level 1
    exact = [d for d in ranked if d.get("blood_group", "") == blood_group]
    top5  = exact[:5]
    top5_ids = [d.get("donor_id") for d in top5 if d.get("donor_id")]

    # If already contacted this batch, re-ping non-responders
    if contacted & set(top5_ids):
        logger.info(f"Level 1: re-pinging non-responders for {request_id}")
        _reping_pending(request, all_eligible_donors, top5_ids)
        # If all 5 declined, escalate
        if declined >= set(top5_ids):
            logger.info(f"Level 1: all 5 declined — escalating to level 2")
            updated = {**request, "escalation_level": 2}
            _persist(request_id, list(contacted), 2)
            return _level_2(updated, all_eligible_donors)
        return request

    logger.info(f"Level 1: contacting top-5 exact donors for {request_id}")
    newly = _contact_donors(top5, request, contacted)
    all_contacted = list(contacted | set(newly))
    _persist(request_id, all_contacted, 1)
    return {**request, "contacted_donor_ids": all_contacted, "escalation_level": 1}


def _level_2(request: dict, all_eligible_donors: list) -> dict:
    """Contact next 5 exact donors + reminder to level-1 batch."""
    request_id  = request.get("request_id", "")
    blood_group = request.get("blood_group", "")
    contacted   = _already_contacted(request)
    declined    = _declined(request)
    ref_lat, ref_lon = _ref_coords(request)

    ranked = prioritise_donors(
        all_eligible_donors, blood_group,
        declined_donor_ids=list(declined),
        ref_lat=ref_lat, ref_lon=ref_lon,
    )
    exact    = [d for d in ranked if d.get("blood_group", "") == blood_group]
    uncontacted = [d for d in exact if d.get("donor_id") not in contacted]
    next5    = uncontacted[:5]
    next5_ids = [d.get("donor_id") for d in next5 if d.get("donor_id")]

    if contacted & set(next5_ids):
        logger.info(f"Level 2: re-pinging non-responders for {request_id}")
        _reping_pending(request, all_eligible_donors, next5_ids)
        if declined >= set(next5_ids):
            logger.info(f"Level 2: all declined — escalating to level 3")
            updated = {**request, "escalation_level": 3}
            _persist(request_id, list(contacted), 3)
            return _level_3(updated, all_eligible_donors)
        return request

    logger.info(f"Level 2: contacting next-5 donors for {request_id}")
    newly = _contact_donors(next5, request, contacted)

    # SMS reminder to level-1 batch
    message      = _get_outreach_message(request)
    level1_ids   = [d for d in list(contacted)[:5] if d not in declined]
    donor_map    = {d.get("donor_id"): d for d in all_eligible_donors}
    for did in level1_ids:
        donor = donor_map.get(did)
        if not donor:
            continue
        phone = donor.get("phone_number") or donor.get("phone") or donor.get("mobile") or ""
        if phone:
            send_sms(phone, f"[REMINDER] {message}")

    all_contacted = list(contacted | set(newly))
    _persist(request_id, all_contacted, 2)
    return {**request, "contacted_donor_ids": all_contacted, "escalation_level": 2}


def _level_3(request: dict, all_eligible_donors: list) -> dict:
    """Expand to all compatible blood groups; contact top 10 not yet reached."""
    request_id  = request.get("request_id", "")
    blood_group = request.get("blood_group", "")
    contacted   = _already_contacted(request)
    declined    = _declined(request)
    ref_lat, ref_lon = _ref_coords(request)

    logger.info(f"Level 3: expanding to compatible blood groups for {request_id}")

    ranked      = prioritise_donors(
        all_eligible_donors, blood_group,
        declined_donor_ids=list(declined),
        ref_lat=ref_lat, ref_lon=ref_lon,
    )
    uncontacted = [d for d in ranked if d.get("donor_id") not in contacted]
    top10       = uncontacted[:10]
    newly       = _contact_donors(top10, request, contacted)

    all_contacted = list(contacted | set(newly))
    _persist(request_id, all_contacted, 4)
    return {**request, "contacted_donor_ids": all_contacted, "escalation_level": 4}


def _level_4(request: dict, all_eligible_donors: list) -> dict:
    """Flag NEEDS_HUMAN and alert coordinator."""
    request_id = request.get("request_id", "")
    logger.warning(f"Level 4: escalating {request_id} to NEEDS_HUMAN")

    alert = (
        f"[BloodLink ALERT] Request {request_id} needs human intervention. "
        f"Blood: {request.get('blood_group')}, "
        f"Hospital: {request.get('hospital_name')}, "
        f"Urgency: {request.get('urgency')}. Automated outreach exhausted."
    )
    if COORDINATOR_PHONE:
        send_sms(COORDINATOR_PHONE, alert)
    else:
        logger.warning("COORDINATOR_PHONE not set — coordinator alert skipped")

    contacted = list(_already_contacted(request))
    _persist(request_id, contacted, 5, status="NEEDS_HUMAN")
    return {**request, "status": "NEEDS_HUMAN", "escalation_level": 5}


def _level_5(request: dict, all_eligible_donors: list) -> dict:
    """Emergency broadcast to ALL eligible donors in the same city."""
    request_id   = request.get("request_id", "")
    blood_group  = request.get("blood_group", "")
    request_city = (request.get("city", "") or "").strip().lower()
    declined     = _declined(request)
    ref_lat, ref_lon = _ref_coords(request)

    logger.warning(f"Level 5: emergency broadcast for {request_id} city='{request_city}'")

    city_donors = [
        d for d in all_eligible_donors
        if not request_city or (d.get("city", "") or "").strip().lower() == request_city
    ]

    ranked = prioritise_donors(
        city_donors, blood_group,
        declined_donor_ids=list(declined),
        ref_lat=ref_lat, ref_lon=ref_lon,
    )

    contacted = _already_contacted(request)
    newly     = _contact_donors(ranked, request, contacted)
    all_contacted = list(contacted | set(newly))

    update_request(request_id, {
        "contacted_donor_ids": all_contacted,
        "escalation_level":    5,
        "status":              "NEEDS_HUMAN",
    })
    return {**request, "contacted_donor_ids": all_contacted, "escalation_level": 5}
