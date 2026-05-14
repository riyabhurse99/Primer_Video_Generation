import json
import os
import time
import uuid
from modules.groot.generator import GrootSlideGenerator
from modules.tts.base import BaseTTS
from modules.video_assembler.base import BaseVideoAssembler
from modules.storage.base import BaseStorage
from utils.logger import get_logger
from utils.metrics import record, StepTimer
from utils.evals import eval_and_improve
from modules.annotation.whiteboard_sketch import generate_sketch_instructions
from modules.animation.detector import detect_animation
from modules.animation.renderer import render_animation

logger = get_logger(__name__)


class DirectPipeline:
    """
    Generates a primer video from a topic string.
    No Claude needed — Groot generates all slide content and narrations.
    """

    def __init__(
        self,
        slide_generator: GrootSlideGenerator,
        tts: BaseTTS,
        video_assembler: BaseVideoAssembler,
        storage: BaseStorage,
        temp_dir: str = "./temp",
        output_dir: str = "./output",
        call_llm=None,
        presenter_overlay: bool = False,
    ):
        self.slide_generator = slide_generator
        self.tts = tts
        self.video_assembler = video_assembler
        self.storage = storage
        self.temp_dir = temp_dir
        self.output_dir = output_dir
        self.call_llm = call_llm  # Optional: (prompt: str) -> str. None = evals skipped.
        self.presenter_overlay = presenter_overlay

    def run(self, topic: str, level: str = None, scribble: bool = False, animation: bool = False, num_scenes: int = 4, lecture_eval: bool = False) -> str:
        """Generates a video for the given topic. Returns the stored video path."""
        logger.info(f"=== Direct Pipeline START — topic='{topic}' level={level or 'generic'} num_scenes={num_scenes} lecture_eval={lecture_eval} presenter_overlay={self.presenter_overlay} ===")
        pipeline_start = time.time()

        safe_topic = topic.replace(" ", "_").replace("/", "-")[:50]
        job_id = uuid.uuid4().hex[:8]
        video_temp_dir = os.path.join(self.temp_dir, f"direct_{safe_topic}_{job_id}")
        os.makedirs(video_temp_dir, exist_ok=True)

        _avatar_path = None
        if self.presenter_overlay:
            import pathlib
            _avatar_path = str(pathlib.Path(__file__).parent.parent / "assets" / "shivank_avatar.png")

        try:
            # Step 1: Generate slides and narrations
            images_dir = os.path.join(video_temp_dir, "slides")
            with StepTimer() as slide_timer:
                images, narrations = self.slide_generator.generate_slides(
                    topic, images_dir, num_scenes=num_scenes, level=level, call_llm=self.call_llm,
                    reserve_corner=self.presenter_overlay,
                )

            if not images:
                logger.warning("No slides generated — aborting")
                record(
                    topic=topic, status="failed",
                    total_time_seconds=time.time() - pipeline_start,
                    error="No slides generated"
                )
                return None

            # Count fallback slides (empty narration = likely a fallback)
            fallback_slides = sum(1 for n in narrations if not n.strip())
            # Each scene = 2 Groot API calls (scene-content + scene-actions)
            groot_api_calls = len(images) * 2

            # Step 1b: Eval + improve loop (skipped if no LLM configured)
            # Evaluates narrations, rewrites any that score below 3, re-evals.
            # Repeats up to 3 times until all slides pass.
            narrations, evals_result = eval_and_improve(
                topic=topic,
                narrations=narrations,
                level=level,
                call_llm=self.call_llm,
                do_lecture_eval=lecture_eval,
            )

            # Step 1c: Generate whiteboard sketches (skipped if scribble is off or no LLM)
            # Analyzes each narration and produces sketch instructions for
            # context-aware drawings (flow diagrams, concept maps, etc.)
            if self.call_llm and scribble:
                for i, (image, narration) in enumerate(zip(images, narrations)):
                    if not narration.strip():
                        continue
                    sketch = generate_sketch_instructions(topic, narration, self.call_llm)
                    if sketch:
                        sketch_path = os.path.splitext(image)[0] + ".sketch.json"
                        with open(sketch_path, "w") as sf:
                            json.dump(sketch, sf)
                        logger.info(f"  Sketch saved for slide {i+1}: {sketch.get('sketch_title', '?')}")

            # Step 1d: Detect animations (skipped if no LLM configured)
            # Analyzes narrations to find slides that benefit from a Manim animation.
            # Each detected animation is rendered as a separate MP4 clip that gets
            # inserted right after the slide it relates to.
            MAX_ANIMATIONS = 1  # max 1 animation per video to keep it focused
            animation_clips = {}  # slide_index → (mp4_path, anim_spec)
            if self.call_llm and animation:
                anim_dir = os.path.join(video_temp_dir, "animations")
                os.makedirs(anim_dir, exist_ok=True)

                for i, (image, narration) in enumerate(zip(images, narrations)):
                    if len(animation_clips) >= MAX_ANIMATIONS:
                        break
                    if not narration.strip():
                        continue
                    slide_title = os.path.splitext(os.path.basename(image))[0]
                    anim_spec = detect_animation(topic, slide_title, narration, self.call_llm)
                    if anim_spec:
                        anim_mp4 = os.path.join(anim_dir, f"anim_{i:03d}.mp4")
                        result = render_animation(anim_spec, anim_mp4)
                        if result and os.path.exists(result):
                            animation_clips[i] = (result, anim_spec)
                            logger.info(f"  Animation rendered for slide {i+1}: {anim_spec.get('title', '?')}")

            # Step 2: Generate audio for each slide + animation bridge narrations
            with StepTimer() as tts_timer:
                audio_paths = []
                paired_images = []
                annotation_mask = []

                for i, (image, narration) in enumerate(zip(images, narrations)):
                    narration_text = narration or f"Slide {i + 1}."
                    audio_path = os.path.join(video_temp_dir, f"audio_{i:03d}.mp3")
                    self.tts.generate_audio(narration_text, audio_path)
                    audio_paths.append(audio_path)
                    paired_images.append(image)
                    annotation_mask.append(scribble)  # controlled by scribble flag

                    # If this slide has an animation, generate narration for the animation
                    if i in animation_clips:
                        anim_path, anim_spec = animation_clips[i]
                        anim_narration = _generate_animation_narration(
                            anim_spec, self.call_llm
                        )
                        bridge_audio = os.path.join(video_temp_dir, f"audio_{i:03d}_anim.mp3")
                        self.tts.generate_audio(anim_narration, bridge_audio)
                        audio_paths.append(bridge_audio)
                        paired_images.append(anim_path)
                        annotation_mask.append(False)  # animation clips never need scribble

            # Step 3: Assemble video
            final_video_path = os.path.join(video_temp_dir, f"{safe_topic}.mp4")
            with StepTimer() as assembly_timer:
                self.video_assembler.assemble(paired_images, audio_paths, final_video_path, annotation_mask=annotation_mask, overlay_image_path=_avatar_path)

            # Step 4: Save to output folder
            with StepTimer() as storage_timer:
                stored_path = self.storage.save(final_video_path, f"{safe_topic}.mp4")

            # Measure video duration and size
            video_size_mb = os.path.getsize(stored_path) / (1024 * 1024)
            video_duration = _get_video_duration(stored_path)
            total_time = time.time() - pipeline_start

            # Save metrics
            record(
                topic=topic,
                status="success",
                total_time_seconds=total_time,
                slide_generation_seconds=slide_timer.elapsed,
                tts_generation_seconds=tts_timer.elapsed,
                video_assembly_seconds=assembly_timer.elapsed,
                storage_seconds=storage_timer.elapsed,
                groot_api_calls=groot_api_calls,
                slides_generated=len(images),
                fallback_slides=fallback_slides,
                tts_provider=type(self.tts).__name__,
                video_duration_seconds=video_duration,
                video_size_mb=video_size_mb,
                evals=evals_result,
            )

            logger.info(f"=== Direct Pipeline COMPLETE — {stored_path} ({total_time:.1f}s) ===")
            return stored_path

        except Exception as e:
            total_time = time.time() - pipeline_start
            record(
                topic=topic,
                status="failed",
                total_time_seconds=total_time,
                error=str(e),
            )
            logger.error(f"=== Direct Pipeline FAILED — {e} ===")
            raise


