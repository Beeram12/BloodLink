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

REGION = os.environ.get("AWS_REGION", "eu-north-1")
DONORS_TABLE   = os.environ.get("DONORS_TABLE",   "donors")
REQUESTS_TABLE = os.environ.get("REQUESTS_TABLE", "requests")
BRIDGES_TABLE  = os.environ.get("BRIDGES_TABLE",  "bridges")

dynamodb = boto3.resource("dynamodb", region_name=REGION)
donors_table   = dynamodb.Table(DONORS_TABLE)
requests_table = dynamodb.Table(REQUESTS_TABLE)
bridges_table  = dynamodb.Table(BRIDGES_TABLE)

# ---------------------------------------------------------------------------
# Donors
# ---------------------------------------------------------------------------

def get_eligible_donors() -> list:
    """Paginated scan for donors that are eligible and actively donating."""
    logger.info("Scanning for eligible donors")
    results = []
    scan_kwargs = {
        "FilterExpression": (
            Attr("eligibility_status").eq("eligible")
            & Attr("user_donation_active_status").eq("Active")
        )
    }
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
