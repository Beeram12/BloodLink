"""
BloodLink Lambda — single entry point for all API routes.

Route table:
  POST /chat                     — AI patient intake
  POST /request                  — direct blood request creation
  GET  /request/{id}             — fetch request by ID
  POST /request/{id}/status      — advance request through status pipeline
  GET  /confirm                  — donor confirm/decline (?request_id=&donor_id=&action=yes|no)
  GET  /bridges/health           — bridge health summary (real patient data, no fake names)
  POST /bridges/approve          — approve a bridge replacement donor
  GET  /requests/active          — all non-COMPLETED requests with status_history
  GET  /patient/search           — volunteer patient lookup (?user_id=X)
  POST /donor/response           — save donor 4-question answers (updates request record)
  POST /nightly/run              — trigger nightly escalation sweep
  POST /escalate/{id}            — manually escalate a request
"""

import json
import logging
import math
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
# Status pipeline
# ---------------------------------------------------------------------------

STATUS_CHAIN = ["SEARCHING", "DONOR_FOUND", "ARRIVING", "TRANSFUSING", "COMPLETED"]

def _next_status(current: str) -> str | None:
    try:
        idx = STATUS_CHAIN.index(current)
        return STATUS_CHAIN[idx + 1] if idx + 1 < len(STATUS_CHAIN) else None
    except ValueError:
        return None

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
# Body / query parser
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

