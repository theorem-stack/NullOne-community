import json
import sys
import time
from pathlib import Path

import pytest

from nullone.exchanges.kalshi.discover import DiscoveryError


def test_dry_run_discovery_error_is_friendly(monkeypatch, capsys):
    from nullone import cli
    import nullone.tools.dry_run as dry_run_module

    async def fail_discovery(**kwargs):
        raise DiscoveryError("No tradeable Kalshi events found for series_ticker=KXGAS.")

    monkeypatch.setattr(sys, "argv", ["nullone", "dry-run"])
    monkeypatch.setattr(dry_run_module, "dry_run", fail_discovery)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert "Kalshi markets unavailable during dry-run" in captured.err
    assert "discovery_error_code=unknown" in captured.err
    assert "Markets may not be listed yet" in captured.err
    assert "Traceback" not in captured.err


def test_run_discovery_error_is_friendly(monkeypatch, capsys):
    from nullone import cli

    async def fail_discovery(**kwargs):
        raise DiscoveryError("No tradeable Kalshi events found for series_ticker=KXGAS.")

    monkeypatch.setattr(sys, "argv", ["nullone", "run", "--mode", "paper"])
    monkeypatch.setattr(cli, "run_release", fail_discovery)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert "Kalshi markets unavailable during run" in captured.err
    assert "discovery_error_code=unknown" in captured.err
    assert "Markets may not be listed yet" in captured.err
    assert "Traceback" not in captured.err


def test_run_simulate_without_path_uses_packaged_fixture(monkeypatch):
    from nullone import cli

    async def fake_run_release(**kwargs):
        assert kwargs["simulate_fixture"].name == "wngsr_sample.json"
        return {"winning_bracket": "101-105"}

    monkeypatch.setattr(sys, "argv", ["nullone", "run", "--mode", "paper", "--simulate"])
    monkeypatch.setattr(cli, "run_release", fake_run_release)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0


def test_health_config_outputs_runtime_json(monkeypatch, capsys):
    from nullone import cli

    monkeypatch.setattr(
        sys,
        "argv",
        ["nullone", "health", "--config", "tests/fixtures/runtime_config.json"],
    )
    monkeypatch.setenv("KALSHI_API_KEY", "test-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/dev/null")

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert '"strategy": "nullone_wngsr_kalshi"' in captured.out
    assert '"run_id": "wngsr-2026-05-22"' in captured.out


def test_execute_config_calls_runtime_pipeline(monkeypatch, capsys):
    from nullone import cli

    async def fake_run_from_runtime_config_path(config_path, **kwargs):
        assert Path(config_path).name == "runtime_config.json"
        return {
            "winning_bracket": "101-105",
            "mode": "paper",
            "runtime_config_path": str(config_path),
        }

    monkeypatch.setattr(
        sys,
        "argv",
        ["nullone", "execute", "--config", "tests/fixtures/runtime_config.json"],
    )
    monkeypatch.setattr(cli, "run_from_runtime_config_path", fake_run_from_runtime_config_path)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert '"winning_bracket": "101-105"' in captured.out


def test_execute_paper_live_release_forces_paper_and_trace_jsonl(monkeypatch, capsys):
    from nullone import cli

    async def fake_run_from_runtime_config_path(config_path, **kwargs):
        assert Path(config_path).name == "runtime_config.json"
        assert kwargs["mode"] == "paper"
        assert kwargs["simulate_fixture"] is None
        kwargs["trace_event"]({"event": "release_wait_start"})
        kwargs["trace_event"]({"event": "release_wait_done"})
        return {"winning_bracket": "101-105", "mode": "paper"}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nullone",
            "execute",
            "--config",
            "tests/fixtures/runtime_config.json",
            "--paper-live-release",
            "--timeout-seconds",
            "5",
            "--trace-jsonl",
        ],
    )
    monkeypatch.setattr(cli, "run_from_runtime_config_path", fake_run_from_runtime_config_path)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    events = [json.loads(line)["event"] for line in captured.out.splitlines()]
    assert exc.value.code == 0
    assert events == ["process_start", "release_wait_start", "release_wait_done"]
    assert captured.err == ""


