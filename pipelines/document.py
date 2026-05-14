"""
Document Pipeline
=================
Generates a video from long-form document content (e.g. case study briefs).
Uses Claude to plan slides and narration from the provided text, then
renders via the existing Groot renderer + TTS + FFmpeg pipeline.
"""

import json
import os
import re
import time
from modules.groot.renderer import (
    render_groot_elements_as_png,
    extract_element_boxes,
)
from modules.groot.generator import _reserve_avatar_corner
from modules.tts.base import BaseTTS
from modules.video_assembler.base import BaseVideoAssembler
from modules.storage.base import BaseStorage
from modules.annotation.whiteboard_sketch import generate_sketch_instructions
from utils.logger import get_logger
from utils.metrics import record, StepTimer

logger = get_logger(__name__)


# ── Claude prompt for slide planning ─────────────────────────────────────────

_PLAN_PROMPT = """You are an expert curriculum designer creating an educational video slide deck.

TOPIC: {topic}

INSTRUCTOR INSTRUCTIONS:
{instructions}

DOCUMENT CONTENT:
{document_content}

YOUR TASK:
Create a thorough slide-by-slide plan for a pre-recorded educational video.
Follow the INSTRUCTOR INSTRUCTIONS carefully — they specify how to treat this document,
what to include or exclude, the depth of explanation, and the tone/style expected.

For each slide, provide:
- title: short slide title (3-8 words)
- subtitle: one-line context or empty string
- bullets: list of 3-5 bullet points (concise, each under 15 words)
- narration: what the instructor says out loud (80-150 words, conversational,
  use contractions like we'll / let's / you're / it's)

RULES:
- Let the INSTRUCTOR INSTRUCTIONS drive the depth, structure, and scope entirely
- Narration should feel like a friendly senior instructor explaining over a video call
- Use "we", "let's", "you'll", "think about" — never robotic language
- Each bullet should be self-contained and scannable
- The narration should add CONTEXT and EXPLANATION beyond what's on the slide
- Make it engaging — use rhetorical questions, analogies, real-world connections
- Create as many slides as the content and instructions require
- Plain JSON only, no markdown fences

Respond with ONLY a valid JSON array:
[
  {{"title": "...", "subtitle": "...", "bullets": ["..."], "narration": "..."}},
  ...
]"""


# ── Slide element builder ────────────────────────────────────────────────────

def _slide_to_elements(slide: dict, slide_idx: int) -> list:
    """Convert a slide plan dict into groot-compatible canvas elements."""
    elements = []
    title = slide.get("title", f"Slide {slide_idx + 1}")
    subtitle = slide.get("subtitle", "")
    bullets = slide.get("bullets", [])

    # Title (large heading)
    elements.append({
        "id": f"doc_title_{slide_idx}",
        "type": "text",
        "left": 60, "top": 50, "width": 880, "height": 70,
        "content": f'<p><span style="font-size:32px;"><strong>{title}</strong></span></p>',
    })

    y = 140

    # Subtitle
    if subtitle:
        elements.append({
            "id": f"doc_subtitle_{slide_idx}",
            "type": "text",
            "left": 60, "top": y, "width": 880, "height": 40,
            "content": f'<p><span style="font-size:20px;">{subtitle}</span></p>',
        })
        y += 55

    # Bullets
    for i, bullet in enumerate(bullets):
        elements.append({
            "id": f"doc_bullet_{slide_idx}_{i}",
            "type": "text",
            "left": 80, "top": y, "width": 840, "height": 50,
            "content": f'<p><span style="font-size:18px;">\u2022 {bullet}</span></p>',
        })
        y += 60

    return elements


# ── Pipeline ─────────────────────────────────────────────────────────────────

