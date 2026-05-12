"""
Pipeline Worker
===============
Background worker functions for multiprocessing-based pipeline execution.
MUST NOT import streamlit — this module runs in a child process.
"""
import datetime
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_session_dir(base_dir: str, label: str) -> str:
    """Create and return a timestamped session directory."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"{ts}_{label}"
    session_dir = os.path.join(base_dir, session_name)
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def _write_progress(progress_path: str, step: str, detail: str = ""):
    try:
        with open(progress_path, "w") as f:
            json.dump({"step": step, "detail": detail, "ts": time.time()}, f)
    except Exception:
        pass


def run_single_topic(topic, level, el_k, el_v, llm_key, scribble, animation, result_path, progress_path):
    sys.path.insert(0, PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    try:
        from pipelines.direct import DirectPipeline
        from modules.groot.generator import GrootSlideGenerator
        from modules.tts.elevenlabs import ElevenLabsTTS
        from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
        from modules.storage.local import LocalStorage
        from config.settings import CLAUDE_MODEL, TEMP_DIR, OUTPUT_DIR, GROOT_COOKIES
        import anthropic

        _write_progress(progress_path, "Starting", f"Setting up pipeline for \"{topic}\"")

        client = anthropic.Anthropic(api_key=llm_key)
        def call_llm(prompt):
            msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=2048,
                                         messages=[{"role": "user", "content": prompt}])
            return "".join(b.text for b in msg.content if hasattr(b, "text"))

        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        session_dir = _make_session_dir(OUTPUT_DIR, "single")

        _write_progress(progress_path, "Generating slides", "Groot is building slide content...")
        pipeline = DirectPipeline(
            slide_generator=GrootSlideGenerator(cookies=GROOT_COOKIES),
            tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
            video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
            storage=LocalStorage(base_path=session_dir),
            temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
            call_llm=call_llm,
        )

        # Patch pipeline to emit progress
        original_run = pipeline.run
        def patched_run(topic, level=None, scribble=False, animation=False):
            _write_progress(progress_path, "Generating slides", "AI is building your slides...")
            return original_run(topic, level=level, scribble=scribble, animation=animation)
        pipeline.run = patched_run

        video_path = pipeline.run(topic, level=level, scribble=scribble, animation=animation)
        _write_progress(progress_path, "Done", "Video ready!")
        with open(result_path, "w") as f:
            json.dump({"status": "ok", "path": video_path}, f)
    except Exception as e:
        _write_progress(progress_path, "Error", str(e))
        with open(result_path, "w") as f:
            json.dump({"status": "error", "error": str(e)}, f)


def run_document(topic, document_content, instructions, el_k, el_v, llm_key, scribble, result_path, progress_path):
    sys.path.insert(0, PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    try:
        from pipelines.document import DocumentPipeline
        from modules.tts.elevenlabs import ElevenLabsTTS
        from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
        from modules.storage.local import LocalStorage
        from config.settings import CLAUDE_MODEL, TEMP_DIR, OUTPUT_DIR
        import anthropic

        _write_progress(progress_path, "Planning slides", "Claude is reading your document...")

        client = anthropic.Anthropic(api_key=llm_key)
        call_count = [0]

        def call_llm(prompt):
            call_count[0] += 1
            if call_count[0] == 1:
                _write_progress(progress_path, "Planning slides", "Claude is structuring your slides...")
            elif call_count[0] <= 5:
                _write_progress(progress_path, "Generating audio scripts", f"Writing narration ({call_count[0]} of ~45 calls)...")
            else:
                _write_progress(progress_path, "Generating audio scripts", f"Writing narration for slides ({call_count[0]} calls done)...")
            msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=16000,
                                         messages=[{"role": "user", "content": prompt}])
            return "".join(b.text for b in msg.content if hasattr(b, "text"))

        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        session_dir = _make_session_dir(OUTPUT_DIR, "document")

        pipeline = DocumentPipeline(
            tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
            video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
            storage=LocalStorage(base_path=session_dir),
            call_llm=call_llm, temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
        )
        video_path = pipeline.run(
            topic=topic,
            document_content=document_content,
            instructions=instructions,
            scribble=scribble,
        )
        _write_progress(progress_path, "Done", "Video ready!")
        with open(result_path, "w") as f:
            json.dump({"status": "ok", "path": video_path}, f)
    except Exception as e:
        _write_progress(progress_path, "Error", str(e))
        with open(result_path, "w") as f:
            json.dump({"status": "error", "error": str(e)}, f)


def run_personalized_primer(course, level, topics, qa_pairs, el_k, el_v, llm_key, scribble, animation, result_path, progress_path):
    sys.path.insert(0, PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    try:
        from models.schemas import QuestionnaireInput, QnA
        from pipelines.dynamic import DynamicPrimerPipeline
        from modules.groot.generator import GrootSlideGenerator
        from modules.tts.elevenlabs import ElevenLabsTTS
        from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
        from modules.storage.local import LocalStorage
        from modules.personalization.claude import ClaudePersonalization
        from config.settings import CLAUDE_MODEL, TEMP_DIR, OUTPUT_DIR, GROOT_COOKIES
        import anthropic

        _write_progress(progress_path, "Analyzing profile", "Claude is building your personalized curriculum...")

        client = anthropic.Anthropic(api_key=llm_key)
        def call_llm(prompt):
            msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=2048,
                                         messages=[{"role": "user", "content": prompt}])
            return "".join(b.text for b in msg.content if hasattr(b, "text"))

        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        safe_course = course.replace(" ", "_").replace("/", "-")[:20]
        session_dir = _make_session_dir(OUTPUT_DIR, f"primer_{safe_course}")

        curriculum = {"course": course, "topics": topics}
        qna_list = [QnA(question=q, answer=a) for q, a in qa_pairs]
        questionnaire = QuestionnaireInput(
            course=course, group_level=level,
            curriculum=curriculum, questions_and_answers=qna_list,
        )

        pipeline = DynamicPrimerPipeline(
            personalization=ClaudePersonalization(),
            slide_generator=GrootSlideGenerator(cookies=GROOT_COOKIES),
            tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
            video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
            storage=LocalStorage(base_path=session_dir),
            temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
        )

        _write_progress(progress_path, "Generating videos", "Building personalized primer videos...")
        result = pipeline.run(questionnaire, student_id="student_demo_001",
                              scribble=scribble, animation=animation)

        videos = [{"path": v.video_path, "topic": v.topic, "section": v.section}
                  for v in result.videos]
        _write_progress(progress_path, "Done", f"{len(videos)} videos ready!")
        with open(result_path, "w") as f:
            json.dump({"status": "ok", "videos": videos}, f)
    except Exception as e:
        _write_progress(progress_path, "Error", str(e))
        with open(result_path, "w") as f:
            json.dump({"status": "error", "error": str(e)}, f)
