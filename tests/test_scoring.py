import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from scoring import (  # noqa: E402
    LIGHT_R_M,
    STEP_M,
    EdgeScore,
    LampIndex,
    dark_runs,
    edge_cost,
    sample_offsets,
    score_samples,
)


def street(length, lamps_at, lamp_offset=0.0):
    """Score a straight east-west street with lamps at the given distances along it."""
    offsets, cell = sample_offsets(length)
    points = np.column_stack([offsets, np.zeros_like(offsets)])
    idx = LampIndex([f"sfpuc:L{i}" for i in range(len(lamps_at))], [(x, lamp_offset) for x in lamps_at])
    return score_samples(idx.lit(points), cell), idx, points, offsets


def test_samples_cover_the_street_exactly():
    offsets, cell = sample_offsets(100.0)
    assert cell <= STEP_M
    assert len(offsets) * cell == pytest.approx(100.0)
    assert offsets[0] == pytest.approx(cell / 2)


def test_short_street_gets_one_sample():
    offsets, cell = sample_offsets(1.2)
    assert len(offsets) == 1 and cell == pytest.approx(1.2)


def test_one_lamp_in_the_middle():
    s, *_ = street(100.0, [50.0])
    assert s.lit_frac == pytest.approx(0.4, abs=STEP_M / 100)
    assert s.lead_dark == pytest.approx(30.0, abs=STEP_M)
    assert s.trail_dark == pytest.approx(30.0, abs=STEP_M)
    assert s.max_gap == pytest.approx(30.0, abs=STEP_M)


def test_no_lamps_is_all_dark():
    s, *_ = street(80.0, [])
    assert s == EdgeScore(0.0, pytest.approx(80.0), pytest.approx(80.0), pytest.approx(80.0))


def test_fully_lit_street():
    s, *_ = street(60.0, [10.0, 30.0, 50.0])
    assert s == EdgeScore(1.0, 0.0, 0.0, 0.0)


def test_lamp_beyond_radius_from_street_does_not_count():
    s, *_ = street(50.0, [25.0], lamp_offset=LIGHT_R_M + 1)
    assert s.lit_frac == 0.0


def test_gap_in_the_middle_is_not_lead_or_trail():
    s, *_ = street(200.0, [10.0, 190.0])
    assert s.lead_dark == 0.0 and s.trail_dark == 0.0
    assert s.max_gap == pytest.approx(140.0, abs=STEP_M)


def test_reversed_swaps_lead_and_trail():
    s, *_ = street(100.0, [20.0])
    r = s.reversed()
    assert (r.lead_dark, r.trail_dark) == (s.trail_dark, s.lead_dark)
    assert (r.lit_frac, r.max_gap) == (s.lit_frac, s.max_gap)


def test_dark_runs_positions():
    assert dark_runs([True, False, False, True, False], 3.0) == [(3.0, 9.0), (12.0, 15.0)]
    assert dark_runs([True, True], 3.0) == []


def test_lamps_along_is_in_walking_order_and_deduplicated():
    _, idx, points, offsets = street(120.0, [100.0, 10.0, 55.0])
    assert idx.lamps_along(points, offsets) == ["sfpuc:L1", "sfpuc:L2", "sfpuc:L0"]
    # walking v->u: same points, distances measured from the other end
    assert idx.lamps_along(points[::-1], 120.0 - offsets[::-1]) == ["sfpuc:L0", "sfpuc:L2", "sfpuc:L1"]


def test_without_removes_dead_lamps():
    _, idx, points, offsets = street(60.0, [10.0, 50.0])
    alive = idx.without(["sfpuc:L0"])
    assert alive.lamps_along(points, offsets) == ["sfpuc:L1"]
    assert idx.without(idx.ids).lit(points).sum() == 0


def test_cost_matches_contract_formula():
    assert edge_cost(100.0, 1.0, 0.0) == pytest.approx(100.0)
    assert edge_cost(100.0, 0.5, 50.0, alpha=3.0, beta=0.02) == pytest.approx(100 * 2.5 + 0.02 * 2500)
    # darker is always more expensive
    assert edge_cost(100.0, 0.2, 80.0) > edge_cost(100.0, 0.8, 20.0)
