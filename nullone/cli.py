from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import TextIO

import orjson

from nullone.backtest.replay import write_replay_report
from nullone.calendar.scheduler import next_release_after
from nullone.engine.pipeline import run_from_runtime_config_path, run_from_spec_path, run_release
from nullone.exchanges.kalshi.discover import DiscoveryError
from nullone.mapping.spec_loader import default_spec_path
from nullone.runtime_config import RuntimeConfigError

EXIT_NO_TRADE_OR_NOT_READY = 1
EXIT_DISCOVERY_ERROR = 2
EXIT_CONFIG_ERROR = 3
EXIT_UNEXPECTED_RUNTIME_ERROR = 10


def _packaged_wngsr_sample():
    return files("nullone.fixtures").joinpath("wngsr_sample.json")


def _simulate_fixture_arg(value: str | None):
    if value is None:
        return None
    if value == "packaged":
        return _packaged_wngsr_sample()
    return Path(value)


def _jsonl_tracer(stream: TextIO):
    def trace(payload: dict) -> None:
        print(orjson.dumps(payload).decode("utf-8"), file=stream, flush=True)

    return trace


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _exit_discovery_error(command: str, exc: DiscoveryError) -> None:
    code = getattr(exc, "code", "unknown")
    print(f"Kalshi markets unavailable during {command}: {exc}", file=sys.stderr)
    print(f"discovery_error_code={code}", file=sys.stderr)
    print(file=sys.stderr)
    print(
        "This is not necessarily an infrastructure failure. Markets may not be listed yet.",
        file=sys.stderr,
    )
    print(
        "Try again later, use a cached spec with --no-discover, or run `nullone simulate` "
        "to validate local execution.",
        file=sys.stderr,
    )
    sys.exit(EXIT_DISCOVERY_ERROR)


def _exit_config_error(command: str, exc: Exception) -> None:
    print(f"Invalid runtime config during {command}: {exc}", file=sys.stderr)
    sys.exit(EXIT_CONFIG_ERROR)


def _exit_unexpected_error(command: str, exc: Exception) -> None:
    print(f"Unexpected runtime error during {command}: {exc}", file=sys.stderr)
    sys.exit(EXIT_UNEXPECTED_RUNTIME_ERROR)


