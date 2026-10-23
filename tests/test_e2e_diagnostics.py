from io import StringIO
import json
from pathlib import Path

import pytest

from nullone.tools.e2e_diagnostics import run_e2e_diagnostics


@pytest.mark.asyncio
async def test_e2e_diagnostics_report_shape(monkeypatch, tmp_path):
    import nullone.tools.e2e_diagnostics as diagnostics

    async def fake_execute(config_path, **kwargs):
        return {
            "winning_bracket": "101-105",
            "net_change_bcf": 101,
            "multi_leg": True,
            "t_fetch_ms": 0.1,
            "t_parse_ms": 0.2,
            "t_map_ms": 0.3,
            "t_order_ms": 0.4,
            "t_read_vectors_ms": 0.01,
            "t_kelly_ms": 0.02,
            "t_batch_ms": 0.4,
            "kalshi": {"leg_count": 3},
        }

    monkeypatch.setattr(diagnostics, "run_from_runtime_config_path", fake_execute)
    output = tmp_path / "e2e.json"

    report = await run_e2e_diagnostics(
        config_path=Path("tests/fixtures/runtime_config.json"),
        include_ready=False,
        output=output,
    )

    assert report["will_place_orders"] is False
    assert report["ready"] is None
    assert report["hot_path"]["winning_bracket"] == "101-105"
    assert report["hot_path"]["kalshi_leg_count"] == 3
    assert output.exists()


class FlushCountingStream(StringIO):
    def __init__(self):
        super().__init__()
        self.flush_count = 0

    def flush(self):
        self.flush_count += 1
        super().flush()


@pytest.mark.asyncio
async def test_e2e_diagnostics_trace_jsonl_flushes_events(monkeypatch):
    import nullone.tools.e2e_diagnostics as diagnostics

    async def fake_execute(config_path, **kwargs):
        kwargs["trace_event"]({"event": "execute_start", "ts": "2026-05-29T00:00:00Z"})
        kwargs["trace_event"](
            {
                "event": "execute_done",
                "ts": "2026-05-29T00:00:00Z",
                "duration_ms": 1.0,
            }
        )
        return {
            "winning_bracket": "101-105",
            "t_fetch_ms": 0.1,
            "t_parse_ms": 0.2,
            "t_map_ms": 0.3,
            "t_order_ms": 0.4,
        }

    monkeypatch.setattr(diagnostics, "run_from_runtime_config_path", fake_execute)
    stream = FlushCountingStream()

    report = await run_e2e_diagnostics(
        config_path=Path("tests/fixtures/runtime_config.json"),
        include_ready=False,
        timeout_seconds=30,
        trace_jsonl=stream,
    )

    lines = stream.getvalue().splitlines()
    assert report["mode"] == "paper"
    assert stream.flush_count == len(lines)
    assert [line["event"] for line in map(json.loads, lines)] == [
        "process_start",
        "diagnostic_fixture_load_start",
        "diagnostic_fixture_loaded",
        "execute_start",
        "execute_done",
    ]
