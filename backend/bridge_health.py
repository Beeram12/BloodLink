import json
import logging
from datetime import datetime, timezone

from matching import rank_donors

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
# Classification thresholds (days)
# ---------------------------------------------------------------------------

RED_THRESHOLD    = 7   # < 7 days → RED
YELLOW_THRESHOLD = 30  # 7-30 days → YELLOW, > 30 days → GREEN

STATUS_ORDER = {"RED": 0, "YELLOW": 1, "GREEN": 2}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _days_until(date_str: str) -> int | None:
    """Return days from today until the given date string. None if unparseable."""
    try:
        target = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        delta  = (target - datetime.now()).days
        return delta
    except Exception:
        return None

def _safe_float(value) -> float:
    try:
        return float(value) if value else 0.0
    except (TypeError, ValueError):
        return 0.0

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_bridge(bridge: dict) -> str:
    """
    Classify a bridge record as GREEN, YELLOW, or RED.

    RED (any one condition):
      - eligibility_status != 'eligible'
      - user_donation_active_status != 'Active'
      - next_transfusion_date within < 7 days (or unparseable)

    YELLOW (any one condition, if not RED):
      - next_transfusion_date 7-30 days away
      - calls_to_donations_ratio > 2

    GREEN: eligible, active, transfusion > 30 days away, ratio <= 2
    """
    bridge_id = bridge.get("bridge_id", "unknown")

    eligibility = bridge.get("eligibility_status", "")
    active_status = bridge.get("user_donation_active_status", "")

    if eligibility != "eligible" or active_status != "Active":
        logger.info(f"Bridge {bridge_id} → RED (ineligible or inactive)")
        return "RED"

    days = _days_until(bridge.get("next_transfusion_date", ""))
    if days is None or days < RED_THRESHOLD:
        logger.info(f"Bridge {bridge_id} → RED (transfusion in {days} days)")
        return "RED"

    calls_ratio = _safe_float(bridge.get("calls_to_donations_ratio", ""))

    if days < YELLOW_THRESHOLD or calls_ratio > 2:
        logger.info(
            f"Bridge {bridge_id} → YELLOW (days={days}, ratio={calls_ratio})"
        )
        return "YELLOW"

    logger.info(f"Bridge {bridge_id} → GREEN")
    return "GREEN"


def get_bridge_health_summary(
    bridges: list[dict],
    eligible_donors: list[dict],
) -> list[dict]:
    """
    Classify each bridge and enrich RED bridges with top-3 replacement candidates.
    Returns list sorted RED → YELLOW → GREEN.
    """
    logger.info(f"Classifying {len(bridges)} bridges")
    enriched = []

    for bridge in bridges:
        try:
            status = classify_bridge(bridge)
            enriched_bridge = dict(bridge)
            enriched_bridge["health_status"] = status

            if status == "RED":
                blood_group = bridge.get("blood_group", "")
                if blood_group:
                    candidates = rank_donors(
                        eligible_donors,
                        blood_group,
                        exact_only=False,
                    )[:3]
                    # Strip internal _score before returning to caller
                    enriched_bridge["replacement_candidates"] = [
                        {k: v for k, v in c.items() if k != "_score"}
                        for c in candidates
                    ]
                    logger.info(
                        f"Bridge {bridge.get('bridge_id')} has "
                        f"{len(candidates)} replacement candidates"
                    )
                else:
                    enriched_bridge["replacement_candidates"] = []
                    logger.warning(
                        f"Bridge {bridge.get('bridge_id')} is RED but has no blood_group"
                    )

            enriched.append(enriched_bridge)

        except Exception as exc:
            bridge_id = bridge.get("bridge_id", "unknown")
            logger.error(f"Failed to classify bridge {bridge_id}: {exc}")
            # Include bridge with unknown status rather than dropping it
            enriched.append({**bridge, "health_status": "UNKNOWN", "replacement_candidates": []})

    enriched.sort(key=lambda b: STATUS_ORDER.get(b.get("health_status", "UNKNOWN"), 3))

    counts = {s: sum(1 for b in enriched if b.get("health_status") == s) for s in ["GREEN", "YELLOW", "RED"]}
    logger.info(f"Bridge summary: {counts}")

    return enriched
