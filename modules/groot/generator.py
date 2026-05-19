"""
Groot Slide Generator
=====================
Uses groot-pied.vercel.app to generate AI slide PNGs and narration text.

Two interfaces:
  generate_slides(topic, images_dir)  — used by DirectPipeline, returns data directly
  generate(video_script, output_path) — used by generic/dynamic pipelines, writes proxy files
"""

import json
import os
import re

from models.schemas import VideoScript
from modules.slide_generator.base import BaseSlideGenerator
from modules.groot.client import GrootAPIClient, generate_scene_titles
from modules.groot.renderer import render_groot_elements_as_png, render_fallback_slide, extract_element_boxes
from utils.logger import get_logger
import utils.run_logger as run_logger

logger = get_logger(__name__)


def _build_napkin_text(topic: str, scene_title: str, elements: list) -> str:
    """Raw fallback: concatenate slide text for Napkin when Claude is unavailable."""
    texts = []
    for e in sorted(elements, key=lambda e: e.get("top", 0)):
        if e.get("type") == "text":
            raw = re.sub(r"<[^>]+>", "", e.get("content", "")).strip()
            if raw:
                texts.append(raw)
    bullet_content = "\n".join(texts) if texts else scene_title
    return f"Topic: {topic}\n\n{bullet_content}"


_NAPKIN_PROMPT = """You are deciding what diagram Napkin.ai should generate for an educational slide.

Napkin.ai generates visual diagrams from plain text descriptions. It works best with:
- Process flows: steps in sequence (e.g. "Request → Load Balancer → App Server → Database")
- Comparisons: two or more things contrasted side by side
- Hierarchies: parent-child or layered structures
- Component relationships: how parts connect (client-server, pipeline stages, system layers)
- Simple data structures: linked list nodes, stack, tree structure

TOPIC: "{topic}"
SLIDE TITLE: "{scene_title}"
SLIDE CONTENT:
{content}

YOUR TASK:
Decide if this slide has a clear visual concept worth diagramming. If yes, write a concise description (maximum 40 words) telling Napkin exactly what to draw — name the actual components, their relationships, and flow direction.

If the content is just definitions, introductions, or abstract prose with no clear visual structure, return exactly: SKIP

RULES:
- Maximum 40 words — Napkin charges per word, so be concise
- Use specific terms from the slide (e.g. not "Component A connects to Component B" but "Client sends HTTP request to API Gateway → forwarded to Lambda → response returned")
- One focused diagram idea only — do not try to visualise everything
- Return ONLY the diagram description or the single word SKIP. No explanation, no markdown, no labels."""

_NAPKIN_PROMPT_FORCED = """You are writing a diagram description for Napkin.ai for an educational slide.

The instructor has explicitly requested a diagram for this slide — you MUST produce one. Do NOT return SKIP.

Napkin.ai generates visual diagrams from plain text descriptions. It works best with:
- Process flows: steps in sequence (e.g. "Request → Load Balancer → App Server → Database")
- Comparisons: two or more things contrasted side by side
- Hierarchies: parent-child or layered structures
- Component relationships: how parts connect (client-server, pipeline stages, system layers)
- Simple data structures: linked list nodes, stack, tree structure

TOPIC: "{topic}"
SLIDE TITLE: "{scene_title}"
SLIDE CONTENT:
{content}

YOUR TASK:
Write a concise description (maximum 40 words) of the most visual concept in this slide. If the content is abstract, pick the single most concrete idea and represent it as a flow, comparison, or relationship diagram.

RULES:
- Maximum 40 words — Napkin charges per word, so be concise
- Use specific terms from the slide content — not generic placeholders
- One focused diagram idea only
- Return ONLY the diagram description. No explanation, no markdown, no labels, no SKIP."""


