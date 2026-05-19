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


def _classify_prompt(prompt: str) -> str:
    """Label a Claude prompt by its purpose for the run log."""
    p = prompt.strip()
    if "strict quality evaluator" in p[:300]:
        return "eval:slide"
    if p[:80].startswith("You are rewriting a narration"):
        return "eval:improve"
    if "whole-lecture level" in p[:400] or "lecture as a whole" in p[:400]:
        return "eval:lecture"
    return "content"


def _make_logged_call_llm(client, model, max_tokens, run_logger):
    """Return a call_llm wrapper that logs timing, tokens, and purpose."""
    def call_llm(prompt):
        purpose = _classify_prompt(prompt)
        t0 = time.perf_counter()
        msg = client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        dur_ms = int((time.perf_counter() - t0) * 1000)
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        usage = getattr(msg, "usage", None)
        # For content/improve calls store more output so narration text is readable in the UI
        out_limit = 600 if purpose in ("content", "eval:improve") else 200
        run_logger.log_api_call(
            api="claude",
            endpoint="messages.create",
            input_summary=prompt[:200],
            output_summary=text[:out_limit],
            duration_ms=dur_ms,
            purpose=purpose,
            tokens_in=getattr(usage, "input_tokens", 0),
            tokens_out=getattr(usage, "output_tokens", 0),
        )
        return text
    return call_llm


