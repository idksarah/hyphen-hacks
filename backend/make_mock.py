"""Generate mocks/route_response.json: real SF streets and real lamps from data/lamps.geojson.

Usage:
    .venv/bin/python backend/make_mock.py

Takes ~80 s; osmnx downloads are cached in tests/cache/.
"""
import json
import math
from collections import defaultdict
from pathlib import Path

import networkx as nx
import osmnx as ox

REPO = Path(__file__).resolve().parent.parent
ox.settings.cache_folder = f"{REPO}/tests/cache"
ox.settings.use_cache = True

FROM = [-122.4194, 37.7749]
TO = [-122.4108, 37.7596]
ALPHA, BETA = 3.0, 0.02
LIGHT_R = 20.0   # lamp counts as lighting a sample within this distance, m
STEP = 3.0       # sample spacing, m
GAP_MIN = 25.0   # dark_gaps threshold, m
DETOUR_CAP = 1.5
MARGIN = 800.0   # only score edges within this distance of the trip's bbox, m

LAT0 = (FROM[1] + TO[1]) / 2
KX, KY = math.cos(math.radians(LAT0)) * 111320.0, 110540.0
to_m = lambda lon, lat: (lon * KX, lat * KY)
to_ll = lambda x, y: [x / KX, y / KY]

G = ox.graph.graph_from_place("San Francisco, California, USA", network_type="walk")
G = ox.truncate.largest_component(G, strongly=True)

