from __future__ import annotations

from dataclasses import dataclass

from nullone.config import LOWER48_SERIES_ID


@dataclass(frozen=True)
class WNGSRReading:
    current_week: str
    release_date: str
    net_change_bcf: int
    revision_flag: bool
    series_id: str = LOWER48_SERIES_ID


def extract_lower48(doc: dict) -> WNGSRReading:
    for series in doc["series"]:
        if series["series_id"] != LOWER48_SERIES_ID:
            continue
        calculated = series["calculated"]
        revision = calculated.get("revision_flag_net_change", "false").lower() == "true"
        return WNGSRReading(
            current_week=doc["current_week"],
            release_date=doc.get("release_date", ""),
            net_change_bcf=int(calculated["net_change"]),
            revision_flag=revision,
        )
    raise ValueError(f"Lower 48 series not found: {LOWER48_SERIES_ID}")


def parse_wngsr_json(raw: bytes) -> WNGSRReading:
    import orjson

    doc = orjson.loads(raw.lstrip(b"\xef\xbb\xbf"))
    return extract_lower48(doc)


def parse_wngsr_csv(raw: bytes) -> WNGSRReading:
    """Fast fixed-layout CSV parse with label guard and search fallback."""
    from nullone.sources.fast_csv import parse_wngsr_csv_fast, parse_wngsr_csv_fallback_search

    try:
        return parse_wngsr_csv_fast(raw).reading
    except ValueError:
        return parse_wngsr_csv_fallback_search(raw)
