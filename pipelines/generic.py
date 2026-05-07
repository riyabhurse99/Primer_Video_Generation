import os
from models.schemas import CurriculumInput, PrimerOutput, GeneratedVideo, VideoScript
from modules.personalization.base import BasePersonalization
from modules.slide_generator.base import BaseSlideGenerator
from modules.tts.base import BaseTTS
from modules.video_assembler.base import BaseVideoAssembler
from modules.storage.base import BaseStorage
from utils.pptx_to_images import pptx_to_images
from utils.logger import get_logger

logger = get_logger(__name__)


class GenericPrimerPipeline:
    """
    Generates primer videos common to all students in a course batch group.
    Input: course curriculum + group level.
    Output: set of MP4 videos organized by section.
    """

    def __init__(
        self,
        personalization: BasePersonalization,
        slide_generator: BaseSlideGenerator,
        tts: BaseTTS,
        video_assembler: BaseVideoAssembler,
        storage: BaseStorage,
        temp_dir: str = "./temp",
        output_dir: str = "./output"
    ):
        self.personalization = personalization
        self.slide_generator = slide_generator
        self.tts = tts
        self.video_assembler = video_assembler
        self.storage = storage
        self.temp_dir = temp_dir
        self.output_dir = output_dir

    def _generate_single_video(self, video_script: VideoScript, context: str) -> str:
        """Runs the full pipeline for one video. Returns final video path."""
        safe_topic = video_script.topic.replace(" ", "_").replace("/", "-")[:50]
        video_temp_dir = os.path.join(self.temp_dir, safe_topic)
        os.makedirs(video_temp_dir, exist_ok=True)

        # Step 1: Generate PPTX
        pptx_path = os.path.join(video_temp_dir, f"{safe_topic}.pptx")
        self.slide_generator.generate(video_script, pptx_path)

        # Step 2: Convert PPTX slides to PNG images
        images_dir = os.path.join(video_temp_dir, "images")
        slide_images = pptx_to_images(pptx_path, images_dir)

        # Step 3: Determine narrations
        # If a .narrations file exists (written by GrootSlideGenerator), use those.
        # They are richer than the VideoScript placeholder narrations.
        import json as _json
        narrations_path = pptx_path.replace(".pptx", ".narrations")
        if os.path.exists(narrations_path):
            with open(narrations_path) as _f:
                groot_narrations = _json.load(_f)
            logger.info(f"Using groot narrations ({len(groot_narrations)} entries)")
        else:
            groot_narrations = None

        # Groot generates one image per scene (no separate title slide).
        # Classic PPTX has title slide at index 0 → skip it.
        # Detect which mode we're in by checking for the .png_list proxy.
        fallback_narrations = [s.narration for s in video_script.slides]
        png_list_path = pptx_path.replace(".pptx", ".png_list")
        if os.path.exists(png_list_path):
            # Groot mode: all images are content images
            content_images = slide_images
            if groot_narrations:
                # Use groot narration where non-empty, else fall back to VideoScript
                script_narrations = [
                    g if g.strip() else (fallback_narrations[i] if i < len(fallback_narrations) else f"Slide {i+1}.")
                    for i, g in enumerate(groot_narrations)
                ]
            else:
                script_narrations = fallback_narrations
        else:
            # PPTX mode: skip title slide (index 0)
            content_images = slide_images[1:]
            script_narrations = fallback_narrations

        # Pair images with narrations (take whichever is shorter)
        pair_count = min(len(content_images), len(script_narrations))
        if pair_count == 0:
            logger.warning("No image/narration pairs — skipping video assembly")
            return None

        if len(content_images) != len(script_narrations):
            logger.warning(
                f"Count mismatch: {len(content_images)} images vs "
                f"{len(script_narrations)} narrations. Pairing {pair_count}."
            )

        audio_paths = []
        paired_images = []
        for i in range(pair_count):
            narration_text = script_narrations[i]
            if not narration_text.strip():
                narration_text = f"Slide {i + 1} content."
            audio_path = os.path.join(video_temp_dir, f"audio_{i:03d}.mp3")
            self.tts.generate_audio(narration_text, audio_path)
            audio_paths.append(audio_path)
            paired_images.append(content_images[i])

        # Step 4: Assemble video
        final_video_path = os.path.join(video_temp_dir, f"{safe_topic}.mp4")
        self.video_assembler.assemble(paired_images, audio_paths, final_video_path)

        # Step 5: Save to storage
        destination = f"{context}/{safe_topic}.mp4"
        stored_path = self.storage.save(final_video_path, destination)

        return stored_path

    def run(self, input: CurriculumInput) -> PrimerOutput:
        logger.info(f"=== Generic Primer Pipeline START — course={input.course}, level={input.group_level} ===")

        # Step 1: Generate full primer plan from curriculum
        plan = self.personalization.generate_generic_plan(input)
        logger.info(f"Plan ready — {len(plan.sections)} sections")

        generated_videos = []

        for section in plan.sections:
            logger.info(f"--- Section: {section.name} ({len(section.videos)} videos) ---")
            for video_script in section.videos:
                logger.info(f"  Generating video: {video_script.topic}")
                try:
                    context = f"generic/{input.course}/{input.group_level}/{section.name}"
                    video_path = self._generate_single_video(video_script, context)
                    generated_videos.append(GeneratedVideo(
                        section=section.name,
                        topic=video_script.topic,
                        video_path=video_path
                    ))
                    logger.info(f"  Done: {video_script.topic}")
                except Exception as e:
                    logger.error(f"  FAILED: {video_script.topic} — {e}")
                    # Continue with remaining videos (do not abort entire pipeline)

        logger.info(f"=== Generic Primer Pipeline COMPLETE — {len(generated_videos)} videos generated ===")

        return PrimerOutput(
            course=input.course,
            group_level=input.group_level,
            primer_type="generic",
            videos=generated_videos
        )
