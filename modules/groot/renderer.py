"""
Groot Slide Renderer
====================
Renders groot's canvas elements as 1920x1080 PNG images using Pillow.

Element coordinate system (from groot API):
  - Canvas is 1000 units wide x 562.5 units tall (16:9 ratio)
  - viewportSize=1000, viewportRatio=0.5625
  - Element fields: left, top, width, height (all in canvas units)
  - Output PNG: 1920x1080 pixels → scale factor = 1920/1000 = 1.92

Element types returned by scene-content:
  - "text": {left, top, width, height, content (HTML), defaultFontName, defaultColor, rotate}
  - "shape": {left, top, width, height, path, fill, viewBox, rotate}
  - "image": {left, top, width, height, src}

HTML in text content uses inline styles: font-size, font-weight, color, text-align, etc.
"""

import re
import os
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Dimensions ─────────────────────────────────────────────────────────────────
CANVAS_W = 1000       # groot canvas width in units
CANVAS_H = 562.5      # groot canvas height in units (1000 * 0.5625)
OUT_W = 1920          # output PNG width
OUT_H = 1080          # output PNG height
SCALE = OUT_W / CANVAS_W   # 1.92

# ── Scaler brand palette (header only) ────────────────────────────────────────
SCALER_HEADER = (15, 61, 135)
ACCENT_GOLD = (255, 200, 80)
HEADER_H = 60   # pixel height of top branding bar


