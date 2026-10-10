"""Plot every street in data/graph.pkl colored by lit_frac, with lamps on top,
and print the lit share per OSM highway type. A quick sanity check after a build.

Usage:
    .venv/bin/python backend/plot_lighting.py [out.png]   # default: data/lit_map.png
"""
import pickle
import sys
from collections import defaultdict

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402

from build_graph import OUT as GRAPH, REPO, edge_line, load_lamps  # noqa: E402


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else str(REPO / "data" / "lit_map.png")
    G = pickle.loads(GRAPH.read_bytes())

    by_type = defaultdict(lambda: [0.0, 0.0])  # highway -> [length, lit length]
    segs, lit = [], []
    for u, v, k, d in G.edges(keys=True, data=True):
        hw = d.get("highway")
        hw = hw[0] if isinstance(hw, list) else hw
        by_type[hw][0] += d["length"]
        by_type[hw][1] += d["length"] * d["lit_frac"]
        if u < v:  # draw each street once
            segs.append(np.asarray(edge_line(G, u, v, d).coords))
            lit.append(d["lit_frac"])

    total = sum(L for L, _ in by_type.values())
    print(f"{'highway':16} {'share':>6} {'lit':>5}")
    for hw, (L, lit_len) in sorted(by_type.items(), key=lambda x: -x[1][0])[:12]:
        print(f"{str(hw):16} {L / total:6.1%} {lit_len / L:5.2f}")

    _, lamp_xy, _ = load_lamps()
    bg = "#0b0d17"
    fig, ax = plt.subplots(figsize=(12, 12), facecolor=bg)
    ax.set_facecolor(bg)
    ax.add_collection(LineCollection(segs, array=np.array(lit), cmap="inferno", linewidths=0.4, clim=(0, 1)))
    ax.scatter(lamp_xy[:, 0], lamp_xy[:, 1], s=0.15, c="#7fd4ff", alpha=0.5, linewidths=0)
    ax.autoscale()
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor=bg)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
