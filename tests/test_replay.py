from pathlib import Path

from nullone.backtest.replay import replay_snapshot
from nullone.mapping.spec_loader import load_spec


def test_replay_finds_winning_bracket():
    spec = load_spec(Path("specs/eia_wngsr_2026-05-15.yaml"))
    result = replay_snapshot(
        spec,
        Path("tests/fixtures/wngsr_sample.json"),
        kalshi_ask=0.85,
        polymarket_ask=0.85,
    )
    assert result.net_change_bcf == 101
    assert result.winning_bracket == "101-105"
    assert result.kalshi_would_trade is True
    assert result.polymarket_would_trade is True
