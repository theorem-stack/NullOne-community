from __future__ import annotations

from dataclasses import dataclass

from nullone.mapping.spec_loader import BracketSpec, EventSpec


@dataclass(frozen=True)
class ScenarioLeg:
    role: str
    label: str
    market_ticker: str


@dataclass(frozen=True)
class Scenario:
    winner_label: str
    winner_ticker: str
    legs: list[ScenarioLeg]


class ScenarioIndex:
    """O(1) net-change lookup for winner YES + loser NO Kalshi legs."""

    def __init__(self, spec: EventSpec) -> None:
        active = [b for b in spec.brackets if b.kalshi is not None]
        if not active:
            raise ValueError("ScenarioIndex requires Kalshi brackets")
        self._scenarios: dict[str, Scenario] = {
            bracket.label: self._scenario_for(bracket, active) for bracket in active
        }
        self._by_bcf: dict[int, Scenario] = {}
        for bracket in active:
            scenario = self._scenarios[bracket.label]
            for value in range(bracket.min_bcf, bracket.max_bcf + 1):
                if value in self._by_bcf:
                    raise ValueError(f"Overlapping bracket value: {value}")
                self._by_bcf[value] = scenario

    @staticmethod
    def _scenario_for(winner: BracketSpec, brackets: list[BracketSpec]) -> Scenario:
        assert winner.kalshi is not None
        legs = [
            ScenarioLeg(
                role="winner_yes",
                label=winner.label,
                market_ticker=winner.kalshi.market_ticker,
            )
        ]
        for bracket in brackets:
            if bracket.label == winner.label or bracket.kalshi is None:
                continue
            legs.append(
                ScenarioLeg(
                    role="loser_no",
                    label=bracket.label,
                    market_ticker=bracket.kalshi.market_ticker,
                )
            )
        return Scenario(
            winner_label=winner.label,
            winner_ticker=winner.kalshi.market_ticker,
            legs=legs,
        )

    def scenario_for_net_change(self, net_change_bcf: int) -> Scenario:
        try:
            return self._by_bcf[net_change_bcf]
        except KeyError as exc:
            raise ValueError(f"No scenario matches net change {net_change_bcf} Bcf") from exc

