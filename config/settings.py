import os
from dotenv import load_dotenv

load_dotenv()

# ─── LLM ───────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"

# ─── TTS ───────────────────────────────────────────
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

# ─── OPENMAIC ──────────────────────────────────────
OPENMAIC_BASE_URL = os.getenv("OPENMAIC_BASE_URL", "http://localhost:3000")
OPENMAIC_API_KEY = os.getenv("OPENMAIC_API_KEY", "")

# ─── GROOT (reverse-engineered API) ────────────────
# Paste the full Cookie header from DevTools → Headers tab of any groot request
GROOT_COOKIES = os.getenv("GROOT_COOKIES", "")

# ─── TTS BACKEND ───────────────────────────────────
# "gtts"       → free Google TTS (no key needed)
# "elevenlabs" → ElevenLabs (needs ELEVENLABS_API_KEY)
# "mock"       → silent audio (mock mode)
TTS_BACKEND = os.getenv("TTS_BACKEND", "gtts")

# ─── STORAGE ───────────────────────────────────────
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
LOCAL_STORAGE_PATH = os.getenv("LOCAL_STORAGE_PATH", "./output")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

# ─── PIPELINE ──────────────────────────────────────
USE_MOCKS = os.getenv("USE_MOCKS", "true").lower() == "true"

# ─── PATHS ─────────────────────────────────────────
TEMP_DIR = "./temp"
OUTPUT_DIR = "./output"
