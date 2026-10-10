"""Route finding over data/graph.pkl, returning Contract 4 response bodies.

The graph's edge scores choose the route. The reported stats (lit_frac,
max_gap_m, dark_gaps) are then recomputed from 3 m samples along the whole
route, so dark runs that continue across intersections are merged.
"""
import json
import math
import pickle
from pathlib import Path

import networkx as nx
import numpy as np
import shapely
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import LineString
from shapely.ops import substring

from scoring import (
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    LampIndex,
    dark_runs,
    sample_offsets,
)

REPO = Path(__file__).resolve().parent.parent
GRAPH = REPO / "data" / "graph.pkl"
LAMPS = REPO / "data" / "lamps.geojson"
DEAD = REPO / "data" / "dead_lamps.json"

SF_BBOX = (-122.52, 37.70, -122.35, 37.84)  # lon_min, lat_min, lon_max, lat_max
ALPHA_RANGE = (0.0, 10.0)
BETA_RANGE = (0.0, 0.2)
DETOUR_CAP = 1.5
# When the lit route is too long, retry with alpha and beta scaled by these.
BACKOFF = (1.0, 0.5, 0.25, 0.125, 0.0)
WALK_MPS = 1.4
GAP_REPORT_M = 25.0
MAX_CONSTELLATION = 15


class RouteError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _r5(xy):
    return [round(float(xy[0]), 5), round(float(xy[1]), 5)]


class Router:
    def __init__(self, graph_path=GRAPH, lamps_path=LAMPS, dead_path=DEAD):
        self.G = pickle.loads(Path(graph_path).read_bytes())
        crs = self.G.graph["crs"]
        self.to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        self.to_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

        self.nodes = np.array(list(self.G.nodes))
        self.node_tree = cKDTree([(self.G.nodes[n]["x"], self.G.nodes[n]["y"]) for n in self.nodes])

        feats = json.loads(Path(lamps_path).read_text())["features"]
        ids = [f["properties"]["id"] for f in feats]
        lon, lat = np.array([f["geometry"]["coordinates"] for f in feats]).T
        self.lamp_ll = dict(zip(ids, (f["geometry"]["coordinates"] for f in feats)))
        self.lamps = LampIndex(ids, np.column_stack(self.to_xy.transform(lon, lat)))

        self.dead = set()
        if Path(dead_path).exists():
            self.dead = {d["lamp_id"] for d in json.loads(Path(dead_path).read_text())["dead"]}
        self.alive = self.lamps.without(self.dead)

    # --- public -------------------------------------------------------------

    def health(self):
        return {
            "ok": True,
            "lamps": self.G.graph["lamp_count"],
            "dead": len(self.dead),
            "built_at": self.G.graph["built_at"],
        }

    def route(self, from_lon, from_lat, to_lon, to_lat,
              alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA, outages=False):
        check_params(from_lon, from_lat, to_lon, to_lat, alpha, beta)
        src, dst = self.snap(from_lon, from_lat), self.snap(to_lon, to_lat)
        if src == dst:
            raise RouteError(400, "same_point", "Start and end are the same spot on the street network.")

        try:
            short_nodes = self.astar(src, dst, self.length_weight)
        except nx.NetworkXNoPath:
            raise RouteError(404, "no_route", "No walking route connects these points.")
        short_len = self.path_length(short_nodes)

        sfx = "_outage" if outages else ""
        for f in BACKOFF:
            a, b = alpha * f, beta * f
            lit_nodes = self.astar(src, dst, self.lit_weight(a, b, sfx))
            if self.path_length(lit_nodes) <= DETOUR_CAP * short_len + 1e-6:
                break
        lamps = self.alive if outages else self.lamps

        return {
            "request": {"from": [from_lon, from_lat], "to": [to_lon, to_lat],
                        "alpha": alpha, "beta": beta, "outages": outages},
            "snapped": {"from": self.node_ll(src), "to": self.node_ll(dst)},
            "alpha_used": round(a, 4),
            "detour_capped": f < 1.0,
            "same_route": lit_nodes == short_nodes,
            "routes": {
                "shortest": self.describe(short_nodes, self.length_weight, lamps),
                "lit": self.describe(lit_nodes, self.lit_weight(a, b, sfx), lamps),
            },
        }

    # --- graph search ---------------------------------------------------------

    def snap(self, lon, lat):
        x, y = self.to_xy.transform(lon, lat)
        return int(self.nodes[self.node_tree.query((x, y))[1]])

    def node_ll(self, n):
        return _r5((self.G.nodes[n]["lon"], self.G.nodes[n]["lat"]))

    @staticmethod
    def length_weight(u, v, ks):
        return min(d["length"] for d in ks.values())

    @staticmethod
    def lit_weight(alpha, beta, sfx):
        if alpha == DEFAULT_ALPHA and beta == DEFAULT_BETA:
            key = "cost" + sfx  # precomputed in build_graph.py
            return lambda u, v, ks: min(d[key] for d in ks.values())
        lf, mg = "lit_frac" + sfx, "max_gap" + sfx
        return lambda u, v, ks: min(
            d["length"] * (1 + alpha * (1 - d[lf])) + beta * d[mg] ** 2 for d in ks.values()
        )

    def astar(self, src, dst, weight):
        # Every weight is >= length, so straight-line distance never overestimates.
        G = self.G
        tx, ty = G.nodes[dst]["x"], G.nodes[dst]["y"]
        h = lambda n, _: math.hypot(G.nodes[n]["x"] - tx, G.nodes[n]["y"] - ty)
        return nx.astar_path(G, src, dst, heuristic=h, weight=weight)

    def path_length(self, nodes):
        return sum(self.length_weight(u, v, self.G[u][v]) for u, v in zip(nodes, nodes[1:]))

    # --- response building ------------------------------------------------------

    def path_edges(self, nodes, weight):
        """The (u, v, data) actually used between each pair of nodes."""
        for u, v in zip(nodes, nodes[1:]):
            ks = self.G[u][v]
            k = min(ks, key=lambda k: weight(u, v, {k: ks[k]}))
            yield u, v, ks[k]

    def describe(self, nodes, weight, lamps):
        G = self.G
        coords, lamp_ids = [], []
        for u, v, d in self.path_edges(nodes, weight):
            seg = edge_coords(G, u, v, d)
            coords.extend(seg if not coords else seg[1:])
            lamp_ids.extend(d["lamp_ids"])
        line = LineString(coords)
        total = line.length

        offsets, cell = sample_offsets(total)
        pts = shapely.get_coordinates(shapely.line_interpolate_point(line, offsets))
        lit = lamps.lit(pts)
        runs = dark_runs(lit, cell)

        alive = set(lamps.ids)
        lamp_ids = [i for i in dict.fromkeys(lamp_ids) if i in alive]
        return {
            "coords": self.to_lonlat(np.asarray(coords)),
            "length_m": round(total, 1),
            "minutes": round(total / WALK_MPS / 60, 1),
            "lit_frac": round(float(lit.mean()), 2),
            "max_gap_m": round(max((e - s for s, e in runs), default=0.0), 1),
            "dark_gaps": [
                {"length_m": round(e - s, 1),
                 "coords": self.to_lonlat(np.asarray(substring(line, s, e).coords))}
                for s, e in runs if e - s >= GAP_REPORT_M
            ],
            "lamp_ids": lamp_ids,
            "constellation": self.constellation(lamp_ids),
        }

    def constellation(self, lamp_ids):
        if len(lamp_ids) < 2:
            return []
        n = min(MAX_CONSTELLATION, len(lamp_ids))
        pick = dict.fromkeys(lamp_ids[round(i * (len(lamp_ids) - 1) / (n - 1))] for i in range(n))
        return [{"id": i, "coord": _r5(self.lamp_ll[i])} for i in pick]

    def to_lonlat(self, xy):
        lon, lat = self.to_ll.transform(xy[:, 0], xy[:, 1])
        return [_r5(p) for p in zip(lon, lat)]