# ── Font utilities ─────────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    candidates = (
        [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        if bold else
        [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )
    size = max(8, int(size))
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except (IOError, OSError):
                continue
    return ImageFont.load_default()


# ── HTML parsing ───────────────────────────────────────────────────────────────

_CSS_FONT_SIZE = re.compile(r'font-size\s*:\s*(\d+(?:\.\d+)?)\s*px', re.I)
_CSS_FONT_WEIGHT = re.compile(r'font-weight\s*:\s*(bold|\d+)', re.I)
_CSS_COLOR = re.compile(r'(?:^|;)\s*color\s*:\s*(#[0-9a-fA-F]{3,8}|rgb\([^)]+\))', re.I)
_CSS_ALIGN = re.compile(r'text-align\s*:\s*(\w+)', re.I)


def _hex_to_rgb(hex_color: str) -> tuple:
    """Convert #RGB or #RRGGBB to (r, g, b)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (51, 51, 51)


def _parse_style(style_str: str) -> dict:
    """Extract font-size, bold, color, align from an inline CSS style string."""
    result = {"font_size": None, "bold": False, "color": None, "align": "left"}
    m = _CSS_FONT_SIZE.search(style_str)
    if m:
        result["font_size"] = float(m.group(1))
    m = _CSS_FONT_WEIGHT.search(style_str)
    if m:
        val = m.group(1)
        result["bold"] = val == "bold" or (val.isdigit() and int(val) >= 600)
    m = _CSS_COLOR.search(style_str)
    if m:
        color_str = m.group(1)
        if color_str.startswith("#"):
            result["color"] = _hex_to_rgb(color_str)
    m = _CSS_ALIGN.search(style_str)
    if m:
        result["align"] = m.group(1)
    return result


class _TextSegment:
    """A span of text with its own style."""
    def __init__(self, text: str, font_size: float, bold: bool, color: tuple, align: str = "left"):
        self.text = text
        self.font_size = font_size
        self.bold = bold
        self.color = color
        self.align = align


def _parse_html_to_segments(
    html: str,
    default_font_size: float = 18.0,
    default_color: tuple = (51, 51, 51),
) -> list[_TextSegment]:
    """
    Parse HTML content into text segments preserving font-size, weight, and color.
    Handles <p>, <span>, <strong>, <em>, <li>, <br>.
    """
    # Normalize line breaks
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"<li[^>]*>", "\n• ", html, flags=re.I)
    html = re.sub(r"</(p|div|h[1-6]|li|ul|ol)>", "\n", html, flags=re.I)

    segments = []
    # Process tag by tag
    pos = 0
    style_stack = [{"font_size": default_font_size, "bold": False, "color": default_color, "align": "left"}]

    token_re = re.compile(r"<([^>]+)>|([^<]+)", re.S)
    for m in token_re.finditer(html):
        tag_content, text = m.group(1), m.group(2)
        if text:
            text = (
                text.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&nbsp;", " ")
                    .replace("&#39;", "'")
                    .replace("&quot;", '"')
            )
            cur = style_stack[-1]
            if text.strip() or "\n" in text:
                segments.append(_TextSegment(
                    text=text,
                    font_size=cur["font_size"],
                    bold=cur["bold"],
                    color=cur["color"],
                    align=cur["align"],
                ))
        elif tag_content:
            tag = tag_content.lower().lstrip("/")
            is_close = tag_content.startswith("/")
            tag_name = tag.split()[0] if " " in tag else tag

            if is_close:
                if len(style_stack) > 1:
                    style_stack.pop()
            else:
                cur = dict(style_stack[-1])
                # Extract style attribute
                style_m = re.search(r'style\s*=\s*["\']([^"\']*)["\']', tag_content, re.I)
                if style_m:
                    parsed = _parse_style(style_m.group(1))
                    if parsed["font_size"]:
                        cur["font_size"] = parsed["font_size"]
                    if parsed["bold"]:
                        cur["bold"] = True
                    if parsed["color"]:
                        cur["color"] = parsed["color"]
                    if parsed["align"] != "left":
                        cur["align"] = parsed["align"]
                # Tag-level bold
                if tag_name in ("strong", "b"):
                    cur["bold"] = True
                if tag_name in ("em", "i"):
                    pass  # could add italic support later
                style_stack.append(cur)

    return segments


# ── Text wrapping ──────────────────────────────────────────────────────────────

def _wrap_line(draw, text: str, font, max_width_px: int) -> list[str]:
    """Word-wrap a single line of text to fit within max_width_px."""
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip() if current else word
        try:
            bbox = draw.textbbox((0, 0), test, font=font)
            w = bbox[2] - bbox[0]
        except AttributeError:
            w = len(test) * max(8, int(font.size if hasattr(font, "size") else 10))
        if w <= max_width_px or not current:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


# ── Main renderer ──────────────────────────────────────────────────────────────

def render_groot_elements_as_png(
    elements: list, output_path: str, scene_title: str = ""
) -> str:
    """
    Renders a list of groot slide elements onto a 1920x1080 PNG.

    Coordinate mapping:
      pixel_x = element.left * SCALE
      pixel_y = element.top  * SCALE + HEADER_H
      font_px  = css_font_size * SCALE
    """
    from PIL import Image, ImageDraw

    # Background: use element background color if present, else white
    bg_color = _get_background_color(elements)
    img = Image.new("RGB", (OUT_W, OUT_H), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Scaler header bar
    draw.rectangle([(0, 0), (OUT_W, HEADER_H)], fill=SCALER_HEADER)
    logo_font = _load_font(24, bold=True)
    draw.text((30, 18), "SCALER", font=logo_font, fill=ACCENT_GOLD)

    if not elements:
        _render_no_content(draw, scene_title)
    else:
        for elem in sorted(elements, key=lambda e: e.get("top", 0)):
            _render_element(draw, elem, bg_color)

    img.save(output_path, "PNG")
    logger.debug(f"Rendered → {output_path}")
    return output_path


def _get_background_color(elements: list) -> tuple:
    """Try to infer background color from elements (return white if not found)."""
    return (255, 255, 255)


def _render_element(draw, elem: dict, bg_color: tuple):
    """Dispatch element rendering by type."""
    elem_type = elem.get("type", "text")
    if elem_type == "text":
        _render_text_element(draw, elem)
    elif elem_type == "shape":
        _render_shape_element(draw, elem)
    # image elements: skip for now (require HTTP fetch)


def _render_text_element(draw, elem: dict):
    """Render a text element at its canvas position."""
    left_px = int(elem.get("left", 0) * SCALE)
    top_px = int(elem.get("top", 0) * SCALE) + HEADER_H
    width_px = int(elem.get("width", 200) * SCALE)
    height_px = int(elem.get("height", 50) * SCALE)

    content_html = elem.get("content", "")
    default_color_hex = elem.get("defaultColor", "#333333")
    default_color = _hex_to_rgb(default_color_hex) if default_color_hex.startswith("#") else (51, 51, 51)

    segments = _parse_html_to_segments(content_html, default_font_size=18.0, default_color=default_color)

    # Render each segment's lines
    y = top_px
    bottom_limit = top_px + height_px + int(30 * SCALE)  # slight overflow allowed

    for seg in segments:
        if not seg.text:
            continue

        font_size_px = max(10, int(seg.font_size * SCALE))
        font = _load_font(font_size_px, bold=seg.bold)
        line_h = int(font_size_px * 1.35)

        for raw_line in seg.text.split("\n"):
            if y > bottom_limit:
                break
            if not raw_line.strip():
                y += int(line_h * 0.4)
                continue
            wrapped = _wrap_line(draw, raw_line, font, width_px)
            for line in wrapped:
                if y > bottom_limit:
                    break
                draw.text((left_px, y), line, font=font, fill=seg.color)
                y += line_h


def _render_shape_element(draw, elem: dict):
    """Render a shape element (rectangles, lines) at canvas position."""
    left_px = int(elem.get("left", 0) * SCALE)
    top_px = int(elem.get("top", 0) * SCALE) + HEADER_H
    width_px = int(elem.get("width", 10) * SCALE)
    height_px = int(elem.get("height", 10) * SCALE)
    fill_hex = elem.get("fill", "#cccccc")

    if fill_hex and fill_hex.startswith("#"):
        fill = _hex_to_rgb(fill_hex)
    else:
        fill = (200, 200, 200)

    # Draw rectangle/bar
    draw.rectangle(
        [(left_px, top_px), (left_px + width_px, top_px + height_px)],
        fill=fill,
    )


def _render_no_content(draw, title: str):
    """Fallback when no elements are available."""
    font = _load_font(48, bold=True)
    draw.text((80, HEADER_H + 80), title or "Slide", font=font, fill=(51, 51, 51))
    sub = _load_font(28)
    draw.text((80, HEADER_H + 160), "Content generated by Groot AI", font=sub, fill=(100, 120, 180))


def render_fallback_slide(title: str, output_path: str) -> str:
    """Creates a minimal error/placeholder slide PNG."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (OUT_W, OUT_H), color=(240, 240, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (OUT_W, HEADER_H)], fill=SCALER_HEADER)
    logo_font = _load_font(24, bold=True)
    draw.text((30, 18), "SCALER", font=logo_font, fill=ACCENT_GOLD)
    title_font = _load_font(48, bold=True)
    draw.text((80, HEADER_H + 80), title, font=title_font, fill=(51, 51, 51))
    img.save(output_path, "PNG")
    return output_path
