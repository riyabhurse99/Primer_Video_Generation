"""
Metrics collection for the Scaler Primer pipeline.
Writes one JSON record per video generation to metrics.json.
Used by the team to evaluate pipeline performance — not shown to students.
"""

import json
import os
import time
from datetime import datetime
from utils.logger import get_logger

logger = get_logger(__name__)

METRICS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metrics.json")


def _load_all() -> list:
    """Read all existing metrics records from disk."""
    if not os.path.exists(METRICS_FILE):
        return []
    try:
        with open(METRICS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_all(records: list):
    """Write all metrics records to disk."""
    with open(METRICS_FILE, "w") as f:
        json.dump(records, f, indent=2)


def record(
    topic: str,
    status: str,                        # "success" or "failed"
    total_time_seconds: float,
    slide_generation_seconds: float = 0,
    tts_generation_seconds: float = 0,
    video_assembly_seconds: float = 0,
    storage_seconds: float = 0,
    groot_api_calls: int = 0,
    slides_generated: int = 0,
    fallback_slides: int = 0,
    tts_provider: str = "elevenlabs",
    video_duration_seconds: float = 0,
    video_size_mb: float = 0,
    evals: dict = None,
    error: str = None,
):
    """Save one metrics record for a video generation run."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "topic": topic,
        "status": status,
        "total_time_seconds": round(total_time_seconds, 2),
        "steps": {
            "slide_generation_seconds": round(slide_generation_seconds, 2),
            "tts_generation_seconds": round(tts_generation_seconds, 2),
            "video_assembly_seconds": round(video_assembly_seconds, 2),
            "storage_seconds": round(storage_seconds, 2),
        },
        "groot_api_calls": groot_api_calls,
        "slides_generated": slides_generated,
        "fallback_slides": fallback_slides,
        "tts_provider": tts_provider,
        "video_duration_seconds": round(video_duration_seconds, 2),
        "video_size_mb": round(video_size_mb, 3),
        "evals": evals,
        "error": error,
    }

    records = _load_all()
    records.append(entry)
    _save_all(records)
    logger.info(f"Metrics saved — topic='{topic}' status={status} total={total_time_seconds:.1f}s")


def load_all() -> list:
    """Return all metrics records. Used by the dashboard."""
    return _load_all()


class StepTimer:
    """Simple context manager to measure how long a step takes."""

    def __init__(self):
        self.elapsed = 0.0

    def __enter__(self):
        self._start = time.time()
        return self

    def __exit__(self, *args):
        self.elapsed = time.time() - self._start
