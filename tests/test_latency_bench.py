from pathlib import Path

import pytest

from nullone.tools.latency_bench import run_benchmark


@pytest.mark.asyncio
async def test_local_latency_budget():
    report = await run_benchmark(
        spec_path=Path("specs/eia_wngsr_2026-05-15.yaml"),
        json_fixture=Path("tests/fixtures/wngsr_sample.json"),
        csv_fixture=Path("tests/fixtures/wngsr_sample.csv"),
        iterations=100,
        live=False,
        live_iterations=0,
    )
    by_name = {item["name"]: item for item in report["benchmarks"]}

    assert by_name["hot_path_budget_plan"]["pass"] is True
    assert by_name["json_parse"]["pass"] is True
    assert by_name["csv_stream_parse"]["pass"] is True
