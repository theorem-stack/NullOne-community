from __future__ import annotations

import csv
import io
from dataclasses import dataclass

# Immutable EIA WNGSR CSV template markers (byte-level, no regex).
HEADER_LABEL = b"Region"
NET_CHANGE_LABEL = b"net change"
TOTAL_LABEL = b"Total"
TOTAL_LABEL_PREFIX_LEN = 5
REVISION_TOKEN = b"R"


@dataclass(frozen=True)
class WNGSRCsvLayout:
    """Validated fixed offsets for wngsr.csv hot-path parsing."""

    header_line_no: int
    total_line_no: int
    net_change_col: int

    @classmethod
    def default(cls) -> WNGSRCsvLayout:
        # Typical live layout (May 2026): net-change header line 7, Total line 15.
        return cls(header_line_no=7, total_line_no=15, net_change_col=7)


def validate_csv_layout(raw: bytes, layout: WNGSRCsvLayout | None = None) -> WNGSRCsvLayout:
    """Pre-flight check: locate net-change column line and Total row by label bytes."""
    lines = _split_lines(raw)
    if not lines:
        raise ValueError("CSV payload empty")

    header_line_no = None
    net_change_col = None
    for idx, line in enumerate(lines, start=1):
        if NET_CHANGE_LABEL in line.lower():
            header_line_no = idx
            net_change_col = _find_net_change_column(line)
            break
    if header_line_no is None or net_change_col is None:
        raise ValueError("CSV net change header mismatch")

    region_line_no = next(
        (idx for idx, line in enumerate(lines, start=1) if line.startswith(HEADER_LABEL)),
        None,
    )
    if region_line_no is None:
        raise ValueError("CSV Region header mismatch")

    total_line_no = None
    for idx in range(region_line_no + 1, len(lines) + 1):
        if _line_starts_with_total(lines[idx - 1]):
            total_line_no = idx
            break
    if total_line_no is None:
        raise ValueError("CSV Total row label mismatch")

    total_line = lines[total_line_no - 1]
    fields = _csv_fields(total_line)
    if net_change_col >= len(fields):
        raise ValueError("CSV net change column out of range")

    return WNGSRCsvLayout(
        header_line_no=header_line_no,
        total_line_no=total_line_no,
        net_change_col=net_change_col,
    )


def _split_lines(raw: bytes) -> list[bytes]:
    text = raw.lstrip(b"\xef\xbb\xbf")
    normalized = text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return [line for line in normalized.split(b"\n") if line]


def _line_starts_with_total(line: bytes) -> bool:
    stripped = line.lstrip()
    return stripped[:TOTAL_LABEL_PREFIX_LEN] == TOTAL_LABEL


def _csv_fields(line: bytes) -> list[bytes]:
    row = next(csv.reader(io.StringIO(line.decode("utf-8"))))
    return [cell.encode("utf-8") for cell in row]


def _find_net_change_column(header: bytes) -> int:
    columns = [col.strip().lower() for col in _csv_fields(header)]
    for idx, name in enumerate(columns):
        if name.startswith(NET_CHANGE_LABEL):
            return idx
    raise ValueError("CSV missing net change column")
