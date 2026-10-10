"""Sweep alpha and beta over random SF trips and report lighting vs. detour.

Usage:
    .venv/bin/python backend/tune.py [n_trips]   # default 120; ~5 min
"""
import math
import random
import sys
import time
from statistics import mean, median

import numpy as np

from routing import Router

ALPHAS = (1.0, 2.0, 3.0, 5.0, 8.0)
BETAS = (0.0, 0.005, 0.01, 0.02, 0.05)
TRIP_M = (800, 3000)  # straight-line distance range


def sample_trips(R, n, seed=0):
    rng = random.Random(seed)
    nodes = list(R.G.nodes)
    trips = []
    while len(trips) < n:
        a, b = rng.sample(nodes, 2)
        A, B = R.G.nodes[a], R.G.nodes[b]
        if TRIP_M[0] <= math.hypot(A["x"] - B["x"], A["y"] - B["y"]) <= TRIP_M[1]:
            trips.append(((A["lon"], A["lat"]), (B["lon"], B["lat"])))
    return trips


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    R = Router()
    trips = sample_trips(R, n)

    base = [R.route(*f, *t, alpha=0.0, beta=0.0)["routes"]["shortest"] for f, t in trips]
    print(f"{n} trips; shortest routes: lit {mean(r['lit_frac'] for r in base):.2f}, "
          f"median max gap {median(r['max_gap_m'] for r in base):.0f} m, "
          f"{np.mean([r['max_gap_m'] > 100 for r in base]):.0%} have a gap > 100 m\n")

    print(f"{'alpha':>5} {'beta':>6} | {'lit':>4} {'+lit':>5} {'gap_med':>7} {'gap>100':>7} | "
          f"{'detour_med':>10} {'detour_p90':>10} {'capped':>6} {'same':>5} | {'ms':>4}")
    for a in ALPHAS:
        for b in BETAS:
            t0 = time.perf_counter()
            res = [R.route(*f, *t, alpha=a, beta=b) for f, t in trips]
            ms = (time.perf_counter() - t0) / n * 1000
            lit = [r["routes"]["lit"] for r in res]
            detour = [l["length_m"] / s["length_m"] - 1 for l, s in zip(lit, base)]
            print(f"{a:5.1f} {b:6.3f} | {mean(l['lit_frac'] for l in lit):4.2f} "
                  f"{mean(l['lit_frac'] - s['lit_frac'] for l, s in zip(lit, base)):+5.2f} "
                  f"{median(l['max_gap_m'] for l in lit):7.0f} {np.mean([l['max_gap_m'] > 100 for l in lit]):7.0%} | "
                  f"{median(detour):10.1%} {np.percentile(detour, 90):10.1%} "
                  f"{np.mean([r['detour_capped'] for r in res]):6.0%} {np.mean([r['same_route'] for r in res]):5.0%} | "
                  f"{ms:4.0f}", flush=True)


if __name__ == "__main__":
    main()
