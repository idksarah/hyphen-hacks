"""Regenerate mocks/route_response.json from the real router (Contract 4 example trip).

Usage:
    .venv/bin/python backend/make_mock.py

Needs data/graph.pkl (backend/build_graph.py). The mock is just a saved
/route response, so the frontend sees exactly what the API returns.
"""
import json

from routing import REPO, Router

EXAMPLE = dict(from_lon=-122.4194, from_lat=37.7749, to_lon=-122.4108, to_lat=37.7596, outages=True)


def main():
    body = Router().route(**EXAMPLE)
    out = REPO / "mocks" / "route_response.json"
    out.write_text(json.dumps(body, indent=2) + "\n")
    for name, r in body["routes"].items():
        print(f"{name:8} {r['length_m']:7.1f} m  lit {r['lit_frac']:.2f}  max gap {r['max_gap_m']:.0f} m  "
              f"{len(r['lamp_ids'])} lamps")
    print(f"wrote {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