def edge_coords(G, u, v, d):
    """Projected vertices of an edge, running u->v."""
    pu = (G.nodes[u]["x"], G.nodes[u]["y"])
    pv = (G.nodes[v]["x"], G.nodes[v]["y"])
    pts = list(d["geometry"].coords) if "geometry" in d else [pu, pv]
    if math.dist(pts[0], pu) > math.dist(pts[-1], pu):
        pts.reverse()
    return pts


def check_params(from_lon, from_lat, to_lon, to_lat, alpha, beta):
    nums = (from_lon, from_lat, to_lon, to_lat, alpha, beta)
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in nums):
        raise RouteError(400, "bad_params", "Coordinates, alpha and beta must be numbers.")
    if not ALPHA_RANGE[0] <= alpha <= ALPHA_RANGE[1]:
        raise RouteError(400, "bad_params", f"alpha must be between {ALPHA_RANGE[0]} and {ALPHA_RANGE[1]}.")
    if not BETA_RANGE[0] <= beta <= BETA_RANGE[1]:
        raise RouteError(400, "bad_params", f"beta must be between {BETA_RANGE[0]} and {BETA_RANGE[1]}.")
    for lon, lat in ((from_lon, from_lat), (to_lon, to_lat)):
        if not (SF_BBOX[0] <= lon <= SF_BBOX[2] and SF_BBOX[1] <= lat <= SF_BBOX[3]):
            raise RouteError(400, "out_of_bounds", "Both points must be inside San Francisco.")
