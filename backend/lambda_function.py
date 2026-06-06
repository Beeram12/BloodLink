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

    Drives AI intake conversation. When all 3 fields are collected,
    creates a request and kicks off level-1 escalation.
    """
    body = _parse_body(event)
    messages = body.get("messages", [])

    if not messages:
        return bad_request("messages array is required")

    logger.info(f"Chat with {len(messages)} message(s)")

    try:
        ai_result    = bedrock_helpers.chat_with_patient(messages)
        response_text = ai_result.get("response_text", "")
        extracted    = ai_result.get("extracted_data", {}) or {}
    except Exception as exc:
        logger.error(f"Bedrock chat error: {exc}", exc_info=True)
        return server_error("AI service unavailable")

    request_id = None
    blood_group   = extracted.get("blood_group")
    hospital_name = extracted.get("hospital_name")
    urgency       = extracted.get("urgency")

    if blood_group and hospital_name and urgency:
        logger.info("All fields collected — creating request")
        try:
            request_id = f"BWR-{int(time.time())}"
            request_data = {
                "request_id":          request_id,
                "blood_group":         blood_group,
                "hospital_name":       hospital_name,
                "urgency":             urgency,
                "status":              "PENDING",
                "escalation_level":    1,
                "ranked_donor_ids":    [],
                "contacted_donor_ids": [],
                "confirmed_donor_id":  None,
                "created_at":          datetime.now(timezone.utc).isoformat(),
                "updated_at":          datetime.now(timezone.utc).isoformat(),
                "outreach_message":    "",
            }
            db_helpers.create_request(request_data)

            eligible = db_helpers.get_eligible_donors()
            ranked   = matching.rank_donors(eligible, blood_group, exact_only=True)
            top10_ids = [d["donor_id"] for d in ranked[:10]]
            db_helpers.update_request(request_id, {"ranked_donor_ids": top10_ids})
            request_data["ranked_donor_ids"] = top10_ids

            escalation_module.run_escalation(request_data, eligible)
            logger.info(f"Request {request_id} created and level-1 escalation triggered")
        except Exception as exc:
            logger.error(f"Failed to create/escalate request: {exc}", exc_info=True)
            request_id = None

    return response(200, {
        "response_text": response_text,
        "extracted_data": extracted,
        "request_id":     request_id,
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
