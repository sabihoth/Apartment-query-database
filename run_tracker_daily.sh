#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/Library/Logs/rent-tracker"

mkdir -p "$LOG_DIR"

"$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/tracker.py" \
  >> "$LOG_DIR/tracker.log" \
  2>> "$LOG_DIR/tracker.err.log"

"$SCRIPT_DIR/venv/bin/python" -B "$SCRIPT_DIR/report_db.py" export-csv raw "$SCRIPT_DIR/outputs/raw_units_history.csv" \
  >> "$LOG_DIR/tracker.log" \
  2>> "$LOG_DIR/tracker.err.log"

"$SCRIPT_DIR/venv/bin/python" -B "$SCRIPT_DIR/report_db.py" dashboard \
  >> "$LOG_DIR/tracker.log" \
  2>> "$LOG_DIR/tracker.err.log"