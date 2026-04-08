import argparse
import csv
import json
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path

from tracker import ensure_schema


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
CHARTS_DIR = OUTPUT_DIR / "charts"
DB_PATH = OUTPUT_DIR / "rent_data.db"


def latest_override_exact_price_sql(unit_alias="u"):
        return f"""
        (
                SELECT o.exact_price
                FROM unit_price_overrides o
                WHERE o.query_date = {unit_alias}.query_date
                    AND o.floorplan = {unit_alias}.floorplan
                    AND o.unit = {unit_alias}.unit
                ORDER BY o.created_at DESC
                LIMIT 1
        )
        """


def latest_override_source_sql(unit_alias="u"):
        return f"""
        (
                SELECT o.source
                FROM unit_price_overrides o
                WHERE o.query_date = {unit_alias}.query_date
                    AND o.floorplan = {unit_alias}.floorplan
                    AND o.unit = {unit_alias}.unit
                ORDER BY o.created_at DESC
                LIMIT 1
        )
        """


LATEST_UNIT_DAY_CTE = """
WITH latest_unit_day AS (
    SELECT query_date,
           floorplan,
           unit,
           MAX(timestamp) AS latest_timestamp
    FROM units
    GROUP BY query_date, floorplan, unit
)
"""


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)
    return conn


def ensure_output_dirs():
    OUTPUT_DIR.mkdir(exist_ok=True)
    CHARTS_DIR.mkdir(exist_ok=True)


def print_saved_data():
    print("Saved columns:")
    print("  timestamp: exact time the scrape ran")
    print("  query_date: calendar day of the scrape")
    print("  floorplan_id: RentCafe floorplan identifier")
    print("  floorplan: floorplan name, e.g. A1")
    print("  unit_id: RentCafe unit identifier")
    print("  unit: unit number, e.g. 1112")
    print("  beds, baths, sqft: floorplan attributes at scrape time")
    print("  price: exact numeric unit price when the site exposes a single amount")
    print("  price_min, price_max: numeric bounds parsed from the displayed rent text")
    print("  price_range: raw displayed rent text from RentCafe")
    print("  availability: displayed availability text")
    print("  unit_price_overrides: optional imported exact prices keyed by query_date + floorplan + unit")


def get_unit_history_rows(conn, unit, floorplan=None):
    override_price_sql = latest_override_exact_price_sql("u")
    override_source_sql = latest_override_source_sql("u")
    sql = """
        SELECT u.query_date,
               u.timestamp,
               u.floorplan,
               u.unit,
               u.price,
    """ + override_price_sql + """ AS imported_price,
               COALESCE(
    """ + override_price_sql + """,
                   u.price
               ) AS effective_price,
               u.price_min,
               u.price_max,
               u.price_range,
               u.availability,
    """ + override_source_sql + """ AS price_source
        FROM units u
        WHERE unit = ?
    """
    params = [unit]
    if floorplan:
        sql += " AND floorplan = ?"
        params.append(floorplan)
    sql += " ORDER BY timestamp"
    return conn.execute(sql, params).fetchall()


def print_unit_history(conn, unit, floorplan=None):
    rows = get_unit_history_rows(conn, unit, floorplan)
    if not rows:
        print("No history found for that unit.")
        return

    print("query_date | timestamp | floorplan | unit | scraped_price | imported_price | effective_price | price_min | price_max | price_range | availability | price_source")
    for row in rows:
        print(" | ".join(str(value) for value in row))


def get_daily_average_rows(conn):
    effective_price_sql = f"COALESCE({latest_override_exact_price_sql('u')}, u.price)"
    return conn.execute(
        f"""
        {LATEST_UNIT_DAY_CTE}
        SELECT u.query_date,
               COUNT(*) AS unit_count,
               SUM(CASE WHEN {effective_price_sql} IS NOT NULL THEN 1 ELSE 0 END) AS exact_price_count,
               ROUND(AVG({effective_price_sql}), 2) AS avg_price,
               ROUND(MIN({effective_price_sql}), 2) AS min_price,
               ROUND(MAX({effective_price_sql}), 2) AS max_price,
               ROUND(MIN(u.price_min), 2) AS displayed_min_price,
               ROUND(MAX(u.price_max), 2) AS displayed_max_price
        FROM units u
        JOIN latest_unit_day l
          ON u.query_date = l.query_date
         AND u.floorplan = l.floorplan
         AND u.unit = l.unit
         AND u.timestamp = l.latest_timestamp
        GROUP BY u.query_date
        ORDER BY u.query_date
        """
    ).fetchall()


def get_daily_floorplan_average_rows(conn):
    shown_price_sql = f"COALESCE({latest_override_exact_price_sql('u')}, u.price, u.price_min)"
    return conn.execute(
        f"""
        {LATEST_UNIT_DAY_CTE}
        SELECT u.query_date,
               u.floorplan,
               COUNT(*) AS unit_count,
               ROUND(AVG({shown_price_sql}), 2) AS avg_shown_price,
               ROUND(MIN({shown_price_sql}), 2) AS min_shown_price,
               ROUND(MAX({shown_price_sql}), 2) AS max_shown_price
        FROM units u
        JOIN latest_unit_day l
          ON u.query_date = l.query_date
         AND u.floorplan = l.floorplan
         AND u.unit = l.unit
         AND u.timestamp = l.latest_timestamp
        WHERE {shown_price_sql} IS NOT NULL
        GROUP BY u.query_date, u.floorplan
        ORDER BY u.query_date, u.floorplan
        """
    ).fetchall()


def print_daily_average_prices(conn):
    rows = get_daily_average_rows(conn)
    if not rows:
        print("No price history found.")
        return

    print("query_date | unit_count | exact_price_count | avg_price | min_price | max_price | displayed_min_price | displayed_max_price")
    for row in rows:
        print(" | ".join(str(value) for value in row))


