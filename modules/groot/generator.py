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

from models.schemas import VideoScript
from modules.slide_generator.base import BaseSlideGenerator
from modules.groot.client import GrootAPIClient, generate_scene_titles
from modules.groot.renderer import render_groot_elements_as_png, render_fallback_slide
from utils.logger import get_logger

logger = get_logger(__name__)


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
        return self._run(topic, images_dir, num_scenes, scene_titles=scene_titles)

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
        scene_titles: list = None,
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
                content_resp = self.client.get_scene_content(
                    outline=outline,
                    all_outlines=all_outlines,
                    stage_id=stage_id,
                    stage_info=stage_info,
                    agents=agents,
                    pdf_images=[],
                    image_mapping={},
                )
                content_obj = content_resp.get("content", {})
                effective_outline = content_resp.get("effectiveOutline", outline)
                elements = content_obj.get("elements", [])

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
                except Exception as exc:
                    logger.warning(f"  scene-actions failed for scene {i}: {exc}")

                if elements:
                    render_groot_elements_as_png(elements, img_path, scene_title)
                    logger.info(f"  [{i+1}/{len(all_outlines)}] '{scene_title}' — {len(elements)} elements")
                else:
                    logger.warning(f"  [{i+1}/{len(all_outlines)}] '{scene_title}' — no elements, using fallback")
                    render_fallback_slide(scene_title, img_path)

            except Exception as exc:
                logger.error(f"  Scene {i} ('{scene_title}') failed: {exc}")
                render_fallback_slide(scene_title, img_path)

            images.append(img_path)
            narrations.append(narration_text)

        return images, narrations