class DocumentPipeline:
    """
    Generates a primer video from document content (problem statement,
    dataset description, approach document) using Claude for planning.
    """

    def __init__(
        self,
        tts: BaseTTS,
        video_assembler: BaseVideoAssembler,
        storage: BaseStorage,
        call_llm,
        temp_dir: str = "./temp",
        output_dir: str = "./output",
        presenter_overlay: bool = False,
    ):
        self.tts = tts
        self.video_assembler = video_assembler
        self.storage = storage
        self.call_llm = call_llm
        self.temp_dir = temp_dir
        self.output_dir = output_dir
        self.presenter_overlay = presenter_overlay

    def run(
        self,
        topic: str,
        document_content: str,
        instructions: str,
        scribble: bool = False,
        max_slides: int = None,
        lecture_eval: bool = False,
    ) -> str:
        """Generate a video from any document content.

        Args:
            topic: Title/name for the video.
            document_content: The raw document text (pasted or extracted).
            instructions: Free-form instructor instructions that guide Claude
                          on how to structure the slides (e.g. "don't give answers",
                          "summarize in 5 slides", "explain for beginners").
            scribble: Whether to apply pen annotation overlays.

        Resume-safe: re-running with the same topic skips completed slides/audio.
        """
        if not self.call_llm:
            raise ValueError("DocumentPipeline requires call_llm (Claude) — no LLM configured")

        logger.info(f"=== Document Pipeline START — topic='{topic}' ===")
        pipeline_start = time.time()

        safe_topic = topic.replace(" ", "_").replace("/", "-")[:50]
        video_temp_dir = os.path.join(self.temp_dir, f"doc_{safe_topic}")
        os.makedirs(video_temp_dir, exist_ok=True)

        _avatar_path = None
        if self.presenter_overlay:
            import pathlib
            _avatar_path = str(pathlib.Path(__file__).parent.parent / "assets" / "shivank_avatar.png")

        # Path where we cache the Claude slide plan — avoids re-calling Claude on retry
        plan_cache_path = os.path.join(video_temp_dir, "slide_plan.json")

        try:
            # ── Step 1: Claude plans the slides (cached on disk) ─────────
            with StepTimer() as plan_timer:
                if os.path.exists(plan_cache_path):
                    logger.info(f"  Resuming — loading cached slide plan from {plan_cache_path}")
                    with open(plan_cache_path) as f:
                        slide_plan = json.load(f)
                else:
                    slide_plan = self._plan_slides(topic, document_content, instructions)
                    with open(plan_cache_path, "w") as f:
                        json.dump(slide_plan, f)
            if max_slides is not None and len(slide_plan) > max_slides:
                logger.info(f"  Capping slide plan from {len(slide_plan)} to {max_slides} slides")
                slide_plan = slide_plan[:max_slides]
            logger.info(f"  {len(slide_plan)} slides planned ({plan_timer.elapsed:.1f}s)")

            # ── Step 2: Render each slide as PNG (skips existing PNGs) ───
            images_dir = os.path.join(video_temp_dir, "slides")
            os.makedirs(images_dir, exist_ok=True)

            images = []
            narrations = []

            with StepTimer() as render_timer:
                for i, slide in enumerate(slide_plan):
                    img_path = os.path.join(images_dir, f"slide_{i:03d}.png")

                    if os.path.exists(img_path):
                        logger.info(f"  Slide {i+1} already rendered — skipping PNG")
                    else:
                        elements = _slide_to_elements(slide, i)
                        if self.presenter_overlay:
                            _reserve_avatar_corner(elements)
                        render_groot_elements_as_png(elements, img_path, slide.get("title", ""))

                    # Bounding boxes for pen annotation (regenerate if missing)
                    boxes_path = os.path.splitext(img_path)[0] + ".boxes.json"
                    if not os.path.exists(boxes_path):
                        elements = _slide_to_elements(slide, i)
                        if self.presenter_overlay:
                            _reserve_avatar_corner(elements)
                        boxes = extract_element_boxes(elements)
                        if boxes:
                            with open(boxes_path, "w") as bf:
                                json.dump(boxes, bf)

                    # Whiteboard sketch for content-heavy slides
                    narration = slide.get("narration", "")
                    sketch_path = os.path.splitext(img_path)[0] + ".sketch.json"
                    if narration and len(slide.get("bullets", [])) >= 3 and not os.path.exists(sketch_path):
                        sketch = generate_sketch_instructions(topic, narration, self.call_llm)
                        if sketch:
                            with open(sketch_path, "w") as sf:
                                json.dump(sketch, sf)

                    images.append(img_path)
                    narrations.append(slide.get("narration", ""))

            logger.info(f"  Slides ready — {len(images)} total ({render_timer.elapsed:.1f}s)")

            # ── Step 1c: Eval + improve narrations before TTS ────────────
            if narrations:
                from utils.evals import eval_and_improve
                narrations, _ = eval_and_improve(
                    topic=topic,
                    narrations=narrations,
                    level=None,
                    call_llm=self.call_llm,
                    do_lecture_eval=lecture_eval,
                )

            # ── Step 3: TTS (skips already-generated audio files) ────────
            with StepTimer() as tts_timer:
                audio_paths = []
                paired_images = []
                annotation_mask = []

                # Intro greeting (no scribble)
                intro_audio = os.path.join(video_temp_dir, "audio_intro.mp3")
                if not os.path.exists(intro_audio):
                    intro_text = _generate_intro(topic, self.call_llm)
                    self.tts.generate_audio(intro_text, intro_audio)
                    logger.info("  Intro audio generated")
                else:
                    logger.info("  Intro audio already exists — skipping")
                audio_paths.append(intro_audio)
                paired_images.append(images[0])
                annotation_mask.append(False)

                # Each slide
                for i, (image, narration) in enumerate(zip(images, narrations)):
                    narration_text = narration or f"Slide {i + 1}."
                    audio_path = os.path.join(video_temp_dir, f"audio_{i:03d}.mp3")
                    if not os.path.exists(audio_path):
                        self.tts.generate_audio(narration_text, audio_path)
                    else:
                        logger.info(f"  audio_{i:03d}.mp3 already exists — skipping")
                    audio_paths.append(audio_path)
                    paired_images.append(image)
                    annotation_mask.append(scribble)

            logger.info(f"  TTS done — {len(audio_paths)} clips ({tts_timer.elapsed:.1f}s)")

            # ── Step 4: Assemble video ───────────────────────────────────
            final_video_path = os.path.join(video_temp_dir, f"{safe_topic}.mp4")
            with StepTimer() as assembly_timer:
                self.video_assembler.assemble(
                    paired_images, audio_paths, final_video_path,
                    annotation_mask=annotation_mask,
                    overlay_image_path=_avatar_path,
                )

            # ── Step 5: Save ─────────────────────────────────────────────
            with StepTimer() as storage_timer:
                stored_path = self.storage.save(final_video_path, f"{safe_topic}.mp4")

            total_time = time.time() - pipeline_start
            video_size_mb = os.path.getsize(stored_path) / (1024 * 1024)

            record(
                topic=topic,
                status="success",
                total_time_seconds=total_time,
                slide_generation_seconds=plan_timer.elapsed + render_timer.elapsed,
                tts_generation_seconds=tts_timer.elapsed,
                video_assembly_seconds=assembly_timer.elapsed,
                storage_seconds=storage_timer.elapsed,
                groot_api_calls=0,
                slides_generated=len(images),
                fallback_slides=0,
                tts_provider="elevenlabs",
                video_duration_seconds=_get_video_duration(stored_path),
                video_size_mb=video_size_mb,
            )

            logger.info(f"=== Document Pipeline COMPLETE — {stored_path} ({total_time:.1f}s) ===")
            return stored_path

        except Exception as e:
            total_time = time.time() - pipeline_start
            record(
                topic=topic,
                status="failed",
                total_time_seconds=total_time,
                error=str(e),
            )
            logger.error(f"=== Document Pipeline FAILED — {e} ===")
            raise

    def _plan_slides(self, topic, document_content, instructions):
        """Ask Claude to structure the document into slides per the instructions."""
        prompt = _PLAN_PROMPT.format(
            topic=topic,
            instructions=instructions,
            document_content=document_content,
        )
        response = self.call_llm(prompt)
        clean = response.strip()

        # Strip markdown fences
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```\s*$", "", clean).strip()

        slides = json.loads(clean)
        if not isinstance(slides, list) or len(slides) == 0:
            raise ValueError("Claude returned empty or non-list slide plan")
        return slides


# ── Helpers ──────────────────────────────────────────────────────────────────

_INTRO_PROMPT = """You are a friendly, energetic online instructor recording a video walkthrough of a case study.

TOPIC: "{topic}"

Write ONE warm, natural opening line (25-40 words) that an instructor would say at the very start. It should:
- Start with a casual greeting
- Mention what the case study is about
- Get students excited about the problem
- Plain text only, no markdown, no quotes"""


def _generate_intro(topic, call_llm):
    """Generate a warm intro greeting for the document video."""
    try:
        prompt = _INTRO_PROMPT.format(topic=topic)
        result = call_llm(prompt).strip().strip('"').strip("'")
        if result:
            return result
    except Exception:
        pass
    return (
        f"Hey everyone! I hope you can hear me clearly. "
        f"Today we're going to walk through the {topic} case study — let's get into it."
    )


def _get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    try:
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        return float(data["streams"][0]["duration"])
    except Exception:
        return 0.0
