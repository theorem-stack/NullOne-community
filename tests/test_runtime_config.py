import json
from pathlib import Path

import pytest

from nullone.runtime_config import RuntimeConfigError, load_runtime_spec


RUNTIME_FIXTURE = Path("tests/fixtures/runtime_config.json")


def test_runtime_config_adapts_to_event_spec():
    runtime_config, spec = load_runtime_spec(RUNTIME_FIXTURE)

    assert runtime_config.engine == "nullone"
    assert runtime_config.runtime["run_id"] == "wngsr-2026-05-22"
    assert spec.event_id == "eia_wngsr_2026-05-15"
    assert spec.week_ending == "2026-05-15"
    assert spec.execution.release_capital_usd == 100
    assert spec.execution.execution_strategy == "auto"
    assert len(spec.brackets) == 3
    assert spec.brackets[1].kalshi.market_ticker == "KXGASSTORAGE-MAY15-103"


def test_runtime_config_rejects_wrong_engine(tmp_path):
    payload = json.loads(RUNTIME_FIXTURE.read_text())
    payload["engine"] = "other"
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(RuntimeConfigError, match="engine"):
        load_runtime_spec(path)


def test_runtime_config_rejects_stale_strategy(tmp_path):
    payload = json.loads(RUNTIME_FIXTURE.read_text())
    payload["execution"]["execution_strategy"] = "multi_leg"
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(RuntimeConfigError, match="execution_strategy"):
        load_runtime_spec(path)