def main() -> None:
    parser = argparse.ArgumentParser(prog="nullone", description="EIA gas storage release trader")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run release pipeline (auto-discovers Kalshi tickers by default)")
    run_parser.add_argument("--spec", type=Path, default=None, help="Path to event spec YAML (optional)")
    run_parser.add_argument("--week", default=None, help="Week-ending date YYYY-MM-DD")
    run_parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default=None,
        help="Override execution mode",
    )
    run_parser.add_argument(
        "--simulate",
        nargs="?",
        const="packaged",
        default=None,
        help="Run against a WNGSR JSON fixture. Omit the value to use the packaged fixture.",
    )
    run_parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Use cached spec only; fail if invalid or missing",
    )
    run_parser.add_argument(
        "--force-discover",
        action="store_true",
        help="Always refresh Kalshi tickers before run",
    )

    next_parser = sub.add_parser("next-release", help="Show next scheduled WNGSR release")
    next_parser.add_argument("--week", default=None, help="Suggest spec path for week ending")

    discover_parser = sub.add_parser("discover", help="Discover Kalshi market IDs for a week")
    discover_parser.add_argument("--week", default=None, help="Week-ending date YYYY-MM-DD (default: next release)")
    discover_parser.add_argument("--output", type=Path, default=None)

    dry_parser = sub.add_parser("dry-run", help="Check order book connectivity")
    dry_parser.add_argument("--spec", type=Path, default=None, help="Path to event spec YAML (optional)")
    dry_parser.add_argument("--week", default=None, help="Week-ending date YYYY-MM-DD")
    dry_parser.add_argument("--no-discover", action="store_true")
    dry_parser.add_argument("--force-discover", action="store_true")

    replay_parser = sub.add_parser("replay", help="Backtest against archived wngsr snapshots")
    replay_parser.add_argument("--spec", type=Path, required=True)
    replay_parser.add_argument("--snapshots", type=Path, required=True)
    replay_parser.add_argument("--output", type=Path, default=Path("logs/replay_report.json"))

    bench_parser = sub.add_parser("bench-latency", help="Benchmark parser and hot-path latency")
    bench_parser.add_argument("--spec", type=Path, default=Path("specs/eia_wngsr_2026-05-15.yaml"))
    bench_parser.add_argument("--json-fixture", type=Path, default=Path("tests/fixtures/wngsr_sample.json"))
    bench_parser.add_argument("--csv-fixture", type=Path, default=Path("tests/fixtures/wngsr_sample.csv"))
    bench_parser.add_argument("--iterations", type=int, default=1000)
    bench_parser.add_argument("--live", action="store_true")
    bench_parser.add_argument("--live-iterations", type=int, default=10)
    bench_parser.add_argument("--output", type=Path, default=None)

    ready_parser = sub.add_parser("ready", help="Run pre-flight readiness checks")
    ready_parser.add_argument("--config", type=Path, default=None, help="Path to runtime JSON config")
    ready_parser.add_argument("--skip-tests", action="store_true")
    ready_parser.add_argument("--skip-markets", action="store_true")
    ready_parser.add_argument("--json", action="store_true")

    execute_parser = sub.add_parser("execute", help="Execute using generated runtime JSON config")
    execute_parser.add_argument("--config", type=Path, required=True, help="Path to runtime JSON config")
    execute_parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default=None,
        help="Override execution mode",
    )
    execute_parser.add_argument(
        "--simulate",
        nargs="?",
        const="packaged",
        default=None,
        help="Run against a WNGSR JSON fixture. Use 'packaged' for the bundled fixture.",
    )
    execute_parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Run bounded paper hot-path diagnostics without release-window waiting",
    )
    execute_parser.add_argument(
        "--paper-live-release",
        action="store_true",
        help="Wait for the live release and run with paper orders only",
    )
    execute_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Bound diagnostic execution time in seconds",
    )
    execute_parser.add_argument(
        "--trace-jsonl",
        action="store_true",
        help="Emit flushed hot-path timing events as JSONL to stdout",
    )

    health_parser = sub.add_parser("health", help="Return runtime health JSON")
    health_parser.add_argument("--config", type=Path, required=True, help="Path to runtime JSON config")

    shutdown_parser = sub.add_parser("shutdown", help="Close runtime resources and exit cleanly")
    shutdown_parser.add_argument("--config", type=Path, required=True, help="Path to runtime JSON config")

    e2e_parser = sub.add_parser(
        "e2e-diagnostics",
        help="Run paper E2E diagnostics through the runtime hot path",
    )
    e2e_parser.add_argument("--config", type=Path, required=True, help="Path to runtime JSON config")
    e2e_parser.add_argument(
        "--simulate",
        type=Path,
        default=None,
        help="Optional WNGSR JSON fixture. Defaults to packaged runtime fixture.",
    )
    e2e_parser.add_argument("--skip-ready", action="store_true")
    e2e_parser.add_argument("--output", type=Path, default=None)

    plan_parser = sub.add_parser("plan-multi-leg", help="Plan Kalshi multi-leg batch without placing orders")
    plan_parser.add_argument("--spec", type=Path, required=True)
    plan_parser.add_argument("--net-change", type=int, required=True)
    plan_parser.add_argument("--wait-seconds", type=float, default=2.0)
    plan_parser.add_argument("--output", type=Path, default=None)
    plan_parser.add_argument("--use-presigned-headers", action="store_true")

    probe_parser = sub.add_parser("kalshi-batch-probe", help="Call Kalshi batch endpoint with no-fill IOC probes")
    probe_parser.add_argument("--ticker", required=True)
    probe_parser.add_argument("--acknowledge", required=True)
    probe_parser.add_argument("--bid-only", action="store_true")
    probe_parser.add_argument("--ask-only", action="store_true")
    probe_parser.add_argument("--count", type=float, default=1.0)
    probe_parser.add_argument("--bid-price", type=float, default=0.01)
    probe_parser.add_argument("--ask-price", type=float, default=0.99)

    args = parser.parse_args()

    if args.command == "run":
        simulate_fixture = _simulate_fixture_arg(args.simulate)
        try:
            if args.spec is not None:
                result = asyncio.run(
                    run_from_spec_path(
                        args.spec,
                        mode=args.mode,
                        simulate_fixture=simulate_fixture,
                    )
                )
            else:
                result = asyncio.run(
                    run_release(
                        week_ending=args.week,
                        mode=args.mode,
                        simulate_fixture=simulate_fixture,
                        no_discover=args.no_discover,
                        force_discover=args.force_discover,
                    )
                )
        except DiscoveryError as exc:
            _exit_discovery_error("run", exc)
        sys.exit(0 if result.get("winning_bracket") else 1)

    if args.command == "next-release":
        window = next_release_after()
        print(f"week_ending={window.week_ending.isoformat()}")
        print(f"release_at_et={window.release_at.isoformat()}")
        spec_path = default_spec_path(window.week_ending.isoformat())
        print(f"suggested_spec={spec_path}")
        sys.exit(0)

    if args.command == "discover":
        from nullone.tools.discover_markets import run_discovery

        try:
            path = asyncio.run(run_discovery(args.week, args.output))
        except DiscoveryError as exc:
            _exit_discovery_error("discover", exc)
        print(path)
        sys.exit(0)

    if args.command == "dry-run":
        from nullone.tools.dry_run import dry_run

        try:
            asyncio.run(
                dry_run(
                    spec_path=args.spec,
                    week_ending=args.week,
                    no_discover=args.no_discover,
                    force_discover=args.force_discover,
                )
            )
        except DiscoveryError as exc:
            _exit_discovery_error("dry-run", exc)
        sys.exit(0)

    if args.command == "replay":
        write_replay_report(args.spec, args.snapshots, args.output)
        print(args.output)
        sys.exit(0)

    if args.command == "bench-latency":
        from nullone.tools.latency_bench import run_benchmark

        report = asyncio.run(
            run_benchmark(
                spec_path=args.spec,
                json_fixture=args.json_fixture,
                csv_fixture=args.csv_fixture,
                iterations=args.iterations,
                live=args.live,
                live_iterations=args.live_iterations,
            )
        )
        text = orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8")
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n")
        sys.exit(0)

    if args.command == "ready":
        from nullone.tools.ready_check import run_ready_check, run_runtime_ready_check

        try:
            if args.config:
                report = asyncio.run(run_runtime_ready_check(args.config))
            else:
                report = asyncio.run(
                    run_ready_check(skip_tests=args.skip_tests, skip_markets=args.skip_markets)
                )
        except RuntimeConfigError as exc:
            _exit_config_error("ready", exc)
        except Exception as exc:
            if args.config:
                _exit_unexpected_error("ready", exc)
            raise
        print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8"))
        print()
        if args.config:
            sys.exit(0 if report["ready"] else EXIT_NO_TRADE_OR_NOT_READY)
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
        sys.exit(0 if report["infra_ready"] else EXIT_NO_TRADE_OR_NOT_READY)

    if args.command == "execute":
        try:
            simulate_fixture = _simulate_fixture_arg(args.simulate)
            if args.diagnostic and args.paper_live_release:
                raise RuntimeConfigError("--diagnostic and --paper-live-release are mutually exclusive")
            if args.diagnostic:
                from nullone.tools.e2e_diagnostics import run_e2e_diagnostics

                report = asyncio.run(
                    run_e2e_diagnostics(
                        config_path=args.config,
                        simulate_fixture=simulate_fixture,
                        include_ready=False,
                        timeout_seconds=args.timeout_seconds,
                        trace_jsonl=sys.stdout if args.trace_jsonl else None,
                    )
                )
                if not args.trace_jsonl:
                    print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8"))
                sys.exit(
                    0
                    if report.get("execution", {}).get("winning_bracket")
                    else EXIT_NO_TRADE_OR_NOT_READY
                )

            trace_event = _jsonl_tracer(sys.stdout) if args.trace_jsonl else None
            if args.paper_live_release and simulate_fixture is not None:
                raise RuntimeConfigError("--paper-live-release cannot be combined with --simulate")

            async def execute_runtime():
                return await run_from_runtime_config_path(
                    args.config,
                    mode="paper" if args.paper_live_release else args.mode,
                    simulate_fixture=simulate_fixture,
                    trace_event=trace_event,
                )

            if trace_event is not None:
                trace_event({"event": "process_start", "ts": _utc_now()})
            if args.timeout_seconds:
                result = asyncio.run(asyncio.wait_for(execute_runtime(), timeout=args.timeout_seconds))
            else:
                result = asyncio.run(execute_runtime())
        except RuntimeConfigError as exc:
            _exit_config_error("execute", exc)
        except DiscoveryError as exc:
            _exit_discovery_error("execute", exc)
        except Exception as exc:
            _exit_unexpected_error("execute", exc)
        if not args.trace_jsonl:
            print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode("utf-8"))
        sys.exit(0 if result.get("winning_bracket") else EXIT_NO_TRADE_OR_NOT_READY)

    if args.command == "health":
        from nullone.tools.ready_check import run_runtime_health

        try:
            report = run_runtime_health(args.config)
        except RuntimeConfigError as exc:
            _exit_config_error("health", exc)
        except Exception as exc:
            _exit_unexpected_error("health", exc)
        print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8"))
        sys.exit(0 if report["ready"] else EXIT_NO_TRADE_OR_NOT_READY)

    if args.command == "shutdown":
        from nullone.runtime_config import load_runtime_config

        try:
            runtime_config = load_runtime_config(args.config)
        except RuntimeConfigError as exc:
            _exit_config_error("shutdown", exc)
        except Exception as exc:
            _exit_unexpected_error("shutdown", exc)
        print(
            orjson.dumps(
                {
                    "shutdown": "ok",
                    "strategy": runtime_config.strategy.get("name"),
                    "run_id": runtime_config.runtime.get("run_id"),
                },
                option=orjson.OPT_INDENT_2,
            ).decode("utf-8")
        )
        sys.exit(0)

    if args.command == "e2e-diagnostics":
        from nullone.tools.e2e_diagnostics import run_e2e_diagnostics

        try:
            report = asyncio.run(
                run_e2e_diagnostics(
                    config_path=args.config,
                    simulate_fixture=args.simulate,
                    include_ready=not args.skip_ready,
                    output=args.output,
                )
            )
        except RuntimeConfigError as exc:
            _exit_config_error("e2e-diagnostics", exc)
        except DiscoveryError as exc:
            _exit_discovery_error("e2e-diagnostics", exc)
        except Exception as exc:
            _exit_unexpected_error("e2e-diagnostics", exc)
        print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8"))
        sys.exit(0 if report.get("execution", {}).get("winning_bracket") else EXIT_NO_TRADE_OR_NOT_READY)

    if args.command == "plan-multi-leg":
        from nullone.tools.plan_multi_leg import plan_multi_leg

        asyncio.run(
            plan_multi_leg(
                spec_path=args.spec,
                net_change_bcf=args.net_change,
                wait_seconds=args.wait_seconds,
                output=args.output,
                use_presigned_headers=args.use_presigned_headers,
            )
        )
        sys.exit(0)

    if args.command == "kalshi-batch-probe":
        from nullone.tools.kalshi_batch_probe import run_kalshi_batch_probe

        asyncio.run(
            run_kalshi_batch_probe(
                ticker=args.ticker,
                acknowledge=args.acknowledge,
                include_bid=not args.ask_only,
                include_ask=not args.bid_only,
                count=args.count,
                bid_price=args.bid_price,
                ask_price=args.ask_price,
            )
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
