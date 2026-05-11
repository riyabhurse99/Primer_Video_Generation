"""
Groot Slide Renderer — Scaler 3.0 Rebrand
==========================================
Renders groot's canvas elements as 1920x1080 PNG images using Pillow,
styled with the Scaler 3.0 design system.

Design system:
  - Background: #FCFCFC (off-white, never pure white)
  - Header: #011845 (navy) with #0055FF accent bar
  - Headlines: Clash Grotesk, color #101E37
  - Body: Plus Jakarta Sans, color #0B1529
  - No rounded corners, no shadows — sharp, editorial, premium

Element coordinate system (from groot API):
  - Canvas: 1000 x 562.5 units (16:9)
  - Output: 1920x1080 pixels → scale = 1.92
  - pixel_x = element.left * 1.92
  - pixel_y = element.top * 1.92 + HEADER_TOTAL_H
"""

import re
import os
from utils.logger import get_logger

# ── Logo (pre-rendered PNG, cached) ───────────────────────────────────────────
_LOGO_PNG_CACHE = None


def _get_logo_png():
    """Load the pre-rendered white Scaler logo PNG (cached)."""
    global _LOGO_PNG_CACHE
    if _LOGO_PNG_CACHE is not None:
        return _LOGO_PNG_CACHE

    png_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "../../assets/logo_white.png")
    )
    if not os.path.exists(png_path):
        return None

    try:
        from PIL import Image
        _LOGO_PNG_CACHE = Image.open(png_path).convert("RGBA")
        return _LOGO_PNG_CACHE
    except Exception:
        return None

logger = get_logger(__name__)

# ── Dimensions ─────────────────────────────────────────────────────────────────
CANVAS_W = 1000
CANVAS_H = 562.5
OUT_W = 1920
OUT_H = 1080
SCALE = OUT_W / CANVAS_W  # 1.92

# ── Scaler 3.0 Color Palette ──────────────────────────────────────────────────
# Primary
BRAND_BLUE = (0, 85, 255)         # #0055FF — accent, links, active states
NAVY = (1, 24, 69)                # #011845 — header bg, dark sections
CTA_BLUE = (0, 76, 229)           # #004CE5 — CTA-weight blue

# Neutrals
BG_COLOR = (252, 252, 252)        # #FCFCFC — page background (off-white)
TEXT_PRIMARY = (11, 21, 41)       # #0B1529 — body text
TEXT_HEADING = (16, 30, 55)       # #101E37 — headlines
TEXT_MUTED = (105, 105, 105)      # #696969 — metadata, secondary text
TEXT_LIGHT_MUTED = (132, 132, 132)  # #848484 — labels, eyebrows
PANEL_BG = (246, 246, 246)        # #F6F6F6 — card/panel backgrounds
ICE_BG = (233, 241, 255)          # #E9F1FF — light blue tint
BORDER_COLOR = (202, 192, 192)    # #CAC0C0 — card borders

# Header layout
HEADER_H = 56           # navy bar height
ACCENT_BAR_H = 4        # brand blue accent bar below header
HEADER_TOTAL_H = HEADER_H + ACCENT_BAR_H  # 60px total

# ── Font paths ─────────────────────────────────────────────────────────────────
_FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts")

_FONT_PATHS = {
    # Clash Grotesk — headlines
    "heading": os.path.join(_FONTS_DIR, "ClashGrotesk-Regular.ttf"),
    "heading_bold": os.path.join(_FONTS_DIR, "ClashGrotesk-Medium.ttf"),
    # Plus Jakarta Sans — body
    "body": os.path.join(_FONTS_DIR, "PlusJakartaSans-Regular.ttf"),
    "body_bold": os.path.join(_FONTS_DIR, "PlusJakartaSans-Medium.ttf"),
}

