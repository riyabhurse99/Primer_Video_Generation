"""
Shared utilities for the dashboard.
Builds the dependency chain based on USE_MOCKS flag.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config.settings import USE_MOCKS, STORAGE_BACKEND, OUTPUT_DIR, TEMP_DIR


def build_dependencies():
    """Build all pipeline module instances based on current config."""
    if USE_MOCKS:
        from modules.personalization.mock import MockPersonalization
        from modules.slide_generator.mock import MockSlideGenerator
        from modules.tts.mock import MockTTS
        personalization = MockPersonalization()
        slide_generator = MockSlideGenerator()
        tts = MockTTS()
    else:
        from modules.personalization.claude import ClaudePersonalization
        from modules.slide_generator.openmaic import OpenMAICSlideGenerator
        from modules.tts.elevenlabs import ElevenLabsTTS
        personalization = ClaudePersonalization()
        slide_generator = OpenMAICSlideGenerator()
        tts = ElevenLabsTTS()

    from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
    video_assembler = FFmpegVideoAssembler(temp_dir=TEMP_DIR)

    if STORAGE_BACKEND == "s3":
        from modules.storage.s3 import S3Storage
        storage = S3Storage()
    else:
        from modules.storage.local import LocalStorage
        storage = LocalStorage(base_path=OUTPUT_DIR)

    return personalization, slide_generator, tts, video_assembler, storage
