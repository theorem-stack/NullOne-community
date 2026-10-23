from __future__ import annotations

from nullone.mapping.spec_loader import BracketSpec, EventSpec


class BracketMapper:
    def __init__(self, spec: EventSpec) -> None:
        self._spec = spec
        self._validate_brackets(spec.brackets)
        self._brackets = sorted(spec.brackets, key=lambda b: b.min_bcf)

    @staticmethod
    def _validate_brackets(brackets: list[BracketSpec]) -> None:
        if not brackets:
            raise ValueError("Spec must contain at least one bracket")
        ordered = sorted(brackets, key=lambda b: b.min_bcf)
        for i, bracket in enumerate(ordered):
            if bracket.min_bcf > bracket.max_bcf:
                raise ValueError(f"Bracket {bracket.label} has min > max")
            if i > 0 and bracket.min_bcf <= ordered[i - 1].max_bcf:
                raise ValueError(f"Bracket overlap: {ordered[i - 1].label} and {bracket.label}")

    def map_net_change(self, net_change_bcf: int) -> BracketSpec:
        for bracket in self._brackets:
            if bracket.min_bcf <= net_change_bcf <= bracket.max_bcf:
                return bracket
        raise ValueError(
            f"No bracket matches net change {net_change_bcf} Bcf for {self._spec.event_id}"
        )