def get_daily_availability_rows(conn):
    return conn.execute(
        f"""
        {LATEST_UNIT_DAY_CTE}
        SELECT u.query_date,
               COUNT(*) AS total_units_seen,
               SUM(CASE WHEN lower(u.availability) = 'now' THEN 1 ELSE 0 END) AS available_now,
               SUM(CASE WHEN lower(u.availability) LIKE 'available on%' THEN 1 ELSE 0 END) AS available_later
        FROM units u
        JOIN latest_unit_day l
          ON u.query_date = l.query_date
         AND u.floorplan = l.floorplan
         AND u.unit = l.unit
         AND u.timestamp = l.latest_timestamp
        GROUP BY u.query_date
        ORDER BY u.query_date
        """
    ).fetchall()


def print_daily_availability(conn):
    rows = get_daily_availability_rows(conn)
    if not rows:
        print("No availability history found.")
        return

    print("query_date | total_units_seen | available_now | available_later")
    for row in rows:
        print(" | ".join(str(value) for value in row))


def get_latest_snapshot(conn):
    row = conn.execute("SELECT MAX(timestamp) FROM units").fetchone()
    if not row or not row[0]:
        return None, []

    latest_timestamp = row[0]
    override_price_sql = latest_override_exact_price_sql("u")
    override_source_sql = latest_override_source_sql("u")
    rows = conn.execute(
        """
        SELECT u.query_date,
               u.floorplan,
               u.unit,
               u.beds,
               u.baths,
               u.sqft,
               u.price,
        """ + override_price_sql + """ AS imported_price,
               COALESCE(
        """ + override_price_sql + """,
                   u.price
               ) AS effective_price,
               u.price_min,
               u.price_max,
               u.price_range,
               u.availability,
        """ + override_source_sql + """ AS price_source
        FROM units u
        WHERE u.timestamp = ?
        ORDER BY floorplan, unit
        """,
        (latest_timestamp,),
    ).fetchall()
    return latest_timestamp, rows


def print_latest_snapshot(conn):
    latest_timestamp, rows = get_latest_snapshot(conn)
    if not latest_timestamp:
        print("Database is empty.")
        return

    print(f"Latest snapshot timestamp: {latest_timestamp}")
    print("query_date | floorplan | unit | beds | baths | sqft | scraped_price | imported_price | effective_price | price_min | price_max | price_range | availability | price_source")
    for row in rows:
        print(" | ".join(str(value) for value in row))


def write_csv(output_path, header, rows):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        writer.writerows(rows)


def export_csv(conn, report, output, unit=None, floorplan=None):
    if report == "latest":
        _, rows = get_latest_snapshot(conn)
        header = ["query_date", "floorplan", "unit", "beds", "baths", "sqft", "scraped_price", "imported_price", "effective_price", "price_min", "price_max", "price_range", "availability", "price_source"]
    elif report == "avg-prices":
        rows = get_daily_average_rows(conn)
        header = ["query_date", "unit_count", "exact_price_count", "avg_price", "min_price", "max_price", "displayed_min_price", "displayed_max_price"]
    elif report == "availability":
        rows = get_daily_availability_rows(conn)
        header = ["query_date", "total_units_seen", "available_now", "available_later"]
    elif report == "unit-history":
        if not unit:
            raise ValueError("unit-history export requires --unit")
        rows = get_unit_history_rows(conn, unit, floorplan)
        header = ["query_date", "timestamp", "floorplan", "unit", "scraped_price", "imported_price", "effective_price", "price_min", "price_max", "price_range", "availability", "price_source"]
    elif report == "raw":
        rows = conn.execute(
            """
            SELECT timestamp, query_date, floorplan_id, floorplan, unit_id, unit, beds, baths, sqft, price, price_min, price_max, price_range, availability
            FROM units
            ORDER BY timestamp, floorplan, unit
            """
        ).fetchall()
        header = ["timestamp", "query_date", "floorplan_id", "floorplan", "unit_id", "unit", "beds", "baths", "sqft", "price", "price_min", "price_max", "price_range", "availability"]
    else:
        raise ValueError(f"Unsupported report: {report}")

    write_csv(output, header, rows)
    print(f"Wrote CSV to {Path(output)}")


