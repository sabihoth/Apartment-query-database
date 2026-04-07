# Rent Tracker

Tracks rent and availability for the Alta Art Tower RentCafe listing, stores the data in SQLite, and generates a local HTML dashboard.

## Setup

```bash
python3.11 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If `python3.11` is not available, use `python3` if it points to Python 3.11 or newer.

## Run

```bash
python tracker.py
python -B report_db.py export-csv raw outputs/raw_units_history.csv
python -B report_db.py dashboard
open outputs/dashboard.html
```

Or run the combined script:

```bash
./run_tracker_daily.sh
```

## Files

- `tracker.py`: scrapes the listing and saves to `outputs/rent_data.db`
- `report_db.py`: builds the dashboard, charts, and CSV exports
- `run_tracker_daily.sh`: runs the tracker and report steps together
- `install_scheduler.py`: installs a daily macOS LaunchAgent

Generated output goes in `outputs/`.

## Scope

This repo is currently configured for Alta Art Tower.

It may work on some other RentCafe properties if they use the same page structure and floorplan endpoints, but it is not a generic scraper for every RentCafe site. In practice, expect to update the listing URL and possibly the parsing logic for another property.