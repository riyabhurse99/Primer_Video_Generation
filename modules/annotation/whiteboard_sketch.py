"""
Whiteboard Sketch Generator
============================
Uses Claude to analyze slide narration and generate context-aware
whiteboard sketches — flow diagrams, concept maps, labeled boxes
with arrows — that get drawn as animated pen strokes on the slide.

Flow:
  1. Send narration + slide content to Claude
  2. Claude returns structured JSON drawing instructions
  3. The pen annotator renders these as animated strokes

Example: if narration says "Data flows from client to server",
Claude might produce:
  [Client] ───→ [Server] ───→ [Database]

This gets drawn in real-time on the slide's blank area.
"""

import json
import re
import math
from PIL import ImageDraw, ImageFont
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Layout constants ──────────────────────────────────────────────────────────
SKETCH_AREA_TOP = 650      # y-pixel where sketch area starts (bottom 40% of 1080)
SKETCH_AREA_BOTTOM = 1030
SKETCH_AREA_LEFT = 80
SKETCH_AREA_RIGHT = 1840
SKETCH_AREA_W = SKETCH_AREA_RIGHT - SKETCH_AREA_LEFT
SKETCH_AREA_H = SKETCH_AREA_BOTTOM - SKETCH_AREA_TOP

# Drawing style — Scaler 3.0 palette
BOX_COLOR = (1, 24, 69, 255)            # #011845 navy
BOX_FILL = (233, 241, 255, 100)         # #E9F1FF ice, semi-transparent
ARROW_COLOR = (0, 85, 255, 255)         # #0055FF brand blue
LABEL_COLOR = (11, 21, 41, 255)         # #0B1529 primary text
SKETCH_STROKE_WIDTH = 3
LABEL_FONT_SIZE = 22
BOX_PADDING = 16
BOX_CORNER_RADIUS = 0                   # sharp corners per rebrand
ARROW_HEAD_SIZE = 14


# ── Claude prompt ─────────────────────────────────────────────────────────────

SKETCH_PROMPT = """You are analyzing a slide narration to decide if a simple whiteboard sketch would help the student understand the concept being explained.

NARRATION:
\"\"\"{narration}\"\"\"

SLIDE TOPIC: "{topic}"

YOUR TASK: Decide whether a sketch would genuinely help, and if so, produce drawing instructions.

WHEN TO SKETCH:
- Processes or flows (data flow, request/response, pipelines)
- Relationships between components (client-server, layers, hierarchies)
- Comparisons (A vs B, before/after)
- Simple data structures (linked list, tree, stack)
- Cause and effect chains

WHEN NOT TO SKETCH (return null):
- The narration is just definitions or introductions
- The content is abstract with no visual structure
- A sketch would be forced or unhelpful

AVAILABLE DRAWING PRIMITIVES:
- "box": a labeled rectangle {{ "type": "box", "label": "text", "id": "unique_id" }}
- "arrow": connects two boxes {{ "type": "arrow", "from": "id1", "to": "id2", "label": "optional text" }}
- "circle": a labeled circle {{ "type": "circle", "label": "text", "id": "unique_id" }}

RULES:
1. Maximum 6 elements total (boxes + circles + arrows combined)
2. Keep labels SHORT (1-3 words max)
3. Use meaningful IDs that match the labels
4. Arrows must reference existing box/circle IDs
5. Think about what visual would actually help a student understand THIS narration

Respond with ONLY valid JSON. Either null (no sketch needed) or:
{{
  "sketch_title": "short title for the sketch",
  "elements": [
    {{ "type": "box", "label": "Client", "id": "client" }},
    {{ "type": "box", "label": "Server", "id": "server" }},
    {{ "type": "arrow", "from": "client", "to": "server", "label": "request" }}
  ]
}}"""


# ── Generate sketch instructions via Claude ───────────────────────────────────

