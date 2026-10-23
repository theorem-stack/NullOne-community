from pathlib import Path

import pytest

from nullone.sources.csv_layout import validate_csv_layout
from nullone.sources.fast_csv import (
    parse_wngsr_csv_fast,
    parse_wngsr_csv_fallback_search,
    parse_wngsr_csv_streaming,
)
from nullone.sources.parsers import parse_wngsr_csv

FIXTURES = Path(__file__).parent / "fixtures"


def test_fast_csv_fixed_row():
    raw = (FIXTURES / "wngsr_sample.csv").read_bytes()
    layout = validate_csv_layout(raw)
    result = parse_wngsr_csv_fast(raw, layout=layout)
    assert result.reading.net_change_bcf == 101
    assert result.used_fallback is False


def test_streaming_csv_matches_fast_path():
    raw = (FIXTURES / "wngsr_sample.csv").read_bytes()
    layout = validate_csv_layout(raw)
    fast = parse_wngsr_csv_fast(raw, layout=layout).reading
    streamed = parse_wngsr_csv_streaming(raw, layout=layout)
    assert streamed.net_change_bcf == fast.net_change_bcf == 101


def test_withdrawal_negative_fast_csv():
    raw = (FIXTURES / "wngsr_withdrawal.csv").read_bytes()
    layout = validate_csv_layout(raw)
    reading = parse_wngsr_csv_streaming(raw, layout=layout)
    assert reading.net_change_bcf == -52


def test_revision_token_rejected():
    raw = (FIXTURES / "wngsr_revision.csv").read_bytes()
    layout = validate_csv_layout(raw)
    with pytest.raises(ValueError, match="Revision"):
        parse_wngsr_csv_streaming(raw, layout=layout)


def test_label_guard_triggers_fallback_search():
    raw = (FIXTURES / "wngsr_sample.csv").read_bytes()
    # Deliberately wrong total row index forces fallback path.
    bad_layout = validate_csv_layout(raw).__class__(
        header_line_no=1,
        total_line_no=99,
        net_change_col=3,
    )
    result = parse_wngsr_csv_fast(raw, layout=bad_layout)
    assert result.used_fallback is True
    assert result.reading.net_change_bcf == 101


def test_parse_wngsr_csv_public_api():
    raw = (FIXTURES / "wngsr_sample.csv").read_bytes()
    assert parse_wngsr_csv(raw).net_change_bcf == 101


def test_parse_live_format_fixture():
    raw = (FIXTURES / "wngsr_live_format.csv").read_bytes()
    layout = validate_csv_layout(raw)
    reading = parse_wngsr_csv_streaming(raw, layout=layout)
    assert reading.net_change_bcf == 101
    assert layout.total_line_no == 15
    assert layout.net_change_col == 7


def test_fallback_search_parser():
    raw = (FIXTURES / "wngsr_withdrawal.csv").read_bytes()
    assert parse_wngsr_csv_fallback_search(raw).net_change_bcf == -52


def test_fallback_search_live_format():
    raw = (FIXTURES / "wngsr_live_format.csv").read_bytes()
    assert parse_wngsr_csv_fallback_search(raw).net_change_bcf == 101
