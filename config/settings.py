import os
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    """
    Read a config value. Checks in order:
    1. Environment variables (.env locally, system env on any host)
    2. Streamlit secrets (st.secrets — only available on Streamlit Cloud)
    Falls back to default if neither has the key.
    """
    value = os.getenv(key, "")
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return default


# ─── LLM ───────────────────────────────────────────
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-6"

# ─── TTS ───────────────────────────────────────────
ELEVENLABS_API_KEY = _get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = _get("ELEVENLABS_VOICE_ID")

# ─── OPENMAIC ──────────────────────────────────────
OPENMAIC_BASE_URL = _get("OPENMAIC_BASE_URL") or "http://localhost:3000"
OPENMAIC_API_KEY = _get("OPENMAIC_API_KEY")

# ─── GROOT (reverse-engineered API) ────────────────
GROOT_COOKIES = _get("GROOT_COOKIES")

# ─── TTS BACKEND ───────────────────────────────────
# "gtts"       → free Google TTS (no key needed)
# "elevenlabs" → ElevenLabs (needs ELEVENLABS_API_KEY)
# "mock"       → silent audio (mock mode)
TTS_BACKEND = _get("TTS_BACKEND") or "gtts"

# ─── STORAGE ───────────────────────────────────────
STORAGE_BACKEND = _get("STORAGE_BACKEND") or "local"
LOCAL_STORAGE_PATH = _get("LOCAL_STORAGE_PATH") or "./output"
AWS_ACCESS_KEY_ID = _get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = _get("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET = _get("AWS_S3_BUCKET")
AWS_REGION = _get("AWS_REGION") or "ap-south-1"

# ─── PIPELINE ──────────────────────────────────────
USE_MOCKS = (_get("USE_MOCKS") or "true").lower() == "true"

# ─── PATHS ─────────────────────────────────────────
TEMP_DIR = "./temp"
OUTPUT_DIR = "./output"
