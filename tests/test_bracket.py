from pathlib import Path

import pytest

from nullone.mapping.bracket import BracketMapper
from nullone.mapping.spec_loader import load_spec


SPEC_PATH = Path(__file__).resolve().parents[1] / "specs" / "eia_wngsr_2026-05-15.yaml"


@pytest.fixture
def spec():
    return load_spec(SPEC_PATH)


@pytest.fixture
def mapper(spec):
    return BracketMapper(spec)


def test_map_winning_bracket(mapper):
    bracket = mapper.map_net_change(101)
    assert bracket.label == "101-105"


def test_map_boundary_min(mapper):
    bracket = mapper.map_net_change(96)
    assert bracket.label == "96-100"


def test_map_boundary_max(mapper):
    bracket = mapper.map_net_change(100)
    assert bracket.label == "96-100"


def test_unmapped_value_raises(mapper):
    with pytest.raises(ValueError):
        mapper.map_net_change(200)


def test_overlap_rejected():
    spec = load_spec(SPEC_PATH)
    bad = spec.brackets[0].__class__(
        label="bad",
        min_bcf=99,
        max_bcf=102,
        kalshi=spec.brackets[0].kalshi,
        polymarket=spec.brackets[0].polymarket,
    )
    with pytest.raises(ValueError):
        BracketMapper(
            spec.__class__(
                event_id=spec.event_id,
                week_ending=spec.week_ending,
                release_at_et=spec.release_at_et,
                metric=spec.metric,
                region=spec.region,
                brackets=[spec.brackets[0], bad],
                execution=spec.execution,
            )
        )
