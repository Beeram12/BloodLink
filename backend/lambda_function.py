"""
BloodLink Lambda — single entry point for all API routes.

Route table:
  POST /chat              — AI patient intake
  POST /request           — direct blood request creation
  GET  /request/{id}      — fetch request by ID
  GET  /confirm           — donor confirm/decline (?request_id=&donor_id=&action=yes|no)
  GET  /bridges/health    — bridge health summary
  POST /bridges/approve   — approve a bridge replacement donor
  GET  /requests/active   — all non-CONFIRMED requests
  POST /nightly/run       — trigger nightly escalation sweep
  POST /escalate/{id}     — manually escalate a request
"""

import json
import logging
import time
from datetime import datetime, timezone

import db_helpers
import matching
import bridge_health
import bedrock_helpers
import escalation as escalation_module

# ---------------------------------------------------------------------------
# Structured logger
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
# Response helper — all responses include CORS headers
# ---------------------------------------------------------------------------

def response(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type":                 "application/json",
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "*",
        },
        "body": json.dumps(body, default=str),
    }

def bad_request(msg: str) -> dict:
    return response(400, {"error": msg})

def not_found(msg: str = "Not found") -> dict:
    return response(404, {"error": msg})

def server_error(msg: str = "Internal server error") -> dict:
    return response(500, {"error": msg})

# ---------------------------------------------------------------------------
# Body parser
# ---------------------------------------------------------------------------

def _parse_body(event: dict) -> dict:
    raw = event.get("body", "") or ""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse request body as JSON")
        return {}

# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    method = (
        event.get("requestContext", {})
             .get("http", {})
             .get("method", "GET")
             .upper()
    )
    path = event.get("rawPath", "/")

    logger.info(f"Incoming {method} {path}")

    # CORS preflight pass-through
    if method == "OPTIONS":
        return response(200, {})

    try:
        # Route dispatch
        if path == "/chat" and method == "POST":
            return handle_chat(event)

        elif path == "/request" and method == "POST":
            return handle_create_request(event)

        elif path.startswith("/request/") and method == "GET":
            return handle_get_request(event)

        elif path == "/confirm" and method == "GET":
            return handle_confirm(event)

        elif path == "/bridges/health" and method == "GET":
            return handle_bridges_health(event)

        elif path == "/bridges/approve" and method == "POST":
            return handle_bridges_approve(event)

        elif path == "/requests/active" and method == "GET":
            return handle_active_requests(event)

        elif path == "/nightly/run" and method == "POST":
            return handle_nightly_run(event)

        elif path.startswith("/escalate/") and method == "POST":
            return handle_escalate(event)

        else:
            logger.warning(f"No route matched: {method} {path}")
            return not_found(f"Route not found: {method} {path}")

    except Exception as exc:
        logger.error(f"Unhandled exception on {method} {path}: {exc}", exc_info=True)
        return server_error()

# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_chat(event: dict) -> dict:
    """
    POST /chat
    Body: {"messages": [...{role, content}...]}

    Agentic intake: searches bridges first (GREEN→YELLOW→RED), falls back to
    donor pool. If outside local area, expands to nearest cities.
    Self-optimisation: donor stats update on every YES/NO.
    """
    body     = _parse_body(event)
    messages = body.get("messages", [])

    if not messages:
        return bad_request("messages array is required")

    logger.info(f"Agent chat with {len(messages)} message(s)")

    # Extract user_id or phone from messages for DB lookup
    known_profile = None
    prior_blood_group = None
    prior_city = None
    import re
    for msg in messages:
        if msg.get("role") == "user":
            txt = msg.get("content", "").strip()
            if known_profile:
                break
            # User ID: looks like a long hex string (40+ chars) or starts with common prefixes
            uid_match = re.search(r'\b([a-f0-9]{32,})\b', txt.lower())
            if uid_match:
                try:
                    candidate = db_helpers.donors_table.get_item(
                        Key={"donor_id": uid_match.group(1)}
                    ).get("Item")
                    if candidate:
                        known_profile = candidate
                        prior_blood_group = known_profile.get("blood_group")
                        prior_city = known_profile.get("city", "")
                        logger.info(f"User ID lookup matched donor_id={uid_match.group(1)}")
                except Exception as exc:
                    logger.warning(f"User ID lookup failed (non-fatal): {exc}")
            # Phone detection fallback
            if not known_profile:
                phone_match = re.search(r'(\+?91[\s-]?)?[6-9]\d{9}', txt.replace(" ", ""))
                if phone_match:
                    try:
                        known_profile = db_helpers.lookup_donor_by_phone(phone_match.group())
                        if known_profile:
                            prior_blood_group = known_profile.get("blood_group")
                            prior_city = known_profile.get("city", "")
                            logger.info(f"Phone lookup matched profile")
                    except Exception as exc:
                        logger.warning(f"Phone lookup failed (non-fatal): {exc}")
            # Blood group detection from text
            for bg, variants in [
                ("O Positive", ["o positive", "o+"]),
                ("O Negative", ["o negative", "o-"]),
                ("A Positive", ["a positive", "a+"]),
                ("A Negative", ["a negative", "a-"]),
                ("B Positive", ["b positive", "b+"]),
                ("B Negative", ["b negative", "b-"]),
                ("AB Positive", ["ab positive", "ab+"]),
                ("AB Negative", ["ab negative", "ab-"]),
            ]:
                if any(v in txt.lower() for v in variants):
                    prior_blood_group = bg
                    break

    # Fetch live bridge/donor availability if blood group is known
    bridge_context = None
    if prior_blood_group:
        try:
            bridges      = db_helpers.get_bridges_for_patient(prior_blood_group, prior_city)
            local_donors = db_helpers.get_eligible_donors(city=prior_city) if prior_city else db_helpers.get_eligible_donors()
            ranked       = matching.rank_donors(local_donors, prior_blood_group, exact_only=False)
            bridge_context = {
                "green_count":  sum(1 for b in bridges if b.get("health_status") == "GREEN"),
                "yellow_count": sum(1 for b in bridges if b.get("health_status") == "YELLOW"),
                "red_count":    sum(1 for b in bridges if b.get("health_status") == "RED"),
                "donor_count":  len(ranked),
            }
            logger.info(f"Bridge context for {prior_blood_group}: {bridge_context}")
        except Exception as exc:
            logger.warning(f"Bridge context fetch failed (non-fatal): {exc}")

    # Single Bedrock call with full context
    try:
        ai_result     = bedrock_helpers.chat_with_patient(messages, bridge_context=bridge_context, known_profile=known_profile)
        response_text = ai_result.get("response_text", "")
        extracted     = ai_result.get("extracted_data", {}) or {}
        ready         = ai_result.get("ready", False)
    except Exception as exc:
        logger.error(f"Bedrock agent error: {exc}", exc_info=True)
        return server_error("AI service unavailable")

    blood_group   = extracted.get("blood_group")
    city          = extracted.get("city")
    hospital_name = extracted.get("hospital_name")
    urgency       = extracted.get("urgency")

    request_id = None
    if ready and blood_group and hospital_name and urgency:
        logger.info("Agent ready — creating request and escalating")
        try:
            request_id = f"BWR-{int(time.time())}"
            now = datetime.now(timezone.utc).isoformat()
            request_data = {
                "request_id":          request_id,
                "blood_group":         blood_group,
                "hospital_name":       hospital_name,
                "city":                city or "",
                "urgency":             urgency,
                "status":              "PENDING",
                "escalation_level":    1,
                "ranked_donor_ids":    [],
                "contacted_donor_ids": [],
                "confirmed_donor_id":  None,
                "created_at":          now,
                "updated_at":          now,
                "outreach_message":    "",
            }
            db_helpers.create_request(request_data)

            # Prioritise with full tier system — pass coordinates if known_profile has them
            ref_lat = float(known_profile.get("latitude") or 0) or None if known_profile else None
            ref_lon = float(known_profile.get("longitude") or 0) or None if known_profile else None
            all_eligible = db_helpers.get_eligible_donors(city=city) or db_helpers.get_eligible_donors()
            ranked = matching.prioritise_donors(
                all_eligible, blood_group,
                declined_donor_ids=[],
                ref_lat=ref_lat, ref_lon=ref_lon,
            )
            top10_ids = [d["donor_id"] for d in ranked[:10] if d.get("donor_id")]
            db_helpers.update_request(request_id, {"ranked_donor_ids": top10_ids})
            request_data["ranked_donor_ids"]  = top10_ids
            request_data["declined_donor_ids"] = []
            if ref_lat:
                request_data["latitude"]  = str(ref_lat)
                request_data["longitude"] = str(ref_lon)

            escalation_module.run_escalation(request_data, all_eligible)
            logger.info(f"Request {request_id} created, level-1 escalation triggered")
        except Exception as exc:
            logger.error(f"Failed to create/escalate request: {exc}", exc_info=True)
            request_id = None

    return response(200, {
        "response_text": response_text,
        "extracted_data": extracted,
        "request_id":     request_id,
        "bridge_context": bridge_context,
    })


