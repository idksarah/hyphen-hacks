"""Build data/graph.pkl (Contract 3 in docs/CONTRACTS.md).

The osmnx walk graph for San Francisco, trimmed to its largest strongly
connected component, projected to EPSG:32610, with every edge scored for
lighting against data/lamps.geojson (and again with data/dead_lamps.json's
lamps switched off, for the *_outage attributes).

Usage:
    .venv/bin/python backend/build_graph.py

osmnx downloads are cached in tests/cache/.
"""
import hashlib
import json
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox
import shapely
from pyproj import Transformer
from shapely.geometry import LineString

from scoring import (
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    LAMP_HEIGHT_M,
    LIGHT_R_M,
    STEP_M,
    LampIndex,
    edge_cost,
    sample_offsets,
    score_samples,
)

REPO = Path(__file__).resolve().parent.parent
LAMPS = REPO / "data" / "lamps.geojson"
DEAD = REPO / "data" / "dead_lamps.json"
OUT = REPO / "data" / "graph.pkl"
CRS = "EPSG:32610"

ox.settings.cache_folder = str(REPO / "tests" / "cache")
ox.settings.use_cache = True


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.1f}s] {msg}", flush=True)


def load_graph():
    G = ox.graph.graph_from_place("San Francisco, California, USA", network_type="walk")
    G = ox.truncate.largest_component(G, strongly=True)
    for _, d in G.nodes(data=True):
        d["lon"], d["lat"] = d["x"], d["y"]
    return ox.projection.project_graph(G, to_crs=CRS)


def load_lamps():
    feats = json.loads(LAMPS.read_text())["features"]
    ids = [f["properties"]["id"] for f in feats]
    lon, lat = np.array([f["geometry"]["coordinates"] for f in feats]).T
    x, y = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(lon, lat)
    dead = set()
    if DEAD.exists():
        dead = {d["lamp_id"] for d in json.loads(DEAD.read_text())["dead"]}
    return ids, np.column_stack([x, y]), dead


def edge_line(G, u, v, d):
    """The edge's geometry as a LineString running u->v."""
    pu = (G.nodes[u]["x"], G.nodes[u]["y"])
    pv = (G.nodes[v]["x"], G.nodes[v]["y"])
    line = d.get("geometry") or LineString([pu, pv])
    start, end = line.coords[0], line.coords[-1]
    if np.hypot(start[0] - pu[0], start[1] - pu[1]) > np.hypot(end[0] - pu[0], end[1] - pu[1]):
        line = line.reverse()
    return line


def lamps_by_edge(lines, lamp_ids, lamp_xy):
    """For each line, ids of lamps within LIGHT_R_M of it, ordered along it."""
    pts = shapely.points(lamp_xy)
    edge_idx, lamp_idx = shapely.STRtree(pts).query(lines, predicate="dwithin", distance=LIGHT_R_M)
    pos = shapely.line_locate_point(lines[edge_idx], pts[lamp_idx])
    order = np.lexsort((pos, edge_idx))
    out = [[] for _ in range(len(lines))]
    for e, l in zip(edge_idx[order], lamp_idx[order]):
        out[e].append(lamp_ids[l])
    return out


def score_lines(lines, index):
    """EdgeScore for each line, sampling all lines in one batch."""
    per_line = [sample_offsets(line.length) for line in lines]
    counts = np.array([len(o) for o, _ in per_line])
    offsets = np.concatenate([o for o, _ in per_line])
    owners = np.repeat(np.arange(len(lines)), counts)
    pts = shapely.line_interpolate_point(lines[owners], offsets)
    lit = index.lit(shapely.get_coordinates(pts))
    splits = np.split(lit, np.cumsum(counts)[:-1])
    return [score_samples(mask, cell) for mask, (_, cell) in zip(splits, per_line)]


def main():
    log("loading walk graph")
    G = load_graph()
    log(f"graph: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

    lamp_ids, lamp_xy, dead = load_lamps()
    unknown_dead = dead - set(lamp_ids)
    if unknown_dead:
        raise SystemExit(f"{len(unknown_dead)} dead lamp ids not in lamps.geojson, e.g. {sorted(unknown_dead)[:3]}")
    all_lamps = LampIndex(lamp_ids, lamp_xy)
    alive = all_lamps.without(dead)
    log(f"lamps: {len(lamp_ids):,} ({len(dead)} reported dead)")

    # Each street is usually stored twice (u->v and v->u). Score one direction
    # and mirror it onto the other.
    edges = list(G.edges(keys=True, data=True))
    canon, mirror = {}, []  # canon: (u, v, k) -> index into `lines`
    lines = []
    for u, v, k, d in edges:
        rev = (v, u, k)
        if rev in canon and abs(G.edges[rev]["length"] - d["length"]) < 0.01:
            mirror.append((u, v, k, canon[rev]))
        else:
            canon[(u, v, k)] = len(lines)
            lines.append(edge_line(G, u, v, d))
    lines = np.array(lines, dtype=object)
    log(f"scoring {len(lines):,} streets ({len(mirror):,} reverse edges mirrored)")

    scores = score_lines(lines, all_lamps)
    log("scored with all lamps")
    scores_out = score_lines(lines, alive) if dead else scores
    log("scored with dead lamps off")
    near = lamps_by_edge(lines, lamp_ids, lamp_xy)
    log("matched lamps to streets")

    def apply(u, v, k, i, flip):
        d = G.edges[u, v, k]
        for suffix, s in (("", scores[i]), ("_outage", scores_out[i])):
            s = s.reversed() if flip else s
            d["lit_frac" + suffix] = s.lit_frac
            d["max_gap" + suffix] = s.max_gap
            d["lead_dark" + suffix] = s.lead_dark
            d["trail_dark" + suffix] = s.trail_dark
            d["cost" + suffix] = edge_cost(d["length"], s.lit_frac, s.max_gap)
        d["lamp_ids"] = near[i][::-1] if flip else list(near[i])

    for (u, v, k), i in canon.items():
        apply(u, v, k, i, flip=False)
    for u, v, k, i in mirror:
        apply(u, v, k, i, flip=True)

    G.graph.update(
        built_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        lamp_count=len(lamp_ids),
        dead_count=len(dead),
        lamps_sha256=hashlib.sha256(LAMPS.read_bytes()).hexdigest(),
        step_m=STEP_M,
        light_r_m=LIGHT_R_M,
        light_model="inverse_square",
        lamp_height_m=LAMP_HEIGHT_M,
        alpha=DEFAULT_ALPHA,
        beta=DEFAULT_BETA,
        osmnx_version=ox.__version__,
        networkx_version=nx.__version__,
    )

    with OUT.open("wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    log(f"wrote {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1e6:.0f} MB)")

    lengths = np.array([d["length"] for _, _, d in G.edges(data=True)])
    lit = np.array([d["lit_frac"] for _, _, d in G.edges(data=True)])
    lit_out = np.array([d["lit_frac_outage"] for _, _, d in G.edges(data=True)])
    print(f"citywide lit share: {np.average(lit, weights=lengths):.2f} "
          f"({np.average(lit_out, weights=lengths):.2f} with outages); "
          f"fully dark streets: {(lit == 0).mean():.0%}")


if __name__ == "__main__":
    main()
