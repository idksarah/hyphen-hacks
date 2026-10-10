"""
Test script: visualize SFPD Incident Reports, 2018 to Present (dataset wg3w-h783).
https://data.sf.gov/d/wg3w-h783

Aggregation happens server-side via SoQL, so only a few hundred rows are
downloaded instead of the full ~1M-row dataset.

Usage:
    pip install matplotlib
    python sf_incidents_viz.py              # show + save sf_incidents.png and sf_incidents_map.png
    python sf_incidents_viz.py --no-show    # just save the PNGs

Optional: set SODA_APP_TOKEN to avoid Socrata's anonymous rate limits.
"""

import argparse
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime

import matplotlib.pyplot as plt

ENDPOINT = "https://data.sf.gov/resource/wg3w-h783.json"
# Police district boundaries in effect 2015-2026 (covers almost all of the incident data)
DISTRICTS_GEOJSON = "https://data.sf.gov/resource/bj3n-hmgf.geojson"

# Palette (validated reference palette, light mode)
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"
SEQ_BLUES = ["#eef4fc", "#c5dbf5", "#8db8ec", "#5596e0", "#2a78d6", "#1a5aa8", "#0f3c73"]

# Map label offsets (degrees lon, lat) for districts too small to label inside
CALLOUT_OFFSETS = {"TENDERLOIN": (0.035, 0.012)}

DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def soql(**params):
    """Run a SoQL query against the dataset and return a list of dicts."""
    query = urllib.parse.urlencode({f"${k}": v for k, v in params.items()})
    req = urllib.request.Request(f"{ENDPOINT}?{query}")
    token = os.environ.get("SODA_APP_TOKEN")
    if token:
        req.add_header("X-App-Token", token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_monthly():
    rows = soql(
        select="date_trunc_ym(incident_datetime) AS month, count(*) AS n",
        where="incident_datetime IS NOT NULL",
        group="month",
        order="month",
        limit=1000,
    )
    data = [(datetime.fromisoformat(r["month"][:19]), int(r["n"])) for r in rows]
    # Drop the current (partial) month so the trend doesn't fall off a cliff
    now = datetime.now()
    return [(m, n) for m, n in data if (m.year, m.month) != (now.year, now.month)]


def fetch_top_categories(k=12):
    rows = soql(
        select="incident_category, count(*) AS n",
        where="incident_category IS NOT NULL",
        group="incident_category",
        order="n DESC",
        limit=k,
    )
    return [(r["incident_category"], int(r["n"])) for r in rows]


def fetch_districts():
    rows = soql(
        select="police_district, count(*) AS n",
        where="police_district IS NOT NULL AND police_district != 'Out of SF'",
        group="police_district",
        order="n DESC",
        limit=50,
    )
    return [(r["police_district"], int(r["n"])) for r in rows]


def fetch_out_of_sf_count():
    rows = soql(select="count(*) AS n", where="police_district = 'Out of SF'")
    return int(rows[0]["n"])


def fetch_dow_hour():
    rows = soql(
        select="date_extract_dow(incident_datetime) AS dow, "
        "date_extract_hh(incident_datetime) AS hh, count(*) AS n",
        where="incident_datetime IS NOT NULL",
        group="dow, hh",
        limit=1000,
    )
    grid = [[0] * 24 for _ in range(7)]
    for r in rows:
        grid[int(r["dow"])][int(r["hh"])] = int(r["n"])
    return grid


def style_axis(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=TEXT, fontsize=12, fontweight="bold", pad=10)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT_MUTED, labelsize=9, length=0)


def plot_monthly(ax, monthly):
    months, counts = zip(*monthly)
    style_axis(ax, "Incidents per month")
    ax.plot(months, counts, color=BLUE, linewidth=2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v / 1000:.0f}k")
    # Label only the most recent point
    ax.annotate(
        f"{counts[-1]:,}",
        (months[-1], counts[-1]),
        textcoords="offset points",
        xytext=(4, 6),
        color=TEXT,
        fontsize=9,
    )


def plot_hbar(ax, pairs, title):
    labels, counts = zip(*reversed(pairs))
    style_axis(ax, title)
    ax.spines["bottom"].set_visible(False)
    ax.barh(labels, counts, color=BLUE, height=0.7, edgecolor=SURFACE, linewidth=2)
    ax.set_xticks([])
    for y, n in enumerate(counts):
        ax.text(n, y, f" {n:,}", va="center", color=TEXT_MUTED, fontsize=8)
    ax.set_xlim(0, max(counts) * 1.15)


def plot_heatmap(fig, ax, grid):
    from matplotlib.colors import LinearSegmentedColormap

    style_axis(ax, "When incidents happen (day x hour)")
    ax.spines["bottom"].set_visible(False)
    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUES)
    im = ax.imshow(grid, aspect="auto", cmap=cmap)
    ax.set_yticks(range(7), DOW)
    ax.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=TEXT_MUTED, labelsize=8, length=0)


