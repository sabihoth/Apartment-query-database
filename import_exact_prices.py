import argparse
import csv
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from tracker import DB_PATH, ensure_schema, normalize_unit_name


def parse_price(value):
    match = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", value or "")
    if not match:
        raise ValueError(f"Could not parse exact price from {value!r}")
    return float(match.group(1).replace(",", ""))


def get_latest_query_date(conn):
    row = conn.execute("SELECT MAX(query_date) FROM units").fetchone()
    return row[0] if row and row[0] else None


def resolve_query_date(conn, cli_value, row_value):
    if row_value:
        return row_value
    if cli_value == "latest":
        latest_query_date = get_latest_query_date(conn)
        if not latest_query_date:
            raise ValueError("Database has no scraped query_date values yet.")
        return latest_query_date
    return cli_value


def resolve_unit_match(conn, query_date, unit, floorplan=None):
    if floorplan:
        rows = conn.execute(
            "SELECT floorplan, unit FROM units WHERE query_date = ? AND floorplan = ? AND unit = ? GROUP BY floorplan, unit",
            (query_date, floorplan, unit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT floorplan, unit FROM units WHERE query_date = ? AND unit = ? GROUP BY floorplan, unit",
            (query_date, unit),
        ).fetchall()

    if not rows:
        raise ValueError(f"No unit match found for query_date={query_date}, floorplan={floorplan!r}, unit={unit!r}")
    if len(rows) > 1:
        raise ValueError(f"Multiple units matched query_date={query_date}, unit={unit!r}; include floorplan in the CSV or CLI arguments")
    return rows[0]


def import_prices(csv_path, default_query_date, default_floorplan, source):
    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)

    inserted = 0
    created_at = datetime.now().isoformat()
    with Path(csv_path).open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError("CSV file is missing a header row")

        for index, row in enumerate(reader, start=2):
            normalized_row = {str(key).strip().lower(): (value or "").strip() for key, value in row.items() if key is not None}
            unit = normalize_unit_name(normalized_row.get("unit", ""))
            if not unit:
                raise ValueError(f"Row {index}: missing unit")

            floorplan = normalized_row.get("floorplan") or default_floorplan
            query_date = resolve_query_date(conn, default_query_date, normalized_row.get("query_date"))
            exact_price = parse_price(normalized_row.get("exact_price") or normalized_row.get("price"))

            resolved_floorplan, resolved_unit = resolve_unit_match(conn, query_date, unit, floorplan)
            conn.execute(
                """
                INSERT INTO unit_price_overrides (created_at, query_date, floorplan, unit, exact_price, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_date, floorplan, unit, source)
                DO UPDATE SET created_at = excluded.created_at, exact_price = excluded.exact_price
                """,
                (created_at, query_date, resolved_floorplan, resolved_unit, exact_price, source),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def main():
    parser = argparse.ArgumentParser(
        description="Import exact unit prices from a manual source such as apartments.com"
    )
    parser.add_argument("csv_path", help="CSV path with columns unit, exact_price and optional floorplan, query_date")
    parser.add_argument(
        "--query-date",
        default="latest",
        help="Default query_date to attach imported prices to. Use YYYY-MM-DD or 'latest'.",
    )
    parser.add_argument("--floorplan", help="Default floorplan to use when CSV rows omit floorplan")
    parser.add_argument("--source", default="apartments.com-manual", help="Source label stored with imported prices")
    args = parser.parse_args()

    inserted = import_prices(args.csv_path, args.query_date, args.floorplan, args.source)
    print(f"Imported {inserted} exact price rows from {args.csv_path} using source {args.source}")


if __name__ == "__main__":
    main()