# --- lamps, grid-indexed in meters ---------------------------------------
lamps = json.load(open(f"{REPO}/data/lamps.geojson"))["features"]
lamp_xy = {f["properties"]["id"]: to_m(*f["geometry"]["coordinates"]) for f in lamps}
lamp_ll = {f["properties"]["id"]: f["geometry"]["coordinates"] for f in lamps}
grid = defaultdict(list)
for lid, (x, y) in lamp_xy.items():
    grid[(int(x // LIGHT_R), int(y // LIGHT_R))].append(lid)


def lamps_near(x, y):
    """(dist, id) of lamps within LIGHT_R of (x, y)."""
    gx, gy = int(x // LIGHT_R), int(y // LIGHT_R)
    out = []
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            for lid in grid.get((gx + i, gy + j), ()):
                d = math.dist((x, y), lamp_xy[lid])
                if d <= LIGHT_R:
                    out.append((d, lid))
    return out


# --- geometry helpers -----------------------------------------------------
def edge_pts(u, v, k):
    d = G[u][v][k]
    nu = (G.nodes[u]["x"], G.nodes[u]["y"])
    if "geometry" in d:
        pts = list(d["geometry"].coords)
        if math.dist(pts[0], nu) > math.dist(pts[-1], nu):
            pts = pts[::-1]
    else:
        pts = [nu, (G.nodes[v]["x"], G.nodes[v]["y"])]
    return pts


def samples(pts_ll):
    """Points every STEP m along a lon/lat polyline, in meters, with their distance along it."""
    m = [to_m(*p) for p in pts_ll]
    out, acc = [], 0.0
    for a, b in zip(m, m[1:]):
        seg = math.dist(a, b)
        n = max(1, int(seg // STEP))
        for i in range(n):
            t = i / n
            out.append((acc + t * seg, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))))
        acc += seg
    out.append((acc, m[-1]))
    return out, acc


def dark_runs(samp, total):
    runs, start = [], None
    for s, (x, y) in samp:
        lit = bool(lamps_near(x, y))
        if not lit and start is None:
            start = s
        elif lit and start is not None:
            runs.append((start, s))
            start = None
    if start is not None:
        runs.append((start, total))
    return runs


# --- score edges near the trip (a stand-in for the real graph.pkl costs) ---
x0, y0 = to_m(min(FROM[0], TO[0]), min(FROM[1], TO[1]))
x1, y1 = to_m(max(FROM[0], TO[0]), max(FROM[1], TO[1]))
inside = lambda n: x0 - MARGIN <= G.nodes[n]["x"] * KX <= x1 + MARGIN and y0 - MARGIN <= G.nodes[n]["y"] * KY <= y1 + MARGIN
H = G.subgraph([n for n in G.nodes if inside(n)]).copy()
for u, v, k, d in H.edges(keys=True, data=True):
    samp, L = samples(edge_pts(u, v, k))
    runs = dark_runs(samp, L)
    dark = sum(b - a for a, b in runs)
    gap = max((b - a for a, b in runs), default=0.0)
    d["lit_frac"] = 1 - dark / L if L else 1.0
    d["max_gap"] = gap


def cost(alpha):
    return lambda u, v, ks: min(
        d["length"] * (1 + alpha * (1 - d["lit_frac"])) + BETA * d["max_gap"] ** 2 for d in ks.values()
    )


def nearest(lon, lat):
    return min(H.nodes, key=lambda n: (H.nodes[n]["x"] - lon) ** 2 * KX**2 + (H.nodes[n]["y"] - lat) ** 2 * KY**2)


src, dst = nearest(*FROM), nearest(*TO)


def best_key(u, v, weight):
    return min(H[u][v], key=lambda k: weight(u, v, {k: H[u][v][k]}))


def build_route(nodes, weight):
    coords = []
    for u, v in zip(nodes, nodes[1:]):
        seg = [list(p) for p in edge_pts(u, v, best_key(u, v, weight))]
        coords.extend(seg if not coords else seg[1:])
    samp, total = samples(coords)

    # lamps in walking order: by where along the route they're closest
    first_seen = {}
    for s, (x, y) in samp:
        for d, lid in lamps_near(x, y):
            if lid not in first_seen or d < first_seen[lid][1]:
                first_seen[lid] = (s, d)
    ids = sorted(first_seen, key=lambda i: first_seen[i][0])

    runs = dark_runs(samp, total)
    dark_len = sum(b - a for a, b in runs)

    def slice_coords(a, b):
        pts = [s_xy for s, s_xy in samp if a <= s <= b]
        pts = pts[:: max(1, len(pts) // 20)] + [pts[-1]]  # thin it out
        return [[round(c, 5) for c in to_ll(*p)] for p in pts]

    if len(ids) < 2:
        constellation = []
    else:
        n = min(15, len(ids))
        pick = dict.fromkeys(ids[round(i * (len(ids) - 1) / (n - 1))] for i in range(n))
        constellation = [{"id": i, "coord": [round(c, 5) for c in lamp_ll[i]]} for i in pick]

    return nodes, {
        "coords": [[round(x, 5), round(y, 5)] for x, y in coords],
        "length_m": round(total, 1),
        "minutes": round(total / 1.4 / 60, 1),
        "lit_frac": round(1 - dark_len / total, 2),
        "max_gap_m": round(max((b - a for a, b in runs), default=0.0), 1),
        "dark_gaps": [
            {"length_m": round(b - a, 1), "coords": slice_coords(a, b)} for a, b in runs if b - a >= GAP_MIN
        ],
        "lamp_ids": ids,
        "constellation": constellation,
    }


length_w = lambda u, v, ks: min(d["length"] for d in ks.values())
short_nodes, shortest = build_route(nx.shortest_path(H, src, dst, weight=length_w), length_w)

# Step alpha down until the lit route is within the detour cap.
alpha = ALPHA
while True:
    lit_nodes, lit = build_route(nx.shortest_path(H, src, dst, weight=cost(alpha)), cost(alpha))
    if lit["length_m"] <= DETOUR_CAP * shortest["length_m"] or alpha <= 0:
        break
    alpha = round(alpha - 0.5, 2)

snap = lambda n: [round(H.nodes[n]["x"], 5), round(H.nodes[n]["y"], 5)]
resp = {
    "request": {"from": FROM, "to": TO, "alpha": ALPHA, "beta": BETA, "outages": True},
    "snapped": {"from": snap(src), "to": snap(dst)},
    "alpha_used": alpha,
    "detour_capped": alpha < ALPHA,
    "same_route": short_nodes == lit_nodes,
    "routes": {"shortest": shortest, "lit": lit},
}

with open(f"{REPO}/mocks/route_response.json", "w") as f:
    json.dump(resp, f, indent=2)
    f.write("\n")

for name, r in resp["routes"].items():
    print(name, r["length_m"], r["minutes"], r["lit_frac"], r["max_gap_m"], [g["length_m"] for g in r["dark_gaps"]], len(r["lamp_ids"]), len(r["constellation"]))
print("ratio", round(lit["length_m"] / shortest["length_m"], 3), "alpha_used", alpha, "same", resp["same_route"])
