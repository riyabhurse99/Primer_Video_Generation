import os
import subprocess
import json
import uuid
from modules.video_assembler.base import BaseVideoAssembler
from modules.annotation.pen_annotator import make_annotated_clip, load_element_boxes
from utils.logger import get_logger

logger = get_logger(__name__)


def _get_audio_duration(audio_path: str) -> float:
    """Get exact audio duration using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    return float(data["streams"][0]["duration"])


def _make_clip(image_path: str, audio_path: str, output_path: str, duration: float, annotate: bool = True):
    """Combine one slide image (or animation MP4) + audio into a single video clip.
    If image_path is an MP4 (animation clip), overlays the audio onto it.
    If annotate=True and a .boxes.json exists alongside a PNG, generates an animated pen-stroke clip.
    Otherwise, creates a static image loop.
    """
    # Animation MP4 — overlay audio onto existing video
    if image_path.lower().endswith(".mp4"):
        logger.info(f"  Animation clip — overlaying audio")
        cmd = [
            "ffmpeg", "-y",
            "-i", image_path,
            "-i", audio_path,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-longest",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg animation clip failed: {result.stderr}")
        return

    # Slide PNG — check for pen annotation
    element_boxes = load_element_boxes(image_path) if annotate else []
    if element_boxes:
        logger.info(f"  Pen annotation enabled — {len(element_boxes)} elements")
        make_annotated_clip(image_path, audio_path, output_path, duration, element_boxes)
        return

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-t", str(duration),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-shortest",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg clip creation failed for {image_path}: {result.stderr}")


def _concat_clips(clip_paths: list[str], output_path: str, temp_dir: str):
    """Concatenate all clips into one final MP4."""
    concat_list_path = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for clip in clip_paths:
            f.write(f"file '{os.path.abspath(clip)}'\n")

    temp_output = output_path + ".tmp.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c", "copy",
        temp_output
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {result.stderr}")

    # Only rename to final path after successful concat
    os.rename(temp_output, output_path)


class FFmpegVideoAssembler(BaseVideoAssembler):

    def __init__(self, temp_dir: str = "./temp"):
        self.temp_dir = temp_dir
        os.makedirs(temp_dir, exist_ok=True)

    def assemble(self, slide_image_paths: list[str], audio_paths: list[str], output_path: str, annotation_mask: list = None) -> str:
        if len(slide_image_paths) != len(audio_paths):
            raise ValueError("Number of slides and audio files must match")

        # Use a unique subdirectory per assembly job to avoid clip collisions
        job_id = uuid.uuid4().hex[:8]
        job_dir = os.path.join(self.temp_dir, f"assembly_{job_id}")
        os.makedirs(job_dir, exist_ok=True)

        logger.info(f"Assembling video — {len(slide_image_paths)} slides → {output_path}")
        clip_paths = []

        for i, (image_path, audio_path) in enumerate(zip(slide_image_paths, audio_paths)):
            clip_path = os.path.join(job_dir, f"clip_{i:03d}.mp4")
            annotate = annotation_mask is None or (i < len(annotation_mask) and annotation_mask[i])

            logger.info(f"  Creating clip {i+1}/{len(slide_image_paths)}: {os.path.basename(image_path)}")
            duration = _get_audio_duration(audio_path)
            _make_clip(image_path, audio_path, clip_path, duration, annotate=annotate)
            clip_paths.append(clip_path)

        logger.info("Concatenating all clips into final video...")
        _concat_clips(clip_paths, output_path, job_dir)

        # Clean up the entire job directory after successful assembly
        try:
            import shutil
            shutil.rmtree(job_dir)
        except Exception:
            pass

        logger.info(f"Video ready: {output_path}")
        return output_path
