"""
Scaler Primer — Video Generation Pipeline
==========================================
Entry point for running both Generic and Dynamic primer pipelines.

Usage:
    python main.py --pipeline generic --course AIML --level basic
    python main.py --pipeline dynamic --course AIML --level basic --student_id student_001
"""

import argparse
import json
import os
import sys

# Ensure project root is on the Python path so that all local imports resolve
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from config.settings import USE_MOCKS, STORAGE_BACKEND, LOCAL_STORAGE_PATH, TEMP_DIR, OUTPUT_DIR, GROOT_COOKIES, TTS_BACKEND

# ─── Load modules based on USE_MOCKS flag ──────────────────────────────────────

def build_dependencies():
    if USE_MOCKS:
        from modules.personalization.mock import MockPersonalization
        from modules.slide_generator.mock import MockSlideGenerator
        from modules.tts.mock import MockTTS
        personalization = MockPersonalization()
        slide_generator = MockSlideGenerator()
        tts = MockTTS()
        print("[MODE] Running with MOCKS — no API keys required")
    else:
        from modules.personalization.claude import ClaudePersonalization
        from modules.groot.generator import GrootSlideGenerator
        personalization = ClaudePersonalization()
        slide_generator = GrootSlideGenerator(cookies=GROOT_COOKIES)

        if TTS_BACKEND == "elevenlabs":
            from modules.tts.elevenlabs import ElevenLabsTTS
            tts = ElevenLabsTTS()
        elif TTS_BACKEND == "mock":
            from modules.tts.mock import MockTTS
            tts = MockTTS()
        else:  # default: gtts (free, no key needed)
            from modules.tts.gtts import GTTSGenerator
            tts = GTTSGenerator()

        print(f"[MODE] Running with REAL APIs — slides: groot, tts: {TTS_BACKEND}")

    from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
    video_assembler = FFmpegVideoAssembler(temp_dir=TEMP_DIR)

    if STORAGE_BACKEND == "s3":
        from modules.storage.s3 import S3Storage
        storage = S3Storage()
    else:
        from modules.storage.local import LocalStorage
        storage = LocalStorage(base_path=OUTPUT_DIR)

    return personalization, slide_generator, tts, video_assembler, storage


# ─── Generic Pipeline ──────────────────────────────────────────────────────────

def run_generic(course: str, level: str):
    from pipelines.generic import GenericPrimerPipeline
    from models.schemas import CurriculumInput

    # Load curriculum from file if available, else use empty placeholder
    curriculum_path = os.path.join(course, "curriculum.json")
    if os.path.exists(curriculum_path):
        with open(curriculum_path) as f:
            curriculum = json.load(f)
        print(f"Loaded curriculum from {curriculum_path}")
    else:
        curriculum = {
            "note": "No curriculum file found. Claude will generate based on course name only.",
            "course": course
        }
        print(f"No curriculum.json found in {course}/ — using course name only")

    personalization, slide_generator, tts, video_assembler, storage = build_dependencies()

    pipeline = GenericPrimerPipeline(
        personalization=personalization,
        slide_generator=slide_generator,
        tts=tts,
        video_assembler=video_assembler,
        storage=storage,
        temp_dir=TEMP_DIR,
        output_dir=OUTPUT_DIR
    )

    input_data = CurriculumInput(
        course=course,
        group_level=level,
        curriculum=curriculum
    )

    result = pipeline.run(input_data)

    print("\n=== RESULT ===")
    print(f"Course: {result.course} | Level: {result.group_level} | Type: {result.primer_type}")
    print(f"Videos generated: {len(result.videos)}")
    for video in result.videos:
        print(f"  [{video.section}] {video.topic} → {video.video_path}")


# ─── Dynamic Pipeline ──────────────────────────────────────────────────────────

def run_dynamic(course: str, level: str, student_id: str):
    from pipelines.dynamic import DynamicPrimerPipeline
    from models.schemas import QuestionnaireInput, QnA

    # Load curriculum for the course
    curriculum_path = os.path.join(course, "curriculum.json")
    if os.path.exists(curriculum_path):
        with open(curriculum_path) as f:
            curriculum = json.load(f)
        print(f"Loaded curriculum from {curriculum_path}")
    else:
        curriculum = {
            "note": "No curriculum file found. Claude will generate based on course name only.",
            "course": course
        }
        print(f"No curriculum.json found in {course}/ — using course name only")

    # Hardcoded sample questionnaire — replace with real student answers
    questionnaire = QuestionnaireInput(
        course=course,
        group_level=level,
        curriculum=curriculum,
        questions_and_answers=[
            QnA(
                question="Rate your Python knowledge on a scale of 1-5",
                answer="2"
            ),
            QnA(
                question="Have you used Python before? If yes, describe what you built.",
                answer="I wrote a small script to rename files but nothing beyond that."
            ),
            QnA(
                question="How familiar are you with SQL?",
                answer="Never used it. Heard of it but never tried."
            ),
            QnA(
                question="Tell us what you know about machine learning in your own words.",
                answer="I think it is about teaching computers to learn from data but I am not sure how."
            )
        ]
    )

    personalization, slide_generator, tts, video_assembler, storage = build_dependencies()

    pipeline = DynamicPrimerPipeline(
        personalization=personalization,
        slide_generator=slide_generator,
        tts=tts,
        video_assembler=video_assembler,
        storage=storage,
        temp_dir=TEMP_DIR,
        output_dir=OUTPUT_DIR
    )

    result = pipeline.run(questionnaire, student_id=student_id)

    print("\n=== RESULT ===")
    print(f"Course: {result.course} | Level: {result.group_level} | Type: {result.primer_type}")
    print(f"Videos generated: {len(result.videos)}")
    for video in result.videos:
        print(f"  [{video.section}] {video.topic} → {video.video_path}")


# ─── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scaler Primer Video Generator")
    parser.add_argument("--pipeline", choices=["generic", "dynamic"], required=True)
    parser.add_argument("--course", choices=["AIML", "DSML", "PGP", "Academy", "DevOps"], required=True)
    parser.add_argument("--level", choices=["basic", "intermediate", "advanced"], required=True)
    parser.add_argument("--student_id", default="student_001", help="Required for dynamic pipeline")

    args = parser.parse_args()

    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if args.pipeline == "generic":
        run_generic(args.course, args.level)
    else:
        run_dynamic(args.course, args.level, args.student_id)
