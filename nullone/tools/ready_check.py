"""Pre-flight readiness checks for NullOne."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import subprocess
import sys
import time
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx
import orjson
import websockets

from nullone.calendar.scheduler import next_release_after
from nullone.config import WNGSR_CSV_URL, WNGSR_JSON_URL, load_settings
from nullone.exchanges.kalshi.auth import auth_headers, load_private_key
from nullone.exchanges.kalshi.discover import KalshiDiscover, match_event_for_week
from nullone.mapping.spec_loader import EventSpec
from nullone.mapping.spec_loader import default_spec_path
from nullone.runtime_config import RuntimeConfig, load_runtime_spec


def _packaged_wngsr_sample():
    return files("nullone.fixtures").joinpath("wngsr_sample.json")


def _status(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


async def _check_kalshi_rest(settings) -> dict[str, Any]:
    if not settings.kalshi:
        return _status(False, error="KALSHI_API_KEY not set")
    pem_path = settings.kalshi.private_key_path
    if pem_path and not Path(pem_path).is_file():
        return _status(False, error=f"PEM not found: {pem_path}")
    try:
        key = load_private_key(
            path=settings.kalshi.private_key_path,
            pem=settings.kalshi.private_key_pem,
        )
        headers = auth_headers(
            key,
            settings.kalshi.api_key,
            method="GET",
            path="/trade-api/v2/portfolio/balance",
        )
        started = time.perf_counter()
        async with httpx.AsyncClient(base_url=settings.kalshi.base_url, timeout=20) as client:
            resp = await client.get("/portfolio/balance", headers=headers)
        latency_ms = round((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            return _status(False, status=resp.status_code, body=resp.text[:200], latency_ms=latency_ms)
        payload = resp.json()
        return _status(True, latency_ms=latency_ms, balance_cents=payload.get("balance"))
    except Exception as exc:
        return _status(False, error=str(exc))


async def _check_kalshi_ws(settings) -> dict[str, Any]:
    if not settings.kalshi:
        return _status(False, error="Kalshi not configured")
    try:
        key = load_private_key(
            path=settings.kalshi.private_key_path,
            pem=settings.kalshi.private_key_pem,
        )
        ws_path = "/trade-api/ws/v2"
        headers = auth_headers(
            key,
            settings.kalshi.api_key,
            method="GET",
            path=ws_path,
        )
        started = time.perf_counter()
        async with websockets.connect(
            settings.kalshi.ws_url,
            additional_headers=headers,
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            await ws.send(
                orjson.dumps(
                    {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {"channels": ["ticker"]},
                    }
                ).decode("utf-8")
            )
            message = await asyncio.wait_for(ws.recv(), timeout=5)
        latency_ms = round((time.perf_counter() - started) * 1000)
        return _status(
            True,
            latency_ms=latency_ms,
            first_message_type=orjson.loads(message).get("type"),
        )
    except Exception as exc:
        return _status(False, error=str(exc))


async def _check_kalshi_markets(settings, week_ending: date) -> dict[str, Any]:
    if not settings.kalshi:
        return _status(False, error="Kalshi not configured")
    try:
        async with KalshiDiscover(settings.kalshi, series_ticker=settings.kalshi_gas_storage_series) as discover:
            await discover.verify_series_ticker()
            events = await discover.fetch_tradeable_events()
            if not events:
                return _status(
                    False,
                    series=settings.kalshi_gas_storage_series,
                    tradeable_events=0,
                    error="No tradeable events listed yet for this series",
                )
            event = match_event_for_week(events, week_ending)
            brackets = [m for m in (event.get("markets") or []) if m.get("status") in {"open", "active"}]
            return _status(
                True,
                series=settings.kalshi_gas_storage_series,
                event_ticker=event.get("event_ticker"),
                tradeable_brackets=len(brackets),
            )
    except Exception as exc:
        return _status(False, series=settings.kalshi_gas_storage_series, error=str(exc))


async def _check_http(url: str) -> dict[str, Any]:
    try:
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
        latency_ms = round((time.perf_counter() - started) * 1000)
        return _status(resp.status_code == 200, status=resp.status_code, latency_ms=latency_ms, bytes=len(resp.content))
    except Exception as exc:
        return _status(False, error=str(exc))


async def _check_paper_pipeline(spec_path: Path, fixture_path: Path) -> dict[str, Any]:
    from nullone.engine.pipeline import run_from_spec_path

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = await run_from_spec_path(spec_path, mode="paper", simulate_fixture=fixture_path)
        return _status(
            bool(result.get("winning_bracket")),
            winning_bracket=result.get("winning_bracket"),
            net_change_bcf=result.get("net_change_bcf"),
        )
    except Exception as exc:
        return _status(False, error=str(exc))


async def _check_paper_pipeline_spec(spec: EventSpec, fixture_path: Path) -> dict[str, Any]:
    from nullone.engine.pipeline import run_from_spec

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = await run_from_spec(spec, mode="paper", simulate_fixture=fixture_path)
        return _status(
            bool(result.get("winning_bracket")),
            winning_bracket=result.get("winning_bracket"),
            net_change_bcf=result.get("net_change_bcf"),
        )
    except Exception as exc:
        return _status(False, error=str(exc))


def _run_pytest() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return _status(proc.returncode == 0, output=proc.stdout.strip() or proc.stderr.strip())


def _runtime_health_payload(
    runtime_config: RuntimeConfig,
    checks: dict[str, Any],
    *,
    ready: bool,
) -> dict[str, Any]:
    return {
        "ready": ready,
        "strategy": runtime_config.strategy.get("name"),
        "run_id": runtime_config.runtime.get("run_id"),
        "checks": checks,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def run_runtime_health(config_path: Path | str) -> dict[str, Any]:
    runtime_config, spec = load_runtime_spec(config_path)
    settings = load_settings()
    checks = {
        "config": _status(True, event_id=spec.event_id, week_ending=spec.week_ending),
        "secrets": _status(
            settings.kalshi is not None,
            configured=settings.kalshi is not None,
            private_key_path=(
                bool(settings.kalshi.private_key_path or settings.kalshi.private_key_pem)
                if settings.kalshi
                else False
            ),
        ),
        "logs": _status(True, path=str(settings.log_dir)),
    }
    ready = all(check.get("ok", False) for check in checks.values())
    return _runtime_health_payload(runtime_config, checks, ready=ready)


async def run_runtime_ready_check(config_path: Path | str) -> dict[str, Any]:
    runtime_config, spec = load_runtime_spec(config_path)
    settings = load_settings()
    fixture_path = _packaged_wngsr_sample()

    checks: dict[str, Any] = {
        "config": _status(True, event_id=spec.event_id, week_ending=spec.week_ending),
        "kalshi_rest": await _check_kalshi_rest(settings),
        "kalshi_ws": await _check_kalshi_ws(settings),
        "eia_json": await _check_http(WNGSR_JSON_URL),
        "eia_csv": await _check_http(WNGSR_CSV_URL),
    }

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    probe = settings.log_dir / ".write_probe"
    try:
        probe.write_text("ok")
        probe.unlink()
        checks["logs"] = _status(True, path=str(settings.log_dir))
    except OSError as exc:
        checks["logs"] = _status(False, path=str(settings.log_dir), error=str(exc))

    checks["paper_pipeline"] = await _check_paper_pipeline_spec(spec, fixture_path)
    ready = all(check.get("ok", False) for check in checks.values())
    payload = _runtime_health_payload(runtime_config, checks, ready=ready)
    payload["mode"] = spec.execution.mode
    payload["release_capital_usd"] = spec.execution.release_capital_usd
    payload["execution_strategy"] = spec.execution.execution_strategy
    return payload


async def run_ready_check(*, skip_tests: bool = False, skip_markets: bool = False) -> dict[str, Any]:
    settings = load_settings()
    release = next_release_after()
    week = release.week_ending
    spec_path = default_spec_path(week.isoformat())
    fixture_path = _packaged_wngsr_sample()
    fallback_spec = Path(__file__).resolve().parents[2] / "specs" / "eia_wngsr_2026-05-15.yaml"

    checks: dict[str, Any] = {}

    if not skip_tests:
        checks["unit_tests"] = _run_pytest()

    checks["kalshi_rest"] = await _check_kalshi_rest(settings)
    checks["kalshi_ws"] = await _check_kalshi_ws(settings)
    checks["eia_json"] = await _check_http(WNGSR_JSON_URL)
    checks["eia_csv"] = await _check_http(WNGSR_CSV_URL)

    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    probe = log_dir / ".write_probe"
    try:
        probe.write_text("ok")
        probe.unlink()
        checks["log_dir"] = _status(True, path=str(log_dir))
    except OSError as exc:
        checks["log_dir"] = _status(False, path=str(log_dir), error=str(exc))

    pipeline_spec = spec_path if spec_path.is_file() else fallback_spec
    checks["paper_pipeline"] = await _check_paper_pipeline(pipeline_spec, fixture_path)

    checks["polymarket"] = _status(
        settings.polymarket is not None,
        configured=settings.polymarket is not None,
        note="Optional unless trading Polymarket",
    )

    if not skip_markets:
        checks["kalshi_markets"] = await _check_kalshi_markets(settings, week)

    infra_keys = ["unit_tests", "kalshi_rest", "kalshi_ws", "eia_json", "eia_csv", "log_dir", "paper_pipeline"]
    if skip_tests:
        infra_keys.remove("unit_tests")

    infra_ready = all(checks[key]["ok"] for key in infra_keys if key in checks)
    release_ready = infra_ready and checks.get("kalshi_markets", {}).get("ok", False)

    blockers = [key for key in infra_keys if key in checks and not checks[key]["ok"]]
    warnings: list[str] = []
    if not checks.get("kalshi_markets", {}).get("ok", True):
        warnings.append("Kalshi markets for next release are not listed yet")
    if not checks.get("polymarket", {}).get("configured"):
        warnings.append("Polymarket is not configured")

    return {
        "infra_ready": infra_ready,
        "release_ready": release_ready,
        "mode": settings.mode,
        "release_capital_usd": settings.release_capital_usd,
        "execution_strategy": settings.execution_strategy,
        "next_release": {
            "week_ending": week.isoformat(),
            "release_at_et": release.release_at_et.isoformat(),
            "spec_path": str(spec_path),
        },
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NullOne readiness checks")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-markets", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    report = asyncio.run(
        run_ready_check(skip_tests=args.skip_tests, skip_markets=args.skip_markets)
    )

    if args.json:
        print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8"))
    else:
        print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8"))
        print()
        if report["infra_ready"]:
            print("INFRA READY: yes")
        else:
            print("INFRA READY: no")
            print("Blockers:", ", ".join(report["blockers"]) or "none")
        if report["release_ready"]:
            print("RELEASE READY: yes")
        else:
            print("RELEASE READY: no")
            if report["warnings"]:
                print("Warnings:", "; ".join(report["warnings"]))

    sys.exit(0 if report["infra_ready"] else 1)


if __name__ == "__main__":
    main()
