from pathlib import Path

from nullone.mapping.spec_loader import load_spec


def _spec_without_release_capital(tmp_path: Path) -> Path:
    spec_path = tmp_path / "spec.yaml"
    text = Path("specs/eia_wngsr_2026-05-15.yaml").read_text()
    text = text.replace("  release_capital_usd: 100\n", "")
    spec_path.write_text(text)
    return spec_path


def test_load_spec_uses_env_release_capital(monkeypatch, tmp_path):
    monkeypatch.setenv("NULLONE_RELEASE_CAPITAL_USD", "42")
    spec = load_spec(_spec_without_release_capital(tmp_path))
    assert spec.execution.release_capital_usd == 42.0


def test_load_spec_supports_legacy_order_size(monkeypatch, tmp_path):
    monkeypatch.delenv("NULLONE_RELEASE_CAPITAL_USD", raising=False)
    monkeypatch.setenv("NULLONE_ORDER_SIZE_USD", "41")
    spec = load_spec(_spec_without_release_capital(tmp_path))
    assert spec.execution.release_capital_usd == 41.0


def test_load_spec_uses_auto_execution_strategy(monkeypatch, tmp_path):
    monkeypatch.setenv("NULLONE_EXECUTION_STRATEGY", "auto")
    spec = load_spec(_spec_without_release_capital(tmp_path))
    assert spec.execution.execution_strategy == "auto"
