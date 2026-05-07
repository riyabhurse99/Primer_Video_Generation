"""
Groot TTS — uses groot-pied.vercel.app's /api/generate/tts endpoint.
Backed by OpenAI TTS internally. No API key needed.

Available voices: alloy, echo, fable, onyx, nova, shimmer
"""

import base64
import json
import uuid
from modules.tts.base import BaseTTS
from modules.groot.client import GrootAPIClient
from utils.logger import get_logger

logger = get_logger(__name__)

AVAILABLE_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
MAX_CHUNK_CHARS = 200  # keep each TTS call short to avoid Vercel timeout


def _split_into_chunks(text: str, max_chars: int) -> list:
    """Split text into sentence-aware chunks under max_chars each."""
    sentences = text.replace("? ", "?|").replace(". ", ".|").replace("! ", "!|").split("|")
    chunks = []
    current = ""
    for sentence in sentences:
        if not sentence.strip():
            continue
        if len(current) + len(sentence) <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            current = sentence.strip()
    if current:
        chunks.append(current)
    return chunks or [text]


class GrootTTSGenerator(BaseTTS):
    """
    TTS using Groot's /api/generate/tts endpoint (OpenAI TTS internally).
    No API key required. Significantly better quality than gTTS.

    Splits long narrations into short chunks to avoid Vercel's timeout,
    then concatenates all audio chunks into one MP3.

    Args:
        voice: OpenAI TTS voice name. One of: alloy, echo, fable, onyx, nova, shimmer
        speed: Playback speed. 1.0 is normal.
        cookies: Optional Groot cookie header string.
    """

    def __init__(self, voice: str = "alloy", speed: float = 1.0, cookies: str = ""):
        if voice not in AVAILABLE_VOICES:
            raise ValueError(f"Invalid voice '{voice}'. Choose from: {AVAILABLE_VOICES}")
        self.voice = voice
        self.speed = speed
        self.client = GrootAPIClient(cookies=cookies)
        self._stage_id = "tts_session_" + uuid.uuid4().hex[:8]

    def _call_groot_tts(self, text: str) -> bytes:
        """Call Groot TTS for a single short chunk. Returns raw MP3 bytes."""
        audio_id = f"tts_{uuid.uuid4().hex[:10]}"
        raw = self.client.generate_tts(
            text=text,
            audio_id=audio_id,
            tts_provider_id="openai-tts",
            tts_voice=self.voice,
            stage_id=self._stage_id,
            tts_speed=self.speed,
        )
        # Server returns JSON with base64-encoded MP3
        if raw[:1] == b"{":
            data = json.loads(raw)
            if data.get("base64"):
                return base64.b64decode(data["base64"])
        return raw

    def generate_audio(self, text: str, output_path: str) -> str:
        logger.info(f"GrootTTS: generating audio — voice={self.voice}, {len(text)} chars")

        try:
            chunks = _split_into_chunks(text, MAX_CHUNK_CHARS)
            logger.info(f"GrootTTS: split into {len(chunks)} chunks")

            all_audio = b""
            for i, chunk in enumerate(chunks):
                logger.info(f"GrootTTS: chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
                all_audio += self._call_groot_tts(chunk)

            with open(output_path, "wb") as f:
                f.write(all_audio)

            logger.info(f"GrootTTS: saved → {output_path}")

        except Exception as e:
            logger.warning(f"GrootTTS failed ({e}) — falling back to gTTS")
            from gtts import gTTS
            gTTS(text=text, lang="en", slow=False).save(output_path)
            logger.info(f"gTTS fallback: saved → {output_path}")

        return output_path