def handle_create_request(event: dict) -> dict:
    """
    POST /request
    Body: {"blood_group": "...", "hospital_name": "...", "urgency": "...", "city": "..."}
    """
    body = _parse_body(event)
    blood_group   = body.get("blood_group", "").strip()
    hospital_name = body.get("hospital_name", "").strip()
    urgency       = body.get("urgency", "urgent").strip()
    city          = body.get("city", "").strip()

    if not blood_group or not hospital_name:
        return bad_request("blood_group and hospital_name are required")

    request_id = f"BWR-{int(time.time())}"
    now        = datetime.now(timezone.utc).isoformat()

    request_data = {
        "request_id":          request_id,
        "blood_group":         blood_group,
        "hospital_name":       hospital_name,
        "city":                city,
        "urgency":             urgency,
        "status":              "PENDING",
        "escalation_level":    1,
        "ranked_donor_ids":    [],
        "contacted_donor_ids": [],
        "confirmed_donor_id":  None,
        "created_at":          now,
        "updated_at":          now,
        "outreach_message":    "",
    }

    logger.info(f"Creating request {request_id} for {blood_group} at {hospital_name}")

    try:
        db_helpers.create_request(request_data)
        eligible = db_helpers.get_eligible_donors()
        ranked   = matching.rank_donors(eligible, blood_group, exact_only=True)
        top10_ids = [d["donor_id"] for d in ranked[:10]]
        db_helpers.update_request(request_id, {"ranked_donor_ids": top10_ids})
        request_data["ranked_donor_ids"] = top10_ids
        escalation_module.run_escalation(request_data, eligible)
    except Exception as exc:
        logger.error(f"handle_create_request error: {exc}", exc_info=True)
        return server_error()

    return response(201, {"request_id": request_id, "status": "PENDING"})


def handle_get_request(event: dict) -> dict:
    """GET /request/{id}"""
    request_id = event.get("rawPath", "").split("/")[-1]
    if not request_id:
        return bad_request("request_id is required")

    try:
        item = db_helpers.get_request(request_id)
    except Exception as exc:
        logger.error(f"handle_get_request error: {exc}", exc_info=True)
        return server_error()

    if not item:
        return not_found(f"Request {request_id} not found")
    return response(200, item)


def handle_confirm(event: dict) -> dict:
    """
    GET /confirm?request_id=X&donor_id=Y&action=yes|no
    Donor taps the confirm/decline URL from WhatsApp.
    """
    params     = event.get("queryStringParameters") or {}
    request_id = params.get("request_id", "")
    donor_id   = params.get("donor_id", "")
    action     = params.get("action", "").lower()

    if not request_id or not donor_id or action not in ("yes", "no"):
        return bad_request("request_id, donor_id, and action (yes|no) are required")

    logger.info(f"Donor {donor_id} responded '{action}' for request {request_id}")

    try:
        # Self-optimisation: update donor stats on every response
        try:
            db_helpers.record_donor_response(donor_id, donated=(action == "yes"))
        except Exception as exc:
            logger.warning(f"record_donor_response non-fatal error: {exc}")

        if action == "yes":
            db_helpers.update_request(request_id, {
                "confirmed_donor_id": donor_id,
                "status":             "CONFIRMED",
            })
            req = db_helpers.get_request(request_id) or {}
            return response(200, {
                "message":      "Thank you! Your confirmation has been recorded.",
                "hospital":     req.get("hospital_name", ""),
                "blood_group":  req.get("blood_group", ""),
                "urgency":      req.get("urgency", ""),
                "request_id":   request_id,
                "confirmed":    True,
            })
        else:
            # Track declined donors so re-ping logic can skip them
            req = db_helpers.get_request(request_id) or {}
            declined = list(set(req.get("declined_donor_ids", []) + [donor_id]))
            db_helpers.update_request(request_id, {"declined_donor_ids": declined})
            return response(200, {
                "message":    "Thank you for letting us know. We will contact another donor.",
                "confirmed":  False,
                "request_id": request_id,
            })
    except Exception as exc:
        logger.error(f"handle_confirm error: {exc}", exc_info=True)
        return server_error()


