"""
Scaler Primer — Demo Dashboard
================================
Run with: streamlit run dashboard/app.py
"""

import sys
import os
import json

# Ensure project root is on Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import streamlit as st

st.set_page_config(
    page_title="Scaler Primer — AI Video Generator",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

from config.settings import USE_MOCKS, TEMP_DIR, OUTPUT_DIR, GROOT_COOKIES, TTS_BACKEND, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID


def get_dependencies():
    if USE_MOCKS:
        from modules.personalization.mock import MockPersonalization
        from modules.slide_generator.mock import MockSlideGenerator
        from modules.tts.mock import MockTTS
        personalization = MockPersonalization()
        slide_generator = MockSlideGenerator()
        tts = MockTTS()
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

    from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
    from modules.storage.local import LocalStorage
    video_assembler = FFmpegVideoAssembler(temp_dir=TEMP_DIR)
    storage = LocalStorage(base_path=OUTPUT_DIR)
    return personalization, slide_generator, tts, video_assembler, storage


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
st.sidebar.title("Scaler Primer")
st.sidebar.markdown("AI Video Lecture Generator")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    ["Generate Video", "Generate Primer", "Video Library", "Module Tester", "Metrics"]
)

st.sidebar.markdown("---")
if USE_MOCKS:
    st.sidebar.warning("Mode: MOCK (no API keys)")
else:
    st.sidebar.success(f"Mode: LIVE | Slides: groot | TTS: elevenlabs")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: GENERATE VIDEO (no Claude needed)
