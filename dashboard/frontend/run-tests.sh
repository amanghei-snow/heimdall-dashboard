#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Heimdall — Combined Playwright Regression Test Runner
#
# Runs the full test suite twice:
#   1. Headless (CI-compatible, fast)
#   2. Headed  (visual verification)
#
# Usage:
#   ./run-tests.sh              # Run both modes
#   ./run-tests.sh headless     # Headless only
#   ./run-tests.sh headed       # Headed only
#
# Prerequisites:
#   - Backend running at http://localhost:8000
#   - Frontend dev server running at http://localhost:5173
# ─────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

# Load node if using nvm
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

MODE="${1:-both}"
DIVIDER="════════════════════════════════════════════════════════"

echo ""
echo "$DIVIDER"
echo "  Heimdall — Playwright Regression Suite"
echo "$DIVIDER"
echo ""

# Check servers are running
if ! curl -s --max-time 3 http://localhost:5173 > /dev/null 2>&1; then
  echo "❌ Frontend not running on :5173 — start with: cd dashboard/frontend && npm run dev"
  exit 1
fi
if ! curl -s --max-time 3 http://localhost:8000/api/summary > /dev/null 2>&1; then
  echo "❌ Backend not running on :8000"
  exit 1
fi
echo "✓ Frontend (:5173) and Backend (:8000) are running"
echo ""

HEADLESS_RESULT=""
HEADED_RESULT=""

# ─── Run 1: Headless ────────────────────────────────────────
if [ "$MODE" = "both" ] || [ "$MODE" = "headless" ]; then
  echo "▶ Run 1: Headless mode"
  echo "─────────────────────────────────────────────────────"
  HEADLESS_RESULT=$(npx playwright test --reporter=list 2>&1 | tee /dev/stderr | tail -3)
  echo ""
fi

# ─── Run 2: Headed ──────────────────────────────────────────
if [ "$MODE" = "both" ] || [ "$MODE" = "headed" ]; then
  echo "▶ Run 2: Headed mode (browser visible)"
  echo "─────────────────────────────────────────────────────"
  HEADED_RESULT=$(npx playwright test --headed --reporter=list 2>&1 | tee /dev/stderr | tail -3)
  echo ""
fi

# ─── Summary ────────────────────────────────────────────────
echo ""
echo "$DIVIDER"
echo "  SUMMARY"
echo "$DIVIDER"
if [ -n "$HEADLESS_RESULT" ]; then
  echo "  Headless: $HEADLESS_RESULT"
fi
if [ -n "$HEADED_RESULT" ]; then
  echo "  Headed:   $HEADED_RESULT"
fi
echo "$DIVIDER"
echo ""
echo "Paste the summary lines into RAP > Run Tests to log results."
