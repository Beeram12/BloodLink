import os
import logging
import json
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

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
# DynamoDB resource
# ---------------------------------------------------------------------------

REGION          = os.environ.get("AWS_REGION", "eu-north-1")
DONORS_TABLE    = os.environ.get("DONORS_TABLE",    "donors")
REQUESTS_TABLE  = os.environ.get("REQUESTS_TABLE",  "requests")
BRIDGES_TABLE   = os.environ.get("BRIDGES_TABLE",   "bridges")
LOCATIONS_TABLE = os.environ.get("LOCATIONS_TABLE", "locations")

dynamodb        = boto3.resource("dynamodb", region_name=REGION)
donors_table    = dynamodb.Table(DONORS_TABLE)
requests_table  = dynamodb.Table(REQUESTS_TABLE)
bridges_table   = dynamodb.Table(BRIDGES_TABLE)
locations_table = dynamodb.Table(LOCATIONS_TABLE)

# In-memory cache so repeated Lambda calls don't re-read DynamoDB
_location_cache: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Donors
# ---------------------------------------------------------------------------

def get_eligible_donors(city: str = None) -> list:
    """Paginated scan for donors that are eligible and actively donating.
    Optionally filter by city for location-aware matching.
    """
    logger.info(f"Scanning for eligible donors" + (f" in city={city}" if city else ""))
    results = []
    filter_expr = (
        Attr("eligibility_status").eq("eligible")
        & Attr("user_donation_active_status").eq("Active")
    )
    if city:
        filter_expr = filter_expr & Attr("city").eq(city)
    scan_kwargs = {"FilterExpression": filter_expr}
    try:
        while True:
            resp = donors_table.scan(**scan_kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        logger.info(f"Found {len(results)} eligible donors")
        return results
    except ClientError as exc:
        logger.error(f"get_eligible_donors failed: {exc.response['Error']['Message']}")
        raise


def get_bridges_for_patient(blood_group: str, city: str = None) -> list:
    """Scan bridges matching blood group, optionally filtered by city.
    Returns sorted GREEN → YELLOW → RED so callers always see best first.
    """
    from bridge_health import classify_bridge  # avoid circular import at module level
    logger.info(f"Searching bridges for blood_group={blood_group} city={city}")
    results = []
    filter_expr = Attr("blood_group").eq(blood_group)
    if city:
        filter_expr = filter_expr & Attr("hospital_name").contains(city)
    scan_kwargs = {"FilterExpression": filter_expr}
    try:
        while True:
            resp = bridges_table.scan(**scan_kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    except ClientError as exc:
        logger.error(f"get_bridges_for_patient failed: {exc.response['Error']['Message']}")
        raise

    order = {"GREEN": 0, "YELLOW": 1, "RED": 2}
    for b in results:
        b["health_status"] = classify_bridge(b)
    results.sort(key=lambda b: order.get(b["health_status"], 3))
    logger.info(f"Found {len(results)} matching bridges")
    return results


def record_donor_response(donor_id: str, donated: bool) -> dict:
    """Update donor stats after a YES/NO response — self-optimisation loop."""
    logger.info(f"Recording donor response donor_id={donor_id} donated={donated}")
    try:
        item = donors_table.get_item(Key={"donor_id": donor_id}).get("Item", {})
        total_calls = int(item.get("total_calls") or item.get("donations_till_date") or 0) + 1
        donations   = int(item.get("donations_till_date", 0) or 0)
        if donated:
            donations += 1
        ratio = round(total_calls / donations, 2) if donations > 0 else float(total_calls)
        updates = {
            "total_calls":              str(total_calls),
            "calls_to_donations_ratio": str(ratio),
            "last_contacted_date":      datetime.now(timezone.utc).date().isoformat(),
        }
        if donated:
            updates["donations_till_date"] = str(donations)
            updates["last_donation_date"]  = datetime.now(timezone.utc).date().isoformat()
        result = _dynamic_update(donors_table, {"donor_id": donor_id}, updates)
        logger.info(f"Donor {donor_id} stats updated: calls={total_calls} donations={donations} ratio={ratio}")
        return result
    except ClientError as exc:
        logger.error(f"record_donor_response failed for {donor_id}: {exc.response['Error']['Message']}")
        raise


def lookup_donor_by_phone(phone: str) -> dict | None:
    """Scan donors table for a matching phone number. Returns first match or None."""
    logger.info(f"Looking up donor by phone (last 4 digits: ...{phone[-4:]})")
    # Normalise: strip spaces, dashes, leading +91 / 0
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    if cleaned.startswith("+91"):
        cleaned = cleaned[3:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("0")

    results = []
    try:
        for field in ("phone_number", "phone", "mobile", "contact_number"):
            resp = donors_table.scan(
                FilterExpression=Attr(field).contains(cleaned),
                Limit=5,
            )
            results.extend(resp.get("Items", []))
            if results:
                break
    except ClientError as exc:
        logger.warning(f"lookup_donor_by_phone scan error: {exc.response['Error']['Message']}")
    if results:
        logger.info(f"Phone lookup found donor_id={results[0].get('donor_id')}")
        return results[0]
    logger.info("Phone lookup: no match found")
    return None


def get_donors_by_ids(ids: list) -> list:
    """Batch-get donors by ID, chunked to respect the 100-item DynamoDB limit."""
    if not ids:
        return []
    logger.info(f"Batch-fetching {len(ids)} donors")
    results = []
    try:
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            keys = [{"donor_id": did} for did in chunk]
            resp = dynamodb.batch_get_item(
                RequestItems={DONORS_TABLE: {"Keys": keys}}
            )
            results.extend(resp.get("Responses", {}).get(DONORS_TABLE, []))
        return results
    except ClientError as exc:
        logger.error(f"get_donors_by_ids failed: {exc.response['Error']['Message']}")
        raise

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

def create_request(data: dict) -> dict:
    """Insert a new request record. data must include request_id."""
    request_id = data.get("request_id", "UNKNOWN")
    logger.info(f"Creating request {request_id}")
    try:
        requests_table.put_item(Item=data)
        logger.info(f"Request {request_id} created successfully")
        return data
    except ClientError as exc:
        logger.error(f"create_request {request_id} failed: {exc.response['Error']['Message']}")
        raise


def get_request(request_id: str) -> dict | None:
    """Fetch a single request by ID. Returns None if not found."""
    logger.info(f"Fetching request {request_id}")
    try:
        resp = requests_table.get_item(Key={"request_id": request_id})
        item = resp.get("Item")
        if item is None:
            logger.warning(f"Request {request_id} not found")
        return item
    except ClientError as exc:
        logger.error(f"get_request {request_id} failed: {exc.response['Error']['Message']}")
        raise


def update_request(request_id: str, updates: dict) -> dict:
    """Dynamically update a request record. Always stamps updated_at."""
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    logger.info(f"Updating request {request_id} fields: {list(updates.keys())}")
    return _dynamic_update(requests_table, {"request_id": request_id}, updates)


def get_active_requests() -> list:
    """Paginated scan for requests that are not yet CONFIRMED."""
    logger.info("Scanning for active (non-CONFIRMED) requests")
    results = []
    scan_kwargs = {
        "FilterExpression": Attr("status").ne("CONFIRMED")
    }
    try:
        while True:
            resp = requests_table.scan(**scan_kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        logger.info(f"Found {len(results)} active requests")
        return results
    except ClientError as exc:
        logger.error(f"get_active_requests failed: {exc.response['Error']['Message']}")
        raise

# ---------------------------------------------------------------------------
# Bridges
# ---------------------------------------------------------------------------

def get_all_bridges() -> list:
    """Full scan of the bridges table (classification done in bridge_health.py)."""
    logger.info("Scanning all bridges")
    results = []
    try:
        while True:
            resp = bridges_table.scan() if not results else bridges_table.scan(
                ExclusiveStartKey=resp.get("LastEvaluatedKey")  # type: ignore[name-defined]
            )
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
        logger.info(f"Found {len(results)} bridges")
        return results
    except ClientError as exc:
        logger.error(f"get_all_bridges failed: {exc.response['Error']['Message']}")
        raise


def get_location_name(latitude: str, longitude: str) -> str:
    """
    Look up pre-geocoded area name from the locations table.
    Falls back to 'Hyderabad Area' if not found.
    Caches results in Lambda memory for the lifetime of the execution environment.
    """
    key = f"{latitude}_{longitude}"
    if key in _location_cache:
        return _location_cache[key]
    try:
        resp = locations_table.get_item(Key={"coord_key": key})
        name = resp.get("Item", {}).get("location_name", "Hyderabad Area")
    except ClientError as exc:
        logger.warning(f"get_location_name failed for {key}: {exc.response['Error']['Message']}")
        name = "Hyderabad Area"
    _location_cache[key] = name
    return name


def update_bridge(bridge_id: str, updates: dict) -> dict:
    """Dynamically update a bridge record."""
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    logger.info(f"Updating bridge {bridge_id} fields: {list(updates.keys())}")
    return _dynamic_update(bridges_table, {"bridge_id": bridge_id}, updates)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dynamic_update(table, key: dict, updates: dict) -> dict:
    """
    Build a dynamic UpdateExpression from a flat updates dict.
    Uses ExpressionAttributeNames to safely handle DynamoDB reserved words.
    """
    expr_names = {}
    expr_values = {}
    set_parts = []

    for idx, (field, value) in enumerate(updates.items()):
        placeholder_name  = f"#f{idx}"
        placeholder_value = f":v{idx}"
        expr_names[placeholder_name]  = field
        expr_values[placeholder_value] = value
        set_parts.append(f"{placeholder_name} = {placeholder_value}")

    update_expr = "SET " + ", ".join(set_parts)

    try:
        resp = table.update_item(
            Key=key,
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="UPDATED_NEW",
        )
        return resp.get("Attributes", {})
    except ClientError as exc:
        logger.error(f"_dynamic_update on {table.name} key={key} failed: {exc.response['Error']['Message']}")
        raise
