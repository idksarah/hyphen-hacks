"""Lighting scores for street edges and routes (Contract 3 in docs/CONTRACTS.md).

Everything here works in projected meters (EPSG:32610). A street is cut into
cells of at most STEP_M; each cell's midpoint is a sample. Each sample stands
for its cell's length, so dark-run lengths add up exactly to the street's length.

Brightness follows the inverse square law: a lamp of intensity I mounted
LAMP_HEIGHT_M up gives E = I / (r^2 + h^2) at a ground point r meters away
horizontally, and nearby lamps add. A sample is lit when E reaches LIT_E,
calibrated so a single lamp alone lights out to LIGHT_R_M. All lamps get the
same intensity (the lamp data has no wattage), so E is in relative units:
1.0 is the threshold.
"""
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

STEP_M = 3.0
LIGHT_R_M = 20.0      # how far one lamp alone lights the ground; also the lamp_ids radius
LAMP_HEIGHT_M = 9.0   # typical SF streetlight pole
LAMP_I = LIGHT_R_M**2 + LAMP_HEIGHT_M**2  # intensity that puts E = 1 at LIGHT_R_M
LIT_E = 1.0
E_CUTOFF_M = 80.0     # ignore lamps farther than this; each adds < 7% of LIT_E
E_MAX_LAMPS = 32      # nearest lamps summed per sample
DEFAULT_ALPHA = 2.0  # tuned with backend/tune.py
DEFAULT_BETA = 0.005


@dataclass(frozen=True)
class EdgeScore:
    lit_frac: float
    max_gap: float     # longest dark run inside the edge, m
    lead_dark: float   # dark run touching the edge's start, m
    trail_dark: float  # dark run touching the edge's end, m

    def reversed(self):
        """Score of the same street walked v->u."""
        return EdgeScore(self.lit_frac, self.max_gap, self.trail_dark, self.lead_dark)


def sample_offsets(length, step=STEP_M):
    """Distances along a street of `length` m at which to sample, and the cell width."""
    n = max(1, int(np.ceil(length / step)))
    cell = length / n
    return (np.arange(n) + 0.5) * cell, cell


def dark_runs(lit_mask, cell):
    """(start_m, end_m) of each run of dark samples, in order."""
    lit = np.asarray(lit_mask, dtype=bool)
    # +1 where a dark run starts, -1 just past where it ends
    edges = np.diff(np.concatenate(([0], (~lit).astype(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return [(s * cell, e * cell) for s, e in zip(starts, ends)]


def score_samples(lit_mask, cell):
    """EdgeScore from one street's samples, in walking order."""
    lit = np.asarray(lit_mask, dtype=bool)
    if lit.size == 0:
        return EdgeScore(1.0, 0.0, 0.0, 0.0)
    runs = dark_runs(lit, cell)
    total = lit.size * cell
    lead = runs[0][1] if runs and runs[0][0] == 0 else 0.0
    trail = total - runs[-1][0] if runs and np.isclose(runs[-1][1], total) else 0.0
    return EdgeScore(
        lit_frac=float(lit.mean()),
        max_gap=float(max((e - s for s, e in runs), default=0.0)),
        lead_dark=float(lead),
        trail_dark=float(trail),
    )


def edge_cost(length, lit_frac, max_gap, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA):
    """Lit-route weight: dark meters cost (1 + alpha) times lit ones, plus beta * gap^2."""
    return length * (1 + alpha * (1 - lit_frac)) + beta * max_gap**2


class LampIndex:
    """Nearest-lamp lookups over lamps in projected meters."""

    def __init__(self, ids, xy, radius=LIGHT_R_M, height=LAMP_HEIGHT_M, intensity=LAMP_I):
        self.ids = list(ids)
        self.xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        self.radius = radius
        self.height, self.intensity = height, intensity
        self.tree = cKDTree(self.xy) if len(self.ids) else None

    def without(self, drop_ids):
        """A copy with some lamps removed (e.g. reported outages)."""
        drop = set(drop_ids)
        keep = [i for i, lid in enumerate(self.ids) if lid not in drop]
        return LampIndex([self.ids[i] for i in keep], self.xy[keep], self.radius, self.height, self.intensity)

    def illuminance(self, points, chunk=200_000):
        """Summed inverse-square brightness at each ground point (LIT_E = lit)."""
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        out = np.zeros(len(points))
        if self.tree is None:
            return out
        k = min(E_MAX_LAMPS, len(self.ids))
        for i in range(0, len(points), chunk):  # bounds memory on citywide builds
            dist, _ = self.tree.query(points[i:i + chunk], k=k, distance_upper_bound=E_CUTOFF_M)
            dist = np.asarray(dist).reshape(len(dist), -1)
            e = self.intensity / (dist**2 + self.height**2)  # inf distance -> 0
            out[i:i + chunk] = e.sum(axis=1)
        return out

    def lit(self, points):
        """Boolean mask: which points are at least as bright as LIT_E."""
        return self.illuminance(points) >= LIT_E * (1 - 1e-9)

    def lamps_along(self, points, offsets):
        """Ids of lamps within radius of any point, ordered by the offset where each is closest."""
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        if self.tree is None or len(points) == 0:
            return []
        best = {}  # lamp index -> (dist, offset)
        for p, off, near in zip(points, offsets, self.tree.query_ball_point(points, self.radius)):
            for j in near:
                d = float(np.hypot(*(self.xy[j] - p)))
                if j not in best or d < best[j][0]:
                    best[j] = (d, float(off))
        return [self.ids[j] for j in sorted(best, key=lambda j: best[j][1])]