def build_svg_line_chart(title, x_values, series, y_axis_label):
    width = 1000
    height = 520
    left = 90
    right = 40
    top = 60
    bottom = 110
    plot_width = width - left - right
    plot_height = height - top - bottom

    all_values = [value for _, _, values in series for value in values if value is not None]
    if not x_values or not all_values:
        return None

    min_y = min(all_values)
    max_y = max(all_values)
    if min_y == max_y:
        min_y -= 1
        max_y += 1
    padding = max((max_y - min_y) * 0.1, 1)
    min_y -= padding
    max_y += padding

    def x_pos(index):
        if len(x_values) == 1:
            return left + plot_width / 2
        return left + (plot_width * index / (len(x_values) - 1))

    def y_pos(value):
        return top + plot_height - ((value - min_y) / (max_y - min_y) * plot_height)

    y_ticks = []
    for tick in range(5):
        value = min_y + (max_y - min_y) * tick / 4
        y_ticks.append((value, y_pos(value)))

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fbfaf7"/>',
        f'<text x="{width / 2}" y="34" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="22" fill="#1a1a1a">{escape(title)}</text>',
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#333" stroke-width="1.5"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#333" stroke-width="1.5"/>',
    ]

    for value, y in y_ticks:
        svg.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" stroke="#d9d5cf" stroke-width="1"/>')
        svg.append(f'<text x="{left - 10}" y="{y + 5:.2f}" text-anchor="end" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#444">{value:.0f}</text>')

    svg.append(
        f'<text x="24" y="{top + plot_height / 2}" transform="rotate(-90 24 {top + plot_height / 2})" '
        f'font-family="Helvetica, Arial, sans-serif" font-size="14" fill="#444">{escape(y_axis_label)}</text>'
    )

    for index, label in enumerate(x_values):
        x = x_pos(index)
        svg.append(f'<line x1="{x:.2f}" y1="{top + plot_height}" x2="{x:.2f}" y2="{top + plot_height + 6}" stroke="#333" stroke-width="1"/>')
        svg.append(
            f'<text x="{x:.2f}" y="{top + plot_height + 24}" text-anchor="end" '
            f'transform="rotate(-35 {x:.2f} {top + plot_height + 24})" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#444">{escape(str(label))}</text>'
        )

    legend_x = left + plot_width - 180
    legend_y = top + 10
    for offset, (name, color, values) in enumerate(series):
        points = " ".join(
            f"{x_pos(index):.2f},{y_pos(value):.2f}"
            for index, value in enumerate(values)
            if value is not None
        )
        svg.append(f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{points}"/>')
        for index, value in enumerate(values):
            if value is None:
                continue
            svg.append(f'<circle cx="{x_pos(index):.2f}" cy="{y_pos(value):.2f}" r="4.5" fill="{color}"/>')

        legend_item_y = legend_y + offset * 22
        svg.append(f'<line x1="{legend_x}" y1="{legend_item_y}" x2="{legend_x + 20}" y2="{legend_item_y}" stroke="{color}" stroke-width="3"/>')
        svg.append(f'<text x="{legend_x + 28}" y="{legend_item_y + 4}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#444">{escape(name)}</text>')

    svg.append('</svg>')
    return "\n".join(svg)


def write_svg_chart(output_path, title, x_values, series, y_axis_label):
    svg = build_svg_line_chart(title, x_values, series, y_axis_label)
    if svg is None:
        return False
    output_path.write_text(svg, encoding="utf-8")
    return True


def format_dashboard_timestamp(timestamp_text):
    try:
        timestamp = datetime.fromisoformat(timestamp_text)
    except (TypeError, ValueError):
        return str(timestamp_text)

    day_text = timestamp.strftime("%A, %B %d, %Y")
    time_text = timestamp.strftime("%I:%M %p").lstrip("0")
    return f"{day_text} at {time_text}"


def generate_charts(conn, unit=None, floorplan=None):
    ensure_output_dirs()

    chart_paths = []

    avg_rows = get_daily_average_rows(conn)
    if avg_rows:
        dates = [row[0] for row in avg_rows]
        avg_prices = [row[3] for row in avg_rows]
        path = CHARTS_DIR / "average_prices_over_time.svg"
        if write_svg_chart(path, "Average Exact Rent By Query Day", dates, [("Average Exact Price", "#1f5aa6", avg_prices)], "Exact Price ($)"):
            chart_paths.append(path)

    availability_rows = get_daily_availability_rows(conn)
    if availability_rows:
        dates = [row[0] for row in availability_rows]
        available_now = [row[2] for row in availability_rows]
        available_later = [row[3] for row in availability_rows]
        path = CHARTS_DIR / "availability_over_time.svg"
        if write_svg_chart(
            path,
            "Availability By Query Day",
            dates,
            [
                ("Available Now", "#2f7d32", available_now),
                ("Available Later", "#d17a00", available_later),
            ],
            "Unit Count",
        ):
            chart_paths.append(path)

    if unit:
        unit_rows = get_unit_history_rows(conn, unit, floorplan)
        if unit_rows:
            timestamps = [row[1] for row in unit_rows]
            prices = [row[6] for row in unit_rows]
            label = f"{floorplan + ' ' if floorplan else ''}Unit {unit}".strip()

            suffix = f"{floorplan + '_' if floorplan else ''}{unit}".replace("/", "-")
            path = CHARTS_DIR / f"unit_price_history_{suffix}.svg"
            if write_svg_chart(path, f"Price History For {label}", timestamps, [(label, "#8b1e3f", prices)], "Price ($)"):
                chart_paths.append(path)

    if not chart_paths:
        print("No chart data available.")
        return

    print("Generated charts:")
    for path in chart_paths:
        print(f"  {path}")


def build_summary_cards(latest_timestamp, latest_snapshot_rows, avg_rows, availability_rows):
    if not latest_snapshot_rows:
        return "<p>No data available.</p>"

    latest_avg = avg_rows[-1] if avg_rows else ("n/a", 0, 0, 0, 0, 0, 0)
    latest_availability = availability_rows[-1] if availability_rows else ("n/a", 0, 0, 0)
    latest_timestamp_display = format_dashboard_timestamp(latest_timestamp)

    cards = [
        ("Latest Scrape", latest_timestamp_display),
        ("Query Day", latest_snapshot_rows[0][0]),
        ("Units In Latest Snapshot", str(len(latest_snapshot_rows))),
        ("Exact Prices Available", str(latest_avg[2])),
        ("Average Exact Price", f"${latest_avg[3]:,.2f}" if latest_avg[3] is not None else "n/a"),
        ("Displayed Range", f"${latest_avg[6]:,.2f} to ${latest_avg[7]:,.2f}" if latest_avg[6] is not None and latest_avg[7] is not None else "n/a"),
        ("Available Now", str(latest_availability[2])),
        ("Available Later", str(latest_availability[3])),
    ]

    return "\n".join(
        f'<article class="card"><h3>{escape(label)}</h3><p>{escape(str(value))}</p></article>'
        for label, value in cards
    )


def build_latest_snapshot_table(rows):
    table_rows = []
    for row in rows:
        exact_price = f"${row[8]:,.2f}" if row[8] is not None else "n/a"
        displayed_bounds = "n/a"
        if row[9] is not None and row[10] is not None:
            displayed_bounds = f"${row[9]:,.2f} to ${row[10]:,.2f}"
        price_source = row[13] or ("rentcafe" if row[8] is not None else "n/a")
        table_rows.append(
            "<tr>"
            f"<td>{escape(str(row[1]))}</td>"
            f"<td>{escape(str(row[2]))}</td>"
            f"<td>{escape(str(row[3]))}</td>"
            f"<td>{escape(str(row[4]))}</td>"
            f"<td>{escape(str(row[5]))}</td>"
            f"<td>{escape(exact_price)}</td>"
            f"<td>{escape(displayed_bounds)}</td>"
            f"<td>{escape(str(row[11]))}</td>"
            f"<td>{escape(str(row[12]))}</td>"
            f"<td>{escape(str(price_source))}</td>"
            "</tr>"
        )
    return "\n".join(table_rows)


def build_latest_snapshot_data(rows):
    snapshot_rows = []
    for row in rows:
        displayed_low_price = None
        if row[9] is not None:
            displayed_low_price = f"${row[9]:,.2f}"

        displayed_bounds = None
        if row[9] is not None and row[10] is not None:
            displayed_bounds = f"${row[9]:,.2f} to ${row[10]:,.2f}"

        exact_price = f"${row[8]:,.2f}" if row[8] is not None else None
        snapshot_rows.append(
            {
                "query_date": row[0],
                "floorplan": row[1],
                "unit": row[2],
                "beds": row[3],
                "baths": row[4],
                "sqft": row[5],
                "exact_price": exact_price,
                "effective_price": row[8],
                "display_price": exact_price or displayed_low_price,
                "sort_price": row[8] if row[8] is not None else row[9],
                "displayed_low_price": displayed_low_price,
                "displayed_bounds": displayed_bounds,
                "displayed_rent": row[11],
                "availability": row[12],
                "price_source": row[13] or ("rentcafe" if row[8] is not None else "displayed range"),
                "is_penthouse": str(row[1]).upper().startswith("PH"),
            }
        )
    return json.dumps(snapshot_rows)


def build_floorplan_history_data(rows):
    if not rows:
        return json.dumps({"dates": [], "series": []})

    dates = []
    for query_date, _, _, _, _, _ in rows:
        if query_date not in dates:
            dates.append(query_date)

    floorplans = []
    floorplan_series = {}
    for query_date, floorplan, unit_count, avg_shown_price, min_shown_price, max_shown_price in rows:
        if floorplan not in floorplans:
            floorplans.append(floorplan)
            floorplan_series[floorplan] = {
                "floorplan": floorplan,
                "values": {date: None for date in dates},
                "unit_counts": {date: 0 for date in dates},
                "min_values": {date: None for date in dates},
                "max_values": {date: None for date in dates},
            }

        floorplan_series[floorplan]["values"][query_date] = avg_shown_price
        floorplan_series[floorplan]["unit_counts"][query_date] = unit_count
        floorplan_series[floorplan]["min_values"][query_date] = min_shown_price
        floorplan_series[floorplan]["max_values"][query_date] = max_shown_price

    series = []
    for floorplan in floorplans:
        item = floorplan_series[floorplan]
        series.append(
            {
                "floorplan": floorplan,
                "values": [item["values"][date] for date in dates],
                "unit_counts": [item["unit_counts"][date] for date in dates],
                "min_values": [item["min_values"][date] for date in dates],
                "max_values": [item["max_values"][date] for date in dates],
            }
        )

    return json.dumps({"dates": dates, "series": series})


def build_availability_breakdown_table(rows):
    if not rows:
        return '<div class="empty-state">No daily availability history is available yet.</div>'

    table_rows = []
    for query_date, total_units_seen, available_now, available_later in rows:
        total_available = (available_now or 0) + (available_later or 0)
        availability_rate = (
            f"{(total_available / total_units_seen) * 100:.0f}%"
            if total_units_seen
            else "n/a"
        )
        future_share = (
            f"{(available_later / total_available) * 100:.0f}%"
            if total_available
            else "n/a"
        )
        table_rows.append(
            "<tr>"
            f"<td>{escape(str(query_date))}</td>"
            f"<td>{escape(str(total_units_seen))}</td>"
            f"<td>{escape(str(available_now))}</td>"
            f"<td>{escape(str(available_later))}</td>"
            f"<td>{escape(str(total_available))}</td>"
            f"<td>{escape(availability_rate)}</td>"
            f"<td>{escape(future_share)}</td>"
            "</tr>"
        )

    return (
        '<div class="table-wrap">'
        '<table>'
        '<thead><tr>'
        '<th>Query Day</th>'
        '<th>Units Seen</th>'
        '<th>Available Now</th>'
        '<th>Available Later</th>'
        '<th>Total Available</th>'
        '<th>Availability Rate</th>'
        '<th>Later Share</th>'
        '</tr></thead>'
        f'<tbody>{"".join(table_rows)}</tbody>'
        '</table>'
        '</div>'
    )


def generate_dashboard(conn):
    ensure_output_dirs()
    generate_charts(conn)

    latest_timestamp, latest_rows = get_latest_snapshot(conn)
    avg_rows = get_daily_average_rows(conn)
    availability_rows = get_daily_availability_rows(conn)
    floorplan_avg_rows = get_daily_floorplan_average_rows(conn)

    if not latest_timestamp:
        print("Database is empty.")
        return

    dashboard_path = OUTPUT_DIR / "dashboard.html"
    snapshot_json = build_latest_snapshot_data(latest_rows)
    floorplan_history_json = build_floorplan_history_data(floorplan_avg_rows)
    latest_timestamp_display = format_dashboard_timestamp(latest_timestamp)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Rent Tracker Dashboard</title>
    <style>
        :root {{
            --bg: #f6f1e8;
            --panel: #fffdf9;
            --ink: #201a14;
            --muted: #6f665d;
            --accent: #9f3a24;
            --accent-2: #1f5aa6;
            --line: #ddd1c2;
        }}
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; font-family: Georgia, "Times New Roman", serif; background: linear-gradient(180deg, #efe6d7 0%, var(--bg) 30%, #f8f6f2 100%); color: var(--ink); }}
        .wrap {{ max-width: 1200px; margin: 0 auto; padding: 32px 20px 48px; }}
        .hero {{ display: grid; gap: 12px; margin-bottom: 28px; }}
        .eyebrow {{ letter-spacing: 0.16em; text-transform: uppercase; font-size: 12px; color: var(--accent); font-weight: bold; }}
        h1 {{ margin: 0; font-size: clamp(32px, 6vw, 56px); line-height: 0.95; }}
        .sub {{ color: var(--muted); max-width: 760px; font-size: 18px; line-height: 1.5; }}
        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin: 26px 0 36px; }}
        .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 18px; box-shadow: 0 10px 30px rgba(32, 26, 20, 0.05); }}
        .card h3 {{ margin: 0 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted); }}
        .card p {{ margin: 0; font-size: 24px; line-height: 1.2; }}
        .section {{ margin-top: 34px; }}
        .section h2 {{ margin: 0 0 14px; font-size: 26px; }}
        .chart-grid {{ display: grid; grid-template-columns: 1fr; gap: 20px; }}
        .chart-panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 14px; box-shadow: 0 10px 30px rgba(32, 26, 20, 0.05); }}
        .chart-panel img {{ width: 100%; height: auto; display: block; border-radius: 12px; }}
        .chart-stage {{ width: 100%; min-height: 320px; border-radius: 12px; overflow: hidden; background: #fbfaf7; }}
        .links {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 14px; }}
        .links a {{ color: white; background: var(--accent); text-decoration: none; padding: 10px 14px; border-radius: 999px; font-size: 14px; }}
        .links a.alt {{ background: var(--accent-2); }}
        .floorplan-picker {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }}
        .floorplan-button {{ border: 1px solid var(--line); border-radius: 999px; background: #f8f2ea; color: var(--ink); padding: 9px 14px; font: inherit; cursor: pointer; transition: background 160ms ease, color 160ms ease, border-color 160ms ease; }}
        .floorplan-button.active {{ background: var(--accent-2); color: #fff; border-color: var(--accent-2); }}
        .chart-caption {{ margin: 12px 0 0; color: var(--muted); font-size: 14px; line-height: 1.5; }}
        .section-head {{ display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 12px; margin-bottom: 14px; }}
        .section-sub {{ margin: 0; color: var(--muted); font-size: 16px; line-height: 1.5; max-width: 760px; }}
        .results-meta {{ color: var(--muted); font-size: 14px; }}
        .breakdown-note {{ margin: 12px 0 0; color: var(--muted); font-size: 14px; line-height: 1.5; }}
        .browser {{ display: grid; gap: 18px; }}
        .controls {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; background: rgba(255, 253, 249, 0.88); border: 1px solid var(--line); border-radius: 18px; padding: 16px; box-shadow: 0 10px 30px rgba(32, 26, 20, 0.05); backdrop-filter: blur(12px); }}
        .control {{ display: grid; gap: 6px; }}
        .control span {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted); font-weight: bold; }}
        .control select {{ width: 100%; border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; background: #fff; color: var(--ink); font: inherit; }}
        .check {{ display: flex; align-items: center; gap: 10px; padding-top: 22px; color: var(--ink); font-size: 14px; }}
        .check input {{ width: 18px; height: 18px; accent-color: var(--accent); }}
        .unit-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
        .unit-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 20px; padding: 18px; box-shadow: 0 12px 30px rgba(32, 26, 20, 0.06); display: grid; gap: 14px; }}
        .unit-top {{ display: flex; justify-content: space-between; gap: 16px; align-items: start; }}
        .unit-title {{ margin: 0; font-size: 24px; line-height: 1; }}
        .unit-plan {{ margin: 4px 0 0; color: var(--muted); font-size: 14px; }}
        .unit-price {{ text-align: right; }}
        .unit-price strong {{ display: block; font-size: 24px; line-height: 1; }}
        .unit-price span {{ display: block; margin-top: 4px; color: var(--muted); font-size: 13px; }}
        .pill-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .pill {{ display: inline-flex; align-items: center; border: 1px solid var(--line); border-radius: 999px; padding: 7px 10px; background: #f8f2ea; font-size: 13px; color: var(--ink); }}
        .availability {{ display: inline-flex; align-items: center; width: fit-content; border-radius: 999px; padding: 8px 12px; font-size: 13px; font-weight: bold; letter-spacing: 0.03em; }}
        .availability-now {{ background: #e5f3e6; color: #215b2b; }}
        .availability-later {{ background: #fff1da; color: #8a5800; }}
        .availability-other {{ background: #efe7dc; color: #65594d; }}
        table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 18px; overflow: hidden; box-shadow: 0 10px 30px rgba(32, 26, 20, 0.05); }}
        th, td {{ padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: left; font-size: 14px; }}
        th {{ background: #f2ebe1; position: sticky; top: 0; }}
        .table-wrap {{ overflow: auto; border-radius: 18px; }}
        .empty-state {{ background: var(--panel); border: 1px dashed var(--line); border-radius: 18px; padding: 24px; text-align: center; color: var(--muted); }}
        .hidden {{ display: none; }}
        .footer {{ margin-top: 28px; color: var(--muted); font-size: 14px; }}
        @media (min-width: 900px) {{ .chart-grid {{ grid-template-columns: 1fr 1fr; }} }}
        @media (max-width: 720px) {{
            .unit-top {{ grid-template-columns: 1fr; display: grid; }}
            .unit-price {{ text-align: left; }}
            .check {{ padding-top: 0; }}
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <header class="hero">
            <div class="eyebrow">Local Rent Tracker</div>
            <h1>Alta Art Tower rent dashboard</h1>
            <p class="sub">This page is generated from your local SQLite database. Latest scrape: {escape(latest_timestamp_display)}. It shows the latest snapshot, daily average rent trend, and availability trend using the same data your tracker saves every morning.</p>
        </header>

        <section class="cards">
            {build_summary_cards(latest_timestamp, latest_rows, avg_rows, availability_rows)}
        </section>

        <section class="section">
            <h2>Trend Charts</h2>
            <div class="chart-grid">
                <div class="chart-panel">
                    <img src="charts/average_prices_over_time.svg" alt="Average prices over time chart">
                </div>
                <div class="chart-panel">
                    <img src="charts/availability_over_time.svg" alt="Availability over time chart">
                </div>
            </div>
            <div class="links">
                <a href="raw_units_history.csv">Download raw CSV</a>
                <a class="alt" href="charts/average_prices_over_time.svg">Open average price chart</a>
                <a class="alt" href="charts/availability_over_time.svg">Open availability chart</a>
            </div>
        </section>

        <section class="section">
            <div class="section-head">
                <div>
                    <h2>Daily Availability Breakdown</h2>
                    <p class="section-sub">This uses the latest saved row for each floorplan and unit on each query day, so repeated scrapes on the same day do not double-count units.</p>
                </div>
            </div>
            {build_availability_breakdown_table(availability_rows)}
            <p class="breakdown-note">Availability rate is total available units divided by total units seen that day. Later share is the portion of available units that were listed with a future move-in date.</p>
        </section>

        <section class="section">
            <div class="section-head">
                <div>
                    <h2>Floorplan Price Over Time</h2>
                    <p class="section-sub">Compare selected floorplans using each day&apos;s average shown price. If exact prices are missing, the chart falls back to the low end of the displayed range so B1 and B2 still track over time.</p>
                </div>
            </div>
            <div class="chart-panel">
                <div class="floorplan-picker" id="floorplanPicker"></div>
                <div class="chart-stage" id="floorplanChart"></div>
                <p class="chart-caption" id="floorplanChartCaption"></p>
            </div>
        </section>

        <section class="section">
            <div class="section-head">
                <div>
                    <h2>Latest Snapshot</h2>
                    <p class="section-sub">Browse the latest units as cards instead of scanning one long raw table. You can filter by bedrooms and bathrooms, change the sort order, and hide penthouses by default.</p>
                </div>
                <div class="results-meta" id="resultsMeta"></div>
            </div>
            <div class="browser">
                <div class="controls">
                    <label class="control">
                        <span>Bedrooms</span>
                        <select id="bedsFilter">
                            <option value="all">All bedroom counts</option>
                        </select>
                    </label>
                    <label class="control">
                        <span>Bathrooms</span>
                        <select id="bathsFilter">
                            <option value="all">All bathroom counts</option>
                        </select>
                    </label>
                    <label class="control">
                        <span>Sort By</span>
                        <select id="sortBy">
                            <option value="price-asc">Lowest price first</option>
                            <option value="price-desc">Highest price first</option>
                            <option value="beds-baths">Bedrooms, then bathrooms</option>
                            <option value="sqft-desc">Largest floorplan first</option>
                            <option value="availability">Availability</option>
                            <option value="unit">Unit number</option>
                        </select>
                    </label>
                    <label class="check">
                        <input id="excludePenthouse" type="checkbox" checked>
                        <span>Hide penthouses</span>
                    </label>
                </div>
                <div class="unit-grid" id="unitGrid"></div>
                <div class="empty-state hidden" id="emptyState">No units match the current filters.</div>
            </div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Floorplan</th>
                            <th>Unit</th>
                            <th>Beds</th>
                            <th>Baths</th>
                            <th>Sqft</th>
                            <th>Shown Price</th>
                            <th>Stored Range</th>
                            <th>Displayed Rent</th>
                            <th>Availability</th>
                            <th>Price Source</th>
                        </tr>
                    </thead>
                    <tbody id="snapshotTableBody"></tbody>
                </table>
            </div>
        </section>

        <p class="footer">Dashboard file: {escape(str(dashboard_path))}</p>
    </div>
    <script>
        const snapshotRows = {snapshot_json};
        const floorplanHistory = {floorplan_history_json};

        const bedsFilter = document.getElementById("bedsFilter");
        const bathsFilter = document.getElementById("bathsFilter");
        const sortBy = document.getElementById("sortBy");
        const excludePenthouse = document.getElementById("excludePenthouse");
        const unitGrid = document.getElementById("unitGrid");
        const snapshotTableBody = document.getElementById("snapshotTableBody");
        const emptyState = document.getElementById("emptyState");
        const resultsMeta = document.getElementById("resultsMeta");
        const floorplanPicker = document.getElementById("floorplanPicker");
        const floorplanChart = document.getElementById("floorplanChart");
        const floorplanChartCaption = document.getElementById("floorplanChartCaption");

        const chartColors = ["#1f5aa6", "#9f3a24", "#2f7d32", "#8b1e3f", "#7f5af0", "#c07a00"];
        const preferredFloorplans = ["B1", "B2"];
        const availableFloorplans = floorplanHistory.series.map((item) => item.floorplan);
        const selectedFloorplans = new Set(
            preferredFloorplans.filter((floorplan) => availableFloorplans.includes(floorplan)).slice(0, 2)
        );
        if (selectedFloorplans.size === 0) {{
            availableFloorplans.slice(0, 2).forEach((floorplan) => selectedFloorplans.add(floorplan));
        }}

        function parseNumber(value) {{
            const match = String(value || "").match(/(\d+(?:\.\d+)?)/);
            return match ? Number(match[1]) : null;
        }}

        function parseSqft(value) {{
            const normalized = String(value || "").replace(/,/g, "");
            const match = normalized.match(/(\d+(?:\.\d+)?)/);
            return match ? Number(match[1]) : null;
        }}

        function availabilityClass(value) {{
            const normalized = String(value || "").toLowerCase();
            if (normalized === "now") return "availability availability-now";
            if (normalized.startsWith("available on")) return "availability availability-later";
            return "availability availability-other";
        }}

        function availabilityRank(value) {{
            const normalized = String(value || "").toLowerCase();
            if (normalized === "now") return 0;
            if (normalized.startsWith("available on")) return 1;
            return 2;
        }}

        function compareValues(left, right) {{
            if (left === right) return 0;
            if (left === null || left === undefined) return 1;
            if (right === null || right === undefined) return -1;
            if (left < right) return -1;
            return 1;
        }}

        function formatCurrency(value) {{
            if (value === null || value === undefined) return "n/a";
            return `$${{Number(value).toLocaleString(undefined, {{ minimumFractionDigits: 0, maximumFractionDigits: 0 }})}}`;
        }}

        function createFloorplanButtons() {{
            floorplanPicker.innerHTML = floorplanHistory.series.map((item) => `
                <button class="floorplan-button${{selectedFloorplans.has(item.floorplan) ? " active" : ""}}" data-floorplan="${{item.floorplan}}" type="button">
                    ${{item.floorplan}}
                </button>
            `).join("");

            floorplanPicker.querySelectorAll(".floorplan-button").forEach((button) => {{
                button.addEventListener("click", () => {{
                    const floorplan = button.dataset.floorplan;
                    if (selectedFloorplans.has(floorplan)) {{
                        if (selectedFloorplans.size === 1) return;
                        selectedFloorplans.delete(floorplan);
                    }} else {{
                        selectedFloorplans.add(floorplan);
                    }}
                    createFloorplanButtons();
                    renderFloorplanChart();
                }});
            }});
        }}

        function renderFloorplanChart() {{
            const selectedSeries = floorplanHistory.series.filter((item) => selectedFloorplans.has(item.floorplan));
            if (!floorplanHistory.dates.length || !selectedSeries.length) {{
                floorplanChart.innerHTML = '<div class="empty-state">No floorplan history is available yet.</div>';
                floorplanChartCaption.textContent = "";
                return;
            }}

            const width = 1000;
            const height = 420;
            const left = 74;
            const right = 28;
            const top = 32;
            const bottom = 76;
            const plotWidth = width - left - right;
            const plotHeight = height - top - bottom;
            const allValues = selectedSeries.flatMap((item) => item.values.filter((value) => value !== null && value !== undefined));

            let minY = Math.min(...allValues);
            let maxY = Math.max(...allValues);
            if (minY === maxY) {{
                minY -= 1;
                maxY += 1;
            }}
            const padding = Math.max((maxY - minY) * 0.1, 1);
            minY -= padding;
            maxY += padding;

            function xPos(index) {{
                if (floorplanHistory.dates.length === 1) return left + plotWidth / 2;
                return left + (plotWidth * index / (floorplanHistory.dates.length - 1));
            }}

            function yPos(value) {{
                return top + plotHeight - ((value - minY) / (maxY - minY) * plotHeight);
            }}

            const yTicks = Array.from({{ length: 5 }}, (_, index) => {{
                const value = minY + (maxY - minY) * index / 4;
                return {{ value, y: yPos(value) }};
            }});

            const legend = selectedSeries.map((item, index) => {{
                const color = chartColors[index % chartColors.length];
                return `<g>
                    <line x1="${{width - 190}}" y1="${{top + 14 + index * 22}}" x2="${{width - 168}}" y2="${{top + 14 + index * 22}}" stroke="${{color}}" stroke-width="3" />
                    <text x="${{width - 158}}" y="${{top + 18 + index * 22}}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#444">${{item.floorplan}}</text>
                </g>`;
            }}).join("");

            const seriesMarkup = selectedSeries.map((item, index) => {{
                const color = chartColors[index % chartColors.length];
                const points = item.values
                    .map((value, pointIndex) => value === null || value === undefined ? null : `${{xPos(pointIndex).toFixed(2)}},${{yPos(value).toFixed(2)}}`)
                    .filter(Boolean)
                    .join(" ");
                const circles = item.values.map((value, pointIndex) => {{
                    if (value === null || value === undefined) return "";
                    return `<circle cx="${{xPos(pointIndex).toFixed(2)}}" cy="${{yPos(value).toFixed(2)}}" r="4.5" fill="${{color}}"><title>${{item.floorplan}} on ${{floorplanHistory.dates[pointIndex]}}: ${{formatCurrency(value)}} average shown price</title></circle>`;
                }}).join("");
                return `<g>
                    <polyline fill="none" stroke="${{color}}" stroke-width="3" points="${{points}}" />
                    ${{circles}}
                </g>`;
            }}).join("");

            floorplanChart.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 ${{width}} ${{height}}" role="img" aria-label="Floorplan average price chart">
                    <rect width="100%" height="100%" fill="#fbfaf7" />
                    <text x="${{width / 2}}" y="24" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="20" fill="#1a1a1a">Average Shown Price By Floorplan</text>
                    <line x1="${{left}}" y1="${{top + plotHeight}}" x2="${{left + plotWidth}}" y2="${{top + plotHeight}}" stroke="#333" stroke-width="1.5" />
                    <line x1="${{left}}" y1="${{top}}" x2="${{left}}" y2="${{top + plotHeight}}" stroke="#333" stroke-width="1.5" />
                    ${{yTicks.map((tick) => `
                        <g>
                            <line x1="${{left}}" y1="${{tick.y.toFixed(2)}}" x2="${{left + plotWidth}}" y2="${{tick.y.toFixed(2)}}" stroke="#d9d5cf" stroke-width="1" />
                            <text x="${{left - 10}}" y="${{(tick.y + 5).toFixed(2)}}" text-anchor="end" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#444">${{Math.round(tick.value)}}</text>
                        </g>
                    `).join("")}}
                    ${{floorplanHistory.dates.map((label, index) => `
                        <g>
                            <line x1="${{xPos(index).toFixed(2)}}" y1="${{top + plotHeight}}" x2="${{xPos(index).toFixed(2)}}" y2="${{top + plotHeight + 6}}" stroke="#333" stroke-width="1" />
                            <text x="${{xPos(index).toFixed(2)}}" y="${{top + plotHeight + 22}}" text-anchor="end" transform="rotate(-35 ${{xPos(index).toFixed(2)}} ${{top + plotHeight + 22}})" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#444">${{label}}</text>
                        </g>
                    `).join("")}}
                    ${{seriesMarkup}}
                    ${{legend}}
                </svg>
            `;

            const latestDateIndex = floorplanHistory.dates.length - 1;
            const latestSummary = selectedSeries.map((item) => {{
                const latestValue = item.values[latestDateIndex];
                const latestUnits = item.unit_counts[latestDateIndex];
                return `${{item.floorplan}}: ${{formatCurrency(latestValue)}} across ${{latestUnits}} unit${{latestUnits === 1 ? "" : "s"}}`;
            }}).join(" • ");
            floorplanChartCaption.textContent = `Selected floorplans use the average shown price for each day. Latest: ${{latestSummary}}.`;
        }}

        function populateSelectOptions(select, values, labelSuffix) {{
            values.forEach((value) => {{
                const option = document.createElement("option");
                option.value = String(value);
                option.textContent = `${{value}} ${{labelSuffix}}`;
                select.appendChild(option);
            }});
        }}

        const uniqueBeds = [...new Set(snapshotRows.map((row) => parseNumber(row.beds)).filter((value) => value !== null))].sort((a, b) => a - b);
        const uniqueBaths = [...new Set(snapshotRows.map((row) => parseNumber(row.baths)).filter((value) => value !== null))].sort((a, b) => a - b);

        populateSelectOptions(bedsFilter, uniqueBeds, "bed");
        populateSelectOptions(bathsFilter, uniqueBaths, "bath");

        function applyFilters() {{
            const selectedBeds = bedsFilter.value === "all" ? null : Number(bedsFilter.value);
            const selectedBaths = bathsFilter.value === "all" ? null : Number(bathsFilter.value);

            let rows = snapshotRows.filter((row) => {{
                if (excludePenthouse.checked && row.is_penthouse) return false;
                if (selectedBeds !== null && parseNumber(row.beds) !== selectedBeds) return false;
                if (selectedBaths !== null && parseNumber(row.baths) !== selectedBaths) return false;
                return true;
            }});

            rows.sort((left, right) => {{
                switch (sortBy.value) {{
                    case "price-desc":
                        return compareValues(right.sort_price, left.sort_price);
                    case "beds-baths": {{
                        const bedCompare = compareValues(parseNumber(left.beds), parseNumber(right.beds));
                        if (bedCompare !== 0) return bedCompare;
                        const bathCompare = compareValues(parseNumber(left.baths), parseNumber(right.baths));
                        if (bathCompare !== 0) return bathCompare;
                        return compareValues(left.sort_price, right.sort_price);
                    }}
                    case "sqft-desc":
                        return compareValues(parseSqft(right.sqft), parseSqft(left.sqft));
                    case "availability": {{
                        const availabilityCompare = compareValues(availabilityRank(left.availability), availabilityRank(right.availability));
                        if (availabilityCompare !== 0) return availabilityCompare;
                        return compareValues(left.sort_price, right.sort_price);
                    }}
                    case "unit":
                        return String(left.unit).localeCompare(String(right.unit), undefined, {{ numeric: true }});
                    case "price-asc":
                    default:
                        return compareValues(left.sort_price, right.sort_price);
                }}
            }});

            render(rows);
        }}

        function render(rows) {{
            unitGrid.innerHTML = rows.map((row) => `
                <article class="unit-card">
                    <div class="unit-top">
                        <div>
                            <h3 class="unit-title">Unit ${{row.unit}}</h3>
                            <p class="unit-plan">Floorplan ${{row.floorplan}}</p>
                        </div>
                        <div class="unit-price">
                            <strong>${{row.display_price || row.displayed_rent || "n/a"}}</strong>
                            <span>${{row.exact_price ? "Exact price" : "Lowest listed price"}}</span>
                        </div>
                    </div>
                    <div class="pill-row">
                        <span class="pill">${{row.beds}}</span>
                        <span class="pill">${{row.baths}}</span>
                        <span class="pill">${{row.sqft}}</span>
                        <span class="pill">Range: ${{row.displayed_bounds || row.displayed_rent || "n/a"}}</span>
                        <span class="pill">Source: ${{row.price_source}}</span>
                    </div>
                    <span class="${{availabilityClass(row.availability)}}">${{row.availability}}</span>
                </article>
            `).join("");

            snapshotTableBody.innerHTML = rows.map((row) => `
                <tr>
                    <td>${{row.floorplan}}</td>
                    <td>${{row.unit}}</td>
                    <td>${{row.beds}}</td>
                    <td>${{row.baths}}</td>
                    <td>${{row.sqft}}</td>
                    <td>${{row.display_price || "n/a"}}</td>
                    <td>${{row.displayed_bounds || row.displayed_rent || "n/a"}}</td>
                    <td>${{row.displayed_rent || "n/a"}}</td>
                    <td>${{row.availability}}</td>
                    <td>${{row.price_source}}</td>
                </tr>
            `).join("");

            const visiblePenthouses = rows.filter((row) => row.is_penthouse).length;
            resultsMeta.textContent = `${{rows.length}} units shown${{visiblePenthouses ? `, including ${{visiblePenthouses}} penthouse${{visiblePenthouses === 1 ? "" : "s"}}` : ""}}`;
            emptyState.classList.toggle("hidden", rows.length !== 0);
        }}

        [bedsFilter, bathsFilter, sortBy, excludePenthouse].forEach((control) => {{
            control.addEventListener("change", applyFilters);
        }});

        createFloorplanButtons();
        renderFloorplanChart();
        applyFilters();
    </script>
</body>
</html>
"""

    dashboard_path.write_text(html, encoding="utf-8")
    print(f"Wrote dashboard to {dashboard_path}")


def main():
    parser = argparse.ArgumentParser(description="Inspect rent tracker history.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("saved-data", help="Show what columns are stored.")
    subparsers.add_parser("latest", help="Show the latest saved scrape.")
    subparsers.add_parser("avg-prices", help="Show average prices by query day.")
    subparsers.add_parser("availability", help="Show availability counts by query day.")

    unit_parser = subparsers.add_parser("unit-history", help="Show history for one unit.")
    unit_parser.add_argument("unit", help="Unit number, e.g. 1112")
    unit_parser.add_argument("--floorplan", help="Optional floorplan filter, e.g. A1")

    export_parser = subparsers.add_parser("export-csv", help="Export data to CSV.")
    export_parser.add_argument("report", choices=["latest", "avg-prices", "availability", "unit-history", "raw"])
    export_parser.add_argument("output", help="Output CSV path")
    export_parser.add_argument("--unit", help="Required for unit-history export")
    export_parser.add_argument("--floorplan", help="Optional floorplan filter for unit-history export")

    charts_parser = subparsers.add_parser("charts", help="Generate SVG charts in outputs/charts.")
    charts_parser.add_argument("--unit", help="Optional unit number for a per-unit price history chart")
    charts_parser.add_argument("--floorplan", help="Optional floorplan filter for the unit chart")

    subparsers.add_parser("dashboard", help="Generate a local HTML dashboard in outputs/dashboard.html.")

    args = parser.parse_args()

    if args.command == "saved-data":
        print_saved_data()
        return

    conn = connect_db()
    try:
        if args.command == "latest":
            print_latest_snapshot(conn)
        elif args.command == "avg-prices":
            print_daily_average_prices(conn)
        elif args.command == "availability":
            print_daily_availability(conn)
        elif args.command == "unit-history":
            print_unit_history(conn, args.unit, args.floorplan)
        elif args.command == "export-csv":
            export_csv(conn, args.report, args.output, args.unit, args.floorplan)
        elif args.command == "charts":
            generate_charts(conn, args.unit, args.floorplan)
        elif args.command == "dashboard":
            generate_dashboard(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()