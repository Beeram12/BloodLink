"""
seed_data.py — Load donor CSV into DynamoDB and seed demo request/bridge records.

Usage (run locally with valid AWS credentials):
    python seed_data.py seed-demo
    python seed_data.py load-csv path/to/donors.csv
"""

import csv
import json
import logging
import sys
import time
from datetime import datetime, timezone, timedelta

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
# DynamoDB setup
# ---------------------------------------------------------------------------

import os

REGION         = os.environ.get("AWS_REGION", "eu-north-1")
DONORS_TABLE   = os.environ.get("DONORS_TABLE",   "donors")
REQUESTS_TABLE = os.environ.get("REQUESTS_TABLE", "requests")
BRIDGES_TABLE  = os.environ.get("BRIDGES_TABLE",  "bridges")

dynamodb       = boto3.resource("dynamodb", region_name=REGION)
donors_table   = dynamodb.Table(DONORS_TABLE)
requests_table = dynamodb.Table(REQUESTS_TABLE)
bridges_table  = dynamodb.Table(BRIDGES_TABLE)

# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def load_csv_donors(csv_path: str) -> dict:
    """
    Read a donor CSV and bulk-insert into DynamoDB.
    All field values are stored as strings (as they arrive from the CSV).
    Returns {"loaded": N, "errors": M}.
    """
    loaded = 0
    errors = 0
    logger.info(f"Loading donors from CSV: {csv_path}")

    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            with donors_table.batch_writer() as batch:
                for row in reader:
                    # Strip whitespace; remove \x27/\x96 escape prefixes from hex-escaped values
                    clean = {}
                    for k, v in row.items():
                        key = k.strip()
                        val = (v.strip() if v else "")
                        # Remove leading backslash-hex escape artifacts (e.g. \x27...)
                        if val.startswith("\\x") and len(val) > 4:
                            val = val[4:]
                        clean[key] = val

                    # Dataset uses user_id — map to donor_id for our schema
                    if not clean.get("donor_id"):
                        uid = clean.get("user_id", "")
                        if uid:
                            clean["donor_id"] = uid
                        else:
                            logger.warning(f"Row missing user_id/donor_id — skipping")
                            errors += 1
                            continue

                    try:
                        batch.put_item(Item=clean)
                        loaded += 1
                        if loaded % 500 == 0:
                            logger.info(f"  ... {loaded} donors loaded so far")
                    except ClientError as exc:
                        logger.error(f"Failed to write donor {clean.get('donor_id')}: {exc.response['Error']['Message']}")
                        errors += 1
    except FileNotFoundError:
        logger.error(f"CSV file not found: {csv_path}")
        return {"loaded": 0, "errors": 1}
    except Exception as exc:
        logger.error(f"CSV load failed: {exc}")
        return {"loaded": loaded, "errors": errors + 1}

    logger.info(f"CSV load complete — loaded: {loaded}, errors: {errors}")
    return {"loaded": loaded, "errors": errors}

# ---------------------------------------------------------------------------
# Demo data seeder
# ---------------------------------------------------------------------------

def _isodate(days_offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days_offset)).strftime("%Y-%m-%d")

def _isotime(seconds_offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds_offset)).isoformat()


REQUEST_RECORDS = [
    {
        "request_id":          "BWR-DEMO-0001",
        "blood_group":         "O Positive",
        "hospital_name":       "Yashoda Hospital",
        "city":                "Hyderabad",
        "urgency":             "critical",
        "status":              "CONFIRMED",
        "escalation_level":    1,
        "ranked_donor_ids":    ["DEMO-DONOR-001", "DEMO-DONOR-002"],
        "contacted_donor_ids": ["DEMO-DONOR-001"],
        "confirmed_donor_id":  "DEMO-DONOR-001",
        "created_at":          _isotime(-3600),
        "updated_at":          _isotime(-1800),
        "outreach_message":    "A critical patient at Yashoda Hospital needs O+ blood urgently.",
    },
    {
        "request_id":          "BWR-DEMO-0002",
        "blood_group":         "B Positive",
        "hospital_name":       "KIMS Hospital",
        "city":                "Hyderabad",
        "urgency":             "urgent",
        "status":              "PENDING",
        "escalation_level":    2,
        "ranked_donor_ids":    ["DEMO-DONOR-003", "DEMO-DONOR-004", "DEMO-DONOR-005"],
        "contacted_donor_ids": ["DEMO-DONOR-003", "DEMO-DONOR-004"],
        "confirmed_donor_id":  None,
        "created_at":          _isotime(-7200),
        "updated_at":          _isotime(-3600),
        "outreach_message":    "Urgent: A patient at KIMS Hospital needs B+ blood.",
    },
    {
        "request_id":          "BWR-DEMO-0003",
        "blood_group":         "O Negative",
        "hospital_name":       "Apollo Hospital",
        "city":                "Hyderabad",
        "urgency":             "critical",
        "status":              "NEEDS_HUMAN",
        "escalation_level":    4,
        "ranked_donor_ids":    ["DEMO-DONOR-006"],
        "contacted_donor_ids": ["DEMO-DONOR-006"],
        "confirmed_donor_id":  None,
        "created_at":          _isotime(-14400),
        "updated_at":          _isotime(-600),
        "outreach_message":    "Critical: O- blood needed at Apollo Hospital — all options exhausted.",
    },
]

