import os
import requests
from modules.tts.base import BaseTTS
from utils.logger import get_logger

logger = get_logger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


def _read_secret(key: str) -> str:
    """Read a secret from env vars first, then st.secrets (Streamlit Cloud)."""
    value = os.getenv(key, "")
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(key, "")
    except Exception:
        return ""


class ElevenLabsTTS(BaseTTS):

    def __init__(self, api_key: str = None, voice_id: str = None):
        self.api_key = api_key or _read_secret("ELEVENLABS_API_KEY")
        self.voice_id = voice_id or _read_secret("ELEVENLABS_VOICE_ID")

        logger.info(f"ElevenLabsTTS init — api_key={'SET' if self.api_key else 'EMPTY'} voice_id={'SET:' + self.voice_id[:6] if self.voice_id else 'EMPTY'}")

        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is not set. Add it to .env or Streamlit secrets.")
        if not self.voice_id:
            raise ValueError("ELEVENLABS_VOICE_ID is not set. Add it to .env or Streamlit secrets.")

        self.headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json"
        }

    def generate_audio(self, text: str, output_path: str) -> str:
        logger.info(f"Generating audio via ElevenLabs — chars={len(text)}")

        url = ELEVENLABS_TTS_URL.format(voice_id=self.voice_id)
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "language_code": "en",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }

        response = requests.post(url, headers=self.headers, json=payload, timeout=60)
        response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(response.content)

        logger.info(f"Audio saved: {output_path}")
        return output_path
