"""API routes for RAP — Heimdall Regression & Performance tracking."""
import subprocess
import re
from typing import List, Optional
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from dashboard.backend.database import get_db
from dashboard.backend.models.instance import RapSession, RapEvent

router = APIRouter(tags=["rap"])


class RapEventIn(BaseModel):
    event_type: str  # 'click', 'render', 'tab'
    timestamp_ms: int
    render_ms: Optional[int] = None
    tab: Optional[str] = None


class RapSessionIn(BaseModel):
    session_start: datetime
    duration_ms: int
    clicks: int
    render_count: int
    avg_render_ms: int
    max_render_ms: int
    tabs_visited: str
    user_agent: str = ""
    events: List[RapEventIn] = []


@router.post("/rap/sessions")
def save_session(data: RapSessionIn, db: Session = Depends(get_db)):
    """Save or update a RAP session with all its events."""
    session = RapSession(
        session_start=data.session_start,
        duration_ms=data.duration_ms,
        clicks=data.clicks,
        render_count=data.render_count,
        avg_render_ms=data.avg_render_ms,
        max_render_ms=data.max_render_ms,
        tabs_visited=data.tabs_visited,
        user_agent=data.user_agent,
    )
    db.add(session)
    db.flush()

    for ev in data.events:
        db.add(RapEvent(
            session_id=session.id,
            event_type=ev.event_type,
            timestamp_ms=ev.timestamp_ms,
            render_ms=ev.render_ms,
            tab=ev.tab,
        ))

    db.commit()
    return {"id": session.id, "status": "ok"}


@router.get("/rap/sessions")
def list_sessions(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    """List RAP sessions, most recent first."""
    total = db.query(RapSession).count()
    sessions = (
        db.query(RapSession)
        .order_by(desc(RapSession.session_start))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "data": [
            {
                "id": s.id,
                "session_start": s.session_start.isoformat(),
                "duration_ms": s.duration_ms,
                "clicks": s.clicks,
                "render_count": s.render_count,
                "avg_render_ms": s.avg_render_ms,
                "max_render_ms": s.max_render_ms,
                "tabs_visited": s.tabs_visited,
            }
            for s in sessions
        ],
    }


@router.get("/rap/sessions/{session_id}/events")
def get_session_events(session_id: int, db: Session = Depends(get_db)):
    """Get all events for a specific RAP session (for charts)."""
    events = (
        db.query(RapEvent)
        .filter(RapEvent.session_id == session_id)
        .order_by(RapEvent.timestamp_ms)
        .all()
    )
    return {
        "events": [
            {
                "event_type": e.event_type,
                "timestamp_ms": e.timestamp_ms,
                "render_ms": e.render_ms,
                "tab": e.tab,
            }
            for e in events
        ]
    }


def _find_npx() -> Optional[str]:
    """Locate npx binary."""
    import shutil
    import os
    npx = shutil.which("npx")
    if npx:
        return npx
    nvm_dir = os.environ.get("NVM_DIR", os.path.expanduser("~/.nvm"))
    node_dir = os.path.join(nvm_dir, "versions/node")
    if os.path.isdir(node_dir):
        for ver in sorted(os.listdir(node_dir), reverse=True):
            p = os.path.join(node_dir, ver, "bin", "npx")
            if os.path.isfile(p):
                return p
    return None


@router.get("/rap/run-tests")
def run_playwright_tests_stream():
    """Stream Playwright test execution via SSE — one event per test result."""
    import json as _json
    import os
    from fastapi.responses import StreamingResponse

    frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    test_spec = frontend_dir / "tests" / "dashboard-regression.spec.js"

    if not test_spec.exists():
        def _err():
            yield f"data: {_json.dumps({'type': 'error', 'message': 'Test file not found'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    npx = _find_npx()
    if not npx:
        def _err():
            yield f"data: {_json.dumps({'type': 'error', 'message': 'npx not found'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    # Count total tests by grepping test titles from spec file
    spec_text = test_spec.read_text()
    total_tests = len(re.findall(r"""test\(\s*['"]""", spec_text))

    def _stream():
        import time as _time
        t0 = _time.time()
        passed = 0
        failed = 0
        flaky = 0
        current = 0
        output_lines = []

        yield f"data: {_json.dumps({'type': 'start', 'total': total_tests})}\n\n"

        proc = subprocess.Popen(
            [npx, "playwright", "test", "--reporter=list"],
            cwd=str(frontend_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "CI": "1"},
            bufsize=1,
        )

        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            if not line:
                continue
            output_lines.append(line)

            # Detect individual test pass/fail from list reporter output
            # Format: "  ✓  1 [chromium] › test.spec.js:10:5 › suite › test name (2.3s)"
            #    or:  "  ✘  2 [chromium] › test.spec.js:20:5 › suite › test name (1.1s)"
            #    or:  "  -  3 [chromium] › test.spec.js:30:5 › suite › test name (0.5s)"  (skipped)
            test_pass = re.search(r"[✓✔].*›\s*(.+?)(?:\s*\([\d.]+[sm]?\))?$", line)
            test_fail = re.search(r"[✘✗×].*›\s*(.+?)(?:\s*\([\d.]+[sm]?\))?$", line)

            if test_pass:
                current += 1
                passed += 1
                name = test_pass.group(1).strip()
                yield f"data: {_json.dumps({'type': 'test', 'status': 'passed', 'name': name, 'current': current, 'total': total_tests, 'passed': passed, 'failed': failed})}\n\n"
            elif test_fail:
                current += 1
                failed += 1
                name = test_fail.group(1).strip()
                yield f"data: {_json.dumps({'type': 'test', 'status': 'failed', 'name': name, 'current': current, 'total': total_tests, 'passed': passed, 'failed': failed})}\n\n"

        proc.wait()
        elapsed = round(_time.time() - t0, 1)
        duration = f"{elapsed}s" if elapsed < 60 else f"{elapsed/60:.1f}m"

        # Parse final summary from output for accurate counts
        full_output = "\n".join(output_lines)
        pm = re.findall(r"(\d+)\s+passed", full_output)
        fm = re.findall(r"(\d+)\s+failed", full_output)
        flm = re.findall(r"(\d+)\s+flaky", full_output)
        if pm:
            passed = int(pm[-1])
        if fm:
            failed = int(fm[-1])
        if flm:
            flaky = int(flm[-1])

        yield f"data: {_json.dumps({'type': 'done', 'passed': passed, 'failed': failed, 'flaky': flaky, 'duration': duration, 'date': datetime.utcnow().isoformat(), 'output': full_output[-3000:]})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")