def handle_bridges_health(event: dict) -> dict:
    """GET /bridges/health"""
    try:
        bridges  = db_helpers.get_all_bridges()
        eligible = db_helpers.get_eligible_donors()
        summary  = bridge_health.get_bridge_health_summary(bridges, eligible)
        return response(200, summary)
    except Exception as exc:
        logger.error(f"handle_bridges_health error: {exc}", exc_info=True)
        return server_error()


def handle_bridges_approve(event: dict) -> dict:
    """
    POST /bridges/approve
    Body: {"bridge_id": "...", "replacement_donor_id": "..."}
    """
    body                 = _parse_body(event)
    bridge_id            = body.get("bridge_id", "").strip()
    replacement_donor_id = body.get("replacement_donor_id", "").strip()

    if not bridge_id or not replacement_donor_id:
        return bad_request("bridge_id and replacement_donor_id are required")

    logger.info(f"Approving replacement donor {replacement_donor_id} for bridge {bridge_id}")

    try:
        db_helpers.update_bridge(bridge_id, {
            "donor_id":       replacement_donor_id,
            "approved_at":    datetime.now(timezone.utc).isoformat(),
            "approval_status": "approved",
        })
        return response(200, {"success": True, "bridge_id": bridge_id, "replacement_donor_id": replacement_donor_id})
    except Exception as exc:
        logger.error(f"handle_bridges_approve error: {exc}", exc_info=True)
        return server_error()


def handle_active_requests(event: dict) -> dict:
    """GET /requests/active"""
    try:
        items = db_helpers.get_active_requests()
        return response(200, items)
    except Exception as exc:
        logger.error(f"handle_active_requests error: {exc}", exc_info=True)
        return server_error()


def handle_nightly_run(event: dict) -> dict:
    """
    POST /nightly/run
    Iterates all active requests and runs escalation on each.
    """
    logger.info("Nightly run triggered")
    processed = 0
    failed    = 0

    try:
        active_requests = db_helpers.get_active_requests()
        eligible        = db_helpers.get_eligible_donors()

        for req in active_requests:
            request_id = req.get("request_id", "?")
            if req.get("status") == "CONFIRMED":
                continue
            try:
                escalation_module.run_escalation(req, eligible)
                processed += 1
            except Exception as exc:
                logger.error(f"Nightly: escalation failed for {request_id}: {exc}")
                failed += 1

        logger.info(f"Nightly run complete — processed={processed}, failed={failed}")
        return response(200, {"processed": processed, "failed": failed})
    except Exception as exc:
        logger.error(f"handle_nightly_run error: {exc}", exc_info=True)
        return server_error()


def handle_escalate(event: dict) -> dict:
    """
    POST /escalate/{request_id}
    Manually trigger escalation for a specific request.
    """
    request_id = event.get("rawPath", "").split("/")[-1]
    if not request_id:
        return bad_request("request_id is required in path")

    logger.info(f"Manual escalation triggered for {request_id}")

    try:
        req = db_helpers.get_request(request_id)
        if not req:
            return not_found(f"Request {request_id} not found")

        eligible = db_helpers.get_eligible_donors()
        updated  = escalation_module.run_escalation(req, eligible)
        return response(200, updated)
    except Exception as exc:
        logger.error(f"handle_escalate error for {request_id}: {exc}", exc_info=True)
        return server_error()
