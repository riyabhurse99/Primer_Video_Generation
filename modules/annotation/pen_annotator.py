"""
Pen Annotation Module
=====================
Overlays real-time pen-stroke highlights on slide video clips.
Mimics an instructor drawing attention to content with a stylus.

Effects:
  - Animated underline that "draws itself" under each text element
  - Circle/bracket around key regions for emphasis
  - Style per element decided by smart heuristic (see renderer.py)

Performance: pre-computes cumulative annotated stages so only the
draw-phase frames (~10-15 per element) need Pillow rendering.
All hold/intro frames stream cached bytes. No OpenCV dependency.
"""

import math
import os
import json
import subprocess
from PIL import Image, ImageDraw
from utils.logger import get_logger
from modules.annotation.whiteboard_sketch import render_sketch, render_sketch_full

logger = get_logger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
FPS = 24

# Annotation line — same style for both underlines and circles
LINE_COLOR = (220, 30, 30, 200)          # vivid red
LINE_WIDTH = 4                           # thin, clean line
STROKE_WOBBLE = 3                        # max vertical wobble (pixels)
UNDERLINE_OFFSET = 20                    # pixels below the element bottom edge (clear of text)

# Circle padding
CIRCLE_PADDING = 14                      # pixels of padding around circled elements

# Timing
DRAW_SPEED_PX_PER_SEC = 550              # how fast the pen moves across (human pace)
INTRO_FRACTION = 0.12                    # first 12% of slide = no annotation

# Sketch area (must match whiteboard_sketch.py constants)
SKETCH_AREA_LEFT = 80
SKETCH_AREA_TOP = 650
SKETCH_AREA_W = 1760
SKETCH_AREA_H = 380


# ── Math helpers ──────────────────────────────────────────────────────────────

def _ease_in_out(t: float) -> float:
    """Smooth ease-in/out. t in [0,1] -> eased t in [0,1]."""
    return t * t * (3.0 - 2.0 * t)


def _wobble_y(x: float, y_base: float, seed: float) -> int:
    """Natural sine-wave wobble — deterministic per seed."""
    wave1 = math.sin(x * 0.025 + seed) * STROKE_WOBBLE * 0.6
    wave2 = math.sin(x * 0.06 + seed * 2.3) * STROKE_WOBBLE * 0.3
    wave3 = math.sin(x * 0.13 + seed * 0.7) * STROKE_WOBBLE * 0.1
    return int(y_base + wave1 + wave2 + wave3)


# ── Circle/bracket around element ────────────────────────────────────────────

def _draw_circle_bracket(draw, box: tuple, seed: float, progress: float = 1.0):
    """
    Draw a hand-drawn-style rounded rectangle (bracket) around a text element.
    Draws progressively based on progress (0.0 to 1.0).
    """
    x1, y1, x2, y2 = box
    pad = CIRCLE_PADDING
    bx1, by1 = x1 - pad, y1 - pad
    bx2, by2 = x2 + pad, y2 + pad
    corner_r = 16

    # Build the full path as a list of (x, y) points tracing the rounded rect
    points = []
    steps_per_side = 30

    # Top edge (left to right)
    for i in range(steps_per_side):
        t = i / steps_per_side
        px = bx1 + corner_r + t * (bx2 - bx1 - 2 * corner_r)
        py = by1 + _wobble_y(px, 0, seed) * 0.4
        points.append((int(px), int(py)))

    # Right edge (top to bottom)
    for i in range(steps_per_side):
        t = i / steps_per_side
        px = bx2 + _wobble_y(by1 + t * (by2 - by1), 0, seed + 10) * 0.4
        py = by1 + corner_r + t * (by2 - by1 - 2 * corner_r)
        points.append((int(px), int(py)))

    # Bottom edge (right to left)
    for i in range(steps_per_side):
        t = i / steps_per_side
        px = bx2 - corner_r - t * (bx2 - bx1 - 2 * corner_r)
        py = by2 + _wobble_y(px, 0, seed + 20) * 0.4
        points.append((int(px), int(py)))

    # Left edge (bottom to top)
    for i in range(steps_per_side):
        t = i / steps_per_side
        px = bx1 + _wobble_y(by2 - t * (by2 - by1), 0, seed + 30) * 0.4
        py = by2 - corner_r - t * (by2 - by1 - 2 * corner_r)
        points.append((int(px), int(py)))

    # Draw up to `progress` fraction of the total path
    n_draw = max(2, int(len(points) * progress))
    for i in range(n_draw - 1):
        draw.line(
            [points[i], points[i + 1]],
            fill=LINE_COLOR,
            width=LINE_WIDTH,
        )