def generate_sketch_instructions(topic: str, narration: str, call_llm) -> dict:
    """
    Ask Claude to analyze the narration and produce sketch instructions.
    Returns the sketch dict or None if no sketch is appropriate.
    """
    if not call_llm or not narration.strip():
        return None

    prompt = SKETCH_PROMPT.format(narration=narration.strip(), topic=topic)

    try:
        response = call_llm(prompt)
        clean = response.strip()

        # Handle "null" response
        if clean.lower() in ("null", "none", ""):
            logger.info(f"  Sketch: Claude says no sketch needed")
            return None

        # Strip markdown fences
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```\s*$", "", clean).strip()

        data = json.loads(clean)
        if data is None:
            return None

        elements = data.get("elements", [])
        if not elements:
            return None

        logger.info(f"  Sketch: {data.get('sketch_title', '?')} — {len(elements)} elements")
        return data

    except Exception as e:
        logger.warning(f"  Sketch generation failed: {e}")
        return None


# ── Layout engine — position elements automatically ──────────────────────────

def _layout_elements(elements: list, content_bottom: int = 0) -> dict:
    """
    Auto-layout boxes/circles/arrows within the sketch area.
    If content_bottom is provided, sketch area starts below it (with padding)
    to avoid overlapping slide text.
    Returns a dict mapping element IDs to (cx, cy, w, h) positions.
    """
    # Dynamic sketch area: start below content or at default
    area_top = max(SKETCH_AREA_TOP, content_bottom + 30) if content_bottom > 0 else SKETCH_AREA_TOP
    area_h = SKETCH_AREA_BOTTOM - area_top

    # Not enough space for a sketch (need at least 120px)
    if area_h < 120:
        logger.info(f"  Sketch skipped — not enough space (content ends at y={content_bottom})")
        return {}

    # Separate nodes (boxes/circles) from arrows
    nodes = [e for e in elements if e["type"] in ("box", "circle")]
    arrows = [e for e in elements if e["type"] == "arrow"]

    if not nodes:
        return {}

    positions = {}
    n = len(nodes)

    if n == 1:
        cx = SKETCH_AREA_LEFT + SKETCH_AREA_W // 2
        cy = area_top + area_h // 2
        positions[nodes[0]["id"]] = (cx, cy, 160, 60)
    elif n == 2:
        gap = SKETCH_AREA_W // 3
        cy = area_top + area_h // 2
        positions[nodes[0]["id"]] = (SKETCH_AREA_LEFT + gap, cy, 160, 60)
        positions[nodes[1]["id"]] = (SKETCH_AREA_LEFT + gap * 2, cy, 160, 60)
    elif n <= 4:
        gap = SKETCH_AREA_W // (n + 1)
        cy = area_top + area_h // 2
        for i, node in enumerate(nodes):
            cx = SKETCH_AREA_LEFT + gap * (i + 1)
            positions[node["id"]] = (cx, cy, 140, 55)
    else:
        top_row = nodes[:n // 2 + n % 2]
        bot_row = nodes[n // 2 + n % 2:]
        cy_top = area_top + area_h // 3
        cy_bot = area_top + area_h * 2 // 3

        for i, node in enumerate(top_row):
            gap = SKETCH_AREA_W // (len(top_row) + 1)
            cx = SKETCH_AREA_LEFT + gap * (i + 1)
            positions[node["id"]] = (cx, cy_top, 130, 50)

        for i, node in enumerate(bot_row):
            gap = SKETCH_AREA_W // (len(bot_row) + 1)
            cx = SKETCH_AREA_LEFT + gap * (i + 1)
            positions[node["id"]] = (cx, cy_bot, 130, 50)

    return positions


# ── Sketch rendering primitives ──────────────────────────────────────────────

def _load_sketch_font(size: int, bold: bool = False):
    """Load font for sketch labels."""
    import os
    candidates = (
        ["/System/Library/Fonts/Helvetica.ttc",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["/System/Library/Fonts/Helvetica.ttc",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except (IOError, OSError):
                continue
    return ImageFont.load_default()


def _wobble(val: float, seed: float, amp: float = 2.0) -> int:
    """Add hand-drawn wobble to a coordinate."""
    return int(val + math.sin(val * 0.05 + seed) * amp)


def _draw_sketch_box(draw, cx, cy, w, h, label, seed, progress=1.0):
    """Draw a hand-drawn rectangle with label, progressively."""
    x1 = cx - w // 2
    y1 = cy - h // 2
    x2 = cx + w // 2
    y2 = cy + h // 2

    # Fill
    if progress >= 0.3:
        draw.rectangle([(x1 + 2, y1 + 2), (x2 - 2, y2 - 2)], fill=BOX_FILL)

    # Build perimeter points with wobble
    sides = []
    steps = 20
    # Top
    for i in range(steps):
        t = i / steps
        px = x1 + t * (x2 - x1)
        py = _wobble(y1, seed + px)
        sides.append((int(px), py))
    # Right
    for i in range(steps):
        t = i / steps
        px = _wobble(x2, seed + 100 + y1 + t * (y2 - y1))
        py = y1 + t * (y2 - y1)
        sides.append((px, int(py)))
    # Bottom (right to left)
    for i in range(steps):
        t = i / steps
        px = x2 - t * (x2 - x1)
        py = _wobble(y2, seed + 200 + px)
        sides.append((int(px), py))
    # Left (bottom to top)
    for i in range(steps):
        t = i / steps
        px = _wobble(x1, seed + 300 + y2 - t * (y2 - y1))
        py = y2 - t * (y2 - y1)
        sides.append((px, int(py)))

    # Draw up to progress
    n_draw = max(2, int(len(sides) * progress))
    for i in range(n_draw - 1):
        draw.line([sides[i], sides[i + 1]], fill=BOX_COLOR, width=SKETCH_STROKE_WIDTH)

    # Label (only after box is mostly drawn)
    if progress > 0.6 and label:
        font = _load_sketch_font(LABEL_FONT_SIZE, bold=True)
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            tw = len(label) * 10
            th = LABEL_FONT_SIZE
        tx = cx - tw // 2
        ty = cy - th // 2
        draw.text((tx, ty), label, fill=LABEL_COLOR, font=font)


def _draw_sketch_circle(draw, cx, cy, w, h, label, seed, progress=1.0):
    """Draw a hand-drawn circle/ellipse with label, progressively."""
    rx = w // 2
    ry = h // 2

    # Build ellipse points with wobble
    points = []
    n_pts = 60
    for i in range(n_pts):
        angle = 2 * math.pi * i / n_pts
        px = cx + int(rx * math.cos(angle)) + int(math.sin(angle * 3 + seed) * 2)
        py = cy + int(ry * math.sin(angle)) + int(math.cos(angle * 3 + seed) * 2)
        points.append((px, py))

    n_draw = max(2, int(len(points) * progress))
    for i in range(n_draw - 1):
        draw.line([points[i], points[i + 1]], fill=BOX_COLOR, width=SKETCH_STROKE_WIDTH)

    # Close the circle
    if progress >= 0.95:
        draw.line([points[-1], points[0]], fill=BOX_COLOR, width=SKETCH_STROKE_WIDTH)

    # Label
    if progress > 0.6 and label:
        font = _load_sketch_font(LABEL_FONT_SIZE, bold=True)
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            tw = len(label) * 10
            th = LABEL_FONT_SIZE
        draw.text((cx - tw // 2, cy - th // 2), label, fill=LABEL_COLOR, font=font)


def _draw_sketch_arrow(draw, from_pos, to_pos, label, seed, progress=1.0):
    """Draw a hand-drawn arrow between two node centers, progressively."""
    fx, fy = from_pos
    tx, ty = to_pos

    # Shorten arrow to stop at box edges (not center)
    dx = tx - fx
    dy = ty - fy
    dist = max(1, math.sqrt(dx * dx + dy * dy))
    ux, uy = dx / dist, dy / dist

    # Start/end offsets (box half-width + padding)
    offset = 90
    sx = fx + int(ux * offset)
    sy = fy + int(uy * offset)
    ex = tx - int(ux * offset)
    ey = ty - int(uy * offset)

    # Arrow shaft with wobble
    steps = 30
    shaft_points = []
    for i in range(steps + 1):
        t = i / steps
        px = sx + t * (ex - sx)
        py = sy + t * (ey - sy)
        # Perpendicular wobble
        perp_x = -uy
        perp_y = ux
        wobble_amt = math.sin(t * math.pi * 3 + seed) * 2
        px += perp_x * wobble_amt
        py += perp_y * wobble_amt
        shaft_points.append((int(px), int(py)))

    n_draw = max(2, int(len(shaft_points) * progress))
    for i in range(n_draw - 1):
        draw.line([shaft_points[i], shaft_points[i + 1]], fill=ARROW_COLOR, width=SKETCH_STROKE_WIDTH)

    # Arrowhead (draw when shaft is almost complete)
    if progress > 0.85:
        angle = math.atan2(ey - sy, ex - sx)
        a1 = angle + math.pi * 0.8
        a2 = angle - math.pi * 0.8
        head1 = (int(ex + ARROW_HEAD_SIZE * math.cos(a1)),
                 int(ey + ARROW_HEAD_SIZE * math.sin(a1)))
        head2 = (int(ex + ARROW_HEAD_SIZE * math.cos(a2)),
                 int(ey + ARROW_HEAD_SIZE * math.sin(a2)))
        draw.line([(ex, ey), head1], fill=ARROW_COLOR, width=SKETCH_STROKE_WIDTH)
        draw.line([(ex, ey), head2], fill=ARROW_COLOR, width=SKETCH_STROKE_WIDTH)

    # Arrow label (midpoint)
    if progress > 0.7 and label:
        font = _load_sketch_font(LABEL_FONT_SIZE - 4)
        mx = (sx + ex) // 2
        my = (sy + ey) // 2 - 18
        draw.text((mx, my), label, fill=ARROW_COLOR, font=font)


# ── High-level sketch renderer ───────────────────────────────────────────────

def render_sketch(draw, sketch_data: dict, progress: float = 1.0, content_bottom: int = 0):
    """
    Render the full sketch onto a Pillow ImageDraw at the given progress (0-1).
    Elements are drawn sequentially: each element gets an equal fraction of progress.
    content_bottom: y-pixel where slide text ends (sketch starts below this).
    """
    elements = sketch_data.get("elements", [])
    if not elements:
        return

    positions = _layout_elements(elements, content_bottom)
    if not positions:
        return

    n = len(elements)

    for i, elem in enumerate(elements):
        # Each element draws during its fraction of overall progress
        elem_start = i / n
        elem_end = (i + 1) / n
        if progress < elem_start:
            break
        elem_progress = min(1.0, (progress - elem_start) / (elem_end - elem_start))

        etype = elem["type"]
        eid = elem.get("id", "")
        label = elem.get("label", "")
        seed = hash(eid) % 1000

        if etype == "box" and eid in positions:
            cx, cy, w, h = positions[eid]
            _draw_sketch_box(draw, cx, cy, w, h, label, seed, elem_progress)

        elif etype == "circle" and eid in positions:
            cx, cy, w, h = positions[eid]
            _draw_sketch_circle(draw, cx, cy, w, h, label, seed, elem_progress)

        elif etype == "arrow":
            from_id = elem.get("from", "")
            to_id = elem.get("to", "")
            if from_id in positions and to_id in positions:
                from_pos = (positions[from_id][0], positions[from_id][1])
                to_pos = (positions[to_id][0], positions[to_id][1])
                _draw_sketch_arrow(draw, from_pos, to_pos, label, seed, elem_progress)


def render_sketch_full(draw, sketch_data: dict, content_bottom: int = 0):
    """Render the complete sketch (no animation)."""
    render_sketch(draw, sketch_data, progress=1.0, content_bottom=content_bottom)
