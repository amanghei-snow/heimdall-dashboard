"""API routes for triggering and monitoring Heimdall scans."""
import subprocess
import os
import json
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["scan"])

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
RUN_SCRIPT = PROJECT_DIR / "run.sh"
LOG_DIR = Path.home() / "Downloads" / "ruckus_aggregator_output" / "logs"
LOCK_FILE = PROJECT_DIR / "data" / ".scan.lock"
PROGRESS_FILE = PROJECT_DIR / "data" / ".scan.progress"

STAGES = ["ranking", "collecting", "targeted", "reporting", "done"]


@router.get("/scan/status")
def scan_status():
    """Return last scan info and whether a scan is currently running."""
    running = LOCK_FILE.exists()

    # Find last scan timestamp from the aggregated JSON
    json_path = PROJECT_DIR / "output" / "ruckus_aggregated_data.json"
    last_scan = None
    last_count = 0
    if json_path.exists():
        last_scan = datetime.fromtimestamp(
            json_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        try:
            with open(json_path) as f:
                data = json.load(f)
            last_count = len(data) if isinstance(data, list) else 0
        except Exception:
            pass

    # Days since last scan
    days_stale = None
    if last_scan:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(last_scan)
        days_stale = round(delta.total_seconds() / 86400, 1)

    # Find latest log
    last_log = None
    if LOG_DIR.exists():
        logs = sorted(LOG_DIR.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            last_log = logs[0].read_text()[-2000:]

    # Read live progress if running
    progress = None
    if running and PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f:
                progress = json.load(f)
            stage = progress.get("stage", "")
            stage_idx = STAGES.index(stage) if stage in STAGES else 0
            stage_pct = progress.get("pct", 0)
            # Overall: each stage is a fraction of 100%
            weights = {"ranking": 5, "collecting": 70, "targeted": 15, "reporting": 10, "done": 0}
            base = sum(weights[s] for s in STAGES[:stage_idx])
            progress["overall_pct"] = min(100, base + int(weights.get(stage, 0) * stage_pct / 100))
        except Exception:
            pass

    return {
        "running": running,
        "last_scan": last_scan,
        "last_count": last_count,
        "days_stale": days_stale,
        "stale": days_stale is not None and days_stale > 4,
        "progress": progress,
    }


@router.post("/scan/start")
def start_scan():
    """Kick off a full Heimdall scan in the background."""
    if LOCK_FILE.exists():
        return {"status": "already_running", "message": "A scan is already in progress."}

    if not RUN_SCRIPT.exists():
        return {"status": "error", "message": "run.sh not found."}

    # Create lock
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(datetime.now(timezone.utc).isoformat())

    # Run in background — the wrapper script handles logging
    env = os.environ.copy()
    subprocess.Popen(
        ["bash", "-c", f'"{RUN_SCRIPT}" ; rm -f "{LOCK_FILE}"'],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    return {
        "status": "started",
        "message": "Full scan started. Check status for progress.",
    }
