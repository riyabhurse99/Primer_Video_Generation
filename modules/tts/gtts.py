"""
gTTS (Google Text-to-Speech) — free, no API key required.
Requires: pip install gtts
"""

from modules.tts.base import BaseTTS
from utils.logger import get_logger

logger = get_logger(__name__)


class GTTSGenerator(BaseTTS):
    """
    Free TTS using the gtts library (wraps Google Translate TTS).
    No API key required.
    Output: MP3 file at the specified path.
    """

    def __init__(self, language: str = "en", slow: bool = False):
        self.language = language
        self.slow = slow

    def generate_audio(self, text: str, output_path: str) -> str:
        try:
            from gtts import gTTS
        except ImportError:
            raise ImportError(
                "gtts is not installed. Run: pip install gtts"
            )

        logger.info(f"gTTS: generating audio — {len(text)} chars")
        tts = gTTS(text=text, lang=self.language, slow=self.slow)
        tts.save(output_path)
        logger.info(f"gTTS: saved → {output_path}")
        return output_path
