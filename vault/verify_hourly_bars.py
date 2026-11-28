"""Check a local SOL/USDC hourly export and optionally write typed Parquet."""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory


COLUMNS = (
    "bar_start_utc",
    "bar_end_utc",
    "modeled_available_at_utc",
    "sol_sold_swaps",
    "sol_bought_swaps",
    "conflicting_swap_keys",
    "sol_lamports",
    "usdc_microunits",
    "vwap_usdc_per_sol",
)
HOUR = timedelta(hours=1)
AVAILABILITY_DELAY = timedelta(minutes=2)


def utc_timestamp(value):
    if value.endswith(" UTC"):
        value = value.removesuffix(" UTC") + "+00:00"
    stamp = datetime.fromisoformat(value)
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def utc_text(stamp):
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")


def number(value, precision, scale=0):
    amount = Decimal(value)
    with localcontext() as context:
        context.prec = precision
        if not amount.is_finite() or amount < 0:
            raise ValueError("expected a finite nonnegative number")
        rounded = amount.quantize(Decimal(1).scaleb(-scale))
    if rounded != amount:
        raise ValueError("numeric field has too many fractional digits")
    return rounded.copy_abs()


def parse_row(raw):
    if len(raw) != len(COLUMNS):
        raise ValueError(f"expected {len(COLUMNS)} columns")
    start, end, available = (utc_timestamp(value) for value in raw[:3])
    if start != start.replace(minute=0, second=0, microsecond=0):
        raise ValueError("bar start must be a whole UTC hour")
    if end != start + HOUR or available != end + AVAILABILITY_DELAY:
        raise ValueError("bar end or modeled availability does not match the query")

    sold, bought, conflicts = (int(number(value, 19)) for value in raw[3:6])
    if max(sold, bought, conflicts) > 2**63 - 1:
        raise ValueError("swap counter exceeds signed BIGINT")
    lamports, micro_usdc, price = (
        number(value, precision, scale) if value else None
        for value, precision, scale in zip(raw[6:], (38, 38, 24), (0, 0, 8))
    )
    has_swaps = sold + bought > 0
    market_values = (lamports, micro_usdc, price)
    if has_swaps and any(value is None or value <= 0 for value in market_values):
        raise ValueError("a nonempty hour must have positive volume and VWAP")
    if not has_swaps and any(value is not None for value in market_values):
        raise ValueError("an empty hour must have null volume and VWAP")
    return start, end, available, sold, bought, conflicts, *market_values


def index_rows(records, start, end):
    rows = {}
    for location, raw in records:
        try:
            row = parse_row(raw)
            hour = row[0]
            if not start <= hour < end:
                raise ValueError("row falls outside the requested UTC window")
            if hour in rows:
                raise ValueError("duplicate UTC hour")
            rows[hour] = row
        except (InvalidOperation, ValueError) as error:
            raise ValueError(f"{location}: {error}") from error
    return rows


def read_export(path, start, end):
    if path.suffix.lower() == ".parquet":
        return index_rows(parquet_records(path), start, end)
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, strict=True)
            if tuple(next(reader, [])) != COLUMNS:
                raise ValueError("CSV header does not match the hourly bars query")
            records = ((f"CSV line {reader.line_num}", raw) for raw in reader)
            return index_rows(records, start, end)
    except csv.Error as error:
        raise ValueError(f"invalid CSV: {error}") from error


def parquet_records(path):
    if not path.is_file():
        raise FileNotFoundError(f"local Parquet file not found: {path}")
    try:
        import duckdb
    except ImportError as error:
        raise ValueError("reading Parquet requires the DuckDB Python package") from error
    try:
        with duckdb.connect() as db:
            db.execute("SET TimeZone = 'UTC'")
            schema = db.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
            if tuple(column[0] for column in schema) != COLUMNS:
                raise ValueError("Parquet columns do not match the hourly bars query")
            # These identifiers come from the fixed export schema above.
            projection = ", ".join(f"CAST({name} AS VARCHAR)" for name in COLUMNS)
            result = db.execute(f"SELECT {projection} FROM read_parquet(?)", [str(path)])
            index = 1
            while batch := result.fetchmany(128):
                for raw in batch:
                    yield f"Parquet row {index}", [value if value is not None else "" for value in raw]
                    index += 1
    except duckdb.Error as error:
        raise ValueError(f"Parquet read failed: {error}") from error


