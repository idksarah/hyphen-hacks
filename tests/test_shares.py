"""ShareStore unit tests (Contract 5). No graph needed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from routing import RouteError  # noqa: E402
from shares import ShareStore  # noqa: E402

ROUTE = {"coords": [[-122.4194, 37.7749], [-122.4108, 37.7596]], "length_m": 1800.0, "minutes": 21.4,
         "lit_frac": 0.9, "max_gap_m": 40.0, "dark_gaps": [], "lamp_ids": [], "constellation": []}


class Clock:
    def __init__(self):
        self.t = 1_760_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return ShareStore(ttl_s=600, ended_keep_s=60, max_shares=3, clock=clock)


def make(store):
    return store.create(ROUTE, "lit", "Near Market St", "Near Bryant St")


def test_create_then_view(store):
    s = make(store)
    assert len(s["id"]) >= 12 and len(s["token"]) >= 32
    view = store.get(s["id"])
    assert view["status"] == "active" and view["location"] is None
    assert view["route"] == ROUTE and view["to_label"] == "Near Bryant St"
    assert "token" not in view


def test_location_updates_and_clamps_progress(store, clock):
    s = make(store)
    clock.t += 5
    store.update(s["id"], s["token"], (-122.415, 37.77), 8.04, 5000.0)
    loc = store.get(s["id"])["location"]
    assert loc["coord"] == [-122.415, 37.77] and loc["accuracy_m"] == 8.0
    assert loc["progress_m"] == 1800.0  # clamped to route length
    assert store.get(s["id"])["updated_at"] == loc["updated_at"]


def test_wrong_token_is_rejected(store):
    s = make(store)
    with pytest.raises(RouteError) as e:
        store.update(s["id"], "nope", (-122.415, 37.77), None, 10.0)
    assert (e.value.status, e.value.code) == (403, "bad_token")
    with pytest.raises(RouteError):
        store.end(s["id"], "nope", True)


def test_unknown_share(store):
    with pytest.raises(RouteError) as e:
        store.get("missing")
    assert (e.value.status, e.value.code) == (404, "share_not_found")


def test_end_marks_arrived_and_blocks_updates(store):
    s = make(store)
    store.end(s["id"], s["token"], arrived=True)
    assert store.get(s["id"])["status"] == "arrived"
    with pytest.raises(RouteError) as e:
        store.update(s["id"], s["token"], (-122.415, 37.77), None, 10.0)
    assert e.value.code == "share_ended"
    store.end(s["id"], s["token"], arrived=False)  # ending twice keeps the first outcome
    assert store.get(s["id"])["status"] == "arrived"


def test_idle_shares_expire(store, clock):
    s = make(store)
    clock.t += 601
    with pytest.raises(RouteError):
        store.get(s["id"])


def test_ended_shares_stay_viewable_briefly(store, clock):
    s = make(store)
    store.end(s["id"], s["token"], arrived=False)
    clock.t += 59
    assert store.get(s["id"])["status"] == "ended"
    clock.t += 2
    with pytest.raises(RouteError):
        store.get(s["id"])


def test_capacity_limit_frees_up_after_expiry(store, clock):
    for _ in range(3):
        make(store)
    with pytest.raises(RouteError) as e:
        make(store)
    assert e.value.status == 503
    clock.t += 601
    make(store)  # the old ones expired


def test_reroute_swaps_route_and_resets_progress(store):
    s = make(store)
    store.update(s["id"], s["token"], (-122.415, 37.77), None, 500.0)
    new = {**ROUTE, "length_m": 2100.0}
    store.set_route(s["id"], s["token"], new, "shortest")
    view = store.get(s["id"])
    assert view["route"]["length_m"] == 2100.0 and view["route_name"] == "shortest"
    assert view["location"]["progress_m"] == 0.0
    with pytest.raises(RouteError):
        store.set_route(s["id"], "nope", new, "lit")