# System font fallbacks
_FALLBACK_FONTS = {
    True: [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
    False: [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
}

# Threshold: text above this size (in canvas units) uses Clash Grotesk (headline font)
_HEADING_FONT_SIZE_THRESHOLD = 24.0


def _load_font(size: int, bold: bool = False, role: str = "body"):
    """
    Load a font for the given role.
      role="heading" → Clash Grotesk
      role="body"    → Plus Jakarta Sans
    Falls back to system fonts if the rebrand fonts are missing.
    """
    from PIL import ImageFont
    size = max(8, int(size))

    # Try rebrand font first
    if role == "heading":
        key = "heading_bold" if bold else "heading"
    else:
        key = "body_bold" if bold else "body"

    path = _FONT_PATHS.get(key, "")
    if path and os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass

    # Fallback to system fonts
    for fallback in _FALLBACK_FONTS.get(bold, []):
        if os.path.exists(fallback):
            try:
                return ImageFont.truetype(fallback, size)
            except (IOError, OSError):
                continue

    return ImageFont.load_default()


def _font_role_for_size(font_size: float) -> str:
    """Large text = heading font (Clash Grotesk), small = body (Plus Jakarta Sans)."""
    return "heading" if font_size >= _HEADING_FONT_SIZE_THRESHOLD else "body"


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
        return TEXT_PRIMARY


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
    default_color: tuple = TEXT_PRIMARY,
) -> list[_TextSegment]:
    """
    Parse HTML content into text segments preserving font-size, weight, and color.
    Handles <p>, <span>, <strong>, <em>, <li>, <br>.
    """
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"<li[^>]*>", "\n• ", html, flags=re.I)
    html = re.sub(r"</(p|div|h[1-6]|li|ul|ol)>", "\n", html, flags=re.I)

    segments = []
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
                if tag_name in ("strong", "b"):
                    cur["bold"] = True
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


# ── Header rendering ──────────────────────────────────────────────────────────

def _render_header(img, draw):
    """
    Scaler 3.0 header: navy bar + brand blue accent strip + SVG logo.
    Falls back to "SCALER" text if logo PNG is unavailable.
    """
    # Navy header bar
    draw.rectangle([(0, 0), (OUT_W, HEADER_H)], fill=NAVY)

    # Brand blue accent bar (4px strip below header)
    draw.rectangle(
        [(0, HEADER_H), (OUT_W, HEADER_H + ACCENT_BAR_H)],
        fill=BRAND_BLUE,
    )

    # Logo: try SVG-rendered PNG first, fall back to text
    logo_img = _get_logo_png()
    if logo_img is not None:
        lx = 40
        ly = (HEADER_H - logo_img.height) // 2
        img.paste(logo_img, (lx, ly), logo_img)
    else:
        logo_font = _load_font(22, bold=True, role="heading")
        draw.text((40, 16), "SCALER", font=logo_font, fill=(252, 252, 252))


# ── Main renderer ──────────────────────────────────────────────────────────────

