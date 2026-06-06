"""
matching.py — Donor prioritisation with tiered scoring.

Tier system:
  Same location (<=5 km from request hospital):
    Tier 1  — Bridge donor, GREEN, exact blood group
    Tier 2  — Bridge donor, GREEN, compatible blood group
    Tier 3  — Bridge donor, YELLOW, exact blood group
    Tier 4  — Bridge donor, YELLOW, compatible blood group
    Tier 5  — Bridge donor, RED, exact blood group
    Tier 6  — Bridge donor, RED, compatible blood group
    Tier 7  — Emergency/One-Time donor, exact blood group, Active
    Tier 8  — Emergency/One-Time donor, compatible, Active
    Tier 9  — Emergency/One-Time donor, exact, Inactive
    Tier 10 — Emergency/One-Time donor, compatible, Inactive

  Different location (>5 km or no location):
    Tier 11 — exact blood group, Active, distance ascending
    Tier 12 — compatible blood group, Active, distance ascending
    Tier 13 — exact blood group, Inactive, distance ascending
    Tier 14 — compatible blood group, Inactive, distance ascending
    Tier 15 — fallback (any compatible, any distance)

Within each tier, secondary sort:
  1. transfusion_gap descending (more days until next transfusion = safer to contact)
  2. reliability_score descending  (1 / (calls_to_donations_ratio + 0.1))
  3. donations_till_date descending
"""

import json
import logging
import math
from datetime import datetime, timedelta, timezone

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
# Blood compatibility map: patient blood group → eligible donor blood groups
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

def _transfusion_gap(donor: dict) -> int:
    """Days until next eligible / transfusion date. Higher = safer to contact now."""
    for field in ("next_eligible_date", "expected_next_transfusion_date", "next_transfusion_date"):
        val = donor.get(field, "")
        if val:
            dt = _parse_date(val)
            if dt:
                return (dt - datetime.now()).days
    return 999  # unknown → treat as far away

def _reliability_score(donor: dict) -> float:
    """Higher is more reliable. Inverse of calls_to_donations_ratio."""
    ratio = _safe_float(donor.get("calls_to_donations_ratio", ""))
    return round(1.0 / (ratio + 0.1), 4)

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def _distance_km(donor: dict, ref_lat: float | None, ref_lon: float | None) -> float:
    """Distance from donor to reference location. Returns 9999 if unavailable."""
    if ref_lat is None or ref_lon is None:
        return 9999.0
    try:
        dlat = _safe_float(donor.get("latitude"))
        dlon = _safe_float(donor.get("longitude"))
        if not dlat or not dlon:
            return 9999.0
        return _haversine_km(ref_lat, ref_lon, dlat, dlon)
    except Exception:
        return 9999.0

def _is_bridge(donor: dict) -> bool:
    role = (donor.get("role") or "").lower()
    return "bridge" in role

def _bridge_health(donor: dict) -> str:
    """Get bridge health classification for a donor record (GREEN/YELLOW/RED)."""
    from bridge_health import classify_bridge
    return classify_bridge(donor)

def _is_active(donor: dict) -> bool:
    return (donor.get("user_donation_active_status") or "").strip() == "Active"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_compatible_blood_groups(required: str) -> list[str]:
    compatible = COMPATIBLE_DONORS.get(required, [required])
    logger.info(f"Compatible donor groups for '{required}': {compatible}")
    return compatible


