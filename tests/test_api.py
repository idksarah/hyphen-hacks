"""Contract 4 checks against the real graph. Needs data/graph.pkl (backend/build_graph.py)."""
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

if not (REPO / "data" / "graph.pkl").exists():
    pytest.skip("data/graph.pkl not built; run backend/build_graph.py", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from scoring import DEFAULT_ALPHA, DEFAULT_BETA  # noqa: E402
from server import app  # noqa: E402

EXAMPLE = dict(from_lon=-122.4194, from_lat=37.7749, to_lon=-122.4108, to_lat=37.7596)
ROUTE_KEYS = {"coords", "length_m", "minutes", "lit_frac", "max_gap_m", "dark_gaps", "lamp_ids", "constellation"}
SF = (-122.52, 37.70, -122.35, 37.84)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs startup, loading the graph once
        yield c


def in_sf(p):
    return SF[0] <= p[0] <= SF[2] and SF[1] <= p[1] <= SF[3]


def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] is True and body["lamps"] > 0 and body["dead"] >= 0
    assert body["built_at"].endswith("Z")


def test_route_has_contract_shape(client):
    res = client.get("/route", params=EXAMPLE)
    assert res.status_code == 200
    body = res.json()
    mock = json.loads((REPO / "mocks" / "route_response.json").read_text())
    assert set(body) == set(mock)
    assert body["request"] == {"from": [EXAMPLE["from_lon"], EXAMPLE["from_lat"]],
                               "to": [EXAMPLE["to_lon"], EXAMPLE["to_lat"]],
                               "alpha": DEFAULT_ALPHA, "beta": DEFAULT_BETA, "outages": False}
    for name in ("shortest", "lit"):
        r = body["routes"][name]
        assert set(r) == ROUTE_KEYS
        assert r["coords"][0] == body["snapped"]["from"] and r["coords"][-1] == body["snapped"]["to"]
        assert all(in_sf(p) for p in r["coords"])
        assert 0 <= r["lit_frac"] <= 1
        assert r["minutes"] == pytest.approx(r["length_m"] / 1.4 / 60, abs=0.06)
        assert all(g["length_m"] >= 25 and len(g["coords"]) >= 2 for g in r["dark_gaps"])
        assert r["max_gap_m"] >= max((g["length_m"] for g in r["dark_gaps"]), default=0)
        assert len(r["lamp_ids"]) == len(set(r["lamp_ids"]))
        assert len(r["constellation"]) <= 15
        assert [c["id"] for c in r["constellation"]] == [i for i in r["lamp_ids"] if i in {c["id"] for c in r["constellation"]}]


def test_lit_route_is_better_lit_and_within_cap(client):
    body = client.get("/route", params=EXAMPLE).json()
    s, l = body["routes"]["shortest"], body["routes"]["lit"]
    assert l["lit_frac"] >= s["lit_frac"]
    assert l["length_m"] <= 1.5 * s["length_m"] + 1
    assert s["length_m"] <= l["length_m"] + 1e-6


def test_outages_drop_dead_lamps(client):
    dead = {d["lamp_id"] for d in json.loads((REPO / "data" / "dead_lamps.json").read_text())["dead"]}
    body = client.get("/route", params={**EXAMPLE, "outages": "true"}).json()
    assert body["request"]["outages"] is True
    for r in body["routes"].values():
        assert not dead & set(r["lamp_ids"])


def test_route_is_fast(client):
    client.get("/route", params=EXAMPLE)  # warm
    t = time.perf_counter()
    client.get("/route", params={**EXAMPLE, "alpha": 5, "beta": 0.05, "outages": "true"})
    assert time.perf_counter() - t < 1.0


def test_cors_header(client):
    res = client.get("/route", params=EXAMPLE, headers={"Origin": "http://localhost:5173"})
    assert res.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("params, status, code", [
    ({**EXAMPLE, "from_lon": None}, 400, "bad_params"),
    ({**EXAMPLE, "from_lon": "abc"}, 400, "bad_params"),
    ({**EXAMPLE, "from_lon": "nan"}, 400, "bad_params"),
    ({**EXAMPLE, "alpha": 11}, 400, "bad_params"),
    ({**EXAMPLE, "beta": -0.1}, 400, "bad_params"),
    ({**EXAMPLE, "to_lon": -122.2}, 400, "out_of_bounds"),
    ({**EXAMPLE, "to_lon": EXAMPLE["from_lon"], "to_lat": EXAMPLE["from_lat"]}, 400, "same_point"),
])
def test_errors(client, params, status, code):
    res = client.get("/route", params={k: v for k, v in params.items() if v is not None})
    assert res.status_code == status
    body = res.json()
    assert body["error"] == code and isinstance(body["message"], str) and body["message"]


# --- Contract 5: live walk sharing -------------------------------------------------

def make_share(client):
    route = client.get("/route", params=EXAMPLE).json()["routes"]["lit"]
    res = client.post("/shares", json={"route": route, "route_name": "lit",
                                       "from_label": "Near Market St", "to_label": "Near Bryant St"})
    assert res.status_code == 201
    return route, res.json()


def test_share_round_trip(client):
    route, share = make_share(client)
    view = client.get(f"/shares/{share['id']}").json()
    assert view["status"] == "active" and view["location"] is None
    assert view["route"]["coords"] == route["coords"]
    assert "token" not in view

    mid = route["coords"][len(route["coords"]) // 2]
    res = client.post(f"/shares/{share['id']}/location",
                      json={"token": share["token"], "coord": mid, "accuracy_m": 6, "progress_m": 900})
    assert res.status_code == 200
    loc = client.get(f"/shares/{share['id']}").json()["location"]
    assert loc["coord"] == [round(c, 6) for c in mid] and loc["progress_m"] == 900

    assert client.post(f"/shares/{share['id']}/end", json={"token": share["token"], "arrived": True}).status_code == 200
    assert client.get(f"/shares/{share['id']}").json()["status"] == "arrived"


def test_share_rejects_wrong_token(client):
    route, share = make_share(client)
    res = client.post(f"/shares/{share['id']}/location",
                      json={"token": "x" * 32, "coord": route["coords"][0], "progress_m": 0})
    assert res.status_code == 403 and res.json()["error"] == "bad_token"


@pytest.mark.parametrize("patch", [
    {"route_name": "fastest"},
    {"route": {"coords": [[0, 0], [1, 1]]}},
])
def test_share_create_validation(client, patch):
    route = client.get("/route", params=EXAMPLE).json()["routes"]["lit"]
    body = {"route": route, "route_name": "lit", **patch}
    if "route" in patch:
        body["route"] = {**route, **patch["route"]}
    res = client.post("/shares", json=body)
    assert res.status_code == 400 and res.json()["error"] == "bad_params"


def test_share_location_outside_sf_rejected(client):
    _, share = make_share(client)
    res = client.post(f"/shares/{share['id']}/location",
                      json={"token": share["token"], "coord": [-73.98, 40.75], "progress_m": 0})
    assert res.status_code == 400


def test_unknown_share_404(client):
    res = client.get("/shares/doesnotexist")
    assert res.status_code == 404 and res.json()["error"] == "share_not_found"


def test_serves_frontend_but_not_the_rest_of_the_repo(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/frontend/"
    assert "maplibre" in client.get("/frontend/").text
    assert client.get("/data/lamps.geojson").status_code == 200
    assert client.get("/mocks/route_response.json").status_code == 200
    for path in ("/data/graph.pkl", "/backend/server.py", "/.git/config", "/frontend/../backend/server.py"):
        assert client.get(path).status_code in (400, 404), path
