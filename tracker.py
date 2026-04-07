import cloudscraper
import json
import sqlite3
import re
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
DB_PATH = OUTPUT_DIR / "rent_data.db"
LISTING_URL = "https://www.rentcafe.com/apartments/or/portland/alta-art-tower-0/default.aspx"


def normalize_unit_name(value):
    return " ".join((value or "").split())


def parse_rent_values(rent_text):
    values = [float(value.replace(",", "")) for value in re.findall(r"\$([\d,]+)", rent_text or "")]
    if not values:
        return None, None, None
    if len(values) == 1:
        return values[0], values[0], values[0]
    return None, min(values), max(values)


def get_property_id(soup):
    tag = soup.find(id="selectedPropertyId")
    if tag and tag.get("value"):
        return tag["value"]
    return None


def fetch_floorplan_units(scraper, property_id, floorplan_id):
    if not property_id or not floorplan_id:
        return {}, {}

    response = scraper.get(
        "https://www.rentcafe.com/details/floorplans/modal-ds",
        params={
            "propertyId": property_id,
            "floorplanId": floorplan_id,
            "UnitId": "undefined",
        },
    )
    response.raise_for_status()

    payload = response.json()
    units = json.loads(payload.get("units") or "[]")

    by_id = {}
    by_name = {}
    for unit in units:
        unit_id = str(unit.get("Id") or "").strip()
        unit_name = normalize_unit_name(unit.get("Name"))
        if unit_id:
            by_id[unit_id] = unit
        if unit_name:
            by_name[unit_name] = unit
    return by_id, by_name


def backfill_price_columns(conn):
    rows = conn.execute(
        "SELECT rowid, price_range, price, price_min, price_max FROM units WHERE price_range IS NOT NULL"
    ).fetchall()

    updates = []
    for rowid, price_range, price, price_min, price_max in rows:
        exact_price, parsed_min, parsed_max = parse_rent_values(price_range)
        if exact_price == price and parsed_min == price_min and parsed_max == price_max:
            continue
        updates.append((exact_price, parsed_min, parsed_max, rowid))

    if updates:
        conn.executemany(
            "UPDATE units SET price = ?, price_min = ?, price_max = ? WHERE rowid = ?",
            updates,
        )


def fetch_units():
    scraper = cloudscraper.create_scraper()
    response = scraper.get(LISTING_URL)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.find_all("div", class_="fp-item")
    property_id = get_property_id(soup)

    units = []
    now = datetime.now()
    timestamp = now.isoformat()
    query_date = now.date().isoformat()

    for card in cards:
        floorplan_id = card.get("data-floorplan-id") or card.get("data-id", "")
        fp_name = card.get("data-name", "Unknown")
        beds = card.get("data-beds", "")
        baths = card.get("data-baths", "")
        sqft = card.get("data-size", "")
        modal_units_by_id, modal_units_by_name = fetch_floorplan_units(
            scraper, property_id, floorplan_id
        )

        # Parse individual unit rows from the table
        rows = card.select("tr.fp-unit")
        for row in rows:
            th = row.find("th")
            cells = row.find_all("td")
            if not th or len(cells) < 2:
                continue

            unit_name = normalize_unit_name(th.get_text(strip=True))
            unit_id = (row.get("data-unit-id") or "").strip()
            modal_unit = modal_units_by_id.get(unit_id) or modal_units_by_name.get(unit_name) or {}

            rent_text = (
                modal_unit.get("Rent")
                or row.get("data-unit-rent")
                or cells[0].get_text(strip=True)
            )
            availability = (
                modal_unit.get("AvailableDate")
                or cells[1].get_text(" ", strip=True)
            )
            price, price_min, price_max = parse_rent_values(rent_text)

            units.append({
                "timestamp": timestamp,
                "query_date": query_date,
                "floorplan_id": floorplan_id,
                "floorplan": fp_name,
                "unit_id": unit_id or str(modal_unit.get("Id") or ""),
                "unit": unit_name,
                "beds": beds,
                "baths": baths,
                "sqft": sqft,
                "price": price,
                "price_min": price_min,
                "price_max": price_max,
                "price_range": rent_text,
                "availability": availability,
            })

    return units


def ensure_schema(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS units (
            timestamp TEXT,
            query_date TEXT,
            floorplan_id TEXT,
            floorplan TEXT,
            unit_id TEXT,
            unit TEXT,
            beds TEXT,
            baths TEXT,
            sqft TEXT,
            price REAL,
            price_min REAL,
            price_max REAL,
            price_range TEXT,
            availability TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS unit_price_overrides (
            created_at TEXT NOT NULL,
            query_date TEXT NOT NULL,
            floorplan TEXT NOT NULL,
            unit TEXT NOT NULL,
            exact_price REAL NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (query_date, floorplan, unit, source)
        )
    """)

    columns = {
        row[1] for row in c.execute("PRAGMA table_info(units)")
    }
    if "query_date" not in columns:
        c.execute("ALTER TABLE units ADD COLUMN query_date TEXT")
        c.execute(
            "UPDATE units SET query_date = substr(timestamp, 1, 10) WHERE query_date IS NULL"
        )
    if "floorplan_id" not in columns:
        c.execute("ALTER TABLE units ADD COLUMN floorplan_id TEXT")
    if "unit_id" not in columns:
        c.execute("ALTER TABLE units ADD COLUMN unit_id TEXT")
    if "price_min" not in columns:
        c.execute("ALTER TABLE units ADD COLUMN price_min REAL")
        c.execute(
            "UPDATE units SET price_min = price WHERE price_min IS NULL AND price IS NOT NULL"
        )
    if "price_max" not in columns:
        c.execute("ALTER TABLE units ADD COLUMN price_max REAL")
        c.execute(
            "UPDATE units SET price_max = price WHERE price_max IS NULL AND price IS NOT NULL"
        )

    backfill_price_columns(conn)

    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_units_query_date ON units(query_date)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_units_floorplan_unit_date ON units(floorplan, unit, query_date)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_overrides_query_date_floorplan_unit ON unit_price_overrides(query_date, floorplan, unit)"
    )


def save_to_db(units):
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    ensure_schema(conn)

    for u in units:
        c.execute("""
            INSERT INTO units (
                timestamp,
                query_date,
                floorplan_id,
                floorplan,
                unit_id,
                unit,
                beds,
                baths,
                sqft,
                price,
                price_min,
                price_max,
                price_range,
                availability
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            u["timestamp"],
            u["query_date"],
            u["floorplan_id"],
            u["floorplan"],
            u["unit_id"],
            u["unit"],
            u["beds"],
            u["baths"],
            u["sqft"],
            u["price"],
            u["price_min"],
            u["price_max"],
            u["price_range"],
            u["availability"],
        ))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    units = fetch_units()
    print(f"Found {len(units)} units")
    for u in units:
        if u["price"] is not None:
            price_text = f"${u['price']:,.0f}"
        elif u["price_min"] is not None and u["price_max"] is not None:
            price_text = f"${u['price_min']:,.0f} - ${u['price_max']:,.0f}"
        else:
            price_text = u["price_range"]
        print(f"  {u['floorplan']} - Unit {u['unit']}: {price_text} ({u['availability']})")
    save_to_db(units)
    print(f"Saved to {DB_PATH} for query date {units[0]['query_date'] if units else 'n/a'}")