# PRD: NullOne Runtime Engine Contract

**Status:** Draft  
**Repo:** `NullOne`  
**Companion repo:** `source-latency-profiler`  
**Last updated:** 2026-05-24

---

## 1. Overview

### 1.1 Problem

NullOne currently owns both trading execution logic and event/spec profile loading. The source-latency-profiler system is becoming the canonical strategy profile, latency profiling, infrastructure placement, deployment, warmup, and teardown control plane.

To keep NullOne focused on execution, NullOne should become a parameterized runtime engine that receives a generated runtime config from source-latency-profiler.

### 1.2 Proposal

Add a stable runtime contract to NullOne:

```bash
nullone ready --config /etc/nullone/runtime.json
nullone execute --config /etc/nullone/runtime.json
nullone health --config /etc/nullone/runtime.json
nullone shutdown --config /etc/nullone/runtime.json
```

NullOne should:

1. Parse a generated runtime config.
2. Validate event metadata, market brackets, and execution controls.
3. Load secrets from environment or cloud secret references.
4. Warm exchange clients, source readers, WebSocket books, and risk checks.
5. Enter a ready state before the profiler blackout window.
6. Execute only when invoked by the orchestrator or configured scheduler handoff.
7. Emit structured logs and exit codes that the orchestrator can consume.

NullOne should not own cloud placement, latency scoring, infrastructure selection, or deployment orchestration.

### 1.3 Secrets

NullOne should not require `.env` or `.pem` files to be copied into the repository, container image, or source-latency-profiler repo.

In production, source-latency-profiler syncs operator-provided `.env` and PEM inputs to AWS Secrets Manager, and the runtime host materializes:

```text
/run/secrets/nullone.env
/run/secrets/kalshi-private-key.pem
```

NullOne should continue to read normal environment variables and `KALSHI_PRIVATE_KEY_PATH`, but production values should come from `/run/secrets`, not checked-in files.

---

## 2. Runtime Contract

### 2.1 Runtime Config

NullOne receives JSON at a local path such as:

```text
/etc/nullone/runtime.json
```

Example:

```json
{
  "schema_version": 1,
  "engine": "nullone",
  "strategy": {
    "name": "nullone_wngsr_kalshi",
    "event_id": "eia_wngsr_2026-05-15",
    "release_at_et": "2026-05-22T10:30:00-04:00",
    "mode": "paper"
  },
  "event": {
    "source": "eia_wngsr",
    "week_ending": "2026-05-15",
    "metric": "net_change_bcf",
    "region": "lower48"
  },
  "markets": {
    "brackets": [
      {
        "label": "96-100",
        "min": 96,
        "max": 100,
        "kalshi": {
          "market_ticker": "KXGASSTORAGE-MAY15-98"
        },
        "polymarket": {
          "token_id": "1234567890",
          "tick_size": "0.01",
          "neg_risk": false
        }
      }
    ]
  },
  "execution": {
    "min_edge_after_fees": 0.05,
    "max_entry_price": 0.92,
    "release_capital_usd": 100,
    "execution_strategy": "auto",
    "mode": "paper"
  },
  "placement": {
    "provider": "aws",
    "region": "us-east-1",
    "runtime": "ec2",
    "instance_type": "c7i.large"
  },
  "runtime": {
    "run_id": "wngsr-2026-05-22",
    "warmup_starts_at": "2026-05-22T10:00:00-04:00",
    "profiler_blackout_at": "2026-05-22T10:28:00-04:00",
    "execute_at": "2026-05-22T10:30:00-04:00"
  }
}
```

### 2.2 Command Semantics

| Command | Purpose |
|---------|---------|
| `nullone ready --config` | Validate runtime config, credentials, source reachability, venue connectivity, market mappings, risk limits, and warm allowed connections |
| `nullone execute --config` | Execute the strategy for the configured event |
| `nullone health --config` | Return readiness/health JSON without mutating state |
| `nullone shutdown --config` | Close sockets, flush logs, and exit cleanly |

### 2.3 Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Strategy completed without eligible trade or readiness failed |
| `2` | Market discovery/spec issue |
| `3` | Config validation error |
| `4` | Secret/credential error |
| `5` | Venue/source connectivity error |
| `6` | Risk gate failure |
| `10` | Unexpected runtime error |

---

## 3. Required Behavior

### 3.1 Ready Command

`nullone ready --config` should:

- parse and validate `runtime.json`
- validate `schema_version`
- validate strategy name and engine
- validate all brackets are non-overlapping and complete for the intended event
- validate Kalshi market tickers and optional Polymarket token IDs
- load credentials without printing secrets
- connect to EIA source endpoints
- connect to Kalshi REST and WebSocket endpoints
- initialize order clients
- warm order book mirrors
- validate execution mode, capital limits, and price/edge gates
- write structured readiness output

It should not place orders.

### 3.2 Execute Command

`nullone execute --config` should:

- assume `ready` has already run successfully
- reload or reuse the same runtime config
- enter the hot release path
- avoid profiler or orchestration work on the hot path
- poll configured source endpoints at the event time
- parse the configured metric
- map metric value to the configured bracket
- run risk checks
- execute according to `execution_strategy` and `mode`
- emit structured release logs

### 3.3 Health Command

`nullone health --config` should emit JSON:

```json
{
  "ready": true,
  "strategy": "nullone_wngsr_kalshi",
  "run_id": "wngsr-2026-05-22",
  "checks": {
    "config": "ok",
    "secrets": "ok",
    "eia": "ok",
    "kalshi_rest": "ok",
    "kalshi_ws": "ok",
    "logs": "ok"
  },
  "timestamp": "2026-05-22T10:20:00-04:00"
}
```

---

## 4. Source-Latency-Profiler Integration

The source-latency-profiler orchestrator owns:

- canonical strategy YAML
- source and venue profiling
- placement scoring
- infrastructure recommendation
- runtime config generation
- runtime deployment
- warmup timing
- profiler blackout
- teardown

NullOne owns:

- parsing the generated runtime config
- trading logic
- exchange clients
- risk checks
- paper/live execution
- structured execution logs

Integration artifact:

```text
source-latency-profiler -> runtime.json -> NullOne
```

NullOne should not import source-latency-profiler code.

---

## 5. Implementation Plan

### Phase 1 - Runtime Config Loader

- [ ] Add `RuntimeConfig` model
- [ ] Parse JSON runtime config
- [ ] Validate schema version, strategy, event, markets, execution, placement, and runtime timing
- [ ] Add unit tests using generated runtime config fixtures

### Phase 2 - CLI Contract

- [ ] Add `--config` support to `ready`
- [ ] Add `execute --config`
- [ ] Add `health --config`
- [ ] Add `shutdown --config`
- [ ] Define and test exit codes

### Phase 2.5 - Runtime Secret Loading

- [ ] Support environment variables loaded from `/run/secrets/nullone.env`
- [ ] Support `KALSHI_PRIVATE_KEY_PATH=/run/secrets/kalshi-private-key.pem`
- [ ] Ensure no secret values are printed in readiness, health, or error logs
- [ ] Add tests with temporary secret files

### Phase 3 - Spec Adapter

- [ ] Convert runtime config markets into the existing internal spec model
- [ ] Preserve support for legacy `--spec` during migration
- [ ] Remove strategy-profile ownership from NullOne once orchestrator flow is stable

### Phase 4 - Warmup and Execution Split

- [ ] Ensure `ready` can warm clients without placing orders
- [ ] Persist readiness status for `execute`
- [ ] Ensure `execute` can run after profiler blackout without extra discovery or profile parsing

### Phase 5 - Container Runtime

- [ ] Build NullOne container image
- [ ] Publish immutable images to ECR
- [ ] Ensure container accepts `/etc/nullone/runtime.json`
- [ ] Emit logs to stdout and `logs/releases/`

---

## 6. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Runtime config diverges from legacy specs | High | Add adapter tests from existing `specs/*.yaml` |
| Secrets leak in logs | High | Redact all secret values and paths |
| `ready` accidentally places orders | High | Enforce no-order invariant in tests |
| `execute` performs slow setup after blackout | High | Move setup to `ready`; fail if readiness is stale |
| Source profiler and NullOne contracts drift | Medium | Version `runtime.json` schema and keep fixtures in both repos |

---

## 7. Decision Summary

| Decision | Current Position |
|----------|------------------|
| Strategy profile ownership | source-latency-profiler |
| NullOne role | Parameterized execution engine |
| Integration artifact | Generated `runtime.json` |
| Deployment artifact | Versioned container image |
| Hot path ownership | NullOne only |
| Infrastructure orchestration | source-latency-profiler |