def test_execute_diagnostic_packaged_trace_jsonl_exits_quickly(monkeypatch, tmp_path, capsys):
    from nullone import cli
    import nullone.engine.pipeline as pipeline

    def fail_release_wait(*args, **kwargs):
        raise AssertionError("diagnostic execute must not wait for the release window")

    monkeypatch.setenv("NULLONE_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_PK", raising=False)
    monkeypatch.setattr(pipeline.asyncio, "sleep", fail_release_wait)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nullone",
            "execute",
            "--config",
            "tests/fixtures/runtime_config.json",
            "--diagnostic",
            "--simulate",
            "packaged",
            "--timeout-seconds",
            "30",
            "--trace-jsonl",
        ],
    )

    started = time.perf_counter()
    with pytest.raises(SystemExit) as exc:
        cli.main()
    elapsed = time.perf_counter() - started

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    names = [event["event"] for event in events]

    assert exc.value.code == 0
    assert elapsed < 1.0
    assert names == [
        "process_start",
        "diagnostic_fixture_load_start",
        "diagnostic_fixture_loaded",
        "runtime_config_load_start",
        "runtime_config_loaded",
        "pipeline_init_start",
        "settings_load_start",
        "settings_loaded",
        "pipeline_initialized",
        "execute_start",
        "source_fetch_start",
        "source_fetch_done",
        "parse_done",
        "map_done",
        "vector_read_done",
        "kelly_done",
        "order_build_done",
        "order_submit_done",
        "execute_done",
    ]
    assert events[10]["source"] == "fixture"
    assert events[17]["paper"] is True
    assert all("duration_ms" in event for event in events if event["event"].endswith("_done"))
    assert captured.err == ""


def test_execute_config_validation_error_uses_contract_exit_code(monkeypatch, tmp_path, capsys):
    from nullone import cli

    bad_config = tmp_path / "runtime.json"
    bad_config.write_text("{}")
    monkeypatch.setattr(sys, "argv", ["nullone", "execute", "--config", str(bad_config)])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 3
    assert "Invalid runtime config during execute" in captured.err


def test_ready_config_uses_runtime_ready_path(monkeypatch, capsys):
    from nullone import cli
    import nullone.tools.ready_check as ready_check_module

    async def fake_runtime_ready(config_path):
        assert Path(config_path).name == "runtime_config.json"
        return {
            "ready": True,
            "strategy": "nullone_wngsr_kalshi",
            "run_id": "wngsr-2026-05-22",
            "checks": {"config": {"ok": True}},
            "timestamp": "2026-05-22T10:20:00Z",
        }

    monkeypatch.setattr(
        sys,
        "argv",
        ["nullone", "ready", "--config", "tests/fixtures/runtime_config.json"],
    )
    monkeypatch.setattr(ready_check_module, "run_runtime_ready_check", fake_runtime_ready)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert '"ready": true' in captured.out


def test_e2e_diagnostics_cli_outputs_report(monkeypatch, capsys):
    from nullone import cli
    import nullone.tools.e2e_diagnostics as diagnostics_module

    async def fake_e2e(**kwargs):
        return {
            "diagnostic": "nullone_e2e_hot_path",
            "will_place_orders": False,
            "execution": {"winning_bracket": "101-105"},
            "hot_path": {"winning_bracket": "101-105"},
        }

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nullone",
            "e2e-diagnostics",
            "--config",
            "tests/fixtures/runtime_config.json",
            "--skip-ready",
        ],
    )
    monkeypatch.setattr(diagnostics_module, "run_e2e_diagnostics", fake_e2e)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert '"diagnostic": "nullone_e2e_hot_path"' in captured.out
