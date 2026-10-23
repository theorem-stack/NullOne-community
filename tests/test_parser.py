from importlib.resources import files
from pathlib import Path

import pytest

from nullone.sources.parsers import extract_lower48, parse_wngsr_json


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_sample_net_change():
    raw = files("nullone.fixtures").joinpath("wngsr_sample.json").read_bytes()
    reading = parse_wngsr_json(raw)
    assert reading.current_week == "2026-05-15"
    assert reading.net_change_bcf == 101
    assert reading.revision_flag is False


def test_parse_withdrawal_negative():
    raw = (FIXTURES / "wngsr_withdrawal.json").read_bytes()
    reading = parse_wngsr_json(raw)
    assert reading.net_change_bcf == -52


def test_revision_flag_detected():
    doc = {
        "current_week": "2026-01-01",
        "release_date": "2026-01-08",
        "series": [
            {
                "series_id": "png.nw2_epg0_swo_r48_bcf.w",
                "calculated": {
                    "net_change": 50,
                    "revision_flag_net_change": "true",
                },
            }
        ],
    }
    reading = extract_lower48(doc)
    assert reading.revision_flag is True
