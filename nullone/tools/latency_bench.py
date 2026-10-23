"""Measure hot-path and end-to-end latency for WNGSR ingestion and trading pipeline."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import orjson

from nullone.config import WNGSR_CSV_URL, WNGSR_JSON_URL
from nullone.engine.risk import evaluate_trade, order_price_for_bracket
from nullone.exchanges.base import OrderBookLevel, OrderBookSnapshot
from nullone.mapping.bracket import BracketMapper
from nullone.mapping.spec_loader import load_spec
from nullone.sources.csv_layout import validate_csv_layout
from nullone.sources.fast_csv import parse_wngsr_csv_fast, parse_wngsr_csv_streaming
from nullone.sources.parsers import parse_wngsr_json


@dataclass(frozen=True)
class BenchResult:
    name: str
    samples_ms: list[float]
    target_ms: float | None = None

    @property
    def count(self) -> int:
        return len(self.samples_ms)

    def percentile(self, p: float) -> float:
        if not self.samples_ms:
            return 0.0
        ordered = sorted(self.samples_ms)
        idx = int(round((p / 100.0) * (len(ordered) - 1)))
        return ordered[idx]

    def summary(self) -> dict:
        if not self.samples_ms:
            return {"name": self.name, "count": 0}

        p50 = self.percentile(50)
        p95 = self.percentile(95)
        p99 = self.percentile(99)
        payload = {
            "name": self.name,
            "count": self.count,
            "min_ms": round(min(self.samples_ms), 4),
            "p50_ms": round(p50, 4),
            "p95_ms": round(p95, 4),
            "p99_ms": round(p99, 4),
            "max_ms": round(max(self.samples_ms), 4),
            "mean_ms": round(statistics.mean(self.samples_ms), 4),
        }
        if self.target_ms is not None:
            payload["target_ms"] = self.target_ms
            payload["pass"] = p95 <= self.target_ms
        return payload


def _timed(iterations: int, fn) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return samples


async def _timed_async(iterations: int, fn) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        await fn()
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def bench_local_parsers(
    *,
    json_bytes: bytes,
    csv_bytes: bytes,
    iterations: int,
) -> list[BenchResult]:
    layout = validate_csv_layout(csv_bytes)

    return [
        BenchResult(
            "json_parse",
            _timed(iterations, lambda: parse_wngsr_json(json_bytes)),
            target_ms=5.0,
        ),
        BenchResult(
            "csv_layout_validate",
            _timed(iterations, lambda: validate_csv_layout(csv_bytes)),
            target_ms=1.0,
        ),
        BenchResult(
            "csv_fast_parse",
            _timed(iterations, lambda: parse_wngsr_csv_fast(csv_bytes, layout=layout)),
            target_ms=1.0,
        ),
        BenchResult(
            "csv_stream_parse",
            _timed(iterations, lambda: parse_wngsr_csv_streaming(csv_bytes, layout=layout)),
            target_ms=1.0,
        ),
    ]


def bench_local_pipeline(
    *,
    json_bytes: bytes,
    spec_path: Path,
    iterations: int,
) -> list[BenchResult]:
    spec = load_spec(spec_path)
    mapper = BracketMapper(spec)
    book = OrderBookSnapshot(
        best_ask=0.85,
        best_bid=0.40,
        asks=[OrderBookLevel(price=0.85, size=100)],
        bids=[OrderBookLevel(price=0.40, size=100)],
    )

    def hot_path() -> None:
        reading = parse_wngsr_json(json_bytes)
        bracket = mapper.map_net_change(reading.net_change_bcf)
        decision = evaluate_trade(spec.execution, book, taker_fee_rate=0.02)
        if decision.approved:
            order_price_for_bracket(decision, bracket)

    return [
        BenchResult("bracket_map", _timed(iterations, lambda: mapper.map_net_change(101)), target_ms=0.1),
        BenchResult(
            "risk_gate",
            _timed(iterations, lambda: evaluate_trade(spec.execution, book, taker_fee_rate=0.02)),
            target_ms=0.1,
        ),
        BenchResult("hot_path_local", _timed(iterations, hot_path), target_ms=10.0),
        BenchResult(
            "hot_path_budget_plan",
            _timed(iterations, hot_path),
            target_ms=200.0,
        ),
    ]


async def bench_live_fetch(*, iterations: int) -> list[BenchResult]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, connect=2.0),
        limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        follow_redirects=True,
    ) as client:

        async def fetch_parse_json() -> None:
            resp = await client.get(WNGSR_JSON_URL)
            resp.raise_for_status()
            parse_wngsr_json(resp.content)

        async def fetch_parse_csv() -> None:
            resp = await client.get(WNGSR_CSV_URL)
            resp.raise_for_status()
            layout = validate_csv_layout(resp.content)
            parse_wngsr_csv_streaming(resp.content, layout=layout)

        async def fetch_json_only() -> None:
            resp = await client.get(WNGSR_JSON_URL)
            resp.raise_for_status()
            _ = resp.content

        async def fetch_csv_stream_partial() -> None:
            layout = validate_csv_layout((await client.get(WNGSR_CSV_URL)).content)
            buffer = bytearray()
            async with client.stream("GET", WNGSR_CSV_URL) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    buffer.extend(chunk)
                    if buffer.count(10) >= layout.total_line_no:
                        break
            parse_wngsr_csv_streaming(bytes(buffer), layout=layout)

        return [
            BenchResult(
                "live_json_fetch_only",
                await _timed_async(iterations, fetch_json_only),
            ),
            BenchResult(
                "live_json_fetch_parse",
                await _timed_async(iterations, fetch_parse_json),
            ),
            BenchResult(
                "live_csv_fetch_parse",
                await _timed_async(iterations, fetch_parse_csv),
            ),
            BenchResult(
                "live_csv_stream_parse",
                await _timed_async(iterations, fetch_csv_stream_partial),
            ),
        ]


async def run_benchmark(
    *,
    spec_path: Path,
    json_fixture: Path,
    csv_fixture: Path,
    iterations: int,
    live: bool,
    live_iterations: int,
) -> dict:
    json_bytes = json_fixture.read_bytes()
    csv_bytes = csv_fixture.read_bytes()

    results = bench_local_parsers(
        json_bytes=json_bytes,
        csv_bytes=csv_bytes,
        iterations=iterations,
    )
    results.extend(
        bench_local_pipeline(
            json_bytes=json_bytes,
            spec_path=spec_path,
            iterations=iterations,
        )
    )

    if live:
        results.extend(await bench_live_fetch(iterations=live_iterations))

    summaries = [result.summary() for result in results]
    return {
        "iterations_local": iterations,
        "iterations_live": live_iterations if live else 0,
        "fixtures": {
            "json": str(json_fixture),
            "csv": str(csv_fixture),
            "spec": str(spec_path),
        },
        "benchmarks": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark NullOne WNGSR latency")
    parser.add_argument("--spec", type=Path, default=Path("specs/eia_wngsr_2026-05-15.yaml"))
    parser.add_argument("--json-fixture", type=Path, default=Path("tests/fixtures/wngsr_sample.json"))
    parser.add_argument("--csv-fixture", type=Path, default=Path("tests/fixtures/wngsr_sample.csv"))
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--live", action="store_true", help="Include live EIA network benchmarks")
    parser.add_argument("--live-iterations", type=int, default=10)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
