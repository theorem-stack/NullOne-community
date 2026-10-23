import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import yaml

from nullone.exchanges.kalshi.discover import (
    DiscoveryError,
    DiscoveryResult,
    KalshiDiscover,
    match_event_for_week,
    parse_brackets_from_event,
)
from nullone.mapping.spec_builder import build_event_spec_dict_from_discovery
from nullone.mapping.spec_loader import load_spec
from nullone.mapping.spec_resolver import (
    discover_and_write_spec,
    is_cached_spec_valid,
    resolve_spec,
)


@pytest.fixture
def kalshi_events_payload() -> dict:
    return json.loads(Path("tests/fixtures/kalshi_events_open.json").read_text())


@pytest.fixture
def mock_kalshi_auth(monkeypatch):
    monkeypatch.setenv("KALSHI_API_KEY", "test-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/dev/null")
    monkeypatch.setenv("KALSHI_GAS_STORAGE_SERIES", "KXGASSTORAGE")
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.discover.load_private_key",
        lambda **kwargs: MagicMock(),
    )
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.discover.auth_headers",
        lambda *args, **kwargs: {"Authorization": "test"},
    )


def _valid_spec_dict(week: date) -> dict:
    event = match_event_for_week(
        json.loads(Path("tests/fixtures/kalshi_events_open.json").read_text())["events"],
        week,
    )
    brackets = parse_brackets_from_event(event)
    from nullone.exchanges.kalshi.discover import DiscoveryResult

    result = DiscoveryResult(
        event_ticker=event["event_ticker"],
        event_title=event["title"],
        week_ending=week,
        brackets=brackets,
    )
    return build_event_spec_dict_from_discovery(result)


def test_build_event_spec_dict_release_at():
    spec = _valid_spec_dict(date(2026, 5, 15))
    assert spec["release_at_et"] == "2026-05-21T10:30:00-04:00"


def test_is_cached_spec_valid(tmp_path):
    week = date(2026, 5, 15)
    path = tmp_path / "spec.yaml"
    yaml.safe_dump(_valid_spec_dict(week), path.open("w"), sort_keys=False)
    assert is_cached_spec_valid(path, week) is True


def test_is_cached_spec_invalid_tbd(tmp_path):
    week = date(2026, 5, 15)
    path = tmp_path / "spec.yaml"
    spec = _valid_spec_dict(week)
    spec["brackets"][0]["kalshi"]["market_ticker"] = "TBD"
    yaml.safe_dump(spec, path.open("w"), sort_keys=False)
    assert is_cached_spec_valid(path, week) is False


@pytest.mark.asyncio
async def test_discover_and_write_spec(tmp_path, kalshi_events_payload, mock_kalshi_auth, monkeypatch):
    async def patched_enter(self):
        self._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=kalshi_events_payload)
            ),
            base_url="https://example.test/trade-api/v2",
        )
        self._owns_client = True
        return self

    monkeypatch.setattr(KalshiDiscover, "__aenter__", patched_enter)

    output = tmp_path / "eia_wngsr_2026-05-15.yaml"
    spec_dict, path = await discover_and_write_spec(date(2026, 5, 15), output=output)

    assert path == output
    assert spec_dict["kalshi_event_ticker"] == "KXGASSTORAGE-MAY15"
    assert len(spec_dict["brackets"]) == 3
    loaded = load_spec(path)
    assert loaded.brackets[0].kalshi is not None


@pytest.mark.asyncio
async def test_resolve_spec_uses_cache(tmp_path, monkeypatch):
    week = date(2026, 5, 15)
    path = tmp_path / "eia_wngsr_2026-05-15.yaml"
    yaml.safe_dump(_valid_spec_dict(week), path.open("w"), sort_keys=False)

    async def fail_discover(*args, **kwargs):
        raise AssertionError("discover should not be called when cache is valid")

    monkeypatch.setattr("nullone.mapping.spec_resolver.discover_and_write_spec", fail_discover)

    spec, resolved_path = await resolve_spec(week_ending=week, spec_path=path)
    assert resolved_path == path
    assert spec.week_ending == "2026-05-15"


@pytest.mark.asyncio
async def test_resolve_spec_rediscover_on_invalid(tmp_path, kalshi_events_payload, mock_kalshi_auth, monkeypatch):
    week = date(2026, 5, 15)
    path = tmp_path / "eia_wngsr_2026-05-15.yaml"
    bad = _valid_spec_dict(week)
    bad["brackets"][0]["kalshi"]["market_ticker"] = "TBD"
    yaml.safe_dump(bad, path.open("w"), sort_keys=False)

    async def patched_enter(self):
        self._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=kalshi_events_payload)
            ),
            base_url="https://example.test/trade-api/v2",
        )
        self._owns_client = True
        return self

    monkeypatch.setattr(KalshiDiscover, "__aenter__", patched_enter)

    spec, resolved_path = await resolve_spec(week_ending=week, spec_path=path)
    assert resolved_path == path
    assert spec.brackets[0].kalshi is not None
    assert spec.brackets[0].kalshi.market_ticker != "TBD"


@pytest.mark.asyncio
async def test_resolve_spec_no_discover_missing(tmp_path):
    week = date(2026, 5, 15)
    path = tmp_path / "missing.yaml"
    with pytest.raises(DiscoveryError, match="not found"):
        await resolve_spec(week_ending=week, spec_path=path, no_discover=True)
