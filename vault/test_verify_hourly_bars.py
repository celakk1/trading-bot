import csv
from datetime import UTC, datetime

import pytest

from vault.verify_hourly_bars import (
    AVAILABILITY_DELAY,
    COLUMNS,
    HOUR,
    audit,
    canonical_bytes,
    parse_row,
    read_export,
)


START = datetime(2026, 6, 8, tzinfo=UTC)


def export_row(index):
    start = START + index * HOUR
    end = start + HOUR
    available = end + AVAILABILITY_DELAY
    return [start.isoformat(), end.isoformat(), available.isoformat(),
            "1", "1", "0", "1000000000", "150000000", "150.00000000"]


def write_export(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(COLUMNS)
        writer.writerows(rows)


def test_canonical_checksum_ignores_export_order_and_numeric_format(tmp_path):
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    original = [export_row(index) for index in range(24)]
    alternate = [row.copy() for row in reversed(original)]
    for row in alternate:
        row[:3] = [stamp.replace("T", " ").replace("+00:00", ".000 UTC") for stamp in row[:3]]
        row[6:] = ["1e9", "150000000.0", "150"]
    write_export(first, original)
    write_export(second, alternate)
    end = START + 24 * HOUR
    first_report = audit(read_export(first, START, end), START, end, True)
    second_report = audit(read_export(second, START, end), START, end, True)
    assert first_report == second_report
    assert first_report["passed"]
    assert first_report["usable_hours"] == 24


def test_distinguishes_empty_bars_missing_rows_and_conflicts():
    source_rows = [export_row(index) for index in range(24)]
    source_rows[1][3:] = ["0", "0", "0", "", "", ""]
    source_rows[3][5] = "2"
    del source_rows[2]
    parsed = [parse_row(row) for row in source_rows]
    rows = {row[0]: row for row in parsed}
    report = audit(rows, START, START + 24 * HOUR, False)
    assert report["missing_grid_hours"] == 1
    assert report["empty_hours"] == 1
    assert report["conflicting_swap_keys"] == 2
    assert report["longest_unusable_gap_hours"] == 3
    assert report["usable_hours"] == 21
    assert not report["passed"]


def test_coverage_threshold_only_applies_when_scoring():
    source_rows = [export_row(index) for index in range(24)]
    source_rows[1][3:] = ["0", "0", "0", "", "", ""]
    parsed = [parse_row(row) for row in source_rows]
    rows = {row[0]: row for row in parsed}
    end = START + 24 * HOUR
    assert audit(rows, START, end, False)["passed"]
    assert not audit(rows, START, end, True)["passed"]


def test_digest_preserves_38_digit_exact_mint_values():
    row = export_row(0)
    amount = "87654321098765432109876543210987654321"
    row[6] = amount
    canonical = canonical_bytes([parse_row(row)]).decode()
    assert f",{amount},150000000,150.00000000\n" in canonical


def test_rejects_duplicate_hour_instead_of_replacing_it(tmp_path):
    path = tmp_path / "duplicate.csv"
    write_export(path, [export_row(0), export_row(0)])
    with pytest.raises(ValueError, match="CSV line 3: duplicate UTC hour"):
        read_export(path, START, START + 24 * HOUR)
