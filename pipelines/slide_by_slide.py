"""
Slide-by-Slide Pipeline
=======================
Instructor provides title + content per slide directly.
Claude only formats content into visual slide layout — no research, no invention.
Audio narration is the instructor's own content read by TTS.
Napkin AI diagrams generated only for slides where the instructor enables them.
"""

import html
import os
import re
import json
import uuid
import unicodedata

from utils.logger import get_logger

logger = get_logger(__name__)

_ELEMENT_PROMPT = """You are a slide formatter. Convert the instructor's content into a clean slide layout.

Slide Title: {title}

Instructor Content:
{content}

Return a JSON object with these exact fields:
{{
  "title": "concise slide title (max 8 words)",
  "subtitle": "one-sentence key takeaway — empty string if not useful",
  "bullets": ["bullet 1", "bullet 2", "bullet 3"]
}}

Rules:
- Extract 3 to 5 key bullet points directly from the content
- Each bullet: 8-15 words, a complete and meaningful thought
- Title should be punchy — no longer than 8 words
- Do NOT invent information not present in the instructor content
- Return ONLY valid JSON, no markdown fences"""


def _format_elements(title: str, content: str, call_llm) -> list:
    """Ask Claude to format instructor content into slide element dicts."""
    try:
        raw = call_llm(_ELEMENT_PROMPT.format(title=title, content=content))
        clean = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        clean = re.sub(r"\s*```\s*$", "", clean).strip()
        data = json.loads(clean)
    except Exception as e:
        logger.warning(f"  Slide format failed for '{title}': {e} — using fallback")
        return _fallback_elements(title, content)

    slide_title = html.escape(data.get("title", title))
    subtitle = html.escape(data.get("subtitle", ""))
    bullets = [html.escape(b) for b in data.get("bullets", [])]

    elements = []
    elements.append({
        "id": "sbs_title", "type": "text",
        "left": 60, "top": 50, "width": 880, "height": 80,
        "content": f'<p><span style="font-size:32px;"><strong>{slide_title}</strong></span></p>',
    })
    y = 145
    if subtitle:
        elements.append({
            "id": "sbs_subtitle", "type": "text",
            "left": 60, "top": y, "width": 880, "height": 45,
            "content": f'<p><span style="font-size:20px;">{subtitle}</span></p>',
        })
        y += 58
    for i, bullet in enumerate(bullets[:5]):
        elements.append({
            "id": f"sbs_bullet_{i}", "type": "text",
            "left": 80, "top": y, "width": 840, "height": 52,
            "content": f'<p><span style="font-size:18px;">• {bullet}</span></p>',
        })
        y += 62
    return elements


def _fallback_elements(title: str, content: str) -> list:
    """No-Claude fallback: title + first 600 chars of content as body text."""
    return [
        {
            "id": "sbs_title", "type": "text",
            "left": 60, "top": 50, "width": 880, "height": 80,
            "content": f'<p><span style="font-size:32px;"><strong>{html.escape(title)}</strong></span></p>',
        },
        {
            "id": "sbs_body", "type": "text",
            "left": 80, "top": 145, "width": 840, "height": 400,
            "content": f'<p><span style="font-size:18px;">{html.escape(content[:600])}</span></p>',
        },
    ]


def _is_math_char(c: str) -> bool:
    """
    Returns True for characters that TTS cannot read sensibly as spoken English.
    Uses Unicode block ranges and categories — no hardcoded character lists.
    """
    cp = ord(c)
    cat = unicodedata.category(c)
    # Unicode math symbol categories
    if cat in ("Sm", "So", "Nl", "No"):
        return True
    # Greek block: U+0370 - U+03FF
    if 0x0370 <= cp <= 0x03FF:
        return True
    # Arrows block: U+2190 - U+21FF
    if 0x2190 <= cp <= 0x21FF:
        return True
    # Supplemental arrows and misc math: U+27F0 - U+297F
    if 0x27F0 <= cp <= 0x297F:
        return True
    # Superscripts and subscripts block: U+2070 - U+209F
    if 0x2070 <= cp <= 0x209F:
        return True
    # Mathematical alphanumeric symbols: U+1D400 - U+1D7FF
    if 0x1D400 <= cp <= 0x1D7FF:
        return True
    # Zero-width / invisible chars
    if cp in (0x200B, 0x200C, 0x200D, 0xFEFF):
        return True
    return False


def _is_formula_line(line: str) -> bool:
    """True if a line clearly starts or belongs to a mathematical formula."""
    stripped = line.strip()
    if not stripped:
        return False
    math_count = sum(1 for c in stripped if _is_math_char(c))
    if math_count >= 2:
        return True
    if len(stripped) <= 6 and math_count >= 1:
        return True
    return False


def _is_clearly_prose(line: str) -> bool:
    """True if a line is clearly prose — used to stop consuming a formula block."""
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) <= 10:
        return False
    if sum(1 for c in stripped if _is_math_char(c)) >= 2:
        return False
    if not re.search(r'[a-zA-Z]{3,}', stripped):
        return False
    return True


