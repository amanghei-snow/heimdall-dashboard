#!/bin/bash
# Heimdall — scheduled collection wrapper (Mon & Fri)
set -euo pipefail

# Ensure cron has a usable PATH (cron uses minimal /usr/bin:/bin)
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

PROJECT_DIR="/Users/aman.ghei/CascadeProjects/ruckus-aggregator"
VENV="$PROJECT_DIR/.venv/bin/activate"
LOG_DIR="$HOME/Downloads/ruckus_aggregator_output/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
LOG_FILE="$LOG_DIR/run_${TIMESTAMP}.log"

echo "=== Heimdall run started at $(date) ===" | tee "$LOG_FILE"

cd "$PROJECT_DIR"
source "$VENV"

# Step 1: Full scan — all instances
python3 main.py --csv-only --no-cache 2>&1 | tee -a "$LOG_FILE"
COLLECT_EXIT=${PIPESTATUS[0]}

# Step 2: Migrate data into dashboard DB
echo "--- Migrating to dashboard DB ---" | tee -a "$LOG_FILE"
python3 -m dashboard.backend.migrate_data --json-path output/ruckus_aggregated_data.json 2>&1 | tee -a "$LOG_FILE"

echo "=== Finished at $(date) with exit code $COLLECT_EXIT ===" | tee -a "$LOG_FILE"

# Keep only last 30 days of logs
find "$LOG_DIR" -name "run_*.log" -mtime +30 -delete 2>/dev/null

exit $COLLECT_EXIT