def _claude_napkin_description(topic: str, scene_title: str, elements: list, call_llm, force: bool = False) -> str:
    """
    Ask Claude to craft the best Napkin diagram description for this slide.

    force=True: instructor explicitly requested a diagram — Claude must produce one, cannot skip.
    force=False: Claude decides whether a diagram is useful; returns None to skip.
    Falls back to raw slide text if Claude fails.
    """
    texts = []
    for e in sorted(elements, key=lambda e: e.get("top", 0)):
        if e.get("type") == "text":
            raw = re.sub(r"<[^>]+>", "", e.get("content", "")).strip()
            if raw:
                texts.append(raw)
    content = "\n".join(texts) if texts else scene_title

    template = _NAPKIN_PROMPT_FORCED if force else _NAPKIN_PROMPT
    prompt = template.format(topic=topic, scene_title=scene_title, content=content)
    try:
        result = call_llm(prompt).strip()
        if not result or result.upper() == "SKIP":
            if force:
                # Instructor explicitly requested a diagram — fall back to raw text rather than skipping
                logger.warning(f"  Claude returned SKIP despite force=True for '{scene_title}' — using raw text fallback")
                return _build_napkin_text(topic, scene_title, elements)
            logger.info(f"  Claude: no Napkin diagram needed for '{scene_title}'")
            return None
        # Hard cap at 40 words to control Napkin credit usage
        words = result.split()
        if len(words) > 40:
            result = " ".join(words[:40])
        logger.info(f"  Claude napkin description ({len(result.split())} words): {result[:80]}...")
        return result
    except Exception as e:
        logger.warning(f"  Claude napkin description failed for '{scene_title}': {e} — using raw text fallback")
        return _build_napkin_text(topic, scene_title, elements)


# ── Claude fallback prompts ────────────────────────────────────────────────────

_SLIDE_PROMPT = """You are generating a single educational slide for a video lesson.

TOPIC: "{topic}"
SLIDE TITLE: "{scene_title}"

Output ONLY a JSON object with this exact structure:
{{
  "title": "the slide title (same or refined)",
  "bullets": ["point 1", "point 2", "point 3"],
  "subtitle": "optional one-line subtitle or empty string"
}}

Rules:
- 3 to 5 concise bullet points
- Each bullet: 5-12 words, educational, specific to the title
- No markdown, no explanation, just the JSON"""

_NARRATION_PROMPT = """You are writing the spoken narration for a slide in a pre-recorded educational video.

TOPIC: "{topic}"
SLIDE TITLE: "{scene_title}"
SLIDE CONTENT:
{slide_summary}

Write 4-6 sentences that sound like a real human explaining this to a friend. NOT a textbook.
Rules:
- Use contractions naturally (it's, you'll, don't, that's)
- Mix sentence lengths — some short and punchy, some longer
- Use casual transitions: "So,", "Now,", "And here's the thing —", "Basically,"
- Start with WHY this matters, then explain WHAT it is
- Do NOT say "in this slide", "as you can see", "I'll pause", or "any questions"
- Plain text only — no markdown, no bullet symbols, no emojis
- 80-130 words"""


# Reserved region for presenter avatar overlay (canvas units, 1000×562.5 space).
# Avatar: 366×240px at pixel (1538, 16). Canvas scale=1.92, header offset=60px.
# x threshold: (1920 - 366 - 16) / 1.92 = 800.6 → 800 canvas units
# y threshold: (16 + 240 - 60) / 1.92 = 102.1 → clip if top < 103
#   (60px header is added to every element's top by the renderer, so the
#    element-canvas y-axis starts at pixel 60, not 0)
_AVATAR_CANVAS_LEFT = 795
_AVATAR_CANVAS_TOP  = 103


def _reserve_avatar_corner(elements: list) -> list:
    """
    Clip element widths so nothing extends into the top-right corner reserved for
    the presenter avatar. Elements entirely inside the reserved zone get width=0
    (invisible). Elements that partially overlap get their right edge clipped to
    canvas unit 929. Modifies the list in-place and returns it.
    """
    for elem in elements:
        if elem.get("top", 0) < _AVATAR_CANVAS_TOP:
            right_edge = elem.get("left", 0) + elem.get("width", 0)
            if right_edge > _AVATAR_CANVAS_LEFT:
                elem["width"] = max(0, _AVATAR_CANVAS_LEFT - elem.get("left", 0))
    return elements


