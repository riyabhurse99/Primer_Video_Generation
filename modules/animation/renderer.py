"""
Animation Renderer
==================
Takes an animation spec (from detector.py), writes it to a temp JSON,
invokes scenes.py under Python 3.11 (where Manim is installed),
and returns the path to the rendered MP4.
"""

import json
import os
import shutil
import subprocess
from utils.logger import get_logger

logger = get_logger(__name__)

# Path to scenes.py (same directory as this file)
_SCENES_SCRIPT = os.path.join(os.path.dirname(__file__), "scenes.py")

# Python 3.11 binary (Homebrew) — Manim is installed here
_PYTHON_311 = "/opt/homebrew/bin/python3.11"


def _find_python_with_manim() -> str:
    """Find a Python binary that has Manim installed."""
    candidates = [
        _PYTHON_311,
        "/opt/homebrew/bin/python3.13",
        "/opt/homebrew/bin/python3.10",
        "python3",
    ]
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "-c", "import manim; print('ok')"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def render_animation(spec_data: dict, output_path: str):
    """
    Render an animation from a spec dict.

    Args:
        spec_data: Animation spec from detector.py
                   {"animation_type": "...", "title": "...", "spec": {...}}
        output_path: Where to save the MP4

    Returns:
        Path to the rendered MP4, or None if rendering failed.
    """
    python_bin = _find_python_with_manim()
    if not python_bin:
        logger.error("No Python with Manim found — cannot render animation")
        return None

    # Use absolute paths so scenes.py can find the files regardless of cwd
    output_path = os.path.abspath(output_path)
    spec_json_path = output_path + ".spec.json"
    try:
        with open(spec_json_path, "w") as f:
            json.dump(spec_data, f)
    except Exception as e:
        logger.error(f"Failed to write animation spec: {e}")
        return None

    anim_type = spec_data.get("animation_type", "?")
    title = spec_data.get("title", "?")
    logger.info(f"  Rendering animation: '{title}' (type={anim_type}) via {python_bin}")

    try:
        result = subprocess.run(
            [python_bin, _SCENES_SCRIPT, spec_json_path, output_path],
            capture_output=True,
            text=True,
            timeout=120,  # 2 minutes max for Manim render
        )

        if result.returncode != 0:
            logger.error(f"  Manim render failed (exit {result.returncode})")
            logger.error(f"  stderr: {result.stderr[-500:]}")
            return None

        if not os.path.exists(output_path):
            logger.error(f"  Manim render produced no output at {output_path}")
            return None

        # Get file size for logging
        size_kb = os.path.getsize(output_path) / 1024
        logger.info(f"  Animation rendered: {output_path} ({size_kb:.0f} KB)")

        return output_path

    except subprocess.TimeoutExpired:
        logger.error("  Manim render timed out (>120s)")
        return None
    except Exception as e:
        logger.error(f"  Animation render error: {e}")
        return None
    finally:
        # Clean up temp spec file
        if os.path.exists(spec_json_path):
            os.remove(spec_json_path)
        # Clean up Manim media dir
        media_dir = os.path.join(os.path.dirname(output_path), "manim_media")
        if os.path.isdir(media_dir):
            shutil.rmtree(media_dir, ignore_errors=True)