# ── Underline stroke ─────────────────────────────────────────────────────────

def _draw_stroke(draw, box: tuple, seed: float, progress: float = 1.0):
    """
    Draw a thin underline stroke under `box`, from left edge to `progress` fraction.
    Same line style as circle/bracket — clean, uniform width.
    """
    x1, y1, x2, y2 = box
    y_base = y2 + UNDERLINE_OFFSET
    x_end = x1 + int((x2 - x1) * progress)

    step = 3  # pixel step between stroke sample points
    points = []
    for x in range(x1, x_end, step):
        y = _wobble_y(x, y_base, seed)
        points.append((x, y))

    # Draw connected segments — uniform thin line
    for i in range(len(points) - 1):
        draw.line([points[i], points[i + 1]], fill=LINE_COLOR, width=LINE_WIDTH)


# ── Pre-compute annotated stages ─────────────────────────────────────────────

def _build_annotated_stages(base_img: Image.Image, element_boxes: list) -> list:
    """
    Pre-render cumulative annotated images.
    element_boxes: list of {"box": (x1,y1,x2,y2), "style": "underline"|"circle"}
    """
    stages = []
    for i, elem in enumerate(element_boxes):
        if i == 0:
            prev = base_img.convert("RGBA")
        else:
            prev = stages[-1].convert("RGBA")

        overlay = Image.new("RGBA", prev.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        box = elem["box"]
        is_circle = elem.get("style") == "circle"

        if is_circle:
            _draw_circle_bracket(draw, box, seed=i * 7.3 + 1.2, progress=1.0)
        else:
            _draw_stroke(draw, box, seed=i * 7.3 + 1.2, progress=1.0)

        composited = Image.alpha_composite(prev, overlay).convert("RGB")
        stages.append(composited)

    return stages


# ── Timestamp helpers ────────────────────────────────────────────────────────

def _load_word_timestamps(audio_path: str) -> list | None:
    """Load word-level timestamps saved by ElevenLabs TTS (if available)."""
    ts_path = os.path.splitext(audio_path)[0] + ".timestamps.json"
    if not os.path.exists(ts_path):
        return None
    try:
        with open(ts_path) as f:
            return json.load(f)
    except Exception:
        return None


def _find_word_time(words: list, element_text: str) -> float | None:
    """
    Find when an element's text is first spoken in the narration.
    Matches by finding the first word from the element in the audio timestamps.
    Returns the start time in seconds, or None if no match.
    """
    if not words or not element_text:
        return None

    # Extract key words from the element text (skip common short words)
    skip = {"the", "a", "an", "and", "or", "of", "in", "to", "for", "is", "it", "on", "at", "by", "with"}
    elem_words = [w.lower().strip(".,!?:;\"'()") for w in element_text.split()]
    elem_words = [w for w in elem_words if w and len(w) > 1 and w not in skip]

    if not elem_words:
        return None

    # Try to find the first significant word from the element in the audio
    audio_words_lower = [(w["word"].lower().strip(".,!?:;\"'()"), w["start"]) for w in words]

    for ew in elem_words[:3]:  # check first 3 significant words
        for aw, start_time in audio_words_lower:
            if ew in aw or aw in ew:
                return start_time

    return None


# ── Frame schedule ────────────────────────────────────────────────────────────

def _build_schedule(total_frames: int, element_boxes: list, audio_path: str = None) -> list:
    """
    Returns a list of dicts, one per element, describing when to draw.
    If audio timestamps are available, syncs annotations to when words are spoken.
    Otherwise falls back to even spacing.
    """
    intro_frames = int(total_frames * INTRO_FRACTION)
    pause_frames = int(FPS * 0.4)  # 0.4 seconds between strokes

    # Try to load word timestamps for audio-synced timing
    word_ts = _load_word_timestamps(audio_path) if audio_path else None

    # Fallback: evenly spaced
    annotation_frames = total_frames - intro_frames
    frames_per_element = max(1, annotation_frames // max(1, len(element_boxes)))

    schedule = []
    for i, elem in enumerate(element_boxes):
        box = elem["box"]
        is_circle = elem.get("style") == "circle"
        x1, _, x2, _ = box
        stroke_width_px = x2 - x1

        if is_circle:
            draw_frame_count = max(24, int(FPS * 1.2))
        else:
            draw_frame_count = max(18, int(stroke_width_px / DRAW_SPEED_PX_PER_SEC * FPS))

        # Determine start frame — audio-synced or evenly spaced
        if word_ts and elem.get("text"):
            match_time = _find_word_time(word_ts, elem["text"])
            if match_time is not None:
                start = max(intro_frames, int(match_time * FPS))
            else:
                start = intro_frames + i * frames_per_element + pause_frames
        else:
            start = intro_frames + i * frames_per_element + pause_frames

        end_draw = min(start + draw_frame_count, total_frames)

        # Hold until next element starts (or end of highlight phase)
        if i + 1 < len(element_boxes):
            end_hold = total_frames  # will be capped by next element's start
        else:
            end_hold = total_frames

        schedule.append({
            "box": box,
            "seed": i * 7.3 + 1.2,
            "idx": i,
            "is_circle": is_circle,
            "start": start,
            "end_draw": end_draw,
            "end_hold": end_hold,
        })

    # Sort by start time (audio-synced order may differ from spatial order)
    schedule.sort(key=lambda s: s["start"])
    # Re-index after sorting
    for i, item in enumerate(schedule):
        item["idx"] = i

    return schedule


# ── Public API ────────────────────────────────────────────────────────────────

SKETCH_FRACTION = 0.30  # last 30% of slide duration for sketch animation


def make_annotated_clip(
    image_path: str,
    audio_path: str,
    output_path: str,
    duration: float,
    element_boxes: list,
):
    """
    Generate a video clip with animated pen-stroke highlights
    and optional whiteboard sketch.
    element_boxes: list of {"box": (x1,y1,x2,y2), "style": "underline"|"circle"}
    """
    sketch_data = load_sketch_data(image_path)

    if not element_boxes and not sketch_data:
        logger.info(f"No annotations for {os.path.basename(image_path)} — static clip")
        _make_static_clip(image_path, audio_path, output_path, duration)
        return

    base_img = Image.open(image_path).convert("RGB")
    W, H = base_img.size
    total_frames = max(1, int(duration * FPS))

    # Time allocation:
    #   [intro 12%] [highlight elements] [sketch animation 30%] [hold]
    # If no sketch, highlights get the full non-intro time.
    intro_end = int(total_frames * INTRO_FRACTION)

    if sketch_data and element_boxes:
        highlight_end = int(total_frames * (1.0 - SKETCH_FRACTION))
        sketch_start = highlight_end
        sketch_end = total_frames - int(FPS * 0.5)  # small hold at end
    elif sketch_data:
        highlight_end = intro_end
        sketch_start = intro_end
        sketch_end = total_frames - int(FPS * 0.5)
    else:
        highlight_end = total_frames
        sketch_start = total_frames
        sketch_end = total_frames

    # Find the lowest text element to avoid sketch overlap
    content_bottom = 0
    if element_boxes:
        content_bottom = max(elem["box"][3] for elem in element_boxes)  # max y2

    # Build schedule first (may reorder elements by audio timing)
    schedule = _build_schedule(highlight_end, element_boxes, audio_path=audio_path) if element_boxes else []

    # Pre-render cumulative annotated stages in SCHEDULE order (not spatial order)
    # This ensures stage[i] matches schedule[i]
    schedule_ordered_boxes = [{"box": s["box"], "style": "circle" if s["is_circle"] else "underline"} for s in schedule]
    stages = _build_annotated_stages(base_img, schedule_ordered_boxes) if schedule else []

    # Pre-render the final frame (all highlights + full sketch) for hold phase
    if stages:
        final_highlighted = stages[-1]
    else:
        final_highlighted = base_img

    if sketch_data:
        final_with_sketch = final_highlighted.convert("RGBA")
        sketch_overlay = Image.new("RGBA", final_with_sketch.size, (0, 0, 0, 0))
        sketch_draw = ImageDraw.Draw(sketch_overlay)
        render_sketch_full(sketch_draw, sketch_data, content_bottom)
        final_with_sketch = Image.alpha_composite(final_with_sketch, sketch_overlay).convert("RGB")
        final_bytes = final_with_sketch.tobytes()
    else:
        final_bytes = final_highlighted.tobytes()

    # Cache raw bytes for static phases
    base_bytes = base_img.tobytes()
    stage_bytes = [s.tobytes() for s in stages]

    logger.info(
        f"Pen annotator: {len(element_boxes)} highlights, "
        f"sketch={'yes' if sketch_data else 'no'}, "
        f"{total_frames} frames @ {FPS}fps, {duration:.1f}s"
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24",
        "-r", str(FPS),
        "-i", "pipe:0",
        "-i", audio_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        for frame_idx in range(total_frames):
            # Phase 1: Intro — plain slide
            if frame_idx < intro_end:
                proc.stdin.write(base_bytes)
                continue

            # Phase 4: Sketch animation
            if frame_idx >= sketch_start and sketch_data:
                if frame_idx >= sketch_end:
                    # Hold final frame
                    proc.stdin.write(final_bytes)
                    continue

                # Animate the sketch progressively
                sketch_progress = (frame_idx - sketch_start) / max(1, sketch_end - sketch_start)
                sketch_progress = _ease_in_out(min(sketch_progress, 1.0))

                if stages:
                    prev = stages[-1].convert("RGBA")
                else:
                    prev = base_img.convert("RGBA")

                overlay = Image.new("RGBA", prev.size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                render_sketch(draw, sketch_data, sketch_progress, content_bottom)

                frame = Image.alpha_composite(prev, overlay).convert("RGB")
                proc.stdin.write(frame.tobytes())
                continue

            # Phase 2 & 3: Highlight animation
            active = None
            last_completed = -1
            for item in schedule:
                if frame_idx >= item["end_draw"]:
                    last_completed = item["idx"]
                elif item["start"] <= frame_idx < item["end_draw"]:
                    active = item

            if active is not None:
                # Drawing animation — line only, no cursor
                raw_progress = (frame_idx - active["start"]) / max(1, active["end_draw"] - active["start"])
                raw_progress = min(raw_progress, 1.0)
                progress = _ease_in_out(raw_progress)

                if active["idx"] > 0:
                    prev = stages[active["idx"] - 1].convert("RGBA")
                else:
                    prev = base_img.convert("RGBA")

                overlay = Image.new("RGBA", prev.size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)

                if active["is_circle"]:
                    _draw_circle_bracket(draw, active["box"], active["seed"], progress)
                else:
                    _draw_stroke(draw, active["box"], active["seed"], progress)

                frame = Image.alpha_composite(prev, overlay).convert("RGB")
                proc.stdin.write(frame.tobytes())

            elif last_completed >= 0:
                # Hold — cached bytes
                proc.stdin.write(stage_bytes[last_completed])
            else:
                proc.stdin.write(base_bytes)

    except BrokenPipeError:
        logger.warning("FFmpeg pipe closed early")
    finally:
        proc.stdin.close()
        proc.wait()

    if proc.returncode != 0:
        stderr = proc.stderr.read().decode(errors="replace")
        logger.error(f"Pen annotator FFmpeg failed: {stderr[-500:]}")
        logger.info("Falling back to static clip")
        _make_static_clip(image_path, audio_path, output_path, duration)
    else:
        logger.info(f"Annotated clip ready: {output_path}")


def _make_static_clip(image_path: str, audio_path: str, output_path: str, duration: float):
    """Fallback: plain static image + audio (no pen strokes)."""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-t", str(duration), "-shortest",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Static clip fallback failed: {result.stderr}")


# ── Utility: load boxes from JSON ────────────────────────────────────────────

def load_element_boxes(image_path: str) -> list:
    """
    Look for a .boxes.json file alongside the slide PNG.
    Returns list of dicts: [{"box": (x1,y1,x2,y2), "style": "underline"|"circle"}, ...]
    Backward-compatible: old format [[x1,y1,x2,y2], ...] treated as all underlines.
    """
    boxes_path = os.path.splitext(image_path)[0] + ".boxes.json"
    if not os.path.exists(boxes_path):
        logger.debug(f"No boxes file found at {boxes_path}")
        return []
    try:
        with open(boxes_path) as f:
            raw = json.load(f)

        # Normalize: support both old format (list of lists) and new format (list of dicts)
        result = []
        for item in raw:
            if isinstance(item, dict) and "box" in item:
                entry = {"box": tuple(item["box"]), "style": item.get("style", "underline")}
                if item.get("text"):
                    entry["text"] = item["text"]
                result.append(entry)
            elif isinstance(item, (list, tuple)) and len(item) >= 4:
                result.append({"box": tuple(item[:4]), "style": "underline"})

        logger.info(f"Loaded {len(result)} element boxes from {boxes_path}")
        return result
    except Exception as e:
        logger.warning(f"Failed to load boxes from {boxes_path}: {e}")
        return []


def load_sketch_data(image_path: str) -> dict:
    """
    Look for a .sketch.json file alongside the slide PNG.
    Returns the sketch dict or None if not found.
    """
    sketch_path = os.path.splitext(image_path)[0] + ".sketch.json"
    if not os.path.exists(sketch_path):
        return None
    try:
        with open(sketch_path) as f:
            data = json.load(f)
        if data and data.get("elements"):
            logger.info(f"Loaded sketch: {data.get('sketch_title', '?')} "
                        f"({len(data['elements'])} elements)")
            return data
        return None
    except Exception as e:
        logger.warning(f"Failed to load sketch from {sketch_path}: {e}")
        return None
