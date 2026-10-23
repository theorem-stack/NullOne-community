import tomllib
from importlib.resources import files
from pathlib import Path

from nullone.sources.parsers import parse_wngsr_json


def test_packaged_wngsr_sample_fixture_is_available():
    raw = files("nullone.fixtures").joinpath("wngsr_sample.json").read_bytes()
    reading = parse_wngsr_json(raw)

    assert reading.current_week == "2026-05-15"
    assert reading.net_change_bcf == 101


def test_wheel_and_docker_include_packaged_fixture():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    wheel_includes = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]
    dockerfile = Path("Dockerfile").read_text()

    assert "nullone/fixtures/*.json" in wheel_includes
    assert "COPY nullone ./nullone" in dockerfile
