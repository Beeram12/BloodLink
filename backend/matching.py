import json
import logging
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Logger (reuses same JSON formatter pattern as db_helpers)
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
# Blood compatibility: maps patient blood group → eligible donor blood groups
# ---------------------------------------------------------------------------

COMPATIBLE_DONORS: dict[str, list[str]] = {
    "O Negative":  ["O Negative"],
    "O Positive":  ["O Negative", "O Positive"],
    "A Negative":  ["O Negative", "A Negative"],
    "A Positive":  ["O Negative", "O Positive", "A Negative", "A Positive"],
    "B Negative":  ["O Negative", "B Negative"],
    "B Positive":  ["O Negative", "O Positive", "B Negative", "B Positive"],
    "AB Negative": ["O Negative", "A Negative", "B Negative", "AB Negative"],
    "AB Positive": [
        "O Negative", "O Positive",
        "A Negative", "A Positive",
        "B Negative", "B Positive",
        "AB Negative", "AB Positive",
    ],
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_int(value) -> int:
    try:
        return int(value) if value else 0
    except (TypeError, ValueError):
        return 0

def _safe_float(value) -> float:
    try:
        return float(value) if value else 0.0
    except (TypeError, ValueError):
        return 0.0

def _parse_date(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_compatible_blood_groups(required: str) -> list[str]:
    """Return donor blood groups that can donate to a patient with `required` blood group."""
    compatible = COMPATIBLE_DONORS.get(required, [required])
    logger.info(f"Compatible donor groups for '{required}': {compatible}")
    return compatible


def score_donor(donor: dict, required_blood_group: str) -> int:
    """
    Score a single donor for a given patient blood group requirement.
    Max 100 points:
      - blood_group_match : 40 (exact) / 20 (compatible)
      - distance_km       : 20 / 15 / 10 / 0
      - past_donations    : min(N * 5, 20)
      - calls_ratio < 1.5 : 10
      - recent_activity   : 10 (donated within last 6 months)
    """
    score = 0
    donor_bg = donor.get("blood_group", "")

    # Blood group match
    if donor_bg == required_blood_group:
        score += 40
    elif donor_bg in COMPATIBLE_DONORS.get(required_blood_group, []):
        score += 20

    # Distance
    distance_raw = donor.get("distance_km", "") or donor.get("distance", "")
    if distance_raw:
        dist = _safe_float(distance_raw)
        if dist <= 5:
            score += 20
        elif dist <= 15:
            score += 15
        elif dist <= 30:
            score += 10

    # Past donations
    donations = _safe_int(donor.get("donations_till_date", ""))
    score += min(donations * 5, 20)

    # Calls-to-donations ratio
    ratio = _safe_float(donor.get("calls_to_donations_ratio", ""))
    if ratio < 1.5:
        score += 10

    # Recent activity (donated in last 6 months)
    last_donation = _parse_date(donor.get("last_donation_date", ""))
    if last_donation:
        six_months_ago = datetime.now() - timedelta(days=180)
        if last_donation >= six_months_ago:
            score += 10

    return score


def rank_donors(
    donors: list[dict],
    required_blood_group: str,
    exact_only: bool = True,
) -> list[dict]:
    """
    Filter donors by blood compatibility then sort descending by score.
    Attaches a `_score` key to each returned donor dict.

    Args:
        donors: list of all eligible donor dicts
        required_blood_group: patient's blood group string
        exact_only: if True, only include exact blood group matches;
                    if False, include all compatible groups
    """
    compatible_groups = get_compatible_blood_groups(required_blood_group)

    if exact_only:
        candidates = [d for d in donors if d.get("blood_group", "") == required_blood_group]
    else:
        candidates = [d for d in donors if d.get("blood_group", "") in compatible_groups]

    logger.info(
        f"Ranking {len(candidates)} candidates for '{required_blood_group}' "
        f"(exact_only={exact_only})"
    )

    scored = []
    for donor in candidates:
        try:
            s = score_donor(donor, required_blood_group)
            donor_copy = dict(donor)
            donor_copy["_score"] = s
            scored.append(donor_copy)
        except Exception as exc:
            donor_id = donor.get("donor_id", "unknown")
            logger.warning(f"Skipping donor {donor_id} due to scoring error: {exc}")

    scored.sort(key=lambda d: d["_score"], reverse=True)
    logger.info(
        f"Top donor score: {scored[0]['_score'] if scored else 'N/A'}, "
        f"bottom: {scored[-1]['_score'] if scored else 'N/A'}"
    )
    return scored
