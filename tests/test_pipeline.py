import asyncio
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nullone.engine.pipeline import run_from_spec_path, run_release


@pytest.fixture
def mock_kalshi_credentials(monkeypatch):
    monkeypatch.setenv("KALSHI_API_KEY", "test-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/dev/null")
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.orders.load_private_key",
        lambda **kwargs: MagicMock(),
    )
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.ws_book.load_private_key",
        lambda **kwargs: MagicMock(),
    )
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.orders.auth_headers",
        lambda *args, **kwargs: {"Authorization": "test"},
    )
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.ws_book.auth_headers",
        lambda *args, **kwargs: {"Authorization": "test"},
    )


@pytest.mark.asyncio
async def test_pipeline_simulate_paper_run(tmp_path, monkeypatch, mock_kalshi_credentials):
    monkeypatch.setenv("NULLONE_LOG_DIR", str(tmp_path))
    spec = Path("specs/eia_wngsr_2026-05-15.yaml")
    fixture = Path("tests/fixtures/wngsr_sample.json")

    result = await run_from_spec_path(
        spec,
        mode="paper",
        simulate_fixture=fixture,
    )

    assert result["net_change_bcf"] == 101
    assert result["winning_bracket"] == "101-105"
    assert result["mode"] == "paper"
    assert result["kalshi"]["paper"] is True
    assert result["polymarket"]["paper"] is True
    assert list(tmp_path.glob("*.json"))


@pytest.mark.asyncio
async def test_pipeline_simulate_multi_leg_paper_run(tmp_path, monkeypatch, mock_kalshi_credentials):
    monkeypatch.setenv("NULLONE_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("NULLONE_EXECUTION_STRATEGY", "auto")
    monkeypatch.setenv("NULLONE_RELEASE_CAPITAL_USD", "30")
    monkeypatch.setenv("NULLONE_MIN_LEG_USD", "1")
    monkeypatch.setenv("NULLONE_MIN_FILL_PROB", "0")
    monkeypatch.setenv("NULLONE_FORCE_WINNER_FIRST", "false")
    spec = Path("specs/eia_wngsr_2026-05-15.yaml")
    fixture = Path("tests/fixtures/wngsr_sample.json")

    result = await run_from_spec_path(
        spec,
        mode="paper",
        simulate_fixture=fixture,
    )

    assert result["multi_leg"] is True
    assert result["kalshi"]["paper"] is True
    assert result["kalshi"]["leg_count"] >= 1
    assert result["kalshi_multi_leg"]["legs"]
    assert {leg["role"] for leg in result["kalshi_multi_leg"]["legs"]} >= {"winner_yes"}


@pytest.mark.asyncio
async def test_run_release_with_cached_spec(tmp_path, monkeypatch, mock_kalshi_credentials):
    monkeypatch.setenv("NULLONE_LOG_DIR", str(tmp_path))
    week = date(2026, 5, 15)
    spec_path = tmp_path / "eia_wngsr_2026-05-15.yaml"
    spec_path.write_text(Path("specs/eia_wngsr_2026-05-15.yaml").read_text())

    async def resolve_cached(**kwargs):
        from nullone.mapping.spec_loader import load_spec

        return load_spec(spec_path), spec_path

    monkeypatch.setattr("nullone.mapping.spec_resolver.resolve_spec", resolve_cached)

    result = await run_release(
        week_ending=week.isoformat(),
        mode="paper",
        simulate_fixture=Path("tests/fixtures/wngsr_sample.json"),
    )

    assert result["winning_bracket"] == "101-105"
    assert result["spec_path"] == str(spec_path)


@pytest.mark.asyncio
async def test_run_release_simulate_auto_uses_cached_spec(tmp_path, monkeypatch, mock_kalshi_credentials):
    monkeypatch.setenv("NULLONE_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KALSHI_GAS_STORAGE_SERIES", "KXGAS")

    result = await run_release(
        mode="paper",
        simulate_fixture=Path("tests/fixtures/wngsr_sample.json"),
    )

    assert result["winning_bracket"] == "101-105"
    assert result["spec_path"].endswith("specs/eia_wngsr_2026-05-15.yaml")


@pytest.mark.asyncio
async def test_run_release_auto_discover_mocked(tmp_path, monkeypatch, mock_kalshi_credentials):
    import json

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    monkeypatch.setenv("NULLONE_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KALSHI_GAS_STORAGE_SERIES", "KXGASSTORAGE")
    monkeypatch.setattr("nullone.config.SPECS_DIR", specs_dir)
    monkeypatch.setattr("nullone.mapping.spec_loader.SPECS_DIR", specs_dir)
    payload = json.loads(Path("tests/fixtures/kalshi_events_open.json").read_text())

    async def fake_discover(week_ending, settings=None):
        from nullone.exchanges.kalshi.discover import DiscoveryResult, match_event_for_week, parse_brackets_from_event

        event = match_event_for_week(payload["events"], week_ending)
        return DiscoveryResult(
            event_ticker=event["event_ticker"],
            event_title=event["title"],
            week_ending=week_ending,
            brackets=parse_brackets_from_event(event),
        )

    monkeypatch.setattr("nullone.mapping.spec_resolver.discover_kalshi_brackets", fake_discover)

    result = await run_release(
        week_ending="2026-05-15",
        mode="paper",
        simulate_fixture=Path("tests/fixtures/wngsr_sample.json"),
        force_discover=True,
    )

    spec_path = Path(result["spec_path"])
    assert spec_path.exists()
    assert result["winning_bracket"] == "101-105"
    assert "KXGASSTORAGE-MAY15" in spec_path.read_text()
