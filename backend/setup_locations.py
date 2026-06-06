"""
setup_locations.py — One-time script to geocode all unique coordinates
from the donor dataset and store results in DynamoDB locations table.

Run once before the hackathon:
    AWS_DEFAULT_REGION=eu-north-1 python setup_locations.py

Prerequisites:
  1. DynamoDB table 'locations' with partition key 'coord_key' (String)
  2. Lambda IAM role has AmazonLocationFullAccess
  3. AWS Location Place Index named 'bloodlink-place-index' created in console
"""

import boto3
import csv
import os
import time
import json
import logging
from datetime import datetime, timezone

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
        return json.dumps(log)

def _get_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(_JsonFormatter())
        logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return logger

logger = _get_logger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REGION          = os.environ.get("AWS_DEFAULT_REGION", "eu-north-1")
PLACE_INDEX     = os.environ.get("LOCATION_PLACE_INDEX", "bloodlink-place-index")
LOCATIONS_TABLE = os.environ.get("LOCATIONS_TABLE", "locations")
CSV_PATH        = os.environ.get("DATASET_CSV", "../Dataset.csv")

location_client  = boto3.client("location", region_name=REGION)
dynamodb         = boto3.resource("dynamodb", region_name=REGION)
locations_table  = dynamodb.Table(LOCATIONS_TABLE)

# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def reverse_geocode(latitude: str, longitude: str) -> str:
    """
    Call AWS Location Service to get a human-readable area name.
    Position must be [longitude, latitude] — AWS order.
    """
    try:
        resp = location_client.search_place_index_for_position(
            IndexName=PLACE_INDEX,
            Position=[float(longitude), float(latitude)],
            MaxResults=1,
        )
        results = resp.get("Results", [])
        if not results:
            return "Hyderabad Area"
        place        = results[0]["Place"]
        neighborhood = place.get("Neighborhood", "")
        municipality = place.get("Municipality", "")
        region       = place.get("Region", "")
        label        = place.get("Label", "")

        if neighborhood and municipality:
            return f"{neighborhood}, {municipality}"
        elif municipality and region:
            return f"{municipality}, {region}"
        else:
            return label or "Hyderabad Area"
    except Exception as exc:
        logger.warning(f"Geocode failed for {latitude},{longitude}: {exc}")
        return "Hyderabad Area"


# ---------------------------------------------------------------------------
# Main setup
# ---------------------------------------------------------------------------

def setup_locations():
    logger.info(f"Reading coordinates from {CSV_PATH}")
    unique_coords: set[tuple[str, str]] = set()

    try:
        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lat = row.get("latitude", "").strip()
                lon = row.get("longitude", "").strip()
                if lat and lon:
                    unique_coords.add((lat, lon))
    except FileNotFoundError:
        logger.error(f"CSV not found: {CSV_PATH}")
        return

    logger.info(f"Found {len(unique_coords)} unique coordinates — geocoding...")
    success = 0
    failed  = 0

    with locations_table.batch_writer() as batch:
        for i, (lat, lon) in enumerate(sorted(unique_coords), 1):
            name = reverse_geocode(lat, lon)
            batch.put_item(Item={
                "coord_key":     f"{lat}_{lon}",
                "latitude":      lat,
                "longitude":     lon,
                "location_name": name,
            })
            if name != "Hyderabad Area":
                success += 1
            else:
                failed += 1
            logger.info(f"[{i}/{len(unique_coords)}] {lat},{lon} → {name}")
            time.sleep(0.1)  # avoid rate limiting

    logger.info(f"Done — success={success} fallback={failed}")
    print(json.dumps({"geocoded": success, "fallback": failed}))


if __name__ == "__main__":
    setup_locations()