import re as _re

_GREETING_PATTERNS = [
    r"^(hey\s+(everyone|guys|folks|there)[!.,\s]*)",
    r"^(hello\s+(everyone|guys|folks|there)[!.,\s]*)",
    r"^(hi\s+(everyone|guys|folks|there)[!.,\s]*)",
    r"^(alright\s+(everyone|guys|folks)[!.,\s]*)",
    r"^(welcome[!.,\s]*(back)?[!.,\s]*)",
    r"^(okay\s+so[,\s]*)",
    r"^(so[,\s]+let'?s\s+(get\s+started|dive\s+in|jump\s+in)[!.,\s]*)",
]


def _strip_greeting(narration: str) -> str:
    """Remove opening greeting from narration to avoid redundancy with intro."""
    text = narration.strip()
    for pattern in _GREETING_PATTERNS:
        text = _re.sub(pattern, "", text, count=1, flags=_re.IGNORECASE).strip()
    # Capitalize first letter after stripping
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


_INTRO_PROMPT = """You are a friendly, energetic online instructor recording a short educational video lesson.

TOPIC: "{topic}"

Write ONE warm, natural opening line (20-35 words) that an instructor would say at the very start of class, before diving into the content. It should feel spontaneous and human, not scripted.

Rules:
- Start with a casual greeting like "Hey everyone", "Hey guys", "Alright everyone", or "Hey folks"
- Mention that you hope they can hear clearly or that the session is going well
- End with a brief lead-in to the topic (e.g. "...so let's jump right into [topic].")
- Use contractions naturally (let's, we're, I'm)
- Plain text only, no markdown, no quotes around the output
- Do NOT start teaching yet — just the warm welcome + topic tease"""