def _claude_fallback_elements(topic: str, scene_title: str, call_llm) -> list:
    """
    Use Claude to generate slide elements when Groot fails.
    Returns a list of element dicts in the renderer's expected format.
    """
    prompt = _SLIDE_PROMPT.format(topic=topic, scene_title=scene_title)
    try:
        raw = call_llm(prompt)
        clean = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        clean = re.sub(r"\s*```\s*$", "", clean).strip()
        data = json.loads(clean)
    except Exception as e:
        logger.warning(f"  Claude slide fallback parse failed: {e}")
        return []

    title = data.get("title", scene_title)
    bullets = data.get("bullets", [])
    subtitle = data.get("subtitle", "")

    # Build elements in Groot canvas format (1000x562.5 coordinate space)
    elements = []

    # Title element
    elements.append({
        "id": "claude_title",
        "type": "text",
        "left": 60, "top": 50, "width": 880, "height": 70,
        "content": f'<p><span style="font-size:32px;"><strong>{title}</strong></span></p>',
    })

    # Divider hint (thin line) — optional, renderer ignores non-text by default
    y = 140
    if subtitle:
        elements.append({
            "id": "claude_subtitle",
            "type": "text",
            "left": 60, "top": y, "width": 880, "height": 40,
            "content": f'<p><span style="font-size:20px;">{subtitle}</span></p>',
        })
        y += 50

    # Bullet points — one text element per bullet
    for i, bullet in enumerate(bullets):
        elements.append({
            "id": f"claude_bullet_{i}",
            "type": "text",
            "left": 80, "top": y, "width": 840, "height": 50,
            "content": f'<p><span style="font-size:18px;">• {bullet}</span></p>',
        })
        y += 60

    logger.info(f"  Claude slide fallback: '{title}' — {len(bullets)} bullets")
    return elements


def _claude_fallback_narration(topic: str, scene_title: str, elements: list, call_llm) -> str:
    """
    Use Claude to generate narration when Groot scene-actions fails.
    """
    # Summarise the slide content from elements for context
    texts = []
    for e in elements:
        if e.get("type") == "text":
            raw_html = e.get("content", "")
            text = re.sub(r"<[^>]+>", "", raw_html).strip()
            if text:
                texts.append(text)
    slide_summary = "\n".join(texts) if texts else "(no content available)"

    prompt = _NARRATION_PROMPT.format(
        topic=topic, scene_title=scene_title, slide_summary=slide_summary
    )
    try:
        narration = call_llm(prompt).strip()
        logger.info(f"  Claude narration fallback: {len(narration.split())} words")
        return narration
    except Exception as e:
        logger.warning(f"  Claude narration fallback failed: {e}")
        return ""


