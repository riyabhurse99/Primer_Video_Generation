import subprocess
from modules.tts.base import BaseTTS
from utils.logger import get_logger

logger = get_logger(__name__)

# Approximate speaking rate: 150 words per minute
WORDS_PER_MINUTE = 150


def _estimate_duration(text: str) -> float:
    words = len(text.split())
    return max(3.0, (words / WORDS_PER_MINUTE) * 60)


class MockTTS(BaseTTS):
    """
    Generates a silent audio file of realistic duration using FFmpeg.
    Duration is estimated from the narration text length.
    No API key needed.
    Replace with ElevenLabsTTS once keys are available.
    """

    def generate_audio(self, text: str, output_path: str) -> str:
        duration = _estimate_duration(text)
        logger.info(f"[MOCK] Generating silent audio — estimated duration={duration:.1f}s")

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=mono",
            "-t", str(duration),
            "-q:a", "9",
            "-acodec", "libmp3lame",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg mock audio failed: {result.stderr}")

        logger.info(f"[MOCK] Silent audio saved: {output_path} ({duration:.1f}s)")
        return output_path
