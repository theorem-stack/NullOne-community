from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from nullone.sources.csv_layout import (
    NET_CHANGE_LABEL,
    REVISION_TOKEN,
    WNGSRCsvLayout,
    _csv_fields,
    _find_net_change_column,
    _line_starts_with_total,
    validate_csv_layout,
)
from nullone.sources.parsers import WNGSRReading


@dataclass(frozen=True)
class FastCsvParseResult:
    reading: WNGSRReading
    used_fallback: bool
    layout: WNGSRCsvLayout


def parse_wngsr_csv_fast(
    raw: bytes,
    *,
    layout: WNGSRCsvLayout | None = None,
) -> FastCsvParseResult:
    """Fixed-row CSV extraction with label guard and safe fallback."""
    layout = layout or WNGSRCsvLayout.default()
    try:
        reading = _parse_with_layout(raw, layout)
        if _guard_total_line(raw, layout):
            return FastCsvParseResult(reading=reading, used_fallback=False, layout=layout)
    except (IndexError, ValueError):
        pass

    validated = validate_csv_layout(raw, layout)
    reading = _parse_with_layout(raw, validated)
    return FastCsvParseResult(reading=reading, used_fallback=True, layout=validated)


def parse_wngsr_csv_streaming(raw: bytes, *, layout: WNGSRCsvLayout | None = None) -> WNGSRReading:
    """Process bytes incrementally using newline counting (no full-file split)."""
    layout = layout or WNGSRCsvLayout.default()
    line_no = 0
    buffer = bytearray()
    net_change_col = layout.net_change_col

    for byte in raw.lstrip(b"\xef\xbb\xbf").replace(b"\r\n", b"\n").replace(b"\r", b"\n"):
        if byte == 10:
            line_no += 1
            line = bytes(buffer)
            buffer.clear()

            if line_no == layout.header_line_no:
                if NET_CHANGE_LABEL not in line.lower():
                    return parse_wngsr_csv_fast(raw).reading
                net_change_col = _find_net_change_column(line)

            if line_no == layout.total_line_no:
                if not _line_starts_with_total(line):
                    return parse_wngsr_csv_fast(raw).reading
                return _reading_from_total_line(line, net_change_col)
        else:
            buffer.append(byte)

    return parse_wngsr_csv_fast(raw).reading


def _guard_total_line(raw: bytes, layout: WNGSRCsvLayout) -> bool:
    lines = raw.lstrip(b"\xef\xbb\xbf").replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    lines = [line for line in lines if line]
    if len(lines) < layout.total_line_no:
        return False
    total_line = lines[layout.total_line_no - 1]
    return _line_starts_with_total(total_line)


def _parse_with_layout(raw: bytes, layout: WNGSRCsvLayout) -> WNGSRReading:
    lines = raw.lstrip(b"\xef\xbb\xbf").replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    lines = [line for line in lines if line]
    header = lines[layout.header_line_no - 1]
    net_change_col = _find_net_change_column(header)
    total_line = lines[layout.total_line_no - 1]
    return _reading_from_total_line(total_line, net_change_col)


def _reading_from_total_line(total_line: bytes, net_change_col: int) -> WNGSRReading:
    if not _line_starts_with_total(total_line):
        raise ValueError("Total row label guard failed")

    fields = _csv_fields(total_line)
    if net_change_col >= len(fields):
        raise ValueError("net change column index out of range")

    net_raw = fields[net_change_col].strip()
    return WNGSRReading(
        current_week="",
        release_date="",
        net_change_bcf=_parse_bcf_int(net_raw),
        revision_flag=False,
    )


def _parse_bcf_int(raw: bytes) -> int:
    cleaned = raw.strip().strip(b'"').replace(b",", b"")
    if cleaned.upper() == REVISION_TOKEN:
        raise ValueError("Revision token in CSV net change field")
    return int(cleaned)


def parse_wngsr_csv_fallback_search(raw: bytes) -> WNGSRReading:
    """Emergency fallback when fixed layout shifts."""
    lines = raw.lstrip(b"\xef\xbb\xbf").decode("utf-8").splitlines()
    lines = [line.strip() for line in lines if line.strip()]

    net_header_idx = next(i for i, line in enumerate(lines) if "net change" in line.lower())
    headers = [h.strip().lower() for h in next(csv.reader(io.StringIO(lines[net_header_idx])))]
    net_idx = next(i for i, name in enumerate(headers) if name.startswith("net change"))

    total_line = next(line for line in lines if line.lower().lstrip().startswith("total"))
    cols = next(csv.reader(io.StringIO(total_line)))
    net_raw = cols[net_idx].strip().replace(",", "")
    if net_raw.upper() == "R":
        raise ValueError("Revision row in CSV — not a fresh print")
    return WNGSRReading(
        current_week="",
        release_date="",
        net_change_bcf=int(net_raw),
        revision_flag=False,
    )