def _generate_intro(topic: str, call_llm) -> str:
    """Generate a warm intro greeting spoken over the first slide."""
    if call_llm:
        try:
            prompt = _INTRO_PROMPT.format(topic=topic)
            result = call_llm(prompt).strip().strip('"').strip("'")
            if result:
                return result
        except Exception:
            pass
    # Friendly fallback template
    return (
        f"Hey everyone! I hope you can hear me clearly. "
        f"Today we're going to be talking about {topic} — let's get right into it."
    )


_ANIM_NARRATION_PROMPT = """You are narrating a short animation in a pre-recorded educational video.

ANIMATION TITLE: "{title}"
ANIMATION TYPE: {anim_type}
STEPS SHOWN: {steps_summary}

Write 2-4 sentences that a narrator would say WHILE the animation plays. Walk the viewer through what's happening on screen step by step.

Rules:
- Use present tense: "First we highlight...", "Now we swap...", "Notice how..."
- Use contractions naturally (we're, it's, that's)
- Keep it short — the animation is only 3-6 seconds
- Plain text only, no markdown
- 30-60 words"""


def _generate_animation_narration(anim_spec: dict, call_llm) -> str:
    """Generate narration text that explains the animation."""
    anim_type = anim_spec.get("animation_type", "visualization")
    title = anim_spec.get("title", "Animation")
    spec = anim_spec.get("spec", {})

    # Summarize what the animation shows
    ops = spec.get("operations", spec.get("highlights", []))
    if isinstance(ops, list):
        steps_summary = ", ".join(str(o) for o in ops[:6])
    else:
        steps_summary = str(ops)

    if not call_llm:
        return f"Let's see how this works. Watch as we walk through {title.lower()} step by step."

    try:
        prompt = _ANIM_NARRATION_PROMPT.format(
            title=title, anim_type=anim_type, steps_summary=steps_summary
        )
        narration = call_llm(prompt).strip()
        return narration
    except Exception:
        return f"Let's see how this works. Watch as we walk through {title.lower()} step by step."


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        import subprocess, json
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        return float(data["streams"][0]["duration"])
    except Exception:
        return 0.0