def prioritise_donors(
    donors: list[dict],
    required_blood_group: str,
    declined_donor_ids: list[str] | None = None,
    ref_lat: float | None = None,
    ref_lon: float | None = None,
    same_location_km: float = 5.0,
) -> list[dict]:
    """
    Full tiered prioritisation. Returns donors sorted by tier then secondary keys.
    Declined donors are filtered out before any processing.

    Args:
        donors: all eligible donor dicts from DynamoDB
        required_blood_group: patient's blood group string
        declined_donor_ids: IDs of donors who already said NO — always skipped
        ref_lat / ref_lon: hospital/request coordinates for distance calculation
        same_location_km: distance threshold for same-location tiers (default 5 km)
    """
    declined = set(declined_donor_ids or [])
    compatible_groups = get_compatible_blood_groups(required_blood_group)

    # Hard filter: blood group compatibility + skip declined
    candidates = [
        d for d in donors
        if d.get("blood_group", "") in compatible_groups
        and d.get("donor_id", "") not in declined
    ]

    logger.info(
        f"Prioritising {len(candidates)} candidates for '{required_blood_group}' "
        f"(declined={len(declined)}, ref_lat={ref_lat}, ref_lon={ref_lon})"
    )

    def _tier(donor: dict) -> int:
        dist   = _distance_km(donor, ref_lat, ref_lon)
        exact  = donor.get("blood_group", "") == required_blood_group
        active = _is_active(donor)
        bridge = _is_bridge(donor)
        nearby = dist <= same_location_km

        if nearby and bridge:
            health = _bridge_health(donor)
            if health == "GREEN":
                return 1 if exact else 2
            elif health == "YELLOW":
                return 3 if exact else 4
            else:  # RED
                return 5 if exact else 6

        if nearby and not bridge:
            if active:
                return 7 if exact else 8
            else:
                return 9 if exact else 10

        # Different location
        if active:
            return 11 if exact else 12
        else:
            return 13 if exact else 14

    def _sort_key(donor: dict):
        tier     = _tier(donor)
        gap      = -_transfusion_gap(donor)     # negate: larger gap = better = lower sort key
        rel      = -_reliability_score(donor)   # negate: higher rel = better
        dist     = _distance_km(donor, ref_lat, ref_lon)
        donations = -_safe_int(donor.get("donations_till_date", 0))
        return (tier, gap, rel, dist, donations)

    scored = []
    for donor in candidates:
        try:
            d = dict(donor)
            d["_tier"]              = _tier(donor)
            d["_reliability"]       = _reliability_score(donor)
            d["_transfusion_gap"]   = _transfusion_gap(donor)
            d["_distance_km"]       = round(_distance_km(donor, ref_lat, ref_lon), 2)
            scored.append(d)
        except Exception as exc:
            logger.warning(f"Skipping donor {donor.get('donor_id')} scoring error: {exc}")

    scored.sort(key=_sort_key)

    if scored:
        logger.info(
            f"Top donor: id={scored[0].get('donor_id')} "
            f"tier={scored[0].get('_tier')} "
            f"gap={scored[0].get('_transfusion_gap')}d "
            f"dist={scored[0].get('_distance_km')}km"
        )

    return scored


def rank_donors(
    donors: list[dict],
    required_blood_group: str,
    exact_only: bool = True,
    declined_donor_ids: list[str] | None = None,
    ref_lat: float | None = None,
    ref_lon: float | None = None,
) -> list[dict]:
    """
    Backward-compatible wrapper around prioritise_donors.
    exact_only=True filters to exact blood group matches only (used in level-1/2 escalation).
    """
    result = prioritise_donors(
        donors,
        required_blood_group,
        declined_donor_ids=declined_donor_ids,
        ref_lat=ref_lat,
        ref_lon=ref_lon,
    )
    if exact_only:
        result = [d for d in result if d.get("blood_group", "") == required_blood_group]
    return result


def classify_donor(donor: dict, patient_transfusion_date: str = None) -> str:
    """
    Classify a single donor as GREEN, YELLOW, or RED based on dataset fields.
    Uses patient's expected_next_transfusion_date for gap_days calculation.

    GREEN  — Active AND gap_days >= 0 AND calls_to_donations_ratio <= 5
    YELLOW — Active BUT gap_days < 0 OR ratio > 5
    RED    — Inactive regardless of anything else
    """
    is_active = (donor.get("user_donation_active_status") or "").strip() == "Active"
    if not is_active:
        return "RED"

    # Use patient transfusion date if provided, else fall back to donor's own field
    transfusion_str = patient_transfusion_date or donor.get("expected_next_transfusion_date", "")
    next_eligible_str = donor.get("next_eligible_date", "")

    gap_days = 0
    if transfusion_str and next_eligible_str:
        t_date = _parse_date(transfusion_str)
        e_date = _parse_date(next_eligible_str)
        if t_date and e_date:
            gap_days = (t_date - e_date).days

    ratio = _safe_float(donor.get("calls_to_donations_ratio", ""))

    if gap_days >= 0 and ratio <= 5:
        return "GREEN"
    return "YELLOW"