BRIDGE_RECORDS = [
    # 5 GREEN — eligible, active, transfusion > 30 days away, normal ratio
    {"bridge_id": "BRG-001", "donor_id": "D-001", "hospital_name": "Yashoda Hospital",  "blood_group": "O Positive",  "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(60),  "calls_to_donations_ratio": "1.2"},
    {"bridge_id": "BRG-002", "donor_id": "D-002", "hospital_name": "Yashoda Hospital",  "blood_group": "A Positive",  "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(45),  "calls_to_donations_ratio": "0.8"},
    {"bridge_id": "BRG-003", "donor_id": "D-003", "hospital_name": "KIMS Hospital",     "blood_group": "B Positive",  "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(90),  "calls_to_donations_ratio": "1.0"},
    {"bridge_id": "BRG-004", "donor_id": "D-004", "hospital_name": "Apollo Hospital",   "blood_group": "AB Positive", "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(55),  "calls_to_donations_ratio": "1.4"},
    {"bridge_id": "BRG-005", "donor_id": "D-005", "hospital_name": "Care Hospital",     "blood_group": "O Negative",  "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(40),  "calls_to_donations_ratio": "0.5"},
    # 3 YELLOW — 7-30 day window or high ratio
    {"bridge_id": "BRG-006", "donor_id": "D-006", "hospital_name": "NIMS Hospital",     "blood_group": "A Negative",  "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(15),  "calls_to_donations_ratio": "1.6"},
    {"bridge_id": "BRG-007", "donor_id": "D-007", "hospital_name": "Medicover Hospital","blood_group": "B Negative",  "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(22),  "calls_to_donations_ratio": "2.5"},
    {"bridge_id": "BRG-008", "donor_id": "D-008", "hospital_name": "Yashoda Hospital",  "blood_group": "O Positive",  "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(8),   "calls_to_donations_ratio": "1.1"},
    # 2 RED — ineligible or imminent transfusion
    {"bridge_id": "BRG-009", "donor_id": "D-009", "hospital_name": "Apollo Hospital",   "blood_group": "O Negative",  "eligibility_status": "ineligible","user_donation_active_status": "Inactive","next_transfusion_date": _isodate(3),  "calls_to_donations_ratio": "3.1"},
    {"bridge_id": "BRG-010", "donor_id": "D-010", "hospital_name": "KIMS Hospital",     "blood_group": "AB Negative", "eligibility_status": "eligible", "user_donation_active_status": "Active", "next_transfusion_date": _isodate(2),   "calls_to_donations_ratio": "1.8"},
]


def seed_demo_data() -> dict:
    """Insert demo request and bridge records into DynamoDB."""
    req_seeded = 0
    brg_seeded = 0

    logger.info("Seeding demo request records")
    for rec in REQUEST_RECORDS:
        try:
            requests_table.put_item(Item=rec)
            req_seeded += 1
            logger.info(f"  Seeded request {rec['request_id']}")
        except ClientError as exc:
            logger.error(f"  Failed to seed request {rec['request_id']}: {exc.response['Error']['Message']}")

    logger.info("Seeding demo bridge records")
    for rec in BRIDGE_RECORDS:
        try:
            bridges_table.put_item(Item=rec)
            brg_seeded += 1
            logger.info(f"  Seeded bridge {rec['bridge_id']}")
        except ClientError as exc:
            logger.error(f"  Failed to seed bridge {rec['bridge_id']}: {exc.response['Error']['Message']}")

    result = {"requests_seeded": req_seeded, "bridges_seeded": brg_seeded}
    logger.info(f"Seed complete: {result}")
    return result


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python seed_data.py [seed-demo | load-csv <path>]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "seed-demo":
        result = seed_demo_data()
        print(json.dumps(result, indent=2))

    elif command == "load-csv":
        if len(sys.argv) < 3:
            print("Usage: python seed_data.py load-csv <csv_path>")
            sys.exit(1)
        result = load_csv_donors(sys.argv[2])
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
