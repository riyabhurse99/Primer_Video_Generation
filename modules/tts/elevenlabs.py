import requests
from modules.tts.base import BaseTTS
from config.settings import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
from utils.logger import get_logger

logger = get_logger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class ElevenLabsTTS(BaseTTS):

    def __init__(self):
        self.api_key = ELEVENLABS_API_KEY
        self.voice_id = ELEVENLABS_VOICE_ID
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