def build_donor_entry(donor: dict, classification: str, patient_lat: float = None,
                      patient_lon: float = None, patient_transfusion_date: str = None) -> dict:
    """Build a clean donor dict for API responses — no raw coords, includes location_name."""
    import db_helpers
    lat = donor.get("latitude", "")
    lon = donor.get("longitude", "")
    location_name = db_helpers.get_location_name(lat, lon) if lat and lon else "Hyderabad Area"

    dist_km = None
    if patient_lat and patient_lon:
        dist_km = round(_distance_km(donor, patient_lat, patient_lon), 2)

    # gap_days relative to patient transfusion date
    gap_days = None
    if patient_transfusion_date:
        t_date = _parse_date(patient_transfusion_date)
        e_date = _parse_date(donor.get("next_eligible_date", "") or "")
        if t_date and e_date:
            gap_days = (t_date - e_date).days

    return {
        "user_id":                     donor.get("user_id") or donor.get("donor_id", ""),
        "donor_id":                    donor.get("donor_id") or donor.get("user_id", ""),
        "blood_group":                 donor.get("blood_group", ""),
        "gender":                      donor.get("gender", ""),
        "active":                      (donor.get("user_donation_active_status") or "").strip() == "Active",
        "eligibility_status":          donor.get("eligibility_status", ""),
        "calls_to_donations_ratio":    donor.get("calls_to_donations_ratio", ""),
        "donations_till_date":         donor.get("donations_till_date", ""),
        "next_eligible_date":          donor.get("next_eligible_date", ""),
        "location_name":               location_name,
        "classification":              classification,
        "gap_days":                    gap_days,
        "distance_km":                 dist_km,
        "role":                        donor.get("role", ""),
    }


def get_emergency_donors_near(all_donors: list, blood_group: str,
                               patient_lat: float, patient_lon: float,
                               limit: int = 5) -> list:
    """
    Return top N emergency donors sorted by distance from patient.
    Filters: role=Emergency Donor, eligibility_status=eligible, Active, compatible blood group.
    """
    compatible = get_compatible_blood_groups(blood_group)
    candidates = [
        d for d in all_donors
        if (d.get("role") or "").strip() == "Emergency Donor"
        and d.get("eligibility_status", "") == "eligible"
        and (d.get("user_donation_active_status") or "").strip() == "Active"
        and d.get("blood_group", "") in compatible
    ]
    # Sort by distance
    candidates.sort(key=lambda d: _distance_km(d, patient_lat, patient_lon))
    return candidates[:limit]


def score_donor(donor: dict, required_blood_group: str) -> int:
    """
    Legacy single-donor score (0-100) kept for bridge_health replacement_candidates.
    Uses the new reliability + gap signals mapped to 0-100 range.
    """
    compatible_groups = get_compatible_blood_groups(required_blood_group)
    bg = donor.get("blood_group", "")

    score = 0
    if bg == required_blood_group:
        score += 40
    elif bg in compatible_groups:
        score += 20

    gap = _transfusion_gap(donor)
    if gap > 60:
        score += 20
    elif gap > 30:
        score += 15
    elif gap > 7:
        score += 10

    donations = _safe_int(donor.get("donations_till_date", ""))
    score += min(donations * 5, 20)

    rel = _reliability_score(donor)
    if rel >= 1.0:
        score += 10
    elif rel >= 0.5:
        score += 5

    if _is_active(donor):
        score += 10

    return min(score, 100)