def _urgency_label(days: int) -> str:
    if days <= 3:
        return "CRITICAL"
    if days <= 7:
        return "URGENT"
    if days <= 14:
        return "SOON"
    return "SCHEDULED"

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

    if method == "OPTIONS":
        return response(200, {})

    try:
        if path == "/chat" and method == "POST":
            return handle_chat(event)

        elif path == "/request" and method == "POST":
            return handle_create_request(event)

        elif path == "/requests/active" and method == "GET":
            return handle_active_requests(event)

        elif path == "/patient/search" and method == "GET":
            return handle_patient_search(event)

        elif path.startswith("/request/") and path.endswith("/status") and method == "POST":
            return handle_request_status(event)

        elif path.startswith("/request/") and method == "GET":
            return handle_get_request(event)

        elif path == "/confirm" and method == "GET":
            return handle_confirm(event)

        elif path == "/bridges/health" and method == "GET":
            return handle_bridges_health(event)

        elif path == "/bridges/approve" and method == "POST":
            return handle_bridges_approve(event)

        elif path == "/donor/response" and method == "POST":
            return handle_donor_response(event)

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

    Flow:
    1. Send messages to Bedrock
    2. If response contains REQUEST_READY|user_id|urgency trigger:
       - Look up patient in DynamoDB by user_id to get blood_group + location
       - Create request record
       - Run escalation (sends WhatsApp/SMS to donors)
    """
    import re as _re

    body     = _parse_body(event)
    messages = body.get("messages", [])

    if not messages:
        return bad_request("messages array is required")

    logger.info(f"Chat with {len(messages)} message(s)")

    try:
        ai_result     = bedrock_helpers.chat_with_patient(messages)
        response_text = ai_result.get("response_text", "")
        extracted     = ai_result.get("extracted_data", {}) or {}
        ready         = ai_result.get("ready", False)
    except Exception as exc:
        logger.error(f"Bedrock error: {exc}", exc_info=True)
        return response(200, {
            "response_text": "We are experiencing a brief technical issue. Please try again in a moment.",
            "extracted_data": {},
            "request_id": None,
        })

    request_id = None

    # Check for REQUEST_READY|user_id|urgency trigger in response text
    trigger_match = _re.search(r'REQUEST_READY\|([^\|\n]+)\|([^\|\n]+)', response_text)

    # Also fire when ready=True even if trigger pattern wasn't in response_text
    # (bot may put the data in extracted_data instead of embedding the marker)
    if ready and (trigger_match or extracted.get("urgency")):
        if trigger_match:
            raw_user_id = trigger_match.group(1).strip()
            urgency     = trigger_match.group(2).strip().lower()
        else:
            raw_user_id = extracted.get("user_id") or "NO_USER_ID"
            urgency     = (extracted.get("urgency") or "urgent").lower()

        logger.info(f"REQUEST_READY triggered: user_id={raw_user_id[:20]} urgency={urgency}")

        try:
            import uuid as _uuid

            no_user_id = raw_user_id.upper() in ("NO_USER_ID", "UNREGISTERED", "NONE", "")
            patient    = None

            if not no_user_id:
                # Look up patient by user_id to get blood_group and location
                patient = db_helpers.donors_table.get_item(
                    Key={"donor_id": raw_user_id}
                ).get("Item")

                if not patient:
                    from boto3.dynamodb.conditions import Attr as _Attr
                    scan = db_helpers.donors_table.scan(
                        FilterExpression=_Attr("user_id").eq(raw_user_id) & _Attr("role").eq("Patient"),
                        Limit=1,
                    )
                    items   = scan.get("Items", [])
                    patient = items[0] if items else None

            # City-to-coords lookup table for common Indian cities
            CITY_COORDS = {
                "hyderabad":  (17.3850, 78.4867), "secunderabad": (17.4399, 78.4983),
                "delhi":      (28.6139, 77.2090), "new delhi":    (28.6139, 77.2090),
                "mumbai":     (19.0760, 72.8777), "bangalore":    (12.9716, 77.5946),
                "bengaluru":  (12.9716, 77.5946), "chennai":      (13.0827, 80.2707),
                "kolkata":    (22.5726, 88.3639), "pune":         (18.5204, 73.8567),
                "ahmedabad":  (23.0225, 72.5714), "jaipur":       (26.9124, 75.7873),
                "surat":      (21.1702, 72.8311), "lucknow":      (26.8467, 80.9462),
                "kanpur":     (26.4499, 80.3319), "nagpur":       (21.1458, 79.0882),
                "visakhapatnam": (17.6868, 83.2185), "bhopal":    (23.2599, 77.4126),
                "patna":      (25.5941, 85.1376), "vadodara":     (22.3072, 73.1812),
            }

            # Extract city from bot's extracted_data or scan conversation
            chat_city = (extracted.get("city") or "").strip().lower()
            if not chat_city:
                conv_text = " ".join(m.get("content","") for m in messages if m.get("role")=="user").lower()
                for city_key in CITY_COORDS:
                    if city_key in conv_text:
                        chat_city = city_key
                        break

            if patient:
                blood_group = patient.get("blood_group", "O Positive")
                p_lat_str   = patient.get("latitude", "")
                p_lon_str   = patient.get("longitude", "")
                db_lat      = float(p_lat_str) if p_lat_str else None
                db_lon      = float(p_lon_str) if p_lon_str else None

                # If patient gave a city, check if it matches DB location
                if chat_city and chat_city in CITY_COORDS:
                    chat_lat, chat_lon = CITY_COORDS[chat_city]
                    # If DB location is missing or very far (>50km), update it
                    location_mismatch = True
                    if db_lat and db_lon:
                        dist_km = math.sqrt((db_lat - chat_lat)**2 + (db_lon - chat_lon)**2) * 111
                        location_mismatch = dist_km > 50
                    if location_mismatch:
                        logger.info(f"Updating patient {raw_user_id} location to {chat_city}")
                        db_helpers.donors_table.update_item(
                            Key={"donor_id": patient.get("donor_id", raw_user_id)},
                            UpdateExpression="SET latitude=:lat, longitude=:lon, city=:city",
                            ExpressionAttributeValues={":lat": str(chat_lat), ":lon": str(chat_lon), ":city": chat_city},
                        )
                        ref_lat, ref_lon = chat_lat, chat_lon
                    else:
                        ref_lat, ref_lon = db_lat, db_lon
                else:
                    ref_lat, ref_lon = db_lat, db_lon

                location_name = chat_city.title() if chat_city else db_helpers.get_location_name(p_lat_str, p_lon_str) if p_lat_str else "Hyderabad"

            else:
                # No user ID or patient not found
                blood_group = extracted.get("blood_group", "")
                if not blood_group:
                    bg_map = {
                        "o positive": "O Positive", "o negative": "O Negative",
                        "o+": "O Positive", "o-": "O Negative",
                        "a positive": "A Positive", "a negative": "A Negative",
                        "a+": "A Positive", "a-": "A Negative",
                        "b positive": "B Positive", "b negative": "B Negative",
                        "b+": "B Positive", "b-": "B Negative",
                        "ab positive": "AB Positive", "ab negative": "AB Negative",
                        "ab+": "AB Positive", "ab-": "AB Negative",
                    }
                    conv_text = " ".join(m.get("content","") for m in messages if m.get("role")=="user").lower()
                    for key, val in bg_map.items():
                        if key in conv_text:
                            blood_group = val
                            break
                    if not blood_group:
                        blood_group = "O Positive"

                if chat_city and chat_city in CITY_COORDS:
                    ref_lat, ref_lon = CITY_COORDS[chat_city]
                    location_name = chat_city.title()
                else:
                    ref_lat, ref_lon = 17.3850, 78.4867
                    location_name = "Hyderabad"

                if no_user_id:
                    raw_user_id = f"UNREG-{_uuid.uuid4().hex[:12].upper()}"
                    logger.info(f"Unregistered patient — assigned temp ID: {raw_user_id}")

            request_id = f"BWR-{int(time.time())}"
            now        = datetime.now(timezone.utc).isoformat()

            request_data = {
                "request_id":          request_id,
                "blood_group":         blood_group,
                "hospital_name":       location_name,
                "city":                location_name,
                "urgency":             urgency,
                "status":              "SEARCHING",
                "status_history":      [{"status": "SEARCHING", "timestamp": now}],
                "escalation_level":    1,
                "ranked_donor_ids":    [],
                "contacted_donor_ids": [],
                "confirmed_donor_id":  None,
                "created_at":          now,
                "updated_at":          now,
                "outreach_message":    "",
                "patient_user_id":     raw_user_id,
            }
            if ref_lat:
                request_data["latitude"]  = str(ref_lat)
                request_data["longitude"] = str(ref_lon)

            db_helpers.create_request(request_data)
            logger.info(f"Request created: {request_id} for blood_group={blood_group} urgency={urgency}")

            # Rank donors and trigger WhatsApp/SMS outreach
            all_eligible = db_helpers.get_eligible_donors()
            try:
                ranked = matching.prioritise_donors(
                    all_eligible, blood_group,
                    declined_donor_ids=[],
                    ref_lat=ref_lat, ref_lon=ref_lon,
                )
            except Exception:
                ranked = matching.rank_donors(all_eligible, blood_group, exact_only=False)

            top10_ids = [d["donor_id"] for d in ranked[:10] if d.get("donor_id")]
            db_helpers.update_request(request_id, {"ranked_donor_ids": top10_ids})
            request_data["ranked_donor_ids"]   = top10_ids
            request_data["declined_donor_ids"] = []

            # Run escalation — sends WhatsApp/SMS to top donors
            escalation_module.run_escalation(request_data, all_eligible)
            logger.info(f"Escalation triggered for request {request_id}")

        except Exception as exc:
            logger.error(f"Failed to create/escalate request: {exc}", exc_info=True)
            request_id = None

    return response(200, {
        "response_text": response_text,
        "extracted_data": extracted,
        "request_id":     request_id,
    })


def handle_create_request(event: dict) -> dict:
    """POST /request"""
    body = _parse_body(event)
    blood_group   = body.get("blood_group", "").strip()
    hospital_name = body.get("hospital_name", "").strip()
    urgency       = body.get("urgency", "urgent").strip()
    city          = body.get("city", "").strip()
    donor_id      = body.get("donor_id", "").strip()   # specific donor from volunteer search

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
        "status":              "SEARCHING",
        "status_history":      [{"status": "SEARCHING", "timestamp": now}],
        "escalation_level":    1,
        "ranked_donor_ids":    [donor_id] if donor_id else [],
        "contacted_donor_ids": [],
        "confirmed_donor_id":  None,
        "created_at":          now,
        "updated_at":          now,
        "outreach_message":    "",
        "patient_user_id":     body.get("patient_user_id", ""),
    }

    try:
        db_helpers.create_request(request_data)
        eligible  = db_helpers.get_eligible_donors()
        ranked    = matching.rank_donors(eligible, blood_group, exact_only=True)
        top10_ids = [d["donor_id"] for d in ranked[:10]]
        db_helpers.update_request(request_id, {"ranked_donor_ids": top10_ids})
        request_data["ranked_donor_ids"] = top10_ids
        escalation_module.run_escalation(request_data, eligible)
    except Exception as exc:
        logger.error(f"handle_create_request error: {exc}", exc_info=True)
        return server_error()

    return response(201, {"request_id": request_id, "status": "SEARCHING"})


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


def handle_request_status(event: dict) -> dict:
    """
    POST /request/{id}/status
    Body: {"new_status": "DONOR_FOUND" | "ARRIVING" | "TRANSFUSING" | "COMPLETED"}

    Validates strict sequential chain: SEARCHING→DONOR_FOUND→ARRIVING→TRANSFUSING→COMPLETED
    """
    parts      = event.get("rawPath", "").split("/")
    request_id = parts[2] if len(parts) >= 4 else ""
    if not request_id:
        return bad_request("request_id is required in path")

    body       = _parse_body(event)
    new_status = body.get("new_status", "").strip().upper()

    if new_status not in STATUS_CHAIN:
        return bad_request(f"new_status must be one of: {', '.join(STATUS_CHAIN)}")

    try:
        req = db_helpers.get_request(request_id)
        if not req:
            return not_found(f"Request {request_id} not found")

        current = req.get("status", "SEARCHING")
        expected_next = _next_status(current)

        if new_status != expected_next:
            return bad_request(
                f"Invalid transition: {current} → {new_status}. "
                f"Expected next status: {expected_next}"
            )

        now     = datetime.now(timezone.utc).isoformat()
        history = list(req.get("status_history") or [])
        history.append({"status": new_status, "timestamp": now})

        updates = {
            "status":         new_status,
            "status_history": history,
        }
        if new_status == "COMPLETED":
            updates["completed_at"] = now

        db_helpers.update_request(request_id, updates)
        logger.info(f"Request {request_id}: {current} → {new_status}")
        return response(200, {
            "request_id":    request_id,
            "previous":      current,
            "status":        new_status,
            "status_history": history,
        })
    except Exception as exc:
        logger.error(f"handle_request_status error for {request_id}: {exc}", exc_info=True)
        return server_error()


def handle_confirm(event: dict) -> dict:
    """
    GET /confirm?request_id=X&donor_id=Y&action=yes|no
    On YES: transitions status from SEARCHING → DONOR_FOUND automatically.
    """
    params     = event.get("queryStringParameters") or {}
    request_id = params.get("request_id", "")
    donor_id   = params.get("donor_id", "")
    action     = params.get("action", "").lower()

    if not request_id or not donor_id or action not in ("yes", "no"):
        return bad_request("request_id, donor_id, and action (yes|no) are required")

    logger.info(f"Donor {donor_id} responded '{action}' for request {request_id}")

    try:
        try:
            db_helpers.record_donor_response(donor_id, donated=(action == "yes"))
        except Exception as exc:
            logger.warning(f"record_donor_response non-fatal error: {exc}")

        if action == "yes":
            req = db_helpers.get_request(request_id) or {}
            now     = datetime.now(timezone.utc).isoformat()
            history = list(req.get("status_history") or [])
            history.append({"status": "DONOR_FOUND", "timestamp": now})
            db_helpers.update_request(request_id, {
                "confirmed_donor_id": donor_id,
                "status":             "DONOR_FOUND",
                "status_history":     history,
            })
            return response(200, {
                "message":     "Thank you! Your confirmation has been recorded.",
                "hospital":    req.get("hospital_name", ""),
                "blood_group": req.get("blood_group", ""),
                "urgency":     req.get("urgency", ""),
                "request_id":  request_id,
                "confirmed":   True,
            })
        else:
            req      = db_helpers.get_request(request_id) or {}
            declined = list(set(req.get("declined_donor_ids", []) + [donor_id]))
            db_helpers.update_request(request_id, {"declined_donor_ids": declined})

            # Check if all contacted donors have now declined — if so escalate immediately
            contacted = set(req.get("contacted_donor_ids", []))
            if contacted and set(declined) >= contacted:
                logger.info(f"All contacted donors declined for {request_id} — triggering escalation")
                try:
                    updated_req = {**req, "declined_donor_ids": declined}
                    all_eligible = db_helpers.get_eligible_donors()
                    escalation_module.run_escalation(updated_req, all_eligible)
                except Exception as esc_exc:
                    logger.error(f"Auto-escalation after decline failed: {esc_exc}", exc_info=True)

            return response(200, {
                "message":    "Thank you for letting us know. We will contact another donor.",
                "confirmed":  False,
                "request_id": request_id,
            })
    except Exception as exc:
        logger.error(f"handle_confirm error: {exc}", exc_info=True)
        return server_error()


def handle_bridges_health(event: dict) -> dict:
    """
    GET /bridges/health

    Queries all patients (role=Patient, bridge_status=true).
    For each patient, gets their bridge donors and classifies GREEN/YELLOW/RED.
    Returns real patient data — no fake hospital names or BRG codes.
    """
    try:
        from boto3.dynamodb.conditions import Attr

        # Scan all patients
        patients = []
        scan_kwargs = {
            "FilterExpression": Attr("role").eq("Patient") & Attr("bridge_status").eq("true")
        }
        while True:
            resp = db_helpers.donors_table.scan(**scan_kwargs)
            patients.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

        logger.info(f"Found {len(patients)} patients with bridge_status=true")

        if not patients:
            # Fall back to legacy bridge table if no patients found
            bridges  = db_helpers.get_all_bridges()
            eligible = db_helpers.get_eligible_donors()
            summary  = bridge_health.get_bridge_health_summary(bridges, eligible)
            return response(200, summary)

        # For each patient, gather their bridge donors
        today = datetime.now()
        results = []

        for patient in patients:
            patient_id        = patient.get("user_id") or patient.get("donor_id", "")
            blood_group       = patient.get("blood_group", "")
            bridge_id         = patient.get("bridge_id", "")
            p_lat             = patient.get("latitude", "")
            p_lon             = patient.get("longitude", "")
            transfusion_str   = patient.get("expected_next_transfusion_date", "")
            freq              = patient.get("frequency_in_days", "")
            gender            = patient.get("gender", "")
            location_name     = db_helpers.get_location_name(p_lat, p_lon) if p_lat and p_lon else "Hyderabad Area"

            # Days until transfusion
            days_until = None
            urgency    = "SCHEDULED"
            if transfusion_str:
                try:
                    t_date     = datetime.strptime(str(transfusion_str)[:10], "%Y-%m-%d")
                    days_until = (t_date - today).days
                    urgency    = _urgency_label(days_until)
                except Exception:
                    pass

            # Get bridge donors for this patient
            bridge_donors = []
            if bridge_id:
                donor_scan_kwargs = {
                    "FilterExpression": (
                        Attr("bridge_id").eq(bridge_id)
                        & Attr("bridge_status").eq("true")
                        & Attr("role").eq("Bridge Donor")
                    )
                }
                while True:
                    dr = db_helpers.donors_table.scan(**donor_scan_kwargs)
                    bridge_donors.extend(dr.get("Items", []))
                    if "LastEvaluatedKey" not in dr:
                        break
                    donor_scan_kwargs["ExclusiveStartKey"] = dr["LastEvaluatedKey"]

            if not bridge_donors:
                continue

            # Classify each donor
            classified_donors = []
            for d in bridge_donors:
                cls = matching.classify_donor(d, transfusion_str)
                entry = matching.build_donor_entry(
                    d, cls,
                    patient_lat=float(p_lat) if p_lat else None,
                    patient_lon=float(p_lon) if p_lon else None,
                    patient_transfusion_date=transfusion_str,
                )
                classified_donors.append(entry)

            # Sort donors: GREEN first, then YELLOW, then RED
            order = {"GREEN": 0, "YELLOW": 1, "RED": 2}
            classified_donors.sort(key=lambda d: order.get(d["classification"], 3))

            # Bridge-level classification: best donor wins
            statuses = [d["classification"] for d in classified_donors]
            if "GREEN" in statuses:
                bridge_classification = "GREEN"
            elif "YELLOW" in statuses:
                bridge_classification = "YELLOW"
            else:
                bridge_classification = "RED"

            results.append({
                "patient_user_id":               patient_id,
                "bridge_id":                     bridge_id,
                "blood_group":                   blood_group,
                "gender":                        gender,
                "location_name":                 location_name,
                "expected_next_transfusion_date": transfusion_str,
                "days_until_transfusion":        days_until,
                "urgency":                       urgency,
                "frequency_in_days":             freq,
                "health_status":                 bridge_classification,
                "donors":                        classified_donors,
                "donor_count":                   len(classified_donors),
            })

        # Sort: RED first, then YELLOW, then GREEN; within each group sort by days_until asc
        status_order = {"RED": 0, "YELLOW": 1, "GREEN": 2}
        results.sort(key=lambda b: (
            status_order.get(b.get("health_status"), 3),
            b.get("days_until_transfusion") if b.get("days_until_transfusion") is not None else 9999,
        ))

        counts = {s: sum(1 for b in results if b.get("health_status") == s) for s in ["GREEN", "YELLOW", "RED"]}
        logger.info(f"Bridge health summary: {counts}")
        return response(200, results)

    except Exception as exc:
        logger.error(f"handle_bridges_health error: {exc}", exc_info=True)
        return server_error()


def handle_bridges_approve(event: dict) -> dict:
    """POST /bridges/approve"""
    body                 = _parse_body(event)
    bridge_id            = body.get("bridge_id", "").strip()
    replacement_donor_id = body.get("replacement_donor_id", "").strip()

    if not bridge_id or not replacement_donor_id:
        return bad_request("bridge_id and replacement_donor_id are required")

    try:
        db_helpers.update_bridge(bridge_id, {
            "donor_id":        replacement_donor_id,
            "approved_at":     datetime.now(timezone.utc).isoformat(),
            "approval_status": "approved",
        })
        return response(200, {"success": True, "bridge_id": bridge_id, "replacement_donor_id": replacement_donor_id})
    except Exception as exc:
        logger.error(f"handle_bridges_approve error: {exc}", exc_info=True)
        return server_error()


def handle_active_requests(event: dict) -> dict:
    """GET /requests/active — includes status and status_history"""
    try:
        items = db_helpers.get_active_requests()
        return response(200, items)
    except Exception as exc:
        logger.error(f"handle_active_requests error: {exc}", exc_info=True)
        return server_error()


def handle_patient_search(event: dict) -> dict:
    """
    GET /patient/search?user_id=X

    Volunteer patient lookup. Finds patient by user_id where role=Patient.
    Returns bridge donors (GREEN/YELLOW/RED) and top 5 emergency donors (BLUE).
    Never returns raw lat/lon — all locations resolved to names.
    """
    params  = event.get("queryStringParameters") or {}
    raw_id  = params.get("user_id", "").strip()

    if not raw_id:
        return bad_request("user_id query parameter is required")

    # Use the raw ID as-is — stored in DynamoDB with full \xNN prefix from CSV
    user_id = raw_id

    logger.info(f"Patient search user_id={user_id[:30]}...")

    try:
        from boto3.dynamodb.conditions import Attr

        # Try direct key lookup first (raw ID as stored from CSV)
        resp    = db_helpers.donors_table.get_item(Key={"donor_id": user_id})
        patient = resp.get("Item")

        # Scan by user_id field if not found by donor_id
        if not patient:
            scan_resp = db_helpers.donors_table.scan(
                FilterExpression=Attr("user_id").eq(user_id) & Attr("role").eq("Patient"),
                Limit=1,
            )
            items   = scan_resp.get("Items", [])
            patient = items[0] if items else None

        if not patient:
            return not_found("User ID not found in the database")

        if patient.get("role") != "Patient":
            actual_role = patient.get("role", "Unknown")
            return response(400, {
                "error": f"This user is a {actual_role}, not a Patient. The volunteer search only works for thalassemia patients.",
                "role": actual_role,
                "user_id": user_id,
            })

        blood_group     = patient.get("blood_group", "")
        bridge_id       = patient.get("bridge_id", "")
        p_lat_str       = patient.get("latitude", "")
        p_lon_str       = patient.get("longitude", "")
        transfusion_str = patient.get("expected_next_transfusion_date", "")
        gender          = patient.get("gender", "")
        freq            = patient.get("frequency_in_days", "")

        p_lat = float(p_lat_str) if p_lat_str else None
        p_lon = float(p_lon_str) if p_lon_str else None
        patient_location = db_helpers.get_location_name(p_lat_str, p_lon_str) if p_lat_str and p_lon_str else "Hyderabad Area"

        # Days until transfusion / urgency
        today      = datetime.now()
        days_until = None
        urgency    = "SCHEDULED"
        if transfusion_str:
            try:
                t_date     = datetime.strptime(str(transfusion_str)[:10], "%Y-%m-%d")
                days_until = (t_date - today).days
                urgency    = _urgency_label(days_until)
            except Exception:
                pass

        # ── Bridge donors (GREEN / YELLOW / RED) ─────────────────────────
        bridge_donors_raw = []
        if bridge_id:
            scan_kwargs = {
                "FilterExpression": (
                    Attr("bridge_id").eq(bridge_id)
                    & Attr("bridge_status").eq("true")
                    & Attr("role").eq("Bridge Donor")
                )
            }
            while True:
                dr = db_helpers.donors_table.scan(**scan_kwargs)
                bridge_donors_raw.extend(dr.get("Items", []))
                if "LastEvaluatedKey" not in dr:
                    break
                scan_kwargs["ExclusiveStartKey"] = dr["LastEvaluatedKey"]

        green_donors  = []
        yellow_donors = []
        red_donors    = []

        for d in bridge_donors_raw:
            cls   = matching.classify_donor(d, transfusion_str)
            entry = matching.build_donor_entry(d, cls, p_lat, p_lon, transfusion_str)
            if cls == "GREEN":
                green_donors.append(entry)
            elif cls == "YELLOW":
                yellow_donors.append(entry)
            else:
                red_donors.append(entry)

        # ── Emergency donors (BLUE) ───────────────────────────────────────
        blue_donors = []
        if p_lat and p_lon:
            all_donors   = db_helpers.get_eligible_donors()
            emergency_raw = matching.get_emergency_donors_near(all_donors, blood_group, p_lat, p_lon, limit=5)
            for d in emergency_raw:
                entry = matching.build_donor_entry(d, "BLUE", p_lat, p_lon, transfusion_str)
                blue_donors.append(entry)

        # ── Smart alert ───────────────────────────────────────────────────
        if green_donors:
            alert = {"level": "green",  "message": "Ready donor available — contact green donors first"}
        elif yellow_donors:
            alert = {"level": "yellow", "message": "No ready donors — contact yellow donors"}
        elif red_donors:
            alert = {"level": "orange", "message": "Bridge donors all inactive — urgent action needed"}
        else:
            alert = {"level": "blue",   "message": "Bridge unavailable — using emergency donor pool"}

        return response(200, {
            "patient": {
                "user_id":                       user_id,
                "blood_group":                   blood_group,
                "gender":                        gender,
                "location_name":                 patient_location,
                "expected_next_transfusion_date": transfusion_str,
                "days_until_transfusion":        days_until,
                "urgency":                       urgency,
                "frequency_in_days":             freq,
            },
            "alert":          alert,
            "green_donors":   green_donors,
            "yellow_donors":  yellow_donors,
            "red_donors":     red_donors,
            "blue_donors":    blue_donors,
        })

    except Exception as exc:
        logger.error(f"handle_patient_search error: {exc}", exc_info=True)
        return server_error()


def handle_donor_response(event: dict) -> dict:
    """
    POST /donor/response
    Body: {
        "request_id": "...",
        "donor_id":   "...",
        "answers": {
            "can_donate":    "Yes, I am available",
            "location":      "Banjara Hills, Hyderabad",
            "transport":     "I will come on my own",
            "arrival_time":  "Within 30 minutes"
        }
    }
    Stores donor answers on the request record.
    The Pipeline dashboard reads these from request.donor_response.
    """
    body       = _parse_body(event)
    request_id = body.get("request_id", "").strip()
    donor_id   = body.get("donor_id", "").strip()
    answers    = body.get("answers", {})

    if not request_id or not donor_id:
        return bad_request("request_id and donor_id are required")

    try:
        req = db_helpers.get_request(request_id)
        if not req:
            return not_found(f"Request {request_id} not found")

        now = datetime.now(timezone.utc).isoformat()
        donor_response = {
            "donor_id":     donor_id,
            "submitted_at": now,
            "can_donate":   answers.get("can_donate",   ""),
            "location":     answers.get("location",     ""),
            "transport":    answers.get("transport",    ""),
            "arrival_time": answers.get("arrival_time", ""),
        }

        # Derive needs_pickup flag
        donor_response["needs_pickup"] = "pickup" in answers.get("transport", "").lower()

        db_helpers.update_request(request_id, {"donor_response": donor_response})
        logger.info(f"Donor {donor_id} response saved for request {request_id}: ETA={answers.get('arrival_time')}")

        return response(200, {"success": True, "donor_response": donor_response})

    except Exception as exc:
        logger.error(f"handle_donor_response error for {request_id}: {exc}", exc_info=True)
        return server_error()


def handle_nightly_run(event: dict) -> dict:
    """POST /nightly/run"""
    logger.info("Nightly run triggered")
    processed = 0
    failed    = 0

    try:
        active_requests = db_helpers.get_active_requests()
        eligible        = db_helpers.get_eligible_donors()

        for req in active_requests:
            request_id = req.get("request_id", "?")
            if req.get("status") in ("COMPLETED", "CONFIRMED"):
                continue
            try:
                escalation_module.run_escalation(req, eligible)
                processed += 1
            except Exception as exc:
                logger.error(f"Nightly: escalation failed for {request_id}: {exc}")
                failed += 1

        return response(200, {"processed": processed, "failed": failed})
    except Exception as exc:
        logger.error(f"handle_nightly_run error: {exc}", exc_info=True)
        return server_error()


def handle_escalate(event: dict) -> dict:
    """POST /escalate/{request_id}"""
    request_id = event.get("rawPath", "").split("/")[-1]
    if not request_id:
        return bad_request("request_id is required in path")

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
