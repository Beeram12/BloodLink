import json
import logging
from datetime import datetime, timezone

from matching import prioritise_donors

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

STATUS_ORDER = {"RED": 0, "YELLOW": 1, "GREEN": 2}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_date(donor: dict, field: str) -> datetime | None:
    val = donor.get(field, "")
    if not val:
        return None
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d")
    except Exception:
        return None

def _safe_float(value) -> float:
    try:
        return float(value) if value else 0.0
    except (TypeError, ValueError):
        return 0.0

# ---------------------------------------------------------------------------
# Donor-level classification (uses dataset fields directly)
# ---------------------------------------------------------------------------

def classify_bridge_donor(donor: dict) -> str:
    """
    Classify a single bridge donor record as GREEN, YELLOW, or RED.

    Uses 4 dataset fields:
      expected_next_transfusion_date — when patient needs blood next
      next_eligible_date             — when donor is allowed to donate next
      user_donation_active_status    — Active / Inactive
      calls_to_donations_ratio       — reliability indicator

    Decision tree:
      Inactive + transfusion ≤ 7d  → RED
      Inactive                     → YELLOW
      gap_days < 0 + ≤ 7d          → RED   (active but can't make it, urgent)
      gap_days < 0                 → YELLOW (active but won't make it, has time)
      ratio > 5 + ≤ 14d            → YELLOW (unreliable + approaching)
      days_until_transfusion < 0   → RED   (overdue)
      otherwise                    → GREEN
    """
    today = datetime.now()
    donor_id = donor.get("donor_id") or donor.get("user_id", "unknown")

    transfusion_date = _safe_date(donor, "expected_next_transfusion_date")
    next_eligible    = _safe_date(donor, "next_eligible_date")

    if not transfusion_date:
        logger.info(f"Donor {donor_id} → UNKNOWN (no transfusion date)")
        return "UNKNOWN"

    days_until_transfusion = (transfusion_date - today).days
    gap_days = (transfusion_date - next_eligible).days if next_eligible else 0
    is_active = donor.get("user_donation_active_status", "") == "Active"
    ratio     = _safe_float(donor.get("calls_to_donations_ratio", ""))

    if not is_active:
        status = "RED" if days_until_transfusion <= 7 else "YELLOW"
        logger.info(f"Donor {donor_id} → {status} (inactive, days={days_until_transfusion})")
        return status

    if gap_days < 0:
        status = "RED" if days_until_transfusion <= 7 else "YELLOW"
        logger.info(f"Donor {donor_id} → {status} (gap={gap_days}, days={days_until_transfusion})")
        return status

    if ratio > 5:
        status = "YELLOW" if days_until_transfusion <= 14 else "GREEN"
        logger.info(f"Donor {donor_id} → {status} (ratio={ratio}, days={days_until_transfusion})")
        return status

    if days_until_transfusion < 0:
        logger.info(f"Donor {donor_id} → RED (transfusion overdue by {-days_until_transfusion}d)")
        return "RED"

    logger.info(f"Donor {donor_id} → GREEN (gap={gap_days}, ratio={ratio})")
    return "GREEN"


# ---------------------------------------------------------------------------
# Bridge-level classification (best donor wins)
# ---------------------------------------------------------------------------

def classify_bridge(bridge_or_donor: dict) -> str:
    """
    Classify a single record. Accepts either:
    - A donor record (has expected_next_transfusion_date) → classify_bridge_donor
    - A legacy bridge summary record → use old simple thresholds as fallback
    A bridge is only as good as its best donor.
    """
    # If it looks like a full donor record use the new calculation
    if bridge_or_donor.get("expected_next_transfusion_date") or bridge_or_donor.get("next_eligible_date"):
        return classify_bridge_donor(bridge_or_donor)

    # Legacy bridge summary fallback (for seeded demo data)
    bridge_id   = bridge_or_donor.get("bridge_id", "unknown")
    eligibility = bridge_or_donor.get("eligibility_status", "")
    active      = bridge_or_donor.get("user_donation_active_status", "")

    if eligibility != "eligible" or active != "Active":
        return "RED"

    try:
        transfusion_field = (
            bridge_or_donor.get("next_transfusion_date") or
            bridge_or_donor.get("expected_next_transfusion_date") or ""
        )
        days = (datetime.strptime(str(transfusion_field)[:10], "%Y-%m-%d") - datetime.now()).days
    except Exception:
        days = 999

    ratio = _safe_float(bridge_or_donor.get("calls_to_donations_ratio", ""))
    if days < 7:
        return "RED"
    if days < 30 or ratio > 2:
        return "YELLOW"
    return "GREEN"


def classify_bridge_group(bridge_donors: list[dict]) -> str:
    """
    Classify a bridge as a whole based on its best donor.
    GREEN = at least one GREEN donor
    YELLOW = no GREEN but at least one YELLOW
    RED = all RED or UNKNOWN
    """
    statuses = [classify_bridge_donor(d) for d in bridge_donors]
    if "GREEN" in statuses:
        return "GREEN"
    if "YELLOW" in statuses:
        return "YELLOW"
    return "RED"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_bridge_health_summary(
    bridges: list[dict],
    eligible_donors: list[dict],
) -> list[dict]:
    """
    Classify each bridge record and enrich RED bridges with replacement candidates.
    Groups by bridge_id when multiple donor rows share a bridge.
    Returns list sorted RED → YELLOW → GREEN.
    """
    logger.info(f"Classifying {len(bridges)} bridge records")

    # Group by bridge_id to support multi-donor bridges
    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    for b in bridges:
        key = b.get("bridge_id") or b.get("donor_id", "unknown")
        grouped[key].append(b)

    enriched = []
    for _, donors in grouped.items():
        try:
            # Use group classification if multiple donors, single otherwise
            if len(donors) > 1:
                status = classify_bridge_group(donors)
            else:
                status = classify_bridge(donors[0])

            # Use first donor record as the summary row
            enriched_bridge = dict(donors[0])
            enriched_bridge["health_status"] = status
            enriched_bridge["donor_count"]   = len(donors)

            if status == "RED":
                blood_group = donors[0].get("blood_group") or donors[0].get("bridge_blood_group", "")
                if blood_group:
                    candidates = prioritise_donors(eligible_donors, blood_group)[:3]
                    enriched_bridge["replacement_candidates"] = [
                        {k: v for k, v in c.items() if not k.startswith("_")}
                        for c in candidates
                    ]
                else:
                    enriched_bridge["replacement_candidates"] = []

            enriched.append(enriched_bridge)

        except Exception as exc:
            logger.error(f"Failed to classify bridge {donors[0].get('bridge_id','unknown')}: {exc}")
            enriched.append({**donors[0], "health_status": "UNKNOWN", "replacement_candidates": []})

    enriched.sort(key=lambda b: STATUS_ORDER.get(b.get("health_status", "UNKNOWN"), 3))

    counts = {s: sum(1 for b in enriched if b.get("health_status") == s) for s in ["GREEN", "YELLOW", "RED"]}
    logger.info(f"Bridge summary: {counts}")
    return enriched