def run_single_topic(topic, level, el_k, el_v, llm_key, scribble, animation, num_scenes, lecture_eval, presenter_overlay, use_groot, result_path, progress_path, log_path):
    sys.path.insert(0, PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    try:
        from pipelines.direct import DirectPipeline
        from modules.groot.generator import GrootSlideGenerator
        from modules.tts.elevenlabs import ElevenLabsTTS
        from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
        from modules.storage.local import LocalStorage
        from config.settings import CLAUDE_MODEL, TEMP_DIR, OUTPUT_DIR, GROOT_COOKIES, NAPKIN_API_KEY
        import anthropic
        import utils.run_logger as run_logger

        run_logger.initialize(log_path)
        _write_progress(progress_path, "Starting", f"Setting up pipeline for \"{topic}\"")
        run_logger.log_step("Starting", f'Setting up pipeline for "{topic}" · level={level or "generic"}')

        client = anthropic.Anthropic(api_key=llm_key)
        call_llm = _make_logged_call_llm(client, CLAUDE_MODEL, 2048, run_logger)

        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        session_dir = _make_session_dir(OUTPUT_DIR, "single")

        if presenter_overlay:
            avatar_file = os.path.join(PROJECT_ROOT, "assets", "shivank_avatar.png")
            if not os.path.exists(avatar_file):
                presenter_overlay = False
                run_logger.log_step("Warning", "Avatar file not found — presenter overlay disabled")

        slide_engine = "Claude" if not use_groot else "Groot"
        _write_progress(progress_path, "Generating slides", f"{slide_engine} is building slide content...")
        run_logger.log_step("Generating slides", f"{slide_engine} is building slide content...")
        pipeline = DirectPipeline(
            slide_generator=GrootSlideGenerator(cookies=GROOT_COOKIES, napkin_api_key=NAPKIN_API_KEY, use_groot=use_groot),
            tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
            video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
            storage=LocalStorage(base_path=session_dir),
            temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
            call_llm=call_llm,
            presenter_overlay=presenter_overlay,
        )

        # Patch pipeline to emit progress
        original_run = pipeline.run
        def patched_run(topic, level=None, scribble=False, animation=False, num_scenes=4, lecture_eval=False):
            _write_progress(progress_path, "Generating slides", f"{slide_engine} is building slide content...")
            return original_run(topic, level=level, scribble=scribble, animation=animation, num_scenes=num_scenes, lecture_eval=lecture_eval)
        pipeline.run = patched_run

        video_path = pipeline.run(topic, level=level, scribble=scribble, animation=animation, num_scenes=num_scenes, lecture_eval=lecture_eval)
        _write_progress(progress_path, "Done", "Video ready!")
        run_logger.log_step("Done", "Video ready!")
        with open(result_path, "w") as f:
            json.dump({"status": "ok", "path": video_path}, f)
    except Exception as e:
        _write_progress(progress_path, "Error", str(e))
        try:
            import utils.run_logger as run_logger
            run_logger.log_error(str(e), context="run_single_topic")
        except Exception:
            pass
        with open(result_path, "w") as f:
            json.dump({"status": "error", "error": str(e)}, f)


def run_document(topic, document_content, instructions, el_k, el_v, llm_key, scribble, max_slides, lecture_eval, presenter_overlay, result_path, progress_path, log_path):
    sys.path.insert(0, PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    try:
        from pipelines.document import DocumentPipeline
        from modules.tts.elevenlabs import ElevenLabsTTS
        from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
        from modules.storage.local import LocalStorage
        from config.settings import CLAUDE_MODEL, TEMP_DIR, OUTPUT_DIR
        import anthropic
        import utils.run_logger as run_logger

        run_logger.initialize(log_path)
        _write_progress(progress_path, "Planning slides", "Claude is reading your document...")
        run_logger.log_step("Planning slides", "Claude is reading your document...")

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
            purpose = _classify_prompt(prompt)
            t0 = time.perf_counter()
            msg = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            dur_ms = int((time.perf_counter() - t0) * 1000)
            text = "".join(b.text for b in msg.content if hasattr(b, "text"))
            usage = getattr(msg, "usage", None)
            run_logger.log_api_call(
                api="claude",
                endpoint="messages.create",
                input_summary=prompt[:200],
                output_summary=text[:200],
                duration_ms=dur_ms,
                purpose=purpose,
                call_n=call_count[0],
                tokens_in=getattr(usage, "input_tokens", 0),
                tokens_out=getattr(usage, "output_tokens", 0),
            )
            return text

        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        session_dir = _make_session_dir(OUTPUT_DIR, "document")

        if presenter_overlay:
            avatar_file = os.path.join(PROJECT_ROOT, "assets", "shivank_avatar.png")
            if not os.path.exists(avatar_file):
                presenter_overlay = False
                run_logger.log_step("Warning", "Avatar file not found — presenter overlay disabled")

        pipeline = DocumentPipeline(
            tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
            video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
            storage=LocalStorage(base_path=session_dir),
            call_llm=call_llm, temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
            presenter_overlay=presenter_overlay,
        )
        video_path = pipeline.run(
            topic=topic,
            document_content=document_content,
            instructions=instructions,
            scribble=scribble,
            max_slides=max_slides,
            lecture_eval=lecture_eval,
        )
        _write_progress(progress_path, "Done", "Video ready!")
        run_logger.log_step("Done", "Video ready!")
        with open(result_path, "w") as f:
            json.dump({"status": "ok", "path": video_path}, f)
    except Exception as e:
        _write_progress(progress_path, "Error", str(e))
        try:
            import utils.run_logger as run_logger
            run_logger.log_error(str(e), context="run_document")
        except Exception:
            pass
        with open(result_path, "w") as f:
            json.dump({"status": "error", "error": str(e)}, f)


def run_personalized_primer(course, level, topics, qa_pairs, el_k, el_v, llm_key, scribble, animation, max_videos, lecture_eval, presenter_overlay, result_path, progress_path, log_path):
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
        from config.settings import CLAUDE_MODEL, TEMP_DIR, OUTPUT_DIR, GROOT_COOKIES, NAPKIN_API_KEY
        import anthropic
        import utils.run_logger as run_logger

        run_logger.initialize(log_path)
        _write_progress(progress_path, "Analyzing profile", "Claude is building your personalized curriculum...")
        run_logger.log_step("Analyzing profile", "Claude is building your personalized curriculum...")

        client = anthropic.Anthropic(api_key=llm_key)
        call_llm = _make_logged_call_llm(client, CLAUDE_MODEL, 2048, run_logger)

        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        safe_course = course.replace(" ", "_").replace("/", "-")[:20]
        session_dir = _make_session_dir(OUTPUT_DIR, f"primer_{safe_course}")

        if presenter_overlay:
            avatar_file = os.path.join(PROJECT_ROOT, "assets", "shivank_avatar.png")
            if not os.path.exists(avatar_file):
                presenter_overlay = False
                run_logger.log_step("Warning", "Avatar file not found — presenter overlay disabled")

        curriculum = {"course": course, "topics": topics}
        qna_list = [QnA(question=q, answer=a) for q, a in qa_pairs]
        questionnaire = QuestionnaireInput(
            course=course, group_level=level,
            curriculum=curriculum, questions_and_answers=qna_list,
        )

        pipeline = DynamicPrimerPipeline(
            personalization=ClaudePersonalization(),
            slide_generator=GrootSlideGenerator(cookies=GROOT_COOKIES, napkin_api_key=NAPKIN_API_KEY),
            tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
            video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
            storage=LocalStorage(base_path=session_dir),
            temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
            call_llm=call_llm,
            presenter_overlay=presenter_overlay,
        )

        _write_progress(progress_path, "Generating videos", "Building personalized primer videos...")
        run_logger.log_step("Generating videos", "Building personalized primer videos...")
        result = pipeline.run(questionnaire, student_id="student_demo_001",
                              scribble=scribble, animation=animation, max_videos=max_videos,
                              lecture_eval=lecture_eval)

        videos = [{"path": v.video_path, "topic": v.topic, "section": v.section}
                  for v in result.videos]
        _write_progress(progress_path, "Done", f"{len(videos)} videos ready!")
        run_logger.log_step("Done", f"{len(videos)} videos ready!")
        with open(result_path, "w") as f:
            json.dump({"status": "ok", "videos": videos}, f)
    except Exception as e:
        _write_progress(progress_path, "Error", str(e))
        try:
            import utils.run_logger as run_logger
            run_logger.log_error(str(e), context="run_personalized_primer")
        except Exception:
            pass
        with open(result_path, "w") as f:
            json.dump({"status": "error", "error": str(e)}, f)


def run_slide_by_slide(slides_data, el_k, el_v, llm_key, scribble, presenter_overlay, result_path, progress_path, log_path):
    sys.path.insert(0, PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    try:
        from pipelines.slide_by_slide import SlideBySlide
        from modules.groot.generator import GrootSlideGenerator
        from modules.tts.elevenlabs import ElevenLabsTTS
        from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
        from modules.storage.local import LocalStorage
        from config.settings import CLAUDE_MODEL, TEMP_DIR, OUTPUT_DIR, NAPKIN_API_KEY
        import anthropic
        import utils.run_logger as run_logger

        run_logger.initialize(log_path)
        total = len(slides_data)
        _write_progress(progress_path, "Starting", f"Setting up {total}-slide pipeline...")
        run_logger.log_step("Starting", f"Slide-by-Slide: {total} slides")

        client = anthropic.Anthropic(api_key=llm_key)
        call_llm = _make_logged_call_llm(client, CLAUDE_MODEL, 2048, run_logger)

        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        session_dir = _make_session_dir(OUTPUT_DIR, "sbs")

        if presenter_overlay:
            avatar_file = os.path.join(PROJECT_ROOT, "assets", "shivank_avatar.png")
            if not os.path.exists(avatar_file):
                presenter_overlay = False
                run_logger.log_step("Warning", "Avatar file not found — presenter overlay disabled")

        pipeline = SlideBySlide(
            slide_generator=GrootSlideGenerator(cookies="", napkin_api_key=NAPKIN_API_KEY, use_groot=False),
            tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
            video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
            storage=LocalStorage(base_path=session_dir),
            temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
            call_llm=call_llm,
            presenter_overlay=presenter_overlay,
        )

        def on_progress(idx, total, title):
            _write_progress(progress_path, f"Slide {idx + 1}/{total}", f"Generating '{title}'...")
            run_logger.log_step(f"Slide {idx + 1}/{total}", f"Formatting and rendering '{title}'")

        video_path = pipeline.run(slides_data, scribble=scribble, on_progress=on_progress)
        _write_progress(progress_path, "Done", "Video ready!")
        run_logger.log_step("Done", "Slide-by-Slide video ready!")
        with open(result_path, "w") as f:
            json.dump({"status": "ok", "path": video_path}, f)
    except Exception as e:
        _write_progress(progress_path, "Error", str(e))
        try:
            import utils.run_logger as run_logger
            run_logger.log_error(str(e), context="run_slide_by_slide")
        except Exception:
            pass
        with open(result_path, "w") as f:
            json.dump({"status": "error", "error": str(e)}, f)