def fetch_district_boundaries():
    """Return {DISTRICT_NAME: [ring, ...]} where each ring is a list of (lon, lat)."""
    with urllib.request.urlopen(DISTRICTS_GEOJSON, timeout=60) as resp:
        geo = json.loads(resp.read().decode("utf-8"))
    shapes = {}
    for feature in geo["features"]:
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        # Outer ring of each polygon only; SF districts have no meaningful holes
        shapes[feature["properties"]["district"].upper()] = [p[0] for p in polygons]
    return shapes


def ring_centroid(ring):
    """Area-weighted centroid and absolute area of a polygon ring (shoelace)."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a /= 2
    return cx / (6 * a), cy / (6 * a), abs(a)


def plot_district_map(fig, ax, shapes, districts):
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.patches import Polygon

    counts = {name.upper(): n for name, n in districts}
    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUES)
    norm = Normalize(vmin=0, vmax=max(counts.values()))

    ax.set_facecolor(SURFACE)
    ax.set_title(
        "Incidents by police district", loc="left", color=TEXT,
        fontsize=14, fontweight="bold", pad=10,
    )
    for name, rings in shapes.items():
        n = counts.get(name, 0)
        fill = cmap(norm(n))
        for ring in rings:
            # 2px surface-colored edge separates adjacent districts
            ax.add_patch(Polygon(ring, closed=True, facecolor=fill, edgecolor=SURFACE, linewidth=2))
        # Label at the centroid of the district's largest piece
        x, y, _ = max((ring_centroid(r) for r in rings), key=lambda c: c[2])
        label = f"{name.title()}\n{n:,}"
        if name in CALLOUT_OFFSETS:
            # District too small to hold its label: place it outside with a leader line
            dx, dy = CALLOUT_OFFSETS[name]
            ax.annotate(label, (x, y), xytext=(x + dx, y + dy), ha="center", va="center",
                        color=TEXT, fontsize=9, fontweight="bold", linespacing=1.3,
                        arrowprops=dict(arrowstyle="-", color=TEXT_MUTED, linewidth=1))
            continue
        ink = "white" if norm(n) > 0.55 else TEXT
        ax.text(x, y, label, ha="center", va="center",
                color=ink, fontsize=9, fontweight="bold", linespacing=1.3)

    all_pts = [pt for rings in shapes.values() for r in rings for pt in r]
    xs, ys = zip(*all_pts)
    ax.set_xlim(min(xs) - 0.005, max(xs) + 0.005)
    ax.set_ylim(min(ys) - 0.005, max(ys) + 0.005)
    # Correct lon/lat distortion at SF's latitude (~37.76°)
    ax.set_aspect(1 / math.cos(math.radians(37.76)))
    ax.axis("off")

    cbar = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, fraction=0.03, pad=0.02)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=TEXT_MUTED, labelsize=8, length=0)
    cbar.set_label("Incident reports", color=TEXT_MUTED, fontsize=9)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", default="sf_incidents.png")
    parser.add_argument("--map-out", default="sf_incidents_map.png")
    parser.add_argument("--no-show", action="store_true")
    args = parser.parse_args()

    print("Querying data.sf.gov ...")
    monthly = fetch_monthly()
    categories = fetch_top_categories()
    districts = fetch_districts()
    dow_hour = fetch_dow_hour()
    total = sum(n for _, n in monthly)
    print(f"  {len(monthly)} months, {total:,} incidents (excl. current month)")

    plt.rcParams["font.family"] = "sans-serif"
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), facecolor=SURFACE)
    fig.suptitle(
        "SFPD Incident Reports, 2018 to present",
        x=0.02,
        ha="left",
        color=TEXT,
        fontsize=16,
        fontweight="bold",
    )

    plot_monthly(axes[0][0], monthly)
    plot_heatmap(fig, axes[0][1], dow_hour)
    plot_hbar(axes[1][0], categories, f"Top {len(categories)} incident categories")
    plot_hbar(axes[1][1], districts, "Incidents by police district")

    fig.text(
        0.02, 0.01, "Source: DataSF, dataset wg3w-h783",
        color=TEXT_MUTED, fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    fig.savefig(args.out, dpi=150, facecolor=SURFACE)
    print(f"Saved {args.out}")

    # --- Map ---
    # The public dataset has no coordinates, intersections, or neighborhoods;
    # police_district is the only geography it has, so map that.
    shapes = fetch_district_boundaries()
    out_of_sf = fetch_out_of_sf_count()
    map_fig, map_ax = plt.subplots(figsize=(10, 9), facecolor=SURFACE)
    plot_district_map(map_fig, map_ax, shapes, districts)
    map_fig.text(
        0.02, 0.02,
        f"Excludes {out_of_sf:,} reports with district 'Out of SF'. "
        "Boundaries: DataSF Historical Police Districts (2015-2026), bj3n-hmgf.\n"
        "Source: DataSF, dataset wg3w-h783. The public data has no point locations.",
        color=TEXT_MUTED, fontsize=8,
    )
    map_fig.tight_layout(rect=(0, 0.05, 1, 1))
    map_fig.savefig(args.map_out, dpi=150, facecolor=SURFACE)
    print(f"Saved {args.map_out}")

    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
