import os
import time
from modules.groot.generator import GrootSlideGenerator
from modules.tts.base import BaseTTS
from modules.video_assembler.base import BaseVideoAssembler
from modules.storage.base import BaseStorage
from utils.logger import get_logger
from utils.metrics import record, StepTimer
from utils.evals import eval_and_improve

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
    ):
        self.slide_generator = slide_generator
        self.tts = tts
        self.video_assembler = video_assembler
        self.storage = storage
        self.temp_dir = temp_dir
        self.output_dir = output_dir
        self.call_llm = call_llm  # Optional: (prompt: str) -> str. None = evals skipped.

    def run(self, topic: str, level: str = None) -> str:
        """Generates a video for the given topic. Returns the stored video path."""
        logger.info(f"=== Direct Pipeline START — topic='{topic}' level={level or 'generic'} ===")
        pipeline_start = time.time()

        safe_topic = topic.replace(" ", "_").replace("/", "-")[:50]
        video_temp_dir = os.path.join(self.temp_dir, f"direct_{safe_topic}")
        os.makedirs(video_temp_dir, exist_ok=True)

        try:
            # Step 1: Generate slides and narrations
            images_dir = os.path.join(video_temp_dir, "slides")
            with StepTimer() as slide_timer:
                images, narrations = self.slide_generator.generate_slides(
                    topic, images_dir, level=level, call_llm=self.call_llm,
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
            )

            # Step 2: Generate audio for each slide
            with StepTimer() as tts_timer:
                audio_paths = []
                paired_images = []
                for i, (image, narration) in enumerate(zip(images, narrations)):
                    narration_text = narration or f"Slide {i + 1}."
                    audio_path = os.path.join(video_temp_dir, f"audio_{i:03d}.mp3")
                    self.tts.generate_audio(narration_text, audio_path)
                    audio_paths.append(audio_path)
                    paired_images.append(image)

            # Step 3: Assemble video
            final_video_path = os.path.join(video_temp_dir, f"{safe_topic}.mp4")
            with StepTimer() as assembly_timer:
                self.video_assembler.assemble(paired_images, audio_paths, final_video_path)

            # Step 4: Save to output folder
            with StepTimer() as storage_timer:
                stored_path = self.storage.save(final_video_path, f"direct/{safe_topic}.mp4")

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
                tts_provider="elevenlabs",
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
