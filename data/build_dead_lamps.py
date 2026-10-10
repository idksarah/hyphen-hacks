"""Build data/dead_lamps.json (Contract 2 in docs/CONTRACTS.md).

Open 311 streetlight cases from DataSF "311 Cases" (vw6y-z8j6), filtered to
burnt-out lights opened in the last MAX_AGE_DAYS, each matched to the nearest
lamp in data/lamps.geojson within MATCH_M. Cases with no lamp in range go in
"unmatched".

Usage:
    python3 data/build_dead_lamps.py            # use cached raw download if present
    python3 data/build_dead_lamps.py --refresh  # re-download open cases first

Run build_lamps.py first; this reads data/lamps.geojson.
"""

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from http_util import get_json

DATA_DIR = Path(__file__).resolve().parent
RAW = DATA_DIR / "raw" / "311_streetlights_open.json"
LAMPS = DATA_DIR / "lamps.geojson"
OUT = DATA_DIR / "dead_lamps.json"

CASES_URL = "https://data.sf.gov/resource/vw6y-z8j6.json"
PAGE_SIZE = 5000

MATCH_M = 30.0
MAX_AGE_DAYS = 365  # older open cases are often fixed but never closed

# 311 timestamps are local SF time with no offset.
SF_TZ = ZoneInfo("America/Los_Angeles")


def download():
    rows, offset = [], 0
    while True:
        page = get_json(CASES_URL, {
            "$select": "service_request_id, requested_datetime, service_subtype, "
                       "service_details, agency_responsible, lat, long",
            "$where": "service_name='Streetlights' AND status_description='Open'",
            "$order": "service_request_id",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        })
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    RAW.parent.mkdir(exist_ok=True)
    RAW.write_text(json.dumps({
        "downloaded_at": utc_iso(datetime.now(timezone.utc)),
        "source": CASES_URL,
        "rows": rows,
    }))
    print(f"Saved {len(rows)} open streetlight cases to {RAW.relative_to(DATA_DIR.parent)}")


def is_light_out(row):
    """Contract: only "light out"-type cases. Older cases use the long subtype;
    newer ones use subtype "light" with the detail in service_details."""
    subtype = row.get("service_subtype") or ""
    details = row.get("service_details") or ""
    return (subtype == "Streetlight - Light_Burnt_Out"
            or (subtype == "light" and details.startswith("light_burnt_out")))


def opened_utc(row):
    local = datetime.fromisoformat(row["requested_datetime"]).replace(tzinfo=SF_TZ)
    return local.astimezone(timezone.utc)


def utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Local equirectangular projection; plenty accurate for 30 m matching in SF.
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(37.77))


def to_m(lon, lat):
    return lon * M_PER_DEG_LON, lat * M_PER_DEG_LAT


class LampIndex:
    """Grid of MATCH_M cells for nearest-lamp lookups."""

    def __init__(self, features):
        self.grid = {}
        for f in features:
            x, y = to_m(*f["geometry"]["coordinates"])
            self.grid.setdefault(self._cell(x, y), []).append((x, y, f["properties"]["id"]))

    @staticmethod
    def _cell(x, y):
        return int(x // MATCH_M), int(y // MATCH_M)

    def nearest(self, lon, lat):
        x, y = to_m(lon, lat)
        cx, cy = self._cell(x, y)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for px, py, lamp_id in self.grid.get((cx + dx, cy + dy), ()):
                    d = math.hypot(x - px, y - py)
                    if d <= MATCH_M and (best is None or d < best[0]):
                        best = (d, lamp_id)
        return best


def build():
    rows = json.loads(RAW.read_text())["rows"]
    lamps = json.loads(LAMPS.read_text())["features"]
    index = LampIndex(lamps)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    skipped = {"not_light_out": 0, "too_old": 0, "no_location": 0}
    dead_by_lamp, unmatched = {}, []
    for row in rows:
        if not is_light_out(row):
            skipped["not_light_out"] += 1
            continue
        opened = opened_utc(row)
        if opened < cutoff:
            skipped["too_old"] += 1
            continue
        if not row.get("lat") or not row.get("long"):
            skipped["no_location"] += 1
            continue
        lon, lat = float(row["long"]), float(row["lat"])
        case_id = str(row["service_request_id"])

        hit = index.nearest(lon, lat)
        if hit is None:
            unmatched.append({"case_id": case_id, "opened": utc_iso(opened),
                              "coord": [round(lon, 6), round(lat, 6)]})
            continue
        dist, lamp_id = hit
        entry = {"lamp_id": lamp_id, "case_id": case_id, "opened": utc_iso(opened),
                 "match_dist_m": round(dist, 1)}
        # One entry per lamp; keep the oldest case (longest outage).
        prev = dead_by_lamp.get(lamp_id)
        if prev is None or entry["opened"] < prev["opened"]:
            dead_by_lamp[lamp_id] = entry

    dead = sorted(dead_by_lamp.values(), key=lambda e: e["lamp_id"])
    unmatched.sort(key=lambda e: e["case_id"])

    lamp_ids = {f["properties"]["id"] for f in lamps}
    assert all(e["lamp_id"] in lamp_ids for e in dead)

    OUT.write_text(json.dumps({
        "generated_at": utc_iso(datetime.now(timezone.utc)),
        "dead": dead,
        "unmatched": unmatched,
    }, indent=1) + "\n")
    matched_cases = len(rows) - sum(skipped.values()) - len(unmatched)
    print(f"Skipped {skipped}")
    print(f"{matched_cases} cases matched to {len(dead)} lamps, {len(unmatched)} unmatched")
    print(f"Wrote {OUT.relative_to(DATA_DIR.parent)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-download open 311 cases")
    args = ap.parse_args()
    if args.refresh or not RAW.exists():
        download()
    build()


if __name__ == "__main__":
    main()
