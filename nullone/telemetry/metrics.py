from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class StageTimer:
    durations_ms: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.durations_ms[name] = (time.perf_counter() - start) * 1000


@dataclass(frozen=True)
class ReleaseMetrics:
    event_id: str
    t_fetch_ms: float
    t_parse_ms: float
    t_map_ms: float
    t_order_ms: float
    net_change_bcf: int
    winning_bracket: str
    kalshi: dict | None
    polymarket: dict | None
    mode: str

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "t_fetch_ms": round(self.t_fetch_ms, 3),
            "t_parse_ms": round(self.t_parse_ms, 3),
            "t_map_ms": round(self.t_map_ms, 3),
            "t_order_ms": round(self.t_order_ms, 3),
            "net_change_bcf": self.net_change_bcf,
            "winning_bracket": self.winning_bracket,
            "kalshi": self.kalshi,
            "polymarket": self.polymarket,
            "mode": self.mode,
        }