class GrootSlideGenerator(BaseSlideGenerator):

    def __init__(self, cookies: str = "", napkin_api_key: str = "", use_groot: bool = True):
        self.client = GrootAPIClient(cookies=cookies)
        self.use_groot = use_groot
        self.napkin = None
        if napkin_api_key:
            from modules.napkin.client import NapkinAPIClient
            self.napkin = NapkinAPIClient(api_key=napkin_api_key)
            logger.info("Napkin integration enabled — slides will use two-column visual layout")
        if not use_groot:
            logger.info("Groot disabled — slides will be generated by Claude only")

    def _try_napkin(self, topic: str, scene_title: str, elements: list, napkin_out_path: str,
                    call_llm=None, force_generate: bool = False):
        """
        Generate a Napkin diagram for the slide. Returns the PNG path or None on failure.

        force_generate=True: instructor explicitly toggled diagram for this slide —
                             Claude must produce a description, cannot skip.
        force_generate=False: Claude decides whether a diagram adds value; may skip.
        """
        if not self.napkin or not elements:
            return None
        import time as _time

        if call_llm:
            text = _claude_napkin_description(topic, scene_title, elements, call_llm, force=force_generate)
            if text is None:
                return None  # Claude decided no diagram is useful for this slide
        else:
            text = _build_napkin_text(topic, scene_title, elements)

        t0 = _time.perf_counter()
        try:
            path = self.napkin.generate_visual(text, napkin_out_path)
            dur_ms = int((_time.perf_counter() - t0) * 1000)
            run_logger.log_api_call(
                api="napkin",
                endpoint="visual",
                input_summary=f'"{scene_title}" ({len(text.split())} words)',
                output_summary="diagram PNG saved",
                duration_ms=dur_ms,
            )
            return path
        except Exception as e:
            dur_ms = int((_time.perf_counter() - t0) * 1000)
            run_logger.log_api_call(
                api="napkin",
                endpoint="visual",
                input_summary=f'"{scene_title}"',
                output_summary=f"FAILED: {str(e)[:120]}",
                duration_ms=dur_ms,
                status="error",
            )
            logger.warning(f"  Napkin failed for '{scene_title}': {e} — using full-width layout")

    # ──────────────────────────────────────────────────────────────────────────
    # Direct interface — used by DirectPipeline
    # ──────────────────────────────────────────────────────────────────────────

    def generate_slides(
        self, topic: str, images_dir: str, num_scenes: int = 4,
        level: str = None, call_llm=None, reserve_corner: bool = False,
    ) -> tuple[list[str], list[str]]:
        """
        Takes a topic string, generates slide PNGs and narrations.
        Returns (image_paths, narrations) directly — no files written.

        If call_llm is provided, uses the LLM to generate topic+level-aware
        scene titles instead of the hardcoded defaults.
        If reserve_corner is True, clips any element that extends into the
        top-right corner (reserved for the presenter avatar overlay).
        """
        scene_titles = generate_scene_titles(topic, num_scenes, level, call_llm)
        return self._run(topic, images_dir, num_scenes, scene_titles=scene_titles, call_llm=call_llm, reserve_corner=reserve_corner)

    # ──────────────────────────────────────────────────────────────────────────
    # BaseSlideGenerator interface — used by generic/dynamic pipelines
    # ──────────────────────────────────────────────────────────────────────────

    def generate(self, video_script: VideoScript, output_path: str, reserve_corner: bool = False) -> str:
        """
        Generates slides from a VideoScript and writes proxy files
        (.png_list, .narrations, stub .pptx) for the generic/dynamic pipelines.
        """
        images_dir = output_path.replace(".pptx", "_groot_images")
        num_scenes = max(len(video_script.slides), 4)
        images, narrations = self._run(video_script.topic, images_dir, num_scenes, reserve_corner=reserve_corner)

        png_list_path = output_path.replace(".pptx", ".png_list")
        with open(png_list_path, "w") as f:
            json.dump(images, f)

        narrations_path = output_path.replace(".pptx", ".narrations")
        with open(narrations_path, "w") as f:
            json.dump(narrations, f)

        with open(output_path, "w") as f:
            f.write(f"groot_proxy:{png_list_path}")

        return png_list_path

    # ──────────────────────────────────────────────────────────────────────────
    # Core generation
    # ──────────────────────────────────────────────────────────────────────────

    def _run(
        self, topic: str, images_dir: str, num_scenes: int,
        scene_titles: list = None, call_llm=None, reserve_corner: bool = False,
    ) -> tuple[list[str], list[str]]:
        """Generates slides and narrations for a topic. Returns (image_paths, narrations)."""
        os.makedirs(images_dir, exist_ok=True)
        logger.info(f"GrootSlideGenerator: topic='{topic}', scenes={num_scenes}")

        stage = self.client.build_stage(topic=topic, num_scenes=num_scenes, scene_titles=scene_titles)
        stage_id = stage["id"]
        all_outlines = stage["allOutlines"]
        stage_info = stage["stageInfo"]
        agents = stage["agents"]

        images: list[str] = []
        narrations: list[str] = []
        previous_speeches: list = []

        for i, outline in enumerate(all_outlines):
            scene_title = outline.get("title", f"Scene {i + 1}")
            img_path = os.path.join(images_dir, f"slide_{i:03d}.png")
            narration_text = ""
            napkin_out = os.path.join(images_dir, f"napkin_{i:03d}.png")

            # ── Claude-only mode (Groot disabled) ────────────────────────────
            if not self.use_groot:
                logger.info(f"  [{i+1}/{len(all_outlines)}] '{scene_title}' — Claude mode")
                if call_llm:
                    elements = _claude_fallback_elements(topic, scene_title, call_llm)
                    if elements:
                        napkin_path = self._try_napkin(topic, scene_title, elements, napkin_out, call_llm=call_llm)
                        render_groot_elements_as_png(elements, img_path, scene_title, napkin_img_path=napkin_path)
                        narration_text = _claude_fallback_narration(topic, scene_title, elements, call_llm)
                        boxes = extract_element_boxes(elements)
                        if boxes:
                            boxes_path = os.path.splitext(img_path)[0] + ".boxes.json"
                            with open(boxes_path, "w") as bf:
                                json.dump(boxes, bf)
                    else:
                        render_fallback_slide(scene_title, img_path)
                else:
                    render_fallback_slide(scene_title, img_path)
                images.append(img_path)
                narrations.append(narration_text)
                continue
            # ─────────────────────────────────────────────────────────────────

            try:
                content_resp = None
                for attempt in range(3):
                    try:
                        content_resp = self.client.get_scene_content(
                            outline=outline,
                            all_outlines=all_outlines,
                            stage_id=stage_id,
                            stage_info=stage_info,
                            agents=agents,
                            pdf_images=[],
                            image_mapping={},
                        )
                        break
                    except Exception as exc:
                        logger.warning(f"  scene-content attempt {attempt+1}/3 failed for scene {i}: {exc}")
                        if attempt == 2:
                            raise

                content_obj = content_resp.get("content", {})
                effective_outline = content_resp.get("effectiveOutline", outline)
                elements = content_obj.get("elements", [])
                if reserve_corner and elements:
                    _reserve_avatar_corner(elements)

                groot_narration_ok = False
                for attempt in range(3):
                    try:
                        actions_resp = self.client.get_scene_actions(
                            outline=effective_outline,
                            all_outlines=all_outlines,
                            content=content_obj,
                            stage_id=stage_id,
                            agents=agents,
                            previous_speeches=previous_speeches,
                            user_profile={},
                        )
                        actions = actions_resp.get("scene", {}).get("actions", [])
                        speeches = GrootAPIClient.extract_speeches(actions)
                        narration_text = " ".join(speeches)
                        previous_speeches = actions_resp.get("previousSpeeches", previous_speeches)
                        groot_narration_ok = True
                        break
                    except Exception as exc:
                        logger.warning(f"  scene-actions attempt {attempt+1}/3 failed for scene {i}: {exc}")

                # Claude narration fallback
                if not groot_narration_ok and call_llm:
                    logger.info(f"  Falling back to Claude for narration: '{scene_title}'")
                    narration_text = _claude_fallback_narration(topic, scene_title, elements, call_llm)

                if elements:
                    napkin_path = self._try_napkin(topic, scene_title, elements, napkin_out, call_llm=call_llm)
                    render_groot_elements_as_png(elements, img_path, scene_title, napkin_img_path=napkin_path)
                    logger.info(f"  [{i+1}/{len(all_outlines)}] '{scene_title}' — {len(elements)} elements" + (" + Napkin visual" if napkin_path else ""))

                    # Save element bounding boxes for pen annotation
                    boxes = extract_element_boxes(elements)
                    if boxes:
                        boxes_path = os.path.splitext(img_path)[0] + ".boxes.json"
                        with open(boxes_path, "w") as bf:
                            json.dump(boxes, bf)
                        logger.info(f"    Saved {len(boxes)} element boxes for pen annotation")
                else:
                    logger.warning(f"  [{i+1}/{len(all_outlines)}] '{scene_title}' — no elements from Groot")
                    if call_llm:
                        logger.info(f"  Falling back to Claude for slide content: '{scene_title}'")
                        elements = _claude_fallback_elements(topic, scene_title, call_llm)
                        if elements:
                            napkin_path = self._try_napkin(topic, scene_title, elements, napkin_out, call_llm=call_llm)
                            render_groot_elements_as_png(elements, img_path, scene_title, napkin_img_path=napkin_path)
                            boxes = extract_element_boxes(elements)
                            if boxes:
                                boxes_path = os.path.splitext(img_path)[0] + ".boxes.json"
                                with open(boxes_path, "w") as bf:
                                    json.dump(boxes, bf)
                            if not narration_text and call_llm:
                                narration_text = _claude_fallback_narration(topic, scene_title, elements, call_llm)
                        else:
                            render_fallback_slide(scene_title, img_path)
                    else:
                        render_fallback_slide(scene_title, img_path)

            except Exception as exc:
                logger.error(f"  Scene {i} ('{scene_title}') failed: {exc}")
                if call_llm:
                    logger.info(f"  Groot fully failed — using Claude for slide + narration: '{scene_title}'")
                    try:
                        elements = _claude_fallback_elements(topic, scene_title, call_llm)
                        if elements:
                            napkin_path = self._try_napkin(topic, scene_title, elements, napkin_out, call_llm=call_llm)
                            render_groot_elements_as_png(elements, img_path, scene_title, napkin_img_path=napkin_path)
                            narration_text = _claude_fallback_narration(topic, scene_title, elements, call_llm)
                            boxes = extract_element_boxes(elements)
                            if boxes:
                                boxes_path = os.path.splitext(img_path)[0] + ".boxes.json"
                                with open(boxes_path, "w") as bf:
                                    json.dump(boxes, bf)
                        else:
                            render_fallback_slide(scene_title, img_path)
                    except Exception as claude_exc:
                        logger.error(f"  Claude fallback also failed: {claude_exc}")
                        render_fallback_slide(scene_title, img_path)
                else:
                    render_fallback_slide(scene_title, img_path)

            images.append(img_path)
            narrations.append(narration_text)

        return images, narrations
