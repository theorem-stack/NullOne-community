import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from nullone.exchanges.kalshi.discover import (
    DiscoveryError,
    KalshiDiscover,
    discover_kalshi_brackets,
    is_tradeable_market_status,
    match_event_for_week,
    parse_brackets_from_event,
)
from nullone.mapping.validate import validate_discovered_brackets


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


def test_match_event_for_week(kalshi_events_payload):
    events = kalshi_events_payload["events"]
    event = match_event_for_week(events, date(2026, 5, 15))
    assert event["event_ticker"] == "KXGASSTORAGE-MAY15"


def test_match_event_for_week_not_found(kalshi_events_payload):
    events = kalshi_events_payload["events"]
    with pytest.raises(DiscoveryError):
        match_event_for_week(events, date(2026, 1, 1))


def test_parse_brackets_from_event(kalshi_events_payload):
    event = kalshi_events_payload["events"][0]
    brackets = parse_brackets_from_event(event)
    assert len(brackets) == 3
    assert brackets[0].market_ticker == "KXGASSTORAGE-MAY15-96"
    assert brackets[0].min_bcf == 96
    assert brackets[0].max_bcf == 100


def test_validate_discovered_brackets(kalshi_events_payload):
    event = kalshi_events_payload["events"][0]
    brackets = parse_brackets_from_event(event)
    validate_discovered_brackets(brackets)


def test_validate_discovered_brackets_too_few(kalshi_events_payload):
    event = kalshi_events_payload["events"][0]
    brackets = parse_brackets_from_event(event)[:2]
    with pytest.raises(DiscoveryError, match="at least 3"):
        validate_discovered_brackets(brackets)


def test_validate_discovered_brackets_accepts_active_status(kalshi_events_payload):
    event = kalshi_events_payload["events"][0]
    brackets = parse_brackets_from_event(event)
    assert any(b.status == "active" for b in brackets)
    validate_discovered_brackets(brackets)


def test_is_tradeable_market_status():
    assert is_tradeable_market_status("open")
    assert is_tradeable_market_status("active")
    assert not is_tradeable_market_status("closed")


@pytest.mark.asyncio
async def test_discover_kalshi_brackets_requires_credentials(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("KALSHI_GAS_STORAGE_SERIES", "KXGAS")

    with pytest.raises(DiscoveryError) as exc_info:
        await discover_kalshi_brackets(date(2026, 5, 15))
    assert exc_info.value.code == "auth_missing"


@pytest.mark.asyncio
async def test_discover_kalshi_brackets_mocked(kalshi_events_payload, mock_kalshi_auth):
    async def handler(request: httpx.Request) -> httpx.Response:
        if "/series/" in request.url.path:
            return httpx.Response(200, json={"series": {"ticker": "KXGASSTORAGE", "title": "Gas weekly"}})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=kalshi_events_payload)
        raise AssertionError(f"unexpected path {request.url.path}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test/trade-api/v2") as client:
        from nullone.config import load_settings

        settings = load_settings()
        assert settings.kalshi is not None
        discover = KalshiDiscover(
            settings.kalshi,
            series_ticker="KXGASSTORAGE",
            client=client,
        )
        async with discover:
            result = await discover.discover_for_week(date(2026, 5, 15))

    assert result.event_ticker == "KXGASSTORAGE-MAY15"
    assert len(result.brackets) == 3


@pytest.mark.asyncio
async def test_discover_kalshi_brackets_helper(kalshi_events_payload, mock_kalshi_auth, monkeypatch):
    async def patched_enter(self):
        self._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=(
                        {"series": {"ticker": "KXGASSTORAGE", "title": "Gas weekly"}}
                        if request.url.path.startswith("/series/")
                        else kalshi_events_payload
                    ),
                )
            ),
            base_url="https://example.test/trade-api/v2",
        )
        self._owns_client = True
        return self

    monkeypatch.setattr(KalshiDiscover, "__aenter__", patched_enter)

    result = await discover_kalshi_brackets(date(2026, 5, 15))
    assert result.brackets[1].label == "101-105"