def _clean_for_tts(text: str) -> str:
    """Clean instructor content so TTS receives natural spoken prose."""
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Strip markdown bold/italic but keep the inner text
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)
    # Strip markdown headers
    text = re.sub(r"#{1,6}\s*", "", text)
    # Strip inline code
    text = re.sub(r"`[^`]*`", "", text)
    # Em dash and en dash → natural spoken comma pause
    text = re.sub(r"\s*—\s*", ", ", text)
    text = re.sub(r"\s*–\s*", ", ", text)
    text = re.sub(r" - ", ", ", text)
    # Ellipsis → period
    text = text.replace("…", ".").replace("...", ".")
    # Curly/smart quotes → plain
    text = text.replace(""", '"').replace(""", '"')
    text = text.replace("'", "'").replace("'", "'")

    # Replace formula blocks with a spoken reference.
    # A formula block is one or more consecutive lines that look like math.
    # We consume the whole block (including blank lines within it) and emit
    # one spoken placeholder so the narration stays coherent.
    lines = text.split("\n")
    output = []
    i = 0
    while i < len(lines):
        if _is_formula_line(lines[i]):
            # Silently drop the formula block — formulas can't be read by TTS.
            # The instructor is warned in the UI to explain formulas in plain words instead.
            while i < len(lines) and not _is_clearly_prose(lines[i]):
                i += 1
        else:
            output.append(lines[i])
            i += 1
    text = "\n".join(output)

    # Remove any leftover invisible/zero-width characters by codepoint
    text = re.sub(r"[​‌‍﻿]", "", text)
    # Collapse 3+ blank lines to a single paragraph break
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Clean up extra spaces
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


class SlideBySlide:
    """
    Generates a single video from instructor-authored slides.
    Each slide has a title, content, and an optional Napkin diagram.
    The renderer's two-column re-flow guarantees no element leaves the canvas
    and no text overlaps the diagram or another element.
    """

    def __init__(self, slide_generator, tts, video_assembler, storage,
                 temp_dir, output_dir, call_llm, presenter_overlay=False):
        self.slide_generator = slide_generator
        self.tts = tts
        self.video_assembler = video_assembler
        self.storage = storage
        self.temp_dir = temp_dir
        self.output_dir = output_dir
        self.call_llm = call_llm
        self.presenter_overlay = presenter_overlay

    def run(self, slides: list, scribble: bool = False, on_progress=None) -> str:
        """
        slides: [{"title": str, "content": str, "use_napkin": bool}, ...]
        on_progress: optional callable(slide_index, total, title) for progress updates
        Returns stored video path.
        """
        import pathlib
        job_id = uuid.uuid4().hex[:8]
        work_dir = os.path.join(self.temp_dir, f"sbs_{job_id}")
        os.makedirs(work_dir, exist_ok=True)

        _avatar_path = None
        if self.presenter_overlay:
            candidate = str(pathlib.Path(__file__).parent.parent / "assets" / "shivank_avatar.png")
            if os.path.exists(candidate):
                _avatar_path = candidate

        image_paths = []
        audio_paths = []
        annotation_mask = []
        total = len(slides)

        for i, slide in enumerate(slides):
            title = (slide.get("title") or f"Slide {i + 1}").strip()
            content = (slide.get("content") or "").strip()
            use_napkin = bool(slide.get("use_napkin", False))

            if on_progress:
                on_progress(i, total, title)

            logger.info(f"  Slide {i+1}/{total}: '{title}' napkin={use_napkin}")

            # 1. Format content into slide elements (Claude only restructures, never invents)
            elements = _format_elements(title, content, self.call_llm)

            # 2. Napkin diagram — only if instructor toggled it for this slide
            napkin_path = None
            if use_napkin and getattr(self.slide_generator, "napkin", None):
                napkin_out = os.path.join(work_dir, f"napkin_{i:03d}.png")
                napkin_path = self.slide_generator._try_napkin(title, title, elements, napkin_out,
                                                                call_llm=self.call_llm, force_generate=True)

            # 3. Render PNG — two-column (re-flowed, bounded) when Napkin exists,
            #    full-width when not. Either way nothing escapes the canvas.
            from modules.groot.renderer import render_groot_elements_as_png
            img_path = os.path.join(work_dir, f"slide_{i:03d}.png")
            render_groot_elements_as_png(elements, img_path, title,
                                         napkin_img_path=napkin_path, reflow=True)
            image_paths.append(img_path)

            # 4. Audio — instructor's own content, no Claude narration pass
            narration = _clean_for_tts(content) or _clean_for_tts(title)
            audio_path = os.path.join(work_dir, f"audio_{i:03d}.mp3")
            self.tts.generate_audio(narration, audio_path)
            audio_paths.append(audio_path)
            annotation_mask.append(scribble)

        # 5. Assemble all slides into one video
        final_path = os.path.join(work_dir, "slide_by_slide.mp4")
        self.video_assembler.assemble(
            image_paths, audio_paths, final_path,
            annotation_mask=annotation_mask,
            overlay_image_path=_avatar_path,
        )
        stored = self.storage.save(final_path, "slide_by_slide.mp4")
        logger.info(f"  SlideBySlide complete → {stored}")
        return stored
