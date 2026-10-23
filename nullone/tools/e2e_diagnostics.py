"""End-to-end diagnostics for the runtime execution hot path."""

from __future__ import annotations

import asyncio
import time
from contextlib import redirect_stdout
from importlib.resources import files
from io import StringIO
from pathlib import Path
from typing import Any, TextIO

import orjson

from nullone.engine.pipeline import run_from_runtime_config_path
from nullone.tools.ready_check import run_runtime_ready_check


def _default_wngsr_sample():
    return files("nullone.fixtures").joinpath("wngsr_sample.json")


def _hot_path_summary(execution: dict[str, Any]) -> dict[str, Any]:
    return {
        "t_fetch_ms": execution.get("t_fetch_ms"),
        "t_parse_ms": execution.get("t_parse_ms"),
        "t_map_ms": execution.get("t_map_ms"),
        "t_order_ms": execution.get("t_order_ms"),
        "t_read_vectors_ms": execution.get("t_read_vectors_ms"),
        "t_kelly_ms": execution.get("t_kelly_ms"),
        "t_batch_ms": execution.get("t_batch_ms"),
        "multi_leg": execution.get("multi_leg"),
        "kalshi_leg_count": (execution.get("kalshi") or {}).get("leg_count"),
        "winning_bracket": execution.get("winning_bracket"),
        "net_change_bcf": execution.get("net_change_bcf"),
    }


def _jsonl_tracer(stream: TextIO):
    def trace(payload: dict[str, Any]) -> None:
        print(orjson.dumps(payload).decode("utf-8"), file=stream, flush=True)

    return trace


async def run_e2e_diagnostics(
    *,
    config_path: Path | str,
    simulate_fixture: Path | None = None,
    include_ready: bool = True,
    output: Path | None = None,
    timeout_seconds: float | None = None,
    trace_jsonl: TextIO | None = None,
) -> dict[str, Any]:
    """Run a paper E2E diagnostic without placing orders.

    This uses the production runtime-config adapter and the normal execution
    pipeline, but forces paper mode and a fixture-backed EIA release so the run
    is deterministic and safe for iteration.
    """

    started_ns = time.time_ns()
    trace_event = _jsonl_tracer(trace_jsonl) if trace_jsonl is not None else None
    if trace_event is not None:
        trace_event({"event": "process_start", "ts": _utc_now()})
    ready_report = await run_runtime_ready_check(config_path) if include_ready else None
    after_ready_ns = time.time_ns()

    if trace_event is not None:
        trace_event({"event": "diagnostic_fixture_load_start", "ts": _utc_now()})
    fixture_started = time.perf_counter()
    fixture = simulate_fixture or _default_wngsr_sample()
    if trace_event is not None:
        trace_event(
            {
                "event": "diagnostic_fixture_loaded",
                "ts": _utc_now(),
                "duration_ms": round((time.perf_counter() - fixture_started) * 1000, 3),
                "source": "packaged" if simulate_fixture is None else "provided",
            }
        )
    pipeline_stdout = StringIO()

    async def execute_hot_path() -> dict[str, Any]:
        return await run_from_runtime_config_path(
            config_path,
            mode="paper",
            simulate_fixture=fixture,
            trace_event=trace_event,
        )

    with redirect_stdout(pipeline_stdout):
        if timeout_seconds is not None:
            execution = await asyncio.wait_for(execute_hot_path(), timeout=timeout_seconds)
        else:
            execution = await execute_hot_path()
    completed_ns = time.time_ns()

    report = {
        "diagnostic": "nullone_e2e_hot_path",
        "will_place_orders": False,
        "mode": "paper",
        "config_path": str(config_path),
        "simulate_fixture": str(fixture),
        "ready": ready_report,
        "execution": execution,
        "hot_path": _hot_path_summary(execution),
        "pipeline_stdout_suppressed": bool(pipeline_stdout.getvalue().strip()),
        "timing_ms": {
            "ready_wall": (after_ready_ns - started_ns) / 1_000_000,
            "execute_wall": (completed_ns - after_ready_ns) / 1_000_000,
            "total_wall": (completed_ns - started_ns) / 1_000_000,
        },
    }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2) + b"\n")
    return report


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
