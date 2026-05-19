"""
Run Logger
==========
Append-only structured event log for a single generation run.
Process-level singleton — call initialize(path) once at worker startup.
All log calls are silent no-ops when no path is set (safe to import anywhere).
"""

import json
import threading
import datetime
from typing import Optional

_log_path: Optional[str] = None
_lock = threading.Lock()


def initialize(path: str):
    """Call once at worker process startup to activate logging."""
    global _log_path
    _log_path = path


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def _write(event: dict):
    if not _log_path:
        return
    event.setdefault("ts", _ts())
    try:
        with _lock:
            with open(_log_path, "a") as f:
                f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def log_step(step: str, detail: str = ""):
    _write({"type": "step", "step": step, "detail": detail})


def log_api_call(
    api: str,
    endpoint: str = "",
    input_summary: str = "",
    output_summary: str = "",
    duration_ms: int = 0,
    status: str = "ok",
    **extra,
):
    event = {
        "type": "api_call",
        "api": api,
        "endpoint": endpoint,
        "input_summary": (input_summary or "")[:300],
        "output_summary": (output_summary or "")[:700],
        "duration_ms": int(duration_ms),
        "status": status,
    }
    event.update(extra)
    _write(event)


def log_eval(eval_type: str, **extra):
    event = {"type": "eval", "eval_type": eval_type}
    event.update(extra)
    _write(event)


def log_narration_improve(slide_num: int, original: str, improved: str, reason: str):
    """Log the before/after text of a narration rewrite so it can be evaluated."""
    _write({
        "type": "narration_improve",
        "slide": slide_num,
        "original": original or "",
        "improved": improved or "",
        "reason": reason or "",
    })


def log_error(message: str, context: str = ""):
    _write({"type": "error", "message": str(message)[:500], "context": context})


def read_events(path: str) -> list:
    """Read all events from a JSONL log file. Safe to call from any process."""
    events = []
    if not path:
        return events
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
    except (FileNotFoundError, OSError):
        pass
    return events
