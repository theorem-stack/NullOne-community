"""Resolve event specs from cache or Kalshi discovery."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from nullone.calendar.scheduler import next_release_after
from nullone.config import load_settings
from nullone.exchanges.kalshi.discover import DiscoveryError, discover_kalshi_brackets
from nullone.mapping.spec_builder import build_event_spec_dict_from_discovery
from nullone.mapping.spec_loader import EventSpec, default_spec_path, load_spec
from nullone.mapping.validate import validate_discovered_brackets, validate_spec_for_run


def _load_raw_spec(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _execution_from_raw(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    return dict(raw.get("execution") or {})


def is_cached_spec_valid(path: Path, week_ending: date) -> bool:
    if not path.exists():
        return False
    try:
        spec = load_spec(path)
        validate_spec_for_run(spec, week_ending=week_ending.isoformat())
        return True
    except (DiscoveryError, ValueError, KeyError, TypeError):
        return False


def write_spec_dict(path: Path, spec_dict: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(spec_dict, f, sort_keys=False)


async def discover_and_write_spec(
    week_ending: date,
    output: Path | None = None,
    *,
    execution: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path]:
    settings = load_settings()
    result = await discover_kalshi_brackets(week_ending, settings)
    validate_discovered_brackets(result.brackets)

    spec_dict = build_event_spec_dict_from_discovery(result, execution=execution)
    output = output or default_spec_path(week_ending.isoformat())
    write_spec_dict(output, spec_dict)
    return spec_dict, output


async def resolve_spec(
    *,
    week_ending: date | None = None,
    spec_path: Path | None = None,
    force_discover: bool = False,
    no_discover: bool = False,
    execution: dict[str, Any] | None = None,
) -> tuple[EventSpec, Path]:
    """Return a validated EventSpec and its path, discovering from Kalshi if needed."""
    if week_ending is None:
        week_ending = next_release_after().week_ending

    path = spec_path or default_spec_path(week_ending.isoformat())

    if not force_discover and is_cached_spec_valid(path, week_ending):
        return load_spec(path), path

    if no_discover:
        if not path.exists():
            raise DiscoveryError(f"Spec not found at {path} and --no-discover was set")
        spec = load_spec(path)
        validate_spec_for_run(spec, week_ending=week_ending.isoformat())
        return spec, path

    preserved_execution = execution
    if path.exists() and preserved_execution is None:
        preserved_execution = _execution_from_raw(_load_raw_spec(path))

    _, path = await discover_and_write_spec(
        week_ending,
        output=path,
        execution=preserved_execution,
    )
    spec = load_spec(path)
    validate_spec_for_run(spec, week_ending=week_ending.isoformat())
    return spec, path