def canonical_bytes(rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow((*map(utc_text, row[:3]), *row[3:8],
                         "" if row[8] is None else f"{row[8]:.8f}"))
    return buffer.getvalue().encode("utf-8")


def audit(rows, start, end, for_scoring):
    expected = (end - start) // HOUR
    gap = longest_gap = usable = nonempty = conflicts = 0
    latest = None
    for index in range(expected):
        row = rows.get(start + index * HOUR)
        if row is not None:
            conflicts += row[5]
            if row[6] is not None:
                nonempty += 1
                latest = row[1]
                if row[5] == 0:
                    usable += 1
                    gap = 0
                    continue
        gap += 1
        longest_gap = max(gap, longest_gap)

    passed = len(rows) == expected and conflicts == 0
    if for_scoring:
        passed = passed and 100 * usable >= 99 * expected and longest_gap <= 2
    return {
        "start_utc": utc_text(start),
        "end_utc": utc_text(end),
        "expected_hours": expected,
        "row_count": len(rows),
        "missing_grid_hours": expected - len(rows),
        "empty_hours": len(rows) - nonempty,
        "usable_hours": usable,
        "longest_unusable_gap_hours": longest_gap,
        "conflicting_swap_keys": conflicts,
        "latest_nonempty_bar_end_utc": utc_text(latest) if latest else None,
        "canonical_sha256": hashlib.sha256(canonical_bytes(rows[hour] for hour in sorted(rows))).hexdigest(),
        "scoring_coverage_checked": for_scoring,
        "passed": passed,
    }


def write_parquet(path, rows):
    try:
        import duckdb
    except ImportError as error:
        raise ValueError("--parquet requires the DuckDB Python package") from error

    if path.suffix.lower() != ".parquet":
        raise ValueError("--parquet must end in .parquet")
    if path.exists():
        raise FileExistsError(f"output already exists: {path}")
    with TemporaryDirectory(dir=path.parent) as directory:
        staged = Path(directory) / "bars.parquet"
        try:
            with duckdb.connect() as db:
                db.execute("SET TimeZone = 'UTC'")
                db.execute("""
                    CREATE TABLE bars (
                        bar_start_utc TIMESTAMPTZ, bar_end_utc TIMESTAMPTZ,
                        modeled_available_at_utc TIMESTAMPTZ,
                        sol_sold_swaps BIGINT, sol_bought_swaps BIGINT, conflicting_swap_keys BIGINT,
                        sol_lamports DECIMAL(38, 0), usdc_microunits DECIMAL(38, 0),
                        vwap_usdc_per_sol DECIMAL(24, 8)
                    )
                """)
                db.executemany("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                db.execute("COPY (SELECT * FROM bars ORDER BY bar_start_utc) TO ? (FORMAT PARQUET)", [str(staged)])
        except duckdb.Error as error:
            raise ValueError(f"Parquet conversion failed: {error}") from error
        with staged.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        os.link(staged, path)
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="local CSV or Parquet export from SOL USDC Hourly Bars.sql")
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="inclusive UTC date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="exclusive UTC date (YYYY-MM-DD)")
    parser.add_argument("--for-scoring", action="store_true", help="apply coverage thresholds to this scored interval")
    parser.add_argument("--parquet", type=Path, help="optional new local Parquet path")
    args = parser.parse_args()
    if not 1 <= (args.end - args.start).days <= 178:
        parser.error("enter a UTC window of 1 to 178 days")
    start = datetime.combine(args.start, time.min, tzinfo=UTC)
    end = datetime.combine(args.end, time.min, tzinfo=UTC)
    try:
        rows = read_export(args.export, start, end)
        report = audit(rows, start, end, args.for_scoring)
        if args.parquet is not None and report["passed"]:
            report["parquet_sha256"] = write_parquet(args.parquet, [rows[hour] for hour in sorted(rows)])
    except (OSError, OverflowError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
