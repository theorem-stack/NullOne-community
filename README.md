# NullOne

NullOne is a Python research system for the EIA Weekly Natural Gas Storage Report (WNGSR). On release, it reads the Lower 48 net change in billion cubic feet, maps that integer onto bracket contracts, and either simulates or submits fee-aware limit orders on Kalshi and, when configured, Polymarket.

The checked-in example is the week ending 2026-05-15. A net change of 101 Bcf falls in the bracket labeled `101-105` in `specs/eia_wngsr_2026-05-15.yaml`.

**Not financial advice. Research and engineering only.** Prediction markets can go to zero. Live mode can send real orders and lose money. This repository is not a solicitation, not a track record, and not a recommendation to trade.

## What it is

WNGSR is published by the U.S. Energy Information Administration, normally on Thursday at 10:30 AM Eastern. Holiday releases are listed in `nullone/calendar/wngsr_schedule.yaml` (the file cites [EIA's schedule](https://ir.eia.gov/ngs/schedule.html)). Kalshi lists bracket markets on how large the storage change will be. NullOne is the execution program around that print:

1. Resolve the release week and a YAML spec that maps Bcf ranges to market tickers.
2. Optionally discover those tickers from Kalshi's API.
3. Poll `https://ir.eia.gov/ngs/wngsr.json` and `https://ir.eia.gov/ngs/wngsr.csv` together and keep the first successful parse.
4. Read Lower 48 net change (`png.nw2_epg0_swo_r48_bcf.w`). Abort if the JSON revision flag is set.
5. Map the integer into a bracket. With `execution_strategy: auto` and at least two Kalshi brackets, size a multi-leg order with the parse-error-aware Kelly allocator in `nullone/engine/allocation.py`. Otherwise run the single-bracket risk gate in `nullone/engine/risk.py`.
6. In paper mode, record a simulated fill. In live mode, submit orders with the configured exchange credentials.
7. Write one JSON record under `NULLONE_LOG_DIR` (default `logs/releases/`).

The effective mode is the CLI `--mode` when you pass it, otherwise `execution.mode` in the spec, otherwise `NULLONE_MODE`. The default environment mode is `paper`. A spec file can still select `live` on its own, so read the spec before a release run.

## Architecture

```
EIA JSON/CSV
    -> WNGSRPoller (nullone/sources)
    -> BracketMapper (nullone/mapping)
    -> risk gate or multi-leg allocator (nullone/engine)
    -> Kalshi and optional Polymarket clients (nullone/exchanges)
    -> JSON release log (nullone/telemetry)
```

| Path | Role |
| --- | --- |
| `nullone/cli.py` | Console entry point. Installed as the `nullone` command. |
| `nullone/calendar/` | Release clock. Thursday 10:30 ET, plus holiday overrides. |
| `nullone/sources/` | JSON parser, fixed-layout CSV fast path, and the release poller. |
| `nullone/mapping/` | Spec load, bracket map, scenario index, Kalshi discovery cache. |
| `nullone/engine/` | Release pipeline, fee-aware risk gate, Kelly multi-leg allocation. |
| `nullone/exchanges/kalshi/` | RSA-signed REST, WebSocket order book, market discovery. |
| `nullone/exchanges/polymarket/` | Gamma discovery, CLOB book, and order client. |
| `nullone/backtest/replay.py` | Offline bracket-and-risk check on saved WNGSR JSON files. |
| `nullone/runtime_config.py` | Adapter from an external runtime JSON document to an `EventSpec`. |
| `specs/` | Example event specs. Tickers and token ids in the samples are fixtures. |
| `deploy/` | systemd timer (Thursday 10:00 ET, 30 minutes before the print) and host scripts. |
| `Dockerfile` | `python:3.12-slim` image. `ENTRYPOINT` is `nullone`. |

Stack: Python 3.12, httpx, orjson, PyYAML, cryptography, websockets, py-clob-client-v2, python-dotenv. Tests use pytest. Packaging is a hatchling project (`pyproject.toml`). `nullone.spec` is a PyInstaller spec for a single-file Linux binary; built artifacts are not part of this tree.

## Run it

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

`.env.example` lists empty credential variables. Paper simulation below does not need them. Live Kalshi access needs `KALSHI_API_KEY` and a matching RSA private key (`KALSHI_PRIVATE_KEY_PATH` or `KALSHI_PRIVATE_KEY_PEM`). Polymarket is optional (`POLYMARKET_PK` and the CLOB fields). Do not commit `.env` or key files.

Next scheduled release, from the calendar file alone:

```bash
nullone next-release
```

Paper run against the packaged sample, using the example spec. This does not call EIA or an exchange. It writes a JSON log under `logs/releases/`:

```bash
nullone run --mode paper \
  --spec specs/eia_wngsr_2026-05-15.yaml \
  --simulate tests/fixtures/wngsr_sample.json
```

`tests/fixtures/wngsr_sample.json` has `net_change` 101. The example spec maps that to bracket `101-105`. The sample spec sets `execution_strategy: auto`, so this path uses the multi-leg allocator on a simulated book (YES ask 0.85, NO ask 0.60) rather than the single-leg risk gate.

Other commands, all defined in `nullone/cli.py`:

| Command | What it does |
| --- | --- |
| `nullone discover --week YYYY-MM-DD` | Refresh Kalshi tickers. Requires Kalshi credentials. |
| `nullone dry-run` | Check order-book connectivity for a spec. |
| `nullone ready` | Local pre-flight (credentials, EIA, log dir, paper pipeline). |
| `nullone bench-latency` | Time local parse, map, and risk against targets in code. |
| `nullone e2e-diagnostics --config tests/fixtures/runtime_config.json --skip-ready` | Paper hot-path report through the runtime-config adapter. |
| `nullone execute --config <runtime.json>` | Run from an external runtime JSON document. |
| `nullone plan-multi-leg` | Plan a multi-leg batch against a live book. No orders. |
| `nullone kalshi-batch-probe` | Live Kalshi call. Requires the acknowledge flag in `--help`. |

`nullone run` without `--spec` resolves a cached spec or discovers one. If discovery fails, the process exits before polling EIA and prints `discovery_error_code=` (`auth_missing`, `series_not_found`, or `no_tradeable_events`).

Host install, the systemd units, and the S3 binary publish script are under `deploy/`, `scripts/publish-s3-binary.sh`, and `docs/`. Those docs use placeholders for bucket names, account ids, and machine paths. Supply your own.

## Backtest

`nullone replay` is an offline check, not a market replay.

For each `*.json` file in a directory it parses the file as a WNGSR document, maps `net_change` through the spec, and runs `evaluate_trade` twice: once with the Kalshi fee rate hardcoded in `replay_snapshot` (0.01) and once with the Polymarket fee rate (0.02). The CLI path (`replay_batch`) passes the same ask, default `0.85`, to both venues. It does not load historical order books, queue position, or exchange fills. A non-WNGSR JSON file raises inside the parser; only `ValueError` is recorded and other exceptions propagate, so point `--snapshots` at a directory of WNGSR JSON only.

The example spec's brackets are 96–100, 101–105, and 106–110. `tests/fixtures/wngsr_sample.json` (101 Bcf) maps to `101-105`. `tests/fixtures/wngsr_withdrawal.json` is a -52 Bcf sample and does not match those brackets, so it is the wrong fixture for this spec.

```bash
mkdir -p /tmp/wngsr-snapshots
cp tests/fixtures/wngsr_sample.json /tmp/wngsr-snapshots/
nullone replay \
  --spec specs/eia_wngsr_2026-05-15.yaml \
  --snapshots /tmp/wngsr-snapshots \
  --output logs/replay_report.json
```

What that command checks, and what `tests/test_replay.py` asserts for ask 0.85 on the sample file:

- net change is 101 Bcf and the winning bracket is `101-105`
- both venues pass the risk gate

The arithmetic is in `nullone/engine/risk.py`. Edge is `1 - ask - fee`. The max price is `min(max_entry_price, 1 - min_edge_after_fees - fee)`. The example spec uses `min_edge_after_fees: 0.05` and `max_entry_price: 0.92`, so ask 0.85 clears both the Kalshi fee (0.01) and the Polymarket fee (0.02). That is a unit test of the gate. It is not a historical P&L.

To use your own archive, put one WNGSR JSON per file in a directory and pass a spec whose brackets cover those prints. There is no production fill log in this tree.

## Latency targets in code

`nullone bench-latency` times local work and compares p95 to targets defined on the `BenchResult` objects in `nullone/tools/latency_bench.py`:

| Measurement | p95 target |
| --- | --- |
| `json_parse` | 5 ms |
| `csv_layout_validate`, `csv_fast_parse`, `csv_stream_parse` | 1 ms |
| `bracket_map`, `risk_gate` | 0.1 ms |
| `hot_path_local` (parse + map + risk) | 10 ms |
| `hot_path_budget_plan` (same function, looser ceiling) | 200 ms |

```bash
nullone bench-latency
nullone bench-latency --live --live-iterations 10
```

`--live` adds fetches to `ir.eia.gov`. Whether a machine meets the targets is an output of that command, not a number this README claims.

## Tests

```bash
pytest
```

The suite covers parsing, the CSV fast path, bracket mapping, the risk gate, replay, the scheduler, spec load and resolve, the pipeline, multi-leg allocation, runtime config, and the CLI. Exchange clients are mocked. Running the suite does not require API keys.

## Layout of a release run

`deploy/systemd/nullone-release.timer` is `OnCalendar=Thu *-*-* 10:00:00` in `America/New_York`. The service runs `nullone run` once. The pipeline sleeps until shortly before 10:30 ET; the timer's job is to be awake early. Deployment steps for a Linux host are in `deploy/DEPLOY.md`.

## Related project

[source-latency-profiler](https://github.com/theorem-stack/source-latency-profiler-community) is a companion Python CLI that measures latency to market data sources and exchange APIs, to help decide where a latency-sensitive bot like NullOne should run. It runs those probes from the host that launches it.

## History

Built by Cameron Gifford with Cursor in May–June 2026.
In October 2026 the code was cleaned up for public release and the documentation was rewritten.

## License

MIT. Copyright (c) 2026 Cameron Gifford. See `LICENSE`.