def render_groot_elements_as_png(
    elements: list, output_path: str, scene_title: str = ""
) -> str:
    """
    Renders a list of groot slide elements onto a 1920x1080 PNG
    using the Scaler 3.0 design system.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (OUT_W, OUT_H), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    _render_header(img, draw)

    if not elements:
        _render_no_content(draw, scene_title)
    else:
        for elem in sorted(elements, key=lambda e: e.get("top", 0)):
            _render_element(draw, elem)

    img.save(output_path, "PNG")
    logger.debug(f"Rendered → {output_path}")
    return output_path


def _render_element(draw, elem: dict):
    """Dispatch element rendering by type."""
    elem_type = elem.get("type", "text")
    if elem_type == "text":
        _render_text_element(draw, elem)
    elif elem_type == "shape":
        _render_shape_element(draw, elem)


def _render_text_element(draw, elem: dict):
    """Render a text element using Scaler 3.0 typography."""
    left_px = int(elem.get("left", 0) * SCALE)
    top_px = int(elem.get("top", 0) * SCALE) + HEADER_TOTAL_H
    width_px = int(elem.get("width", 200) * SCALE)
    height_px = int(elem.get("height", 50) * SCALE)

    content_html = elem.get("content", "")
    default_color_hex = elem.get("defaultColor", "#0B1529")
    default_color = _hex_to_rgb(default_color_hex) if default_color_hex.startswith("#") else TEXT_PRIMARY

    segments = _parse_html_to_segments(content_html, default_font_size=18.0, default_color=default_color)

    y = top_px
    bottom_limit = top_px + height_px + int(30 * SCALE)

    for seg in segments:
        if not seg.text:
            continue

        font_size_px = max(10, int(seg.font_size * SCALE))
        role = _font_role_for_size(seg.font_size)

        # Headings use Clash Grotesk + heading color override
        if role == "heading" and seg.color == TEXT_PRIMARY:
            fill_color = TEXT_HEADING
        else:
            fill_color = seg.color

        font = _load_font(font_size_px, bold=seg.bold, role=role)
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
                draw.text((left_px, y), line, font=font, fill=fill_color)
                y += line_h


def _render_shape_element(draw, elem: dict):
    """Render a shape element at canvas position."""
    left_px = int(elem.get("left", 0) * SCALE)
    top_px = int(elem.get("top", 0) * SCALE) + HEADER_TOTAL_H
    width_px = int(elem.get("width", 10) * SCALE)
    height_px = int(elem.get("height", 10) * SCALE)
    fill_hex = elem.get("fill", "#D7DDE8")

    if fill_hex and fill_hex.startswith("#"):
        fill = _hex_to_rgb(fill_hex)
    else:
        fill = (215, 221, 232)  # steel gray default

    # Sharp corners — no border-radius per Scaler 3.0
    draw.rectangle(
        [(left_px, top_px), (left_px + width_px, top_px + height_px)],
        fill=fill,
    )


def _render_no_content(draw, title: str):
    """Fallback when no elements are available — Scaler 3.0 styled."""
    title_font = _load_font(48, bold=True, role="heading")
    draw.text((80, HEADER_TOTAL_H + 80), title or "Slide", font=title_font, fill=TEXT_HEADING)
    sub_font = _load_font(24, role="body")
    draw.text((80, HEADER_TOTAL_H + 150), "Content generated by Scaler Primer", font=sub_font, fill=TEXT_MUTED)


# ── Text measurement (for pen annotation bounding boxes) ─────────────────────

def _measure_text_bounds(elem: dict) -> tuple:
    """
    Simulate the same text layout as _render_text_element to find the
    actual pixel bounds of rendered text (not the container bounds).

    Returns (x1, y1, x2, y_text_bottom) where y_text_bottom is where
    the last line of text actually ends, NOT the container bottom.
    """
    from PIL import Image, ImageDraw as _ID

    left_px = int(elem.get("left", 0) * SCALE)
    top_px = int(elem.get("top", 0) * SCALE) + HEADER_TOTAL_H
    width_px = int(elem.get("width", 200) * SCALE)
    height_px = int(elem.get("height", 50) * SCALE)

    content_html = elem.get("content", "")
    if not content_html.strip():
        return (left_px, top_px, left_px + width_px, top_px)

    segments = _parse_html_to_segments(content_html, default_font_size=18.0)

    _tmp = Image.new("RGB", (1, 1))
    _draw = _ID.Draw(_tmp)

    y = top_px
    bottom_limit = top_px + height_px + int(30 * SCALE)
    max_x_right = left_px

    for seg in segments:
        if not seg.text:
            continue
        font_size_px = max(10, int(seg.font_size * SCALE))
        role = _font_role_for_size(seg.font_size)
        font = _load_font(font_size_px, bold=seg.bold, role=role)
        line_h = int(font_size_px * 1.35)

        for raw_line in seg.text.split("\n"):
            if y > bottom_limit:
                break
            if not raw_line.strip():
                y += int(line_h * 0.4)
                continue
            wrapped = _wrap_line(_draw, raw_line, font, width_px)
            for line in wrapped:
                if y > bottom_limit:
                    break
                try:
                    bbox = _draw.textbbox((0, 0), line, font=font)
                    line_w = bbox[2] - bbox[0]
                except AttributeError:
                    line_w = len(line) * max(8, font_size_px // 2)
                max_x_right = max(max_x_right, left_px + line_w)
                y += line_h

    y_text_bottom = y
    max_x_right = min(max_x_right, left_px + width_px)

    return (left_px, top_px, max_x_right, y_text_bottom)


def extract_element_boxes(elements: list) -> list:
    """
    Extract bounding boxes for text elements that should be annotated,
    with a style decision per element based on its properties.

    Style rules (smart heuristic — no AI call needed):
      - Titles (font >= 28):         "underline"  (main heading highlight)
      - Key terms (font 20-27, bold, short ≤6 words): "circle" (emphasis)
      - Sub-headings (font 20-27, longer text):        "underline"
      - Body text (font < 20):       skipped entirely

    Returns list of dicts: [{"box": [x1,y1,x2,y2], "style": "underline"|"circle"}, ...]
    Sorted top-to-bottom, max 5 annotations per slide.
    """
    MIN_WIDTH = 100
    MIN_TEXT_HEIGHT = 10
    MIN_FONT_FOR_ANNOTATION = 20.0  # skip small body text

    annotations = []
    for elem in sorted(elements, key=lambda e: e.get("top", 0)):
        if elem.get("type", "text") != "text":
            continue

        content_html = elem.get("content", "")
        if not content_html.strip():
            continue
        segments = _parse_html_to_segments(content_html, default_font_size=18.0)
        text_segments = [s for s in segments if s.text.strip()]
        if not text_segments:
            continue

        max_font_size = max(s.font_size for s in text_segments)
        is_bold = any(s.bold for s in text_segments)
        plain_text = " ".join(s.text.strip() for s in text_segments)
        word_count = len(plain_text.split())

        # Skip small body text
        if max_font_size < MIN_FONT_FOR_ANNOTATION:
            continue

        x1, y1, x2, y2 = _measure_text_bounds(elem)
        w = x2 - x1
        h = y2 - y1

        if w < MIN_WIDTH or h < MIN_TEXT_HEIGHT:
            continue

        x2 = min(x2, OUT_W - 20)
        y2 = min(y2, OUT_H - 30)

        # Decide style based on element properties
        if max_font_size >= 28:
            style = "underline"  # big title
        elif is_bold and word_count <= 6:
            style = "circle"     # short bold key term
        else:
            style = "underline"  # sub-heading or medium text

        annotations.append({"box": [x1, y1, x2, y2], "style": style, "text": plain_text})

    # Cap at 5 annotations per slide
    annotations = annotations[:5]

    logger.info(
        f"  extract_element_boxes: {len(elements)} elements → "
        f"{len(annotations)} annotations "
        f"({sum(1 for a in annotations if a['style']=='underline')} underline, "
        f"{sum(1 for a in annotations if a['style']=='circle')} circle)"
    )
    return annotations


def render_fallback_slide(title: str, output_path: str) -> str:
    """Creates a minimal fallback slide PNG — Scaler 3.0 styled."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (OUT_W, OUT_H), color=BG_COLOR)
    draw = ImageDraw.Draw(img)
    _render_header(img, draw)

    title_font = _load_font(48, bold=True, role="heading")
    draw.text((80, HEADER_TOTAL_H + 80), title, font=title_font, fill=TEXT_HEADING)

    sub_font = _load_font(22, role="body")
    draw.text((80, HEADER_TOTAL_H + 150), "Scaler Primer", font=sub_font, fill=TEXT_MUTED)

    img.save(output_path, "PNG")
    return output_path
