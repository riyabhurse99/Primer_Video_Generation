import os
import json
import base64
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from modules.tts.base import BaseTTS
from utils.logger import get_logger
import utils.run_logger as run_logger

logger = get_logger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
ELEVENLABS_TTS_URL_BASIC = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class ElevenLabsTTS(BaseTTS):
    """ElevenLabs TTS — pass api_key and voice_id explicitly, or reads from env/st.secrets."""

    def __init__(self, api_key=None, voice_id=None):
        # Read from params first, then env, then Streamlit secrets
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "") or self._st_secret("ELEVENLABS_API_KEY")
        self.voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "") or self._st_secret("ELEVENLABS_VOICE_ID")

        logger.info(
            f"ElevenLabsTTS init — "
            f"api_key={'SET' if self.api_key else 'EMPTY'} "
            f"voice_id={'SET:' + self.voice_id[:6] + '...' if self.voice_id else 'EMPTY'}"
        )

        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is not set. Add it to .env or Streamlit Cloud secrets.")
        if not self.voice_id:
            raise ValueError("ELEVENLABS_VOICE_ID is not set. Add it to .env or Streamlit Cloud secrets.")

        self.headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        _retry = Retry(
            total=3,
            backoff_factor=1,          # waits: 0s, 1s, 2s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            raise_on_status=False,
        )
        self._session = requests.Session()
        self._session.mount("https://", HTTPAdapter(max_retries=_retry))
        self._session.mount("http://", HTTPAdapter(max_retries=_retry))

    @staticmethod
    def _st_secret(key):
        try:
            import streamlit as st
            return st.secrets[key]
        except Exception:
            return ""

    def generate_audio(self, text, output_path):
        logger.info(f"Generating audio via ElevenLabs — chars={len(text)}")

        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "language_code": "en",
            "voice_settings": {
                "stability": 0.4,
                "similarity_boost": 0.6,
                "style": 0.35,
                "use_speaker_boost": True,
            },
        }

        # Try with-timestamps endpoint first; fall back to basic if not available
        url = ELEVENLABS_TTS_URL.format(voice_id=self.voice_id)
        t0 = time.perf_counter()
        response = self._session.post(url, headers=self.headers, json=payload, timeout=60)

        if response.status_code == 401:
            logger.warning("with-timestamps endpoint returned 401 — falling back to basic TTS endpoint")
            url = ELEVENLABS_TTS_URL_BASIC.format(voice_id=self.voice_id)
            response = self._session.post(url, headers=self.headers, json=payload, timeout=60)
            dur_ms = int((time.perf_counter() - t0) * 1000)
            response.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(response.content)
            audio_kb = len(response.content) // 1024
            run_logger.log_api_call(
                api="elevenlabs", endpoint="text-to-speech",
                input_summary=text[:100],
                output_summary=f"{audio_kb} KB audio (no timestamps)",
                duration_ms=dur_ms,
                chars=len(text), audio_kb=audio_kb,
            )
            logger.info(f"Audio saved (no timestamps): {output_path}")
            return output_path

        dur_ms = int((time.perf_counter() - t0) * 1000)
        response.raise_for_status()
        data = response.json()

        # Decode and save audio (returned as base64)
        audio_bytes = base64.b64decode(data["audio_base64"])
        with open(output_path, "wb") as f:
            f.write(audio_bytes)

        # Save word-level timestamps alongside the MP3
        alignment = data.get("alignment")
        if alignment:
            ts_path = os.path.splitext(output_path)[0] + ".timestamps.json"
            words = _chars_to_words(alignment)
            with open(ts_path, "w") as f:
                json.dump(words, f)
            logger.info(f"Timestamps saved: {ts_path} ({len(words)} words)")

        audio_kb = len(audio_bytes) // 1024
        run_logger.log_api_call(
            api="elevenlabs", endpoint="text-to-speech",
            input_summary=text[:100],
            output_summary=f"{audio_kb} KB audio",
            duration_ms=dur_ms,
            chars=len(text), audio_kb=audio_kb,
        )
        logger.info(f"Audio saved: {output_path}")
        return output_path


def _chars_to_words(alignment: dict) -> list[dict]:
    """
    Convert ElevenLabs character-level alignment to word-level timing.
    Returns: [{"word": "hello", "start": 0.1, "end": 0.35}, ...]
    """
    chars = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])

    words = []
    current_word = ""
    word_start = None

    for i, ch in enumerate(chars):
        if ch in (" ", "\n", "\t"):
            if current_word:
                words.append({
                    "word": current_word,
                    "start": word_start,
                    "end": ends[i - 1] if i > 0 else word_start,
                })
                current_word = ""
                word_start = None
        else:
            if word_start is None:
                word_start = starts[i] if i < len(starts) else 0.0
            current_word += ch

    # Last word
    if current_word:
        words.append({
            "word": current_word,
            "start": word_start,
            "end": ends[-1] if ends else word_start,
        })

    return words
