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

logger = get_logger(__name__)


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

    def __init__(self, cookies: str = ""):
        self.client = GrootAPIClient(cookies=cookies)

    # ──────────────────────────────────────────────────────────────────────────
    # Direct interface — used by DirectPipeline
    # ──────────────────────────────────────────────────────────────────────────

    def generate_slides(
        self, topic: str, images_dir: str, num_scenes: int = 4,
        level: str = None, call_llm=None,
    ) -> tuple[list[str], list[str]]:
        """
        Takes a topic string, generates slide PNGs and narrations.
        Returns (image_paths, narrations) directly — no files written.

        If call_llm is provided, uses the LLM to generate topic+level-aware
        scene titles instead of the hardcoded defaults.
        """
        scene_titles = generate_scene_titles(topic, num_scenes, level, call_llm)
        return self._run(topic, images_dir, num_scenes, scene_titles=scene_titles, call_llm=call_llm)

    # ──────────────────────────────────────────────────────────────────────────
    # BaseSlideGenerator interface — used by generic/dynamic pipelines
    # ──────────────────────────────────────────────────────────────────────────

    def generate(self, video_script: VideoScript, output_path: str) -> str:
        """
        Generates slides from a VideoScript and writes proxy files
        (.png_list, .narrations, stub .pptx) for the generic/dynamic pipelines.
        """
        images_dir = output_path.replace(".pptx", "_groot_images")
        num_scenes = max(len(video_script.slides), 4)
        images, narrations = self._run(video_script.topic, images_dir, num_scenes)

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
        scene_titles: list = None, call_llm=None,
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
                    render_groot_elements_as_png(elements, img_path, scene_title)
                    logger.info(f"  [{i+1}/{len(all_outlines)}] '{scene_title}' — {len(elements)} elements")

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
                            render_groot_elements_as_png(elements, img_path, scene_title)
                            boxes = extract_element_boxes(elements)
                            if boxes:
                                boxes_path = os.path.splitext(img_path)[0] + ".boxes.json"
                                with open(boxes_path, "w") as bf:
                                    json.dump(boxes, bf)
                            # Also generate narration if not already done
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
                            render_groot_elements_as_png(elements, img_path, scene_title)
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
