"""Build data/lamps.geojson (Contract 1 in docs/CONTRACTS.md).

v0: SFPUC streetlights only. v1 will merge OSM street_lamp nodes and dedupe
within 5 m, keeping the SFPUC record.

Usage:
    python3 data/build_lamps.py            # use cached raw download if present
    python3 data/build_lamps.py --refresh  # re-download from SFPUC first

Standard library only.
"""

import argparse
import json
import ssl
import sys
import time
import urllib.parse
from collections import Counter
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
RAW_DIR = DATA_DIR / "raw"
SFPUC_RAW = RAW_DIR / "sfpuc_streetlights.geojson"
OUT = DATA_DIR / "lamps.geojson"

SFPUC_LAYER = (
    "https://services.arcgis.com/Zs2aNLFN00jrS4gG/arcgis/rest/services/"
    "SFPUC_Streetlights/FeatureServer/0"
)
PAGE_SIZE = 2000  # layer's maxRecordCount

# SF bounding box from the contract: lon -122.52..-122.35, lat 37.70..37.84
BBOX = (-122.52, 37.70, -122.35, 37.84)

COORD_DECIMALS = 6  # ~0.1 m


def ssl_context():
    """Verified TLS context. python.org macOS installs ship without a CA bundle
    until "Install Certificates.command" is run, so fall back to the system one."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and Path("/etc/ssl/cert.pem").exists():
        ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx


SSL_CTX = ssl_context()


def get_json(url, params, retries=3):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(full, timeout=60, context=SSL_CTX) as resp:
                body = json.load(resp)
            if "error" in body:
                raise RuntimeError(body["error"])
            return body
        except Exception as e:  # network blips, ArcGIS throttling
            if attempt == retries:
                raise
            print(f"  retry {attempt} after error: {e}", file=sys.stderr)
            time.sleep(2 * attempt)


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


def clean(value):
    """Empty strings become null (contract: missing values are null)."""
    if isinstance(value, str):
        value = value.strip()
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


def check(lamps):
    counts = Counter(f["properties"]["id"] for f in lamps)
    dupes = {i for i, n in counts.items() if n > 1}
    if dupes:
        raise SystemExit(f"Duplicate ids ({len(dupes)}), e.g. {sorted(dupes)[:5]}")
    for f in lamps:
        p = f["properties"]
        assert p["id"] and p["source"] in ("sfpuc", "osm"), p
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
    lamps = load_sfpuc()
    check(lamps)
    write(lamps)


if __name__ == "__main__":
    main()