# ═══════════════════════════════════════════════════════════════════════════════
if page == "Generate Video":
    st.title("Generate Video")
    st.markdown("Enter a topic and get a full lecture video — slides, narration, and audio. No API key needed.")
    st.markdown("---")

    topic = st.text_input(
        "Topic",
        placeholder="e.g. Python Lists, SQL Joins, Linear Regression",
        key="dv_topic"
    )

    if st.button("Generate Video", type="primary", key="dv_run"):
        if not topic.strip():
            st.error("Please enter a topic.")
        else:
            from pipelines.direct import DirectPipeline
            from modules.groot.generator import GrootSlideGenerator
            from modules.tts.elevenlabs import ElevenLabsTTS
            from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
            from modules.storage.local import LocalStorage

            slide_generator = GrootSlideGenerator(cookies=GROOT_COOKIES)
            try:
                el_api_key = st.secrets["ELEVENLABS_API_KEY"]
                el_voice_id = st.secrets["ELEVENLABS_VOICE_ID"]
            except Exception:
                el_api_key = ELEVENLABS_API_KEY
                el_voice_id = ELEVENLABS_VOICE_ID
            tts = ElevenLabsTTS(api_key=el_api_key, voice_id=el_voice_id)
            video_assembler = FFmpegVideoAssembler(temp_dir=TEMP_DIR)
            storage = LocalStorage(base_path=OUTPUT_DIR)

            pipeline = DirectPipeline(
                slide_generator=slide_generator,
                tts=tts,
                video_assembler=video_assembler,
                storage=storage,
                temp_dir=TEMP_DIR,
                output_dir=OUTPUT_DIR,
            )

            os.makedirs(TEMP_DIR, exist_ok=True)
            os.makedirs(OUTPUT_DIR, exist_ok=True)

            with st.spinner(f"Generating video for '{topic}'... this takes a minute."):
                video_path = pipeline.run(topic)

            if video_path and os.path.exists(video_path):
                st.success(f"Video ready!")
                with open(video_path, "rb") as f:
                    st.video(f.read())
                with open(video_path, "rb") as f:
                    st.download_button(
                        "Download MP4",
                        f.read(),
                        file_name=f"{topic.replace(' ', '_')[:50]}.mp4",
                        mime="video/mp4"
                    )
            else:
                st.error("Video generation failed. Check the logs for details.")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: GENERATE PRIMER
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Generate Primer":
    st.title("Generate Primer Videos")
    st.markdown("Choose a pipeline type and provide input to generate primer videos.")

    if USE_MOCKS:
        st.info("Running in **MOCK mode** — videos will have placeholder slides and silent audio.")

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        course = st.selectbox("Course", ["AIML", "DSML", "PGP", "Academy", "DevOps"])
    with col2:
        level = st.selectbox("Group Level", ["basic", "intermediate", "advanced"])

    pipeline_type = st.radio("Pipeline Type", ["Generic Primer", "Dynamic Primer"], horizontal=True)

    st.markdown("---")

    # ── Generic ────────────────────────────────────────────────────────────────
    if pipeline_type == "Generic Primer":
        st.subheader("Curriculum Input")
        st.caption("Enter the course curriculum. Claude will decide what sections and videos to create.")

        topics = st.text_area(
            "Enter curriculum topics (one per line)",
            value="Python Variables and Data Types\nLoops and Conditionals\nFunctions and Modules\nSQL SELECT and Joins\nBasic Statistics and Probability",
            height=150
        )
        curriculum = {
            "course": course,
            "topics": [t.strip() for t in topics.strip().split("\n") if t.strip()]
        }

        if st.button("Generate Generic Primer", type="primary"):
            from models.schemas import CurriculumInput
            from pipelines.generic import GenericPrimerPipeline

            personalization, slide_generator, tts, video_assembler, storage = get_dependencies()
            pipeline = GenericPrimerPipeline(
                personalization=personalization,
                slide_generator=slide_generator,
                tts=tts,
                video_assembler=video_assembler,
                storage=storage,
                temp_dir=TEMP_DIR,
                output_dir=OUTPUT_DIR
            )
            input_data = CurriculumInput(course=course, group_level=level, curriculum=curriculum)

            os.makedirs(TEMP_DIR, exist_ok=True)
            os.makedirs(OUTPUT_DIR, exist_ok=True)

            with st.spinner("Generating videos... This may take a minute."):
                result = pipeline.run(input_data)

            st.success(f"Generated **{len(result.videos)} videos** successfully!")
            for v in result.videos:
                st.write(f"- **[{v.section}]** {v.topic}")

    # ── Dynamic ────────────────────────────────────────────────────────────────
    else:
        st.subheader("Curriculum + Student Questionnaire")
        st.caption("Curriculum tells AI what the student needs. Questionnaire tells it what they already know.")

        student_id = st.text_input("Student ID", value="student_demo_001")

        st.markdown("**Course Curriculum:**")
        dyn_topics = st.text_area(
            "Topics the student needs to be ready for (one per line)",
            value="Python Basics\nSQL Fundamentals\nLinear Algebra\nProbability and Statistics",
            height=100,
            key="dyn_curr"
        )
        curriculum = {
            "course": course,
            "topics": [t.strip() for t in dyn_topics.strip().split("\n") if t.strip()]
        }

        st.markdown("**Student Answers:**")
        q1 = st.text_input("Rate your Python knowledge on a scale of 1-5", "2")
        q2 = st.text_input("Have you used Python before? Describe what you built.", "Wrote a small script to rename files")
        q3 = st.text_input("How familiar are you with SQL?", "Never used it")
        q4 = st.text_input("What do you know about machine learning?", "I think it is about teaching computers to learn from data")

        if st.button("Generate Dynamic Primer", type="primary"):
            from models.schemas import QuestionnaireInput, QnA
            from pipelines.dynamic import DynamicPrimerPipeline

            qna_list = [
                QnA(question="Rate your Python knowledge 1-5", answer=q1),
                QnA(question="Have you used Python before?", answer=q2),
                QnA(question="How familiar are you with SQL?", answer=q3),
                QnA(question="What do you know about ML?", answer=q4),
            ]
            # Filter empty answers
            qna_list = [q for q in qna_list if q.answer.strip()]

            if not qna_list:
                st.error("Please answer at least one question.")
            else:
                personalization, slide_generator, tts, video_assembler, storage = get_dependencies()
                pipeline = DynamicPrimerPipeline(
                    personalization=personalization,
                    slide_generator=slide_generator,
                    tts=tts,
                    video_assembler=video_assembler,
                    storage=storage,
                    temp_dir=TEMP_DIR,
                    output_dir=OUTPUT_DIR
                )
                questionnaire = QuestionnaireInput(
                    course=course, group_level=level,
                    curriculum=curriculum, questions_and_answers=qna_list
                )

                os.makedirs(TEMP_DIR, exist_ok=True)
                os.makedirs(OUTPUT_DIR, exist_ok=True)

                with st.spinner("Generating personalized videos..."):
                    result = pipeline.run(questionnaire, student_id=student_id)

                st.success(f"Generated **{len(result.videos)} personalized videos**!")
                for v in result.videos:
                    st.write(f"- **[{v.section}]** {v.topic}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: VIDEO LIBRARY
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Video Library":
    st.title("Video Library")
    st.markdown("Browse and play all generated primer videos.")
    st.markdown("---")

    output_dir = os.path.join(PROJECT_ROOT, "output")

    # Scan for videos
    all_videos = []
    if os.path.exists(output_dir):
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                if file.endswith(".mp4"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, output_dir)
                    all_videos.append({
                        "name": file.replace("_", " ").replace(".mp4", ""),
                        "path": full_path,
                        "category": os.path.dirname(rel_path),
                        "size_kb": os.path.getsize(full_path) / 1024
                    })

    if not all_videos:
        st.warning("No videos generated yet. Go to **Generate Video** to create some.")
    else:
        # Stats
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Videos", len(all_videos))
        categories = set(v["category"] for v in all_videos)
        col2.metric("Categories", len(categories))
        total_mb = sum(v["size_kb"] for v in all_videos) / 1024
        col3.metric("Total Size", f"{total_mb:.1f} MB")

        st.markdown("---")

        # Group by category
        for category in sorted(categories):
            vids_in_cat = [v for v in all_videos if v["category"] == category]
            with st.expander(f"📁 {category} ({len(vids_in_cat)} videos)", expanded=False):
                for video in vids_in_cat:
                    st.markdown(f"**{video['name']}** — {video['size_kb']:.0f} KB")
                    with open(video["path"], "rb") as f:
                        st.video(f.read())
                    st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: MODULE TESTER
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Module Tester":
    st.title("Module Tester")
    st.markdown("Test each pipeline module independently.")

    if USE_MOCKS:
        st.info("Running in **MOCK mode**. Set `USE_MOCKS=false` in `.env` to test real APIs.")

    st.markdown("---")

    tab1, tab2, tab3, tab4 = st.tabs([
        "1. Personalization (Claude)",
        "2. Slide Generator",
        "3. TTS (ElevenLabs)",
        "4. Video Assembler (FFmpeg)"
    ])

    # ── Tab 1: Personalization ─────────────────────────────────────────────────
    with tab1:
        st.subheader("Test: Personalization Engine")
        st.caption("Generates the structured script plan (sections, videos, slides, narration).")

        pers_course = st.selectbox("Course", ["AIML", "DSML", "PGP", "Academy", "DevOps"], key="t1_course")
        pers_level = st.selectbox("Level", ["basic", "intermediate", "advanced"], key="t1_level")

        pers_topics = st.text_area(
            "Curriculum topics",
            value="Python Basics\nSQL Fundamentals",
            key="t1_topics"
        )

        if st.button("Run Personalization", key="t1_run"):
            from models.schemas import CurriculumInput
            personalization, _, _, _, _ = get_dependencies()
            curriculum = {"course": pers_course, "topics": [t.strip() for t in pers_topics.split("\n") if t.strip()]}
            input_data = CurriculumInput(course=pers_course, group_level=pers_level, curriculum=curriculum)

            with st.spinner("Generating plan..."):
                plan = personalization.generate_generic_plan(input_data)

            st.success(f"Plan generated — {len(plan.sections)} sections, {sum(len(s.videos) for s in plan.sections)} videos")
            st.json(json.loads(plan.model_dump_json()))

    # ── Tab 2: Slide Generator ─────────────────────────────────────────────────
    with tab2:
        st.subheader("Test: Slide Generator")
        st.caption("Generates a PPTX from a video script. You can download it and view the rendered images.")

        slide_topic = st.text_input("Video topic", "Python Lists and Loops", key="t2_topic")
        slide_depth = st.selectbox("Depth", ["beginner", "intermediate", "advanced"], key="t2_depth")

        if st.button("Generate Slides", key="t2_run"):
            from models.schemas import VideoScript, Slide
            _, slide_generator, _, _, _ = get_dependencies()

            video_script = VideoScript(
                topic=slide_topic,
                depth=slide_depth,
                estimated_duration_minutes=10,
                slides=[
                    Slide(title=slide_topic, content=[slide_topic], narration=slide_topic),
                ]
            )

            os.makedirs(TEMP_DIR, exist_ok=True)
            safe = slide_topic.replace(" ", "_").replace("/", "-")[:50]
            pptx_path = os.path.join(TEMP_DIR, f"test_{safe}.pptx")

            with st.spinner("Generating PPTX..."):
                slide_generator.generate(video_script, pptx_path)

            st.success("PPTX generated!")

            with open(pptx_path, "rb") as f:
                st.download_button("Download PPTX", f.read(), file_name=f"{safe}.pptx")

            # Show images
            from utils.pptx_to_images import pptx_to_images
            images_dir = os.path.join(TEMP_DIR, f"test_{safe}_images")
            images = pptx_to_images(pptx_path, images_dir)

            st.write(f"**{len(images)} slides rendered:**")
            cols = st.columns(min(len(images), 5))
            for i, img in enumerate(images):
                with cols[i % 5]:
                    st.image(img, caption=f"Slide {i+1}", use_container_width=True)

    # ── Tab 3: TTS ────────────────────────────────────────────────────────────
    with tab3:
        st.subheader("Test: Text-to-Speech")
        st.caption("Generates audio narration from text. Play it directly.")

        narration = st.text_area(
            "Narration text",
            value="Welcome to this session on Python Lists. In this video, we will cover what a list is, how to create one, and how to access elements using indexing. By the end, you will be comfortable working with lists in Python.",
            height=120,
            key="t3_text"
        )

        if st.button("Generate Audio", key="t3_run"):
            _, _, tts, _, _ = get_dependencies()
            os.makedirs(TEMP_DIR, exist_ok=True)
            audio_path = os.path.join(TEMP_DIR, "test_audio.mp3")

            with st.spinner("Generating audio..."):
                tts.generate_audio(narration, audio_path)

            st.success("Audio generated!")
            with open(audio_path, "rb") as f:
                st.audio(f.read(), format="audio/mp3")

            words = len(narration.split())
            duration = max(3.0, (words / 150) * 60)
            st.write(f"Words: {words} | Estimated duration: {duration:.1f}s")

    # ── Tab 4: Video Assembler ─────────────────────────────────────────────────
    with tab4:
        st.subheader("Test: Video Assembler (FFmpeg)")
        st.caption("Combines slide images + audio into a final MP4. Run Slide Generator and TTS tests first.")

        # Check for test files
        test_images_dir = os.path.join(TEMP_DIR, "test_Python_Lists_and_Loops_images")
        test_audio = os.path.join(TEMP_DIR, "test_audio.mp3")

        has_images = os.path.isdir(test_images_dir) and any(f.endswith(".png") for f in os.listdir(test_images_dir)) if os.path.isdir(test_images_dir) else False
        has_audio = os.path.isfile(test_audio)

        if not has_images:
            st.warning("No test images found. Run **Slide Generator** tab first.")
        if not has_audio:
            st.warning("No test audio found. Run **TTS** tab first.")

        if has_images and has_audio:
            st.success("Test files ready!")

            if st.button("Assemble Video", key="t4_run"):
                from modules.video_assembler.ffmpeg import FFmpegVideoAssembler

                images = sorted([
                    os.path.join(test_images_dir, f)
                    for f in os.listdir(test_images_dir)
                    if f.endswith(".png")
                ])
                audio_paths = [test_audio] * len(images)

                assembler = FFmpegVideoAssembler(temp_dir=TEMP_DIR)
                output_path = os.path.join(TEMP_DIR, "test_assembled.mp4")

                with st.spinner(f"Assembling {len(images)} slides..."):
                    assembler.assemble(images, audio_paths, output_path)

                st.success("Video assembled!")
                with open(output_path, "rb") as f:
                    st.video(f.read())

                size_kb = os.path.getsize(output_path) / 1024
                st.write(f"File size: {size_kb:.0f} KB")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: METRICS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Metrics":
    st.title("Pipeline Metrics")
    st.markdown("Internal team dashboard — tracks every video generation run.")
    st.markdown("---")

    from utils.metrics import load_all
    records = load_all()

    if not records:
        st.warning("No metrics recorded yet. Generate a video first.")
    else:
        # ── Summary stats ──────────────────────────────────────────────────────
        total = len(records)
        successes = sum(1 for r in records if r["status"] == "success")
        failures = total - successes
        success_rate = (successes / total * 100) if total else 0

        avg_time = sum(r["total_time_seconds"] for r in records if r["status"] == "success") / max(successes, 1)
        avg_slides = sum(r["slides_generated"] for r in records if r["status"] == "success") / max(successes, 1)
        avg_duration = sum(r["video_duration_seconds"] for r in records if r["status"] == "success") / max(successes, 1)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Runs", total)
        col2.metric("Success Rate", f"{success_rate:.0f}%")
        col3.metric("Avg Generation Time", f"{avg_time:.0f}s")
        col4.metric("Avg Video Duration", f"{avg_duration:.0f}s")

        st.markdown("---")

        # ── Step breakdown (success runs only) ────────────────────────────────
        st.subheader("Step Breakdown (avg across successful runs)")

        success_records = [r for r in records if r["status"] == "success"]
        if success_records:
            step_keys = ["slide_generation_seconds", "tts_generation_seconds", "video_assembly_seconds", "storage_seconds"]
            step_labels = ["Slide Generation", "TTS / Audio", "Video Assembly", "Storage"]

            cols = st.columns(len(step_keys))
            for col, key, label in zip(cols, step_keys, step_labels):
                avg = sum(r["steps"].get(key, 0) for r in success_records) / len(success_records)
                col.metric(label, f"{avg:.1f}s")

        st.markdown("---")

        # ── Run history table ──────────────────────────────────────────────────
        st.subheader("Run History")

        table_rows = []
        for r in reversed(records):  # newest first
            evals = r.get("evals") or {}
            lecture_eval = evals.get("lecture_eval") or {}
            slide_evals = evals.get("slide_evals") or []
            flagged = sum(1 for s in slide_evals if s.get("needs_regeneration"))

            table_rows.append({
                "Timestamp": r["timestamp"],
                "Topic": r["topic"],
                "Status": r["status"],
                "Total Time (s)": r["total_time_seconds"],
                "Slide Gen (s)": r["steps"].get("slide_generation_seconds", ""),
                "TTS (s)": r["steps"].get("tts_generation_seconds", ""),
                "Assembly (s)": r["steps"].get("video_assembly_seconds", ""),
                "Slides": r["slides_generated"],
                "Eval Overall": lecture_eval.get("overall_score", "—"),
                "Eval Pass": "✓" if lecture_eval.get("pass") else ("✗" if lecture_eval else "—"),
                "Slides Flagged": flagged if slide_evals else "—",
                "Groot API Calls": r["groot_api_calls"],
                "Video Duration (s)": r["video_duration_seconds"],
                "Size (MB)": r["video_size_mb"],
                "Error": r.get("error") or "",
            })

        st.dataframe(table_rows, use_container_width=True)

        # ── Eval detail expander (per run) ────────────────────────────────────
        eval_records = [r for r in records if r.get("evals") and r["evals"].get("lecture_eval")]
        if eval_records:
            st.markdown("---")
            st.subheader("Eval Details")
            for r in reversed(eval_records):
                evals = r["evals"]
                lecture = evals.get("lecture_eval", {})
                slides = evals.get("slide_evals", [])
                level = evals.get("level", "generic")
                passed = "PASS" if lecture.get("pass") else "FAIL"
                with st.expander(f"{r['timestamp']} — {r['topic']} [{passed}]"):
                    st.markdown(f"**Level:** {level} | **Overall Score:** {lecture.get('overall_score')} / 5")
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Coverage", f"{lecture.get('coverage_score')} / 5")
                    col2.metric("Flow", f"{lecture.get('flow_score')} / 5")
                    col3.metric("Appropriateness", f"{lecture.get('appropriateness_score')} / 5")

                    if lecture.get("verdict"):
                        st.info(lecture["verdict"])

                    if lecture.get("missing_concepts"):
                        st.warning("Missing concepts: " + ", ".join(lecture["missing_concepts"]))

                    if slides:
                        st.markdown("**Per-slide scores:**")
                        for s in slides:
                            flag = " ⚠️ needs regeneration" if s.get("needs_regeneration") else ""
                            st.markdown(
                                f"- Slide {s['slide']}: "
                                f"Relevance {s.get('relevance_score')}/5 | "
                                f"Quality {s.get('quality_score')}/5{flag}  \n"
                                f"  _{s.get('reason', '')}_"
                            )
