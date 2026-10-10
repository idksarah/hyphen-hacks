"""Build data/lamps.geojson (Contract 1 in docs/CONTRACTS.md).

Sources: SFPUC Streetlights (ArcGIS) plus OpenStreetMap highway=street_lamp
nodes. An OSM lamp within 5 m of an SFPUC lamp is the same physical lamp, so
only the SFPUC record is kept.

Usage:
    python3 data/build_lamps.py            # use cached raw downloads if present
    python3 data/build_lamps.py --refresh  # re-download both sources first

Standard library only.
"""

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from http_util import get_json, post_json

DATA_DIR = Path(__file__).resolve().parent
RAW_DIR = DATA_DIR / "raw"
SFPUC_RAW = RAW_DIR / "sfpuc_streetlights.geojson"
OSM_RAW = RAW_DIR / "osm_street_lamps.json"
OUT = DATA_DIR / "lamps.geojson"

SFPUC_LAYER = (
    "https://services.arcgis.com/Zs2aNLFN00jrS4gG/arcgis/rest/services/"
    "SFPUC_Streetlights/FeatureServer/0"
)
PAGE_SIZE = 2000  # layer's maxRecordCount

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# SF bounding box from the contract: lon -122.52..-122.35, lat 37.70..37.84
BBOX = (-122.52, 37.70, -122.35, 37.84)

COORD_DECIMALS = 6  # ~0.1 m
DEDUPE_M = 5.0

# Every lamp carries the same keys; contract says missing values are null.
EXTRA_KEYS = ("pole_number", "pole_type", "material", "street")



def download_sfpuc():
    count = get_json(f"{SFPUC_LAYER}/query",
                     {"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
    print(f"SFPUC layer reports {count} lamps; downloading in pages of {PAGE_SIZE}")

    features = []
    offset = 0
    while True:
        page = get_json(f"{SFPUC_LAYER}/query", {
            "where": "1=1",
            "outFields": "*",
            "outSR": 4326,
            "orderByFields": "objectid",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "f": "geojson",
        })
        batch = page.get("features", [])
        features.extend(batch)
        print(f"  {len(features)}/{count}")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if len(features) != count:
        raise SystemExit(f"Downloaded {len(features)} lamps but layer reports {count}")

    RAW_DIR.mkdir(exist_ok=True)
    raw = {
        "type": "FeatureCollection",
        "downloaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": SFPUC_LAYER,
        "features": features,
    }
    SFPUC_RAW.write_text(json.dumps(raw))
    print(f"Saved {SFPUC_RAW.relative_to(DATA_DIR.parent)}")


def download_osm():
    s, w, n, e = BBOX[1], BBOX[0], BBOX[3], BBOX[2]
    query = f'[out:json][timeout:120];node["highway"="street_lamp"]({s},{w},{n},{e});out;'
    print("Downloading OSM street lamps from Overpass")
    raw = post_json(OVERPASS_URL, {"data": query})
    raw["downloaded_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RAW_DIR.mkdir(exist_ok=True)
    OSM_RAW.write_text(json.dumps(raw))
    print(f"Saved {len(raw['elements'])} OSM lamps to {OSM_RAW.relative_to(DATA_DIR.parent)}")


def clean(value):
    """Empty strings become null (contract: missing values are null)."""
    if isinstance(value, str):
        value = value.replace("_", " ").strip()  # source has e.g. "SOUTH_VAN_NESS"
        return value or None
    return value


def in_bbox(lon, lat):
    return BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def load_sfpuc():
    raw = json.loads(SFPUC_RAW.read_text())
    lamps, dropped = [], {"no_geometry": 0, "out_of_bbox": 0}
    for f in raw["features"]:
        geom = f.get("geometry")
        if not geom or not geom.get("coordinates"):
            dropped["no_geometry"] += 1
            continue
        lon, lat = (round(c, COORD_DECIMALS) for c in geom["coordinates"][:2])
        if not in_bbox(lon, lat):
            dropped["out_of_bbox"] += 1
            continue
        p = f["properties"]
        street = " ".join(s for s in (clean(p.get("street_name")), clean(p.get("street_type"))) if s)
        lamps.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": f"sfpuc:{clean(p['assetnum'])}",
                "source": "sfpuc",
                "pole_number": clean(p.get("pole_number")),
                "pole_type": clean(p.get("pole_type")),
                "material": clean(p.get("material")),
                "street": street or None,
            },
        })
    print(f"SFPUC: kept {len(lamps)}, dropped {dropped}")
    return lamps


def load_osm():
    raw = json.loads(OSM_RAW.read_text())
    lamps, dropped = [], {"out_of_bbox": 0}
    for el in raw["elements"]:
        lon, lat = round(el["lon"], COORD_DECIMALS), round(el["lat"], COORD_DECIMALS)
        if not in_bbox(lon, lat):
            dropped["out_of_bbox"] += 1
            continue
        lamps.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": f"osm:{el['id']}", "source": "osm", **dict.fromkeys(EXTRA_KEYS)},
        })
    print(f"OSM: kept {len(lamps)}, dropped {dropped}")
    return lamps


# Local equirectangular projection; plenty accurate for 5 m checks across SF.
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(37.77))


def to_m(coords):
    return coords[0] * M_PER_DEG_LON, coords[1] * M_PER_DEG_LAT


def merge(sfpuc, osm):
    """Drop OSM lamps within DEDUPE_M of an SFPUC lamp (grid lookup, 5 m cells)."""
    grid = {}
    for f in sfpuc:
        x, y = to_m(f["geometry"]["coordinates"])
        grid.setdefault((int(x // DEDUPE_M), int(y // DEDUPE_M)), []).append((x, y))

    kept = []
    for f in osm:
        x, y = to_m(f["geometry"]["coordinates"])
        cx, cy = int(x // DEDUPE_M), int(y // DEDUPE_M)
        near = any(
            math.hypot(x - px, y - py) <= DEDUPE_M
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            for px, py in grid.get((cx + dx, cy + dy), ())
        )
        if not near:
            kept.append(f)
    print(f"Merge: {len(osm) - len(kept)} OSM lamps within {DEDUPE_M:g} m of SFPUC dropped, "
          f"{len(kept)} added")
    return sfpuc + kept


def check(lamps):
    counts = Counter(f["properties"]["id"] for f in lamps)
    dupes = {i for i, n in counts.items() if n > 1}
    if dupes:
        raise SystemExit(f"Duplicate ids ({len(dupes)}), e.g. {sorted(dupes)[:5]}")
    for f in lamps:
        p = f["properties"]
        assert p["id"] and p["source"] in ("sfpuc", "osm"), p
        assert set(p) == {"id", "source", *EXTRA_KEYS}, p
        assert in_bbox(*f["geometry"]["coordinates"]), f


def write(lamps):
    # One feature per line so git diffs show which lamps changed.
    lamps = sorted(lamps, key=lambda f: f["properties"]["id"])
    lines = [json.dumps(f, separators=(",", ":")) for f in lamps]
    OUT.write_text(
        '{"type":"FeatureCollection","features":[\n' + ",\n".join(lines) + "\n]}\n"
    )
    print(f"Wrote {len(lamps)} lamps to {OUT.relative_to(DATA_DIR.parent)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-download source data")
    args = ap.parse_args()

    if args.refresh or not SFPUC_RAW.exists():
        download_sfpuc()
    if args.refresh or not OSM_RAW.exists():
        download_osm()
    lamps = merge(load_sfpuc(), load_osm())
    check(lamps)
    write(lamps)


if __name__ == "__main__":
    main()
