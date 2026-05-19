"""
Scaler Primer — Student Portal (Demo)
======================================
Production-ready student-facing dashboard for the Scaler Primer AI system.
Run with: streamlit run dashboard/student_app.py
"""

from PIL import ImageColor
import sys
import os
import json
import time as _time
import multiprocessing

# Use "spawn" on macOS — forking after Streamlit starts ObjC threads crashes the process.
# pipeline_worker.py does not import streamlit, so spawn is safe.
if sys.platform == "darwin":
    multiprocessing.set_start_method("spawn", force=True)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import streamlit as st

st.set_page_config(
    page_title="Scaler — My Learning",
    page_icon="S",
    layout="wide",
)

from config.settings import (
    TEMP_DIR, OUTPUT_DIR, GROOT_COOKIES,
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _scan_videos() -> list[dict]:
    """Scan output directory for all generated videos, deduplicated by filename."""
    output_dir = os.path.join(PROJECT_ROOT, "output")
    vids = []
    seen_names = set()
    if not os.path.exists(output_dir):
        return vids
    for root, _dirs, files in os.walk(output_dir):
        for f in sorted(files):
            if not f.endswith(".mp4"):
                continue
            if f in seen_names:
                continue
            seen_names.add(f)
            full = os.path.join(root, f)
            rel = os.path.relpath(full, output_dir)
            cat = os.path.dirname(rel) or "general"
            size_mb = os.path.getsize(full) / (1024 * 1024)
            name = f.replace("_", " ").replace(".mp4", "").title()
            vids.append(dict(name=name, path=full, category=cat, size_mb=size_mb))
    return vids


VOICE_MAP = {
    "Shivank Sir": "7M69Y78mYqPLZS5ZZSTT",
    "Anshuman Sir": "SEUfK8UWvlGZ28kz31ts"
}

def _get_el_creds():
    """Get ElevenLabs credentials from secrets or .env."""
    try:
        return st.secrets["ELEVENLABS_API_KEY"], st.secrets["ELEVENLABS_VOICE_ID"]
    except Exception:
        pass
    try:
        from dotenv import dotenv_values
        env = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
        k = env.get("ELEVENLABS_API_KEY", "") or ELEVENLABS_API_KEY
        v = env.get("ELEVENLABS_VOICE_ID", "") or ELEVENLABS_VOICE_ID
        return k, v
    except Exception:
        return ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID


def _get_call_llm(max_tokens=2048):
    """Build a call_llm function using Anthropic Claude."""
    try:
        import anthropic
        from config.settings import CLAUDE_MODEL
        try:
            key = st.secrets["ANTHROPIC_API_KEY"]
        except Exception:
            from config.settings import ANTHROPIC_API_KEY as key
        if not key:
            return None
        client = anthropic.Anthropic(api_key=key)
        def call_llm(prompt: str) -> str:
            msg = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in msg.content if hasattr(b, "text"))
        return call_llm
    except Exception:
        return None


def _video_duration_str(path: str) -> str:
    """Get human-readable duration via ffprobe."""
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True,
        )
        dur = float(json.loads(r.stdout)["streams"][0]["duration"])
        m, s = divmod(int(dur), 60)
        return f"{m}:{s:02d}"
    except Exception:
        return ""


def _get_llm_key():
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        from config.settings import ANTHROPIC_API_KEY
        return ANTHROPIC_API_KEY


def _cancel_generation():
    """Kill the running generation process and clean up."""
    proc = st.session_state.get("gen_process")
    if proc and proc.is_alive():
        proc.terminate()
        proc.join(timeout=3)
        if proc.is_alive():
            proc.kill()
    st.session_state.gen_process = None
    st.session_state.gen_result_path = None
    st.session_state.gen_progress_path = None
    # gen_log_path intentionally kept — partial log stays visible after cancel


def _start_process(target, args):
    """Start a background process and store it in session state."""
    import multiprocessing, tempfile
    result_file = tempfile.mktemp(suffix=".json")
    progress_file = tempfile.mktemp(suffix=".json")
    log_file = tempfile.mktemp(suffix=".jsonl")
    p = multiprocessing.Process(target=target, args=args + (result_file, progress_file, log_file), daemon=True)
    p.start()
    st.session_state.gen_process = p
    st.session_state.gen_result_path = result_file
    st.session_state.gen_progress_path = progress_file
    st.session_state.gen_log_path = log_file


def _read_progress() -> dict:
    path = st.session_state.get("gen_progress_path")
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _read_result() -> dict | None:
    """Read and consume the result file once process is done."""
    path = st.session_state.get("gen_result_path")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        os.remove(path)
        return data
    except Exception:
        return None


def _read_log_events() -> list:
    from utils.run_logger import read_events
    return read_events(st.session_state.get("gen_log_path"))


def _render_log_section(events: list, title: str = "Generation Log"):
    """Render structured run events as a readable monospace log block."""
    if not events:
        st.caption("No events yet…")
        return

    rows = []
    for e in events:
        ts = e.get("ts", "")
        etype = e.get("type", "")

        if etype == "step":
            badge_color = "#4A9EFF"
            badge = "STEP"
            detail = e.get("detail", "")
            msg = f"{e.get('step', '')}" + (f" &nbsp;·&nbsp; {detail}" if detail else "")

        elif etype == "api_call":
            api = e.get("api", "")
            dur = e.get("duration_ms", 0)
            dur_str = f"{dur / 1000:.1f}s" if dur >= 500 else f"{dur}ms"
            status = e.get("status", "ok")

            if api == "claude":
                badge_color = "#EF4444" if status == "error" else "#A78BFA"
                badge = "CLAUDE"
                purpose = e.get("purpose", "content")
                tin = e.get("tokens_in", 0)
                tout = e.get("tokens_out", 0)
                full_out = (e.get("output_summary") or "").replace("<", "&lt;")
                if purpose in ("content", "eval:improve"):
                    # Show up to 300 chars — enough to read a full narration snippet
                    out_prev = full_out[:300]
                    ellipsis = "…" if len(full_out) > 300 else ""
                    msg = (f"[{purpose}] &nbsp;·&nbsp; {dur_str}"
                           f" &nbsp;·&nbsp; {tin}→{tout} tokens"
                           + (f'<br><span style="color:#94A3B8;font-size:10px;padding-left:4px">'
                              f'OUT: <i>"{out_prev}{ellipsis}"</i></span>' if out_prev else ""))
                else:
                    out_prev = full_out[:80]
                    msg = (f"[{purpose}] &nbsp;·&nbsp; {dur_str}"
                           f" &nbsp;·&nbsp; {tin}→{tout} tokens"
                           + (f' &nbsp;·&nbsp; <i>"{out_prev}…"</i>' if out_prev else ""))

            elif api == "groot":
                badge_color = "#EF4444" if status == "error" else "#F97316"
                badge = "GROOT"
                endpoint = e.get("endpoint", "")
                input_s = (e.get("input_summary") or "").replace("<", "&lt;")
                out_s = (e.get("output_summary") or "").replace("<", "&lt;")
                msg = f"{endpoint} &nbsp;·&nbsp; {dur_str} &nbsp;·&nbsp; <b>{input_s}</b> → {out_s}"

            elif api == "elevenlabs":
                badge_color = "#EF4444" if status == "error" else "#22C55E"
                badge = "EL TTS"
                chars = e.get("chars", 0)
                audio_kb = e.get("audio_kb", 0)
                msg = f"TTS &nbsp;·&nbsp; {dur_str} &nbsp;·&nbsp; {chars} chars → {audio_kb} KB audio"

            elif api == "napkin":
                badge_color = "#EF4444" if status == "error" else "#8B5CF6"
                badge = "NAPKIN"
                input_s = (e.get("input_summary") or "").replace("<", "&lt;")
                out_s = (e.get("output_summary") or "").replace("<", "&lt;")
                msg = f"{dur_str} &nbsp;·&nbsp; {input_s} → {out_s}"

            else:
                badge_color = "#94A3B8"
                badge = api.upper()
                msg = f"{e.get('endpoint', '')} &nbsp;·&nbsp; {dur_str}"

        elif etype == "eval":
            eval_type = e.get("eval_type", "")
            if eval_type == "slide":
                flagged = e.get("flagged", 0)
                total = e.get("total", 0)
                attempt = e.get("attempt", 1)
                badge_color = "#22C55E" if flagged == 0 else "#EAB308"
                badge = "EVAL"
                details = e.get("slide_details", [])
                flagged_lines = ""
                if flagged > 0:
                    for d in details:
                        if d.get("needs_regen"):
                            reason = (d.get("reason") or "").replace("<", "&lt;")
                            flagged_lines += (
                                f'<br><span style="color:#94A3B8;font-size:10px;padding-left:4px">'
                                f'slide {d["slide"]} &nbsp;rel={d.get("relevance")} qual={d.get("quality")}'
                                + (f' &nbsp;·&nbsp; <i>{reason}</i>' if reason else "")
                                + '</span>'
                            )
                msg = (f"slide eval attempt {attempt} &nbsp;·&nbsp; {total} slides &nbsp;·&nbsp; {flagged} flagged"
                       + flagged_lines)

            elif eval_type == "lecture":
                passed = e.get("passed", False)
                overall = e.get("overall_score", "?")
                cov = e.get("coverage", "?")
                flow = e.get("flow", "?")
                app = e.get("appropriateness", "?")
                verdict = (e.get("verdict") or "").replace("<", "&lt;")
                missing = e.get("missing_concepts", [])
                badge_color = "#22C55E" if passed else "#EF4444"
                badge = "EVAL"
                status_str = "✓ PASS" if passed else "✗ FAIL"
                missing_line = ""
                if missing:
                    missing_str = ", ".join(str(m) for m in missing)
                    missing_line = (f'<br><span style="color:#94A3B8;font-size:10px;padding-left:4px">'
                                    f'Missing concepts: <i>{missing_str}</i></span>')
                msg = (f"lecture eval &nbsp;·&nbsp; {status_str} &nbsp;·&nbsp; "
                       f"overall {overall} (cov={cov} flow={flow} app={app})"
                       + (f'<br><span style="color:#94A3B8;font-size:10px;padding-left:4px"><i>"{verdict}"</i></span>' if verdict else "")
                       + missing_line)
            else:
                badge_color = "#94A3B8"
                badge = "EVAL"
                msg = eval_type

        elif etype == "narration_improve":
            badge_color = "#F59E0B"
            badge = "REWRITE"
            slide_n = e.get("slide", "?")
            reason = (e.get("reason") or "").replace("<", "&lt;")
            original = (e.get("original") or "").replace("<", "&lt;")
            improved = (e.get("improved") or "").replace("<", "&lt;")
            msg = (
                f"slide {slide_n} &nbsp;·&nbsp; <span style='color:#94A3B8'>{reason}</span>"
                + (f'<br><span style="color:#64748B;font-size:10px;padding-left:4px">WAS: <i>"{original}"</i></span>' if original else "")
                + (f'<br><span style="color:#86EFAC;font-size:10px;padding-left:4px">NOW: <i>"{improved}"</i></span>' if improved else "")
            )

        elif etype == "error":
            badge_color = "#EF4444"
            badge = "ERROR"
            msg = (e.get("message") or "").replace("<", "&lt;")

        else:
            badge_color = "#94A3B8"
            badge = etype.upper()
            msg = ""

        rows.append(
            f'<div style="display:flex;align-items:flex-start;padding:5px 0;'
            f'border-bottom:1px solid rgba(255,255,255,0.05);">'
            f'<span style="color:#4B5563;min-width:72px;flex-shrink:0;font-size:10px;padding-top:1px">{ts}</span>'
            f'<span style="color:{badge_color};font-weight:700;min-width:80px;flex-shrink:0;'
            f'font-size:10px;letter-spacing:0.5px">{badge}</span>'
            f'<span style="color:#CBD5E1;flex:1;font-size:11px;line-height:1.5;word-break:break-word">{msg}</span>'
            f'</div>'
        )

    st.markdown(
        f'<div style="background:#0D1525;border:1px solid rgba(255,255,255,0.1);border-radius:8px;'
        f'padding:14px 16px;max-height:380px;overflow-y:auto;'
        f'font-family:\'Courier New\',Courier,monospace;">'
        f'<div style="font-size:9px;font-weight:700;letter-spacing:2px;color:rgba(255,255,255,0.25);'
        f'margin-bottom:10px;text-transform:uppercase">{title} &nbsp;·&nbsp; {len(events)} events</div>'
        + "".join(rows) +
        '</div>',
        unsafe_allow_html=True,
    )


def _show_generation_status(label: str):
    """
    Show animated status while a background process is running.
    Returns True if still running, False if done/cancelled.
    """
    proc = st.session_state.get("gen_process")
    if not proc:
        return False

    if proc.is_alive():
        progress = _read_progress()
        step = progress.get("step", "Starting")
        detail = progress.get("detail", "")

        st.markdown(f"""
        <div style="border-left:3px solid var(--blue);padding:20px 24px;background:var(--navy);
                    margin-bottom:16px;border-radius:0 8px 8px 0">
            <div style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
                        color:var(--blue);margin-bottom:6px">Generating</div>
            <div style="font-size:16px;font-weight:600;color:#ffffff;margin-bottom:4px">{label}</div>
            <div style="font-size:13px;color:rgba(255,255,255,0.55);margin-bottom:16px">{detail or 'Please wait…'}</div>
            <div style="background:rgba(255,255,255,0.12);height:3px;border-radius:2px;overflow:hidden;">
                <div style="background:var(--blue);height:3px;width:100%;
                    animation:indeterminate 1.5s infinite linear;
                    transform-origin:0% 50%;"></div>
            </div>
            <style>
            @keyframes indeterminate {{
                0% {{ transform: translateX(-100%) scaleX(0.5); }}
                50% {{ transform: translateX(0%) scaleX(0.5); }}
                100% {{ transform: translateX(200%) scaleX(0.5); }}
            }}
            </style>
            <div style="font-size:11px;color:rgba(255,255,255,0.35);margin-top:10px">
                Step: <strong style="color:rgba(255,255,255,0.7)">{step}</strong>
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("Generation Log", expanded=True):
            _render_log_section(_read_log_events())

        if st.button("Stop Generation", key="cancel_gen"):
            _cancel_generation()
            st.rerun()

        _time.sleep(3)
        st.rerun()
        return True

    return False


def _generate_next_question(course: str, topics: list, qa_history: list) -> str | None:
    """Ask Claude to generate the next adaptive question based on course + previous answers."""
    call_llm = _get_call_llm(max_tokens=256)
    if not call_llm:
        return None

    history_text = "\n".join(
        f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(qa_history)
    ) if qa_history else "No previous answers yet."

    prompt = f"""You are assessing a student's background before they join the {course} program.
Topics they need to cover: {", ".join(topics)}.

Previous Q&A:
{history_text}

Generate ONE short, specific question to understand what this student already knows or struggles with.
- Cover a DIFFERENT topic than what was already asked
- If they showed weakness somewhere, dig deeper into that area
- Keep it conversational, 1 sentence only
- Return ONLY the question text, no numbering, no prefix, no quotes"""

    try:
        return call_llm(prompt).strip().strip('"').strip("'")
    except Exception:
        return None


def _append_gen_result(entry: dict):
    """Prepend a completed generation session to gen_history."""
    import datetime
    entry.setdefault("timestamp", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    if "gen_history" not in st.session_state:
        st.session_state.gen_history = []
    st.session_state.gen_history.insert(0, entry)


_TYPE_LABELS = {
    "single": "Single Topic",
    "primer": "Personalized Primer",
    "document": "Case Study Document",
    "slide_by_slide": "Slide by Slide",
}
_TYPE_COLORS = {
    "single": ("#E9F1FF", "#0055FF"),
    "primer": ("#011845", "#FFFFFF"),
    "document": ("#E6F7EE", "#1A7A3C"),
    "slide_by_slide": ("#FFF4E5", "#B45309"),
}


def _render_gen_history(context: str = "generate"):
    """
    Render all generation sessions from gen_history as stacked cards.
    context: 'generate' (full detail) or 'dashboard' (compact summary).
    """
    history = st.session_state.get("gen_history", [])
    if not history:
        return

    st.markdown("<div class='s-spacer'></div>", unsafe_allow_html=True)
    st.markdown('<div class="s-h2">Generation Results</div>', unsafe_allow_html=True)

    for idx, entry in enumerate(history):
        gtype = entry.get("type", "single")
        topic = entry.get("topic", "—")
        ts = entry.get("timestamp", "")
        videos = entry.get("videos", [])
        sections = entry.get("sections")

        type_label = _TYPE_LABELS.get(gtype, gtype.title())
        bg_color, text_color = _TYPE_COLORS.get(gtype, ("#F6F6F6", "#101E37"))

        # ── Session header ────────────────────────────────────────────────────
        st.markdown(f"""
        <div style="border:1px solid var(--border);background:white;margin-bottom:4px">
            <div style="display:flex;align-items:center;gap:14px;
                        padding:16px 20px;border-bottom:1px solid var(--border-light)">
                <div style="background:{bg_color};color:{text_color};
                            font-size:10px;font-weight:700;letter-spacing:1.2px;
                            text-transform:uppercase;padding:4px 12px;flex-shrink:0">
                    {type_label}
                </div>
                <div style="flex:1">
                    <div style="font-size:15px;font-weight:600;color:var(--heading);
                                letter-spacing:-0.2px">{topic}</div>
                    <div style="font-size:11px;color:var(--muted);margin-top:2px">
                        {len(videos)} video{'s' if len(videos) != 1 else ''} &middot; {ts}
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        # ── Video list ────────────────────────────────────────────────────────
        if sections:
            # Grouped by section (Personalized Primer)
            for si, (section_name, svids) in enumerate(sections.items()):
                st.markdown(f"""
                <div style="padding:10px 20px 4px 20px;background:var(--panel);
                            font-size:10px;font-weight:700;letter-spacing:1.5px;
                            text-transform:uppercase;color:var(--muted)">
                    {section_name}
                </div>
                """, unsafe_allow_html=True)
                for vi, vid in enumerate(svids):
                    _render_video_row(vid, f"{context}_{idx}_sec{si}_v{vi}", show_player=(context == "generate"))
        else:
            for vi, vid in enumerate(videos):
                _render_video_row(vid, f"{context}_{idx}_v{vi}", show_player=True)

        st.markdown('</div>', unsafe_allow_html=True)

        # ── Remove button ─────────────────────────────────────────────────────
        if st.button("Remove this result", key=f"rm_{context}_{idx}"):
            st.session_state.gen_history.pop(idx)
            st.rerun()

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


def _render_video_row(vid: dict, key: str, show_player: bool = True):
    """Render one video row with optional inline player."""
    path = vid.get("path", "")
    vtopic = vid.get("topic", os.path.basename(path))
    section = vid.get("section", "")

    exists = path and os.path.exists(path)
    dur = _video_duration_str(path) if exists else ""
    size_str = f"{os.path.getsize(path) / (1024*1024):.1f} MB" if exists else ""

    meta_parts = [p for p in [dur, size_str, "AI Generated"] if p]
    meta_str = " · ".join(meta_parts)

    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:14px;padding:12px 20px;
                border-bottom:1px solid var(--border-light)">
        <div style="width:32px;height:32px;background:var(--ice);border:1px solid var(--border-light);
                    display:flex;align-items:center;justify-content:center;
                    color:var(--blue);font-size:12px;flex-shrink:0">&#9654;</div>
        <div style="flex:1">
            <div style="font-size:13px;font-weight:600;color:var(--heading)">{vtopic}</div>
            <div style="font-size:11px;color:var(--muted);margin-top:1px">{meta_str}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if exists and show_player:
        with st.expander(f"Watch: {vtopic}", expanded=False):
            with open(path, "rb") as f:
                st.video(f.read())
            col_dl, _ = st.columns([1, 3])
            with col_dl:
                with open(path, "rb") as f:
                    st.download_button(
                        "Download MP4", f.read(),
                        file_name=os.path.basename(path),
                        mime="video/mp4",
                        key=f"dl_{key}",
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════════════════
if "page" not in st.session_state:
    st.session_state.page = "dashboard"
if "play_video" not in st.session_state:
    st.session_state.play_video = None
if "gen_history" not in st.session_state:
    st.session_state.gen_history = []  # list of completed generation sessions, newest first
if "gen_mode" not in st.session_state:
    st.session_state.gen_mode = "Single Topic"
if "qa_step" not in st.session_state:
    st.session_state.qa_step = 0
if "qa_questions" not in st.session_state:
    st.session_state.qa_questions = []
if "qa_answers" not in st.session_state:
    st.session_state.qa_answers = []
if "qa_course" not in st.session_state:
    st.session_state.qa_course = "AIML"
if "qa_level" not in st.session_state:
    st.session_state.qa_level = "basic"
if "qa_topics" not in st.session_state:
    st.session_state.qa_topics = []
if "qa_error" not in st.session_state:
    st.session_state.qa_error = None
if "gen_scribble" not in st.session_state:
    st.session_state.gen_scribble = False
if "gen_animation" not in st.session_state:
    st.session_state.gen_animation = False
if "gen_lecture_eval" not in st.session_state:
    st.session_state.gen_lecture_eval = False
if "gen_presenter_overlay" not in st.session_state:
    st.session_state.gen_presenter_overlay = False
if "gen_use_groot" not in st.session_state:
    st.session_state.gen_use_groot = True
if "sbs_num_slides" not in st.session_state:
    st.session_state.sbs_num_slides = 3
if "sbs_slides" not in st.session_state:
    st.session_state.sbs_slides = [{"title": "", "content": "", "use_napkin": False} for _ in range(3)]
if "gen_process" not in st.session_state:
    st.session_state.gen_process = None
if "gen_result_path" not in st.session_state:
    st.session_state.gen_result_path = None
if "gen_progress_path" not in st.session_state:
    st.session_state.gen_progress_path = None
if "gen_log_path" not in st.session_state:
    st.session_state.gen_log_path = None


# ═══════════════════════════════════════════════════════════════════════════════
# DESIGN SYSTEM CSS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap');

:root {
    --blue: #0055FF;
    --navy: #011845;
    --cta: #004CE5;
    --bg: #FAFAFA;
    --text: #0B1529;
    --heading: #101E37;
    --muted: #6B7280;
    --light-muted: #9CA3AF;
    --panel: #F3F4F6;
    --ice: #EEF3FF;
    --border: #E5E7EB;
    --border-light: #F0F0F0;
    --success: #059669;
    --error-bg: #FEF2F2;
    --error: #DC2626;
}

html, body, [class*="css"] {
    font-family: 'Plus Jakarta Sans', -apple-system, sans-serif !important;
    background-color: var(--bg) !important;
    color: var(--text);
}

/* ── Hide Streamlit ── */
#MainMenu, footer, header, .stDeployButton,
[data-testid="stSidebar"],
[data-testid="stToolbar"] { display: none !important; }
.block-container { padding-top: 0 !important; max-width: 100% !important; }

/* ── Nav logo (inside first stColumn of nav row) ── */
.s-nav-logo {
    height: 28px;
    filter: brightness(0) invert(1);
}

/* ── Content area ── */
.s-content { max-width: 1120px; margin: 0 auto; padding: 0 24px; }
.s-spacer { height: 40px; }
.s-spacer-sm { height: 24px; }

/* ── Eyebrow + Heading ── */
.s-eyebrow {
    font-size: 10px; font-weight: 700;
    letter-spacing: 2.5px; text-transform: uppercase;
    color: var(--blue);
    margin: 0 0 10px 0;
}
.s-h1 {
    font-size: 34px; font-weight: 700;
    color: var(--heading);
    letter-spacing: -0.8px; line-height: 1.18;
    margin: 0 0 8px 0;
}
.s-h1 b { color: var(--blue); font-weight: 700; }
.s-h2 {
    font-size: 11px; font-weight: 700;
    letter-spacing: 2px; text-transform: uppercase;
    color: var(--muted);
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
    margin: 0 0 20px 0;
}
.s-subtitle {
    font-size: 15px;
    color: var(--muted);
    letter-spacing: -0.1px;
    line-height: 1.6;
    margin: 0;
}

/* ── Hero ── */
.s-hero {
    background: var(--navy);
    padding: 52px 60px;
    position: relative;
    overflow: hidden;
    margin-bottom: 36px;
}
.s-hero::before {
    content: '';
    position: absolute;
    right: -60px; top: -100px;
    width: 500px; height: 500px;
    background: radial-gradient(circle, rgba(0,85,255,0.12) 0%, transparent 65%);
}
.s-hero::after {
    content: '';
    position: absolute;
    left: 40%; bottom: -80px;
    width: 300px; height: 300px;
    background: radial-gradient(circle, rgba(0,85,255,0.06) 0%, transparent 70%);
}
.s-hero .s-eyebrow { color: var(--blue); }
.s-hero .s-h1 { color: white; font-size: 32px; }
.s-hero .s-subtitle { color: rgba(255,255,255,0.5); }
.s-hero-tag {
    display: inline-block;
    border: 1px solid var(--blue);
    color: var(--blue);
    font-size: 10px; font-weight: 600;
    letter-spacing: 1.5px; text-transform: uppercase;
    padding: 5px 16px;
    margin-bottom: 16px;
}

/* ── Stat row ── */
.s-stats { display: flex; gap: 1px; background: var(--border); margin-bottom: 36px; }
.s-stat {
    flex: 1; background: var(--bg);
    padding: 24px 32px;
}
.s-stat-num {
    font-size: 36px; font-weight: 700;
    color: var(--heading);
    letter-spacing: -2px; line-height: 1;
    margin-bottom: 2px;
}
.s-stat-num b { color: var(--blue); }
.s-stat-label {
    font-size: 11px; font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--muted);
}

/* ── Track card ── */
.s-track {
    background: var(--panel);
    padding: 20px 24px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 16px;
}
.s-track-icon {
    width: 40px; height: 40px;
    background: var(--navy);
    display: flex; align-items: center; justify-content: center;
    color: white; font-size: 16px; flex-shrink: 0;
}
.s-track-body { flex: 1; }
.s-track-name { font-size: 14px; font-weight: 600; color: var(--heading); }
.s-track-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.s-track-bar { background: #D7DDE8; height: 3px; margin-top: 8px; }
.s-track-fill { background: var(--blue); height: 3px; }
.s-badge {
    font-size: 9px; font-weight: 600;
    letter-spacing: 1px; text-transform: uppercase;
    padding: 4px 12px; flex-shrink: 0;
}
.s-badge-active { background: var(--ice); color: var(--blue); }
.s-badge-locked { background: var(--navy); color: white; }

/* ── Video card ── */
.s-vcard {
    border: 1px solid var(--border);
    background: var(--bg);
    margin-bottom: 16px;
    transition: border-color 0.15s;
}
.s-vcard:hover { border-color: var(--blue); }
.s-vcard-thumb {
    background: var(--ice);
    height: 140px;
    display: flex; align-items: center; justify-content: center;
    position: relative;
}
.s-vcard-play {
    width: 40px; height: 40px;
    border: 2px solid var(--blue);
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    color: var(--blue); font-size: 16px;
}
.s-vcard-dur {
    position: absolute; bottom: 8px; right: 10px;
    background: var(--blue);
    color: white; font-size: 10px;
    padding: 2px 7px; font-weight: 500;
}
.s-vcard-body { padding: 16px 20px 20px; }
.s-vcard-cat {
    font-size: 10px; font-weight: 600;
    letter-spacing: 1.5px; text-transform: uppercase;
    color: var(--blue); margin-bottom: 6px;
}
.s-vcard-title {
    font-size: 15px; font-weight: 600;
    color: var(--heading); line-height: 1.3;
    letter-spacing: -0.2px; margin-bottom: 8px;
}
.s-vcard-meta { font-size: 11px; color: var(--light-muted); }

/* ── Section divider inside results ── */
.s-section-divider {
    background: var(--panel);
    color: var(--heading);
    padding: 14px 24px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin: 32px 0 20px 0;
    display: flex;
    align-items: center;
    gap: 12px;
}
.s-section-divider .s-section-count {
    background: var(--blue);
    padding: 2px 10px;
    font-size: 10px;
}

/* ── Video in results list ── */
.s-result-card {
    border: 1px solid var(--border);
    background: var(--bg);
    margin-bottom: 12px;
    display: flex;
    align-items: stretch;
}
.s-result-num {
    background: var(--panel);
    width: 56px;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; font-weight: 700;
    color: var(--blue);
    flex-shrink: 0;
    border-right: 1px solid var(--border-light);
}
.s-result-body {
    padding: 16px 20px;
    flex: 1;
}
.s-result-title {
    font-size: 14px; font-weight: 600;
    color: var(--heading);
    letter-spacing: -0.2px;
    margin-bottom: 4px;
}
.s-result-sub { font-size: 12px; color: var(--muted); }

/* ── Form card ── */
.s-form {
    border: 1px solid var(--border);
    background: white;
    padding: 40px 48px;
    margin-bottom: 32px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
/* Section heading */
.s-section-head {
    padding: 24px 0 18px 0;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
}
.s-form-title {
    font-size: 20px; font-weight: 700;
    color: var(--heading);
    letter-spacing: -0.4px;
    margin: 0 0 6px 0;
}
.s-form-desc {
    font-size: 14px; color: var(--muted);
    line-height: 1.55;
    margin: 0;
}
.s-divider { height: 1px; background: var(--border); margin: 20px 0; }
.s-form-label {
    font-size: 11px; font-weight: 700;
    letter-spacing: 1.2px; text-transform: uppercase;
    color: var(--muted); margin-bottom: 8px;
}

/* ── Player ── */
.s-player-frame {
    background: var(--panel);
    padding: 3px 3px 0 3px;
}
.s-player-info {
    background: var(--panel);
    padding: 20px 24px;
    border: 1px solid var(--border-light);
    border-top: none;
    margin-bottom: 20px;
}
.s-player-title {
    font-size: 18px; font-weight: 600;
    color: var(--heading); letter-spacing: -0.3px;
    margin-bottom: 4px;
}
.s-player-sub { font-size: 12px; color: var(--muted); }

/* ── Info / success / error banners ── */
.s-info {
    background: var(--ice);
    border-left: 3px solid var(--blue);
    padding: 12px 18px;
    font-size: 13px; color: var(--text);
    line-height: 1.5;
    margin-bottom: 16px;
    border-radius: 0 4px 4px 0;
}
.s-success {
    background: #ECFDF5;
    border-left: 3px solid var(--success);
    padding: 12px 18px;
    font-size: 13px; color: var(--text);
    line-height: 1.5;
    margin-bottom: 16px;
    border-radius: 0 4px 4px 0;
}
.s-error {
    background: var(--error-bg);
    border-left: 3px solid var(--error);
    padding: 12px 18px;
    font-size: 13px; color: var(--text);
    line-height: 1.5;
    margin-bottom: 16px;
    border-radius: 0 4px 4px 0;
}

/* ── Empty state ── */
.s-empty {
    text-align: center; padding: 80px 40px;
    border: 1px dashed var(--border);
}
.s-empty-icon { font-size: 32px; color: #C4C4C4; margin-bottom: 12px; }
.s-empty-title { font-size: 16px; font-weight: 600; color: var(--heading); margin-bottom: 6px; }
.s-empty-sub { font-size: 13px; color: var(--muted); }

/* ── Overrides ── */
.stTextInput input, .stTextArea textarea {
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    background: white !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 14px !important;
    color: var(--text) !important;
    padding: 10px 14px !important;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04) !important;
    transition: border-color 0.15s, box-shadow 0.15s !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--blue) !important;
    box-shadow: 0 0 0 3px rgba(0,85,255,0.12) !important;
}
div[data-testid="stSelectbox"] > div {
    border-radius: 6px !important;
}
.stButton > button,
.stButton button,
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-secondary"] {
    background: var(--blue) !important; color: white !important;
    border: none !important; border-radius: 6px !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 12px !important; font-weight: 600 !important;
    letter-spacing: 0.8px !important; text-transform: uppercase !important;
    padding: 8px 20px !important; height: auto !important; min-height: 0 !important;
    line-height: 1.4 !important;
    transition: background 0.15s, transform 0.1s, box-shadow 0.15s !important;
    box-shadow: 0 1px 3px rgba(0,85,255,0.25) !important;
}
.stButton > button:hover,
.stButton button:hover,
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-secondary"]:hover {
    background: var(--cta) !important;
    box-shadow: 0 3px 8px rgba(0,85,255,0.3) !important;
    transform: translateY(-1px) !important;
}
.stButton > button:active,
.stButton button:active {
    transform: translateY(0) !important;
    box-shadow: 0 1px 3px rgba(0,85,255,0.2) !important;
}
.stDownloadButton > button {
    background: transparent !important; color: var(--blue) !important;
    border: 1.5px solid var(--blue) !important; border-radius: 6px !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 11px !important; font-weight: 600 !important;
    letter-spacing: 0.8px !important; text-transform: uppercase !important;
    padding: 7px 20px !important; height: auto !important; min-height: 0 !important;
    transition: all 0.15s !important;
}
.stDownloadButton > button:hover {
    background: var(--blue) !important; color: white !important;
    box-shadow: 0 2px 6px rgba(0,85,255,0.25) !important;
}
label { font-size: 12px !important; font-weight: 600 !important; color: var(--text) !important; letter-spacing: 0.3px !important; }
h1, h2, h3 { font-family: 'Plus Jakarta Sans', sans-serif !important; color: var(--heading) !important; }
.stRadio > div { gap: 0 !important; }
.stRadio label { font-weight: 500 !important; }

/* ── Footer ── */
.s-footer {
    border-top: 1px solid var(--border);
    padding: 24px 0;
    text-align: center;
    margin-top: 80px;
}
.s-footer span {
    font-size: 10px; color: var(--light-muted);
    letter-spacing: 2px; text-transform: uppercase;
}

/* ── Nav row ── */
/* Nav columns are a DIRECT child of the outermost stVerticalBlock (depth 1).
   Page content columns are inside a nested stVerticalBlock created by Streamlit
   for each if/elif branch (depth 2+). We exploit this depth difference:
   the high-specificity reset rule (3 attr selectors) overrides the nav rule
   (1 attr + 1 pseudo-class) for any stHorizontalBlock inside a nested
   stVerticalBlock, leaving only the true nav styled.                         */

/* 1 — nav styling (matches first stHorizontalBlock at depth 1) */
[data-testid="stHorizontalBlock"]:first-of-type {
    background: var(--navy) !important;
    margin: 0 -1rem !important;
    padding: 0 24px !important;
    gap: 0 !important;
    border-bottom: 4px solid var(--blue) !important;
    min-height: 60px !important;
    align-items: center !important;
}
[data-testid="stHorizontalBlock"]:first-of-type > div {
    padding: 0 !important;
    display: flex !important;
    align-items: center !important;
}

/* 2 — reset: any stHorizontalBlock nested inside 2+ stVerticalBlocks
   (higher specificity → wins over the nav rule for page content columns) */
[data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] {
    background: transparent !important;
    border-bottom: none !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    gap: 1rem !important;
    align-items: stretch !important;
}
[data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] > div {
    padding: revert !important;
    display: revert !important;
    align-items: revert !important;
}
/* restore normal button styles inside page content columns */
[data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] .stButton > button,
[data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] .stButton button,
[data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] [data-testid="stBaseButton-primary"],
[data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] [data-testid="stBaseButton-secondary"] {
    background: var(--blue) !important;
    color: white !important;
    border: none !important;
    border-bottom: none !important;
    border-radius: 0 !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    letter-spacing: 1px !important;
    text-transform: uppercase !important;
    padding: 7px 20px !important;
    height: auto !important;
    min-height: 0 !important;
    width: auto !important;
    white-space: nowrap !important;
    transition: background 0.15s !important;
}
[data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] .stButton > button:hover,
[data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] .stButton button:hover {
    background: var(--cta) !important;
    color: white !important;
}

/* 3 — nav button styles (applied only to the true nav bar) */
[data-testid="stHorizontalBlock"]:first-of-type .stButton > button,
[data-testid="stHorizontalBlock"]:first-of-type .stButton button,
[data-testid="stHorizontalBlock"]:first-of-type [data-testid="stBaseButton-secondary"],
[data-testid="stHorizontalBlock"]:first-of-type [data-testid="stBaseButton-primary"] {
    background: transparent !important;
    color: rgba(255,255,255,0.65) !important;
    border: none !important;
    border-bottom: 3px solid transparent !important;
    border-radius: 0 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    letter-spacing: 0.3px !important;
    text-transform: none !important;
    padding: 0 20px !important;
    height: 56px !important;
    width: 100% !important;
    white-space: nowrap !important;
    transition: color 0.15s !important;
}
[data-testid="stHorizontalBlock"]:first-of-type .stButton > button:hover,
[data-testid="stHorizontalBlock"]:first-of-type .stButton button:hover,
[data-testid="stHorizontalBlock"]:first-of-type [data-testid="stBaseButton-secondary"]:hover,
[data-testid="stHorizontalBlock"]:first-of-type [data-testid="stBaseButton-primary"]:hover {
    color: white !important;
    background: transparent !important;
}

/* ── Radio (library filter — keep default look, just clean up) ── */
.stRadio > div { gap: 8px !important; }
.stRadio > div > label {
    padding: 6px 14px !important;
    border: 1px solid var(--border) !important;
    background: var(--panel) !important;
    color: var(--text) !important;
    font-size: 12px !important;
    font-weight: 500 !important;
    cursor: pointer !important;
}
.stRadio > div > label:has(input:checked) {
    background: var(--blue) !important;
    color: white !important;
    border-color: var(--blue) !important;
}

/* ── Mode selector buttons ── */
.s-mode-btn > button,
.s-mode-btn .stButton > button,
.s-mode-btn [data-testid="stBaseButton-secondary"] {
    background: var(--panel) !important;
    color: var(--muted) !important;
    border: 1px solid var(--border) !important;
    border-radius: 0 !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    letter-spacing: 0.8px !important;
    text-transform: uppercase !important;
    padding: 12px 16px !important;
    height: auto !important;
    width: 100% !important;
    transition: all 0.15s !important;
}
.s-mode-btn > button:hover,
.s-mode-btn .stButton > button:hover {
    background: var(--ice) !important;
    color: var(--blue) !important;
    border-color: var(--blue) !important;
}
.s-mode-btn-active > button,
.s-mode-btn-active .stButton > button,
.s-mode-btn-active [data-testid="stBaseButton-secondary"] {
    background: var(--blue) !important;
    color: white !important;
    border: 1px solid var(--blue) !important;
}

/* ── Toggle switch — always white (all toggles in student_app live in dark navy panels) ── */
[data-testid="stToggle"],
[data-testid="stCheckbox"] { margin: 0 !important; }
[data-testid="stToggle"] p,
[data-testid="stToggle"] span,
[data-testid="stToggle"] label,
[data-testid="stToggle"] div > p,
[data-testid="stCheckbox"] p,
[data-testid="stCheckbox"] span,
[data-testid="stCheckbox"] label,
[data-testid="stCheckbox"] div > p,
.stToggle p,
.stToggle label,
.stCheckbox p,
.stCheckbox label {
    font-size: 13px !important;
    font-weight: 500 !important;
    color: #ffffff !important;
    background: transparent !important;
    letter-spacing: 0 !important;
    text-transform: none !important;
}

/* ── Video options panel ── */
.s-video-options {
    background: var(--navy);
    border: 1px solid rgba(255,255,255,0.1);
    border-left: 3px solid var(--blue);
    border-radius: 8px;
    padding: 14px 20px 14px 20px;
    margin: 16px 0 4px 0;
}
.s-video-options-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: rgba(255,255,255,0.4);
    margin-bottom: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    padding-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# NAVIGATION BAR
# ═══════════════════════════════════════════════════════════════════════════════
import base64 as _b64
with open(os.path.join(PROJECT_ROOT, "assets", "logo_white.png"), "rb") as _lf:
    _logo_b64 = _b64.b64encode(_lf.read()).decode()

# Nav: logo in first col (wide), then spacer, then three nav buttons.
# CSS targets div[data-testid="stHorizontalBlock"]:first-of-type to style
# the whole row as the navy bar without any :has() hacks.
_pg = st.session_state.page
_nc_logo, _nc_sp, _nc1, _nc2, _nc3, _nc4 = st.columns([2, 3, 1, 1, 1, 1])

# Inject active-state highlight for whichever nav button matches current page
_nav_label_map = {
    "dashboard": "Dashboard", "generate": "Generate",
    "library": "Library", "metrics": "Metrics"
}
_active_nav_label = _nav_label_map.get(_pg, "Dashboard")
st.markdown(f"""
<style>
[data-testid="stBaseButton-secondary"][aria-label="Dashboard"],
[data-testid="stBaseButton-secondary"][aria-label="Generate"],
[data-testid="stBaseButton-secondary"][aria-label="Library"],
[data-testid="stBaseButton-secondary"][aria-label="Metrics"] {{
    border-bottom: 3px solid transparent !important;
}}
[data-testid="stBaseButton-secondary"][aria-label="{_active_nav_label}"] {{
    color: white !important;
    border-bottom: 3px solid var(--blue) !important;
}}
</style>
""", unsafe_allow_html=True)
with _nc_logo:
    st.markdown(
        f'<img src="data:image/png;base64,{_logo_b64}" class="s-nav-logo" alt="Scaler" '
        f'style="height:28px;filter:brightness(0) invert(1);margin-top:16px">',
        unsafe_allow_html=True,
    )
with _nc1:
    if st.button("Dashboard", key="_nav1", width='stretch'):
        st.session_state.page = "dashboard"
        st.rerun()
with _nc2:
    if st.button("Generate", key="_nav2", width='stretch'):
        st.session_state.page = "generate"
        st.rerun()
with _nc3:
    if st.button("Library", key="_nav3", width='stretch'):
        st.session_state.page = "library"
        st.rerun()
with _nc4:
    if st.button("Metrics", key="_nav4", width='stretch'):
        st.session_state.page = "metrics"
        st.rerun()

st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

page = st.session_state.page


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
if page == "dashboard":

    # ── Hero ──
    all_videos = _scan_videos()
    st.markdown(f"""
    <div style="padding: 40px 0 20px 0;">
        <div class="s-h1">Scaler <b>Primer</b></div>
        <p class="s-subtitle">AI-generated personalized video lectures &middot; {len(all_videos)} video{'s' if len(all_videos) != 1 else ''} generated</p>
    </div>
    """, unsafe_allow_html=True)

    # ── Generation session history ─────────────────────────────────────────────
    history = st.session_state.get("gen_history", [])
    if history:
        _render_gen_history(context="dashboard")
    else:
        st.markdown("<div class='s-spacer'></div>", unsafe_allow_html=True)
        st.markdown("""
        <div class="s-empty">
            <div class="s-empty-icon">&#9654;</div>
            <div class="s-empty-title">No primers yet</div>
            <div class="s-empty-sub">Generate your first personalized primer to get started.</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Generate My Primer", type="primary"):
            st.session_state.page = "generate"
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: GENERATE
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "generate":

    st.markdown("""
    <div class="s-eyebrow">AI-Powered Learning</div>
    <div class="s-h1">Generate Your <b>Primer</b></div>
    <p class="s-subtitle">Choose a generation mode below to get started.</p>
    """, unsafe_allow_html=True)
    st.markdown("<div class='s-spacer-sm'></div>", unsafe_allow_html=True)

    # ── 3 mode selector buttons ───────────────────────────────────────────────
    gen_mode = st.session_state.gen_mode

    # Inline style per mode button — inject a scoped style block using unique key IDs
    # Streamlit renders button keys into the DOM via aria labels we can use
    st.markdown(f"""
    <style>
    /* Mode selector row: all 4 buttons inherit base tab style */
    [data-testid="stBaseButton-secondary"][aria-label="Single Topic"],
    [data-testid="stBaseButton-secondary"][aria-label="Personalized Primer"],
    [data-testid="stBaseButton-secondary"][aria-label="Case Study Document"],
    [data-testid="stBaseButton-secondary"][aria-label="Slide by Slide"] {{
        border-radius: 0 !important;
        font-size: 12px !important;
        font-weight: 600 !important;
        letter-spacing: 1px !important;
        text-transform: uppercase !important;
        padding: 13px 10px !important;
        height: auto !important;
        width: 100% !important;
        border: 1px solid var(--border) !important;
        background: var(--panel) !important;
        color: var(--muted) !important;
        transition: all 0.15s !important;
    }}
    /* Active mode button */
    [data-testid="stBaseButton-secondary"][aria-label="{gen_mode}"] {{
        background: var(--blue) !important;
        color: white !important;
        border-color: var(--blue) !important;
    }}
    /* Hover on inactive */
    [data-testid="stBaseButton-secondary"][aria-label="Single Topic"]:not([aria-label="{gen_mode}"]):hover,
    [data-testid="stBaseButton-secondary"][aria-label="Personalized Primer"]:not([aria-label="{gen_mode}"]):hover,
    [data-testid="stBaseButton-secondary"][aria-label="Case Study Document"]:not([aria-label="{gen_mode}"]):hover,
    [data-testid="stBaseButton-secondary"][aria-label="Slide by Slide"]:not([aria-label="{gen_mode}"]):hover {{
        background: var(--ice) !important;
        color: var(--blue) !important;
        border-color: var(--blue) !important;
    }}
    </style>
    """, unsafe_allow_html=True)

    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        if st.button("Single Topic", width='stretch', key="mode_single"):
            st.session_state.gen_mode = "Single Topic"
            st.rerun()
    with mc2:
        if st.button("Personalized Primer", width='stretch', key="mode_primer"):
            st.session_state.gen_mode = "Personalized Primer"
            st.rerun()
    with mc3:
        if st.button("Case Study Document", width='stretch', key="mode_doc"):
            st.session_state.gen_mode = "Case Study Document"
            st.rerun()
    with mc4:
        if st.button("Slide by Slide", width='stretch', key="mode_sbs"):
            st.session_state.gen_mode = "Slide by Slide"
            st.rerun()

    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

    # ── Single Topic ──────────────────────────────────────────────────────────
    if gen_mode == "Single Topic":
        st.markdown("""
        <div class="s-section-head">
            <div class="s-form-title">Quick Topic Video</div>
            <div class="s-form-desc">Enter any topic and get a complete lecture video with slides, narration, and AI voice.</div>
        </div>
        """, unsafe_allow_html=True)

        c1, c2 = st.columns([3, 1])
        with c1:
            topic = st.text_input("Topic", placeholder="e.g. Binary Search, SQL Joins, Neural Networks", label_visibility="collapsed")
        with c2:
            level = st.selectbox("Level", ["basic", "intermediate", "advanced"], label_visibility="collapsed")

        # Check if a process just finished
        proc = st.session_state.get("gen_process")
        if proc and not proc.is_alive():
            result_data = _read_result()
            st.session_state.gen_process = None
            if result_data and result_data.get("status") == "ok":
                video_path = result_data["path"]
                if video_path and os.path.exists(video_path):
                    _append_gen_result({
                        "type": "single", "topic": topic,
                        "videos": [{"path": video_path, "topic": topic, "section": "Single Topic"}],
                    })
                    st.rerun()
            elif result_data:
                st.error(f"Generation failed: {result_data.get('error', 'Unknown error')}")

        st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

        # Video options panel
        st.markdown("""
        <div class="s-video-options">
            <div class="s-video-options-label">Video Options</div>
        </div>
        """, unsafe_allow_html=True)

        instructor_voice = st.selectbox("Instructor Voice", options=["Shivank Sir", "Anshuman Sir"], key="voice_single")
        is_shivank = (instructor_voice == "Shivank Sir")

        voc1, voc2, voc3, voc4 = st.columns(4)
        with voc1:
            scribble = st.toggle("Pen Annotations", value=st.session_state.gen_scribble, key="tog_scribble_single")
            st.session_state.gen_scribble = scribble
        with voc2:
            animation = st.toggle("Animations (Manim)", value=False, key="tog_anim_single", disabled=True, help="Temporarily disabled")
            st.session_state.gen_animation = animation
        with voc3:
            lecture_eval = st.toggle(
                "Lecture Eval",
                value=st.session_state.gen_lecture_eval,
                key="tog_lecture_eval_single",
                help="After slide-level evals, runs a full-lecture quality check. If it fails, triggers one extra rewrite pass using the lecture findings. ~1 extra Claude call.",
            )
            st.session_state.gen_lecture_eval = lecture_eval
        with voc4:
            presenter_overlay = st.toggle(
                "Presenter Avatar",
                value=st.session_state.gen_presenter_overlay and is_shivank,
                key="tog_avatar_single",
                disabled=not is_shivank,
                help="Add Shivank Sir's avatar to the corner of each slide. Only available with Shivank Sir's voice.",
            )
            presenter_overlay = presenter_overlay and is_shivank
            st.session_state.gen_presenter_overlay = presenter_overlay

        eng1, eng2 = st.columns([1, 3])
        with eng1:
            use_groot = st.toggle(
                "Use Groot",
                value=st.session_state.gen_use_groot,
                key="tog_use_groot_single",
                help="ON: Groot AI generates richer slide layouts (may have occasional coordinate issues).\nOFF: Claude generates clean, reliable slides directly — faster and more predictable.",
            )
            st.session_state.gen_use_groot = use_groot

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        num_scenes = st.slider("Max Slides", min_value=2, max_value=40, value=4, step=1, key="slider_scenes_single")

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # Show status OR generate button
        if _show_generation_status(f'"{topic}" — Single Topic Video'):
            pass  # rerun handled inside
        else:
            if st.button("Generate Video →", type="primary", key="gen_single"):
                if not topic.strip():
                    st.error("Please enter a topic.")
                else:
                    from dashboard.pipeline_worker import run_single_topic
                    el_k, _ = _get_el_creds()
                    selected_voice_id = VOICE_MAP[instructor_voice]
                    _start_process(run_single_topic, (topic, level, el_k, selected_voice_id, _get_llm_key(), scribble, animation, num_scenes, lecture_eval, presenter_overlay, use_groot))
                    st.rerun()

    # ── Personalized Primer ───────────────────────────────────────────────────
    elif gen_mode == "Personalized Primer":

        MAX_QUESTIONS = 5

        # Show any persistent error from previous action
        if st.session_state.qa_error:
            st.error(st.session_state.qa_error)
            st.session_state.qa_error = None

        # ── Step 0: Course setup ──────────────────────────────────────────────
        if st.session_state.qa_step == 0:
            st.markdown("""
            <div class="s-section-head">
                <div class="s-form-title">Personalized Primer</div>
                <div class="s-form-desc">We'll ask you a few questions to understand your background. Our AI adapts each question based on your answers.</div>
            </div>
            """, unsafe_allow_html=True)

            c1, c2 = st.columns(2)
            with c1:
                course = st.selectbox("Program", ["AIML", "DSML", "PGP", "DevOps", "Academy"])
            with c2:
                level = st.selectbox("Level", ["basic", "intermediate", "advanced"])

            st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)
            st.markdown('<div class="s-form-label">What topics do you need to cover?</div>', unsafe_allow_html=True)
            dyn_topics = st.text_area(
                "Topics", height=100, label_visibility="collapsed",
                value="Python Basics\nSQL Fundamentals\nLinear Algebra\nProbability and Statistics",
            )

            st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

            # Video options — visible upfront so user can set before generating
            st.markdown("""
            <div class="s-video-options">
                <div class="s-video-options-label">Video Options</div>
            </div>
            """, unsafe_allow_html=True)
            _pps1, _pps2 = st.columns(2)
            with _pps1:
                _pp_scr = st.toggle("Pen Annotations", value=st.session_state.gen_scribble, key="tog_scribble_pp0")
                st.session_state.gen_scribble = _pp_scr
            with _pps2:
                _pp_ani = st.toggle("Animations (Manim)", value=False, key="tog_anim_pp0", disabled=True, help="Temporarily disabled")
                st.session_state.gen_animation = _pp_ani

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            instructor_voice = st.selectbox("Instructor Voice", options=["Shivank Sir", "Anshuman Sir"], key="voice_pp0")

            st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

            if st.button("Start Assessment →", type="primary", key="qa_start"):
                topics = [t.strip() for t in dyn_topics.strip().split("\n") if t.strip()]
                if not topics:
                    st.error("Please enter at least one topic.")
                else:
                    st.session_state.qa_course = course
                    st.session_state.qa_level = level
                    st.session_state.qa_topics = topics
                    st.session_state.qa_questions = []
                    st.session_state.qa_answers = []

                    with st.spinner("Generating your first question..."):
                        q = _generate_next_question(course, topics, [])

                    if q:
                        st.session_state.qa_questions.append(q)
                        st.session_state.qa_step = 1
                        st.rerun()
                    else:
                        st.error("Could not generate question. Check that ANTHROPIC_API_KEY is set in .env")

        # ── Steps 1-N: Adaptive questions ─────────────────────────────────────
        elif 1 <= st.session_state.qa_step <= MAX_QUESTIONS:
            course = st.session_state.qa_course
            level = st.session_state.qa_level
            topics = st.session_state.qa_topics
            step = st.session_state.qa_step
            current_q = st.session_state.qa_questions[step - 1]

            st.markdown(f"""
            <div class="s-section-head">
                <div class="s-form-title">Background Assessment</div>
                <div class="s-form-desc">{course} Program &middot; Question {step} of {MAX_QUESTIONS}</div>
            </div>
            """, unsafe_allow_html=True)

            # Progress bar
            pct = int((step - 1) / MAX_QUESTIONS * 100)
            st.markdown(f"""
            <div style="background:#D7DDE8;height:3px;margin-bottom:28px;">
                <div style="background:var(--blue);height:3px;width:{pct}%;transition:width 0.3s"></div>
            </div>
            """, unsafe_allow_html=True)

            # Show all previous Q&A as read-only
            if st.session_state.qa_answers:
                for i, (pq, pa) in enumerate(zip(st.session_state.qa_questions[:-1], st.session_state.qa_answers)):
                    st.markdown(f"""
                    <div style="margin-bottom:16px;padding:14px 18px;background:var(--panel);border-left:2px solid var(--blue)">
                        <div style="font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:4px">Q{i+1}</div>
                        <div style="font-size:13px;font-weight:500;color:var(--heading);margin-bottom:6px">{pq}</div>
                        <div style="font-size:13px;color:var(--muted)">{pa}</div>
                    </div>
                    """, unsafe_allow_html=True)

            # Current question
            st.markdown(f"""
            <div style="margin-bottom:12px">
                <div style="font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:var(--blue);margin-bottom:8px">Question {step}</div>
                <div style="font-size:16px;font-weight:600;color:var(--heading);margin-bottom:16px">{current_q}</div>
            </div>
            """, unsafe_allow_html=True)

            answer = st.text_area("Your answer", height=80, label_visibility="collapsed", key=f"qa_ans_{step}")

            st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

            col_back, col_next = st.columns([1, 4])
            with col_back:
                if step > 1 and st.button("← Back", key="qa_back"):
                    st.session_state.qa_step -= 1
                    st.session_state.qa_answers.pop()
                    st.rerun()

            with col_next:
                is_last = (step == MAX_QUESTIONS)
                btn_label = "Generate My Primer →" if is_last else f"Next Question ({step}/{MAX_QUESTIONS}) →"
                btn_type = "primary" if is_last else "secondary"

                if st.button(btn_label, type=btn_type, key="qa_next"):
                    if not answer.strip():
                        st.markdown('<div class="s-error">Please type an answer before continuing.</div>', unsafe_allow_html=True)
                    else:
                        st.session_state.qa_answers.append(answer.strip())

                        if is_last:
                            # All questions answered → generate primer
                            st.session_state.qa_step = MAX_QUESTIONS + 1
                            st.rerun()
                        else:
                            # Generate next question
                            qa_so_far = list(zip(st.session_state.qa_questions, st.session_state.qa_answers))
                            with st.spinner("Thinking of the next question..."):
                                next_q = _generate_next_question(course, topics, qa_so_far)

                            if next_q:
                                st.session_state.qa_questions.append(next_q)
                                st.session_state.qa_step += 1
                                st.rerun()
                            else:
                                st.markdown('<div class="s-error">Failed to generate next question.</div>', unsafe_allow_html=True)

        # ── Final step: Generate primer ───────────────────────────────────────
        elif st.session_state.qa_step > MAX_QUESTIONS:
            course = st.session_state.qa_course
            level = st.session_state.qa_level
            topics = st.session_state.qa_topics
            qa_pairs = list(zip(st.session_state.qa_questions, st.session_state.qa_answers))

            st.markdown(f"""
            <div class="s-success">
                Assessment complete &mdash; {len(qa_pairs)} questions answered.
                Ready to generate your personalized primer for <strong>{course}</strong>.
            </div>
            """, unsafe_allow_html=True)

            # Show summary
            for i, (q, a) in enumerate(qa_pairs):
                st.markdown(f"""
                <div style="margin-bottom:10px;padding:12px 16px;background:var(--panel);border-left:2px solid var(--blue)">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:3px">Q{i+1}</div>
                    <div style="font-size:13px;font-weight:500;color:var(--heading)">{q}</div>
                    <div style="font-size:13px;color:var(--muted);margin-top:4px">{a}</div>
                </div>
                """, unsafe_allow_html=True)

            # Check if process just finished
            proc = st.session_state.get("gen_process")
            if proc and not proc.is_alive():
                result_data = _read_result()
                st.session_state.gen_process = None
                if result_data and result_data.get("status") == "ok":
                    videos = result_data["videos"]
                    sections = {}
                    for v in videos:
                        sections.setdefault(v["section"], []).append(v)
                    _append_gen_result({
                        "type": "primer",
                        "topic": f"{course} — Personalized Primer",
                        "sections": sections,
                        "videos": videos,
                    })
                    st.session_state.qa_step = 0
                    st.session_state.qa_questions = []
                    st.session_state.qa_answers = []
                    st.rerun()
                elif result_data:
                    st.error(f"Generation failed: {result_data.get('error', 'Unknown error')}")

            st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

            # Video options panel (top-level, always visible)
            st.markdown("""
            <div class="s-video-options">
                <div class="s-video-options-label">Video Options</div>
            </div>
            """, unsafe_allow_html=True)
            instructor_voice_pp = st.selectbox("Instructor Voice", options=["Shivank Sir", "Anshuman Sir"], key="voice_pp")
            is_shivank_pp = (instructor_voice_pp == "Shivank Sir")

            ppc1, ppc2, ppc3, ppc4 = st.columns(4)
            with ppc1:
                pp_scribble = st.toggle("Pen Annotations", value=st.session_state.gen_scribble, key="tog_scribble_pp")
                st.session_state.gen_scribble = pp_scribble
            with ppc2:
                pp_animation = st.toggle("Animations (Manim)", value=False, key="tog_anim_pp", disabled=True, help="Temporarily disabled")
                st.session_state.gen_animation = pp_animation
            with ppc3:
                pp_lecture_eval = st.toggle(
                    "Lecture Eval",
                    value=st.session_state.gen_lecture_eval,
                    key="tog_lecture_eval_pp",
                    help="After slide-level evals, runs a full-lecture quality check per video. ~1 extra Claude call per video.",
                )
                st.session_state.gen_lecture_eval = pp_lecture_eval
            with ppc4:
                pp_presenter_overlay = st.toggle(
                    "Presenter Avatar",
                    value=st.session_state.gen_presenter_overlay and is_shivank_pp,
                    key="tog_avatar_pp",
                    disabled=not is_shivank_pp,
                    help="Add Shivank Sir's avatar to the corner of each slide. Only available with Shivank Sir's voice.",
                )
                pp_presenter_overlay = pp_presenter_overlay and is_shivank_pp
                st.session_state.gen_presenter_overlay = pp_presenter_overlay

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            max_videos = st.slider("Max Videos", min_value=2, max_value=40, value=5, step=1, key="slider_max_videos_pp")

            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

            if _show_generation_status(f"{course} — Personalized Primer"):
                pass
            else:
                bc1, bc2 = st.columns([1, 3])
                with bc1:
                    if st.button("← Retake", key="qa_retake"):
                        st.session_state.qa_step = 0
                        st.session_state.qa_questions = []
                        st.session_state.qa_answers = []
                        st.rerun()
                with bc2:
                    if st.button("Generate My Primer →", type="primary", key="gen_primer"):
                        from dashboard.pipeline_worker import run_personalized_primer
                        el_k, _ = _get_el_creds()
                        selected_voice_id = VOICE_MAP[instructor_voice_pp]
                        _start_process(run_personalized_primer,
                                       (course, level, topics, qa_pairs, el_k, selected_voice_id, _get_llm_key(),
                                        pp_scribble, pp_animation, max_videos, pp_lecture_eval, pp_presenter_overlay))
                        st.rerun()

    # ── Slide by Slide ────────────────────────────────────────────────────────
    elif gen_mode == "Slide by Slide":
        st.markdown("""
        <div class="s-section-head">
            <div class="s-form-title">Slide by Slide</div>
            <div class="s-form-desc">Write each slide yourself — title, content, and whether you want a Napkin diagram. Claude only formats your content into a clean layout.</div>
        </div>
        """, unsafe_allow_html=True)

        sbs_topic = st.text_input("Video Title", placeholder="e.g. Introduction to Neural Networks", key="sbs_topic")

        st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

        st.markdown("""
        <div style="background:#FEF3C7;border-left:4px solid #D97706;border-radius:8px;padding:16px 20px;margin-bottom:18px;">
            <div style="font-weight:700;color:#92400E;font-size:15px;margin-bottom:10px;">⚠️ Avoid doing this in your content or title</div>
            <div style="color:#78350F;font-size:14px;line-height:1.7;">
                <b>Mathematical formulas</b> — e.g. <code>Q(s,a) ← Q(s,a) + α[r + γ max Q(s′,a′)]</code><br>
                The voice cannot read symbols like <code>←  α  γ  ∑  ∫  ′  ₀</code> correctly. Any formula found in the content or title will be <b>silently removed from the audio</b> — the surrounding text will still be read, but the formula itself will be skipped entirely.
                <br><br>
                <b>Title:</b> The title appears on the slide visually, so a formula in the title will display correctly on screen. But if you leave the content box empty, the title is also read aloud — so avoid formulas in titles unless you fill in the content.<br><br>
                <b>✅ Instead, do this:</b><br>
                • In the <b>title</b>, use a plain readable name: <i>"The Q-Learning Update Rule"</i><br>
                • In the <b>content</b>, explain it in plain words: <i>"The Q-value is updated by adding the learning rate times the difference between the expected and actual reward."</i><br>
                • Enable the <b>Napkin diagram toggle</b> — Claude will generate a diagram that visually represents the concept, so you get the visual without needing the formula in the audio
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Number of slides selector — adjusts the slides list in session state
        num_slides = st.slider("Number of Slides", min_value=1, max_value=10,
                               value=st.session_state.sbs_num_slides, step=1, key="sbs_num_slider")
        if num_slides != st.session_state.sbs_num_slides:
            current = st.session_state.sbs_slides
            if num_slides > len(current):
                current += [{"title": "", "content": "", "use_napkin": False}
                             for _ in range(num_slides - len(current))]
            else:
                current = current[:num_slides]
            st.session_state.sbs_slides = current
            st.session_state.sbs_num_slides = num_slides
            st.rerun()

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Per-slide editors
        slides_state = st.session_state.sbs_slides
        for idx in range(num_slides):
            with st.expander(f"Slide {idx + 1}  —  {slides_state[idx]['title'] or '(untitled)'}", expanded=(idx == 0)):
                s_title = st.text_input(
                    "Title", value=slides_state[idx]["title"],
                    placeholder=f"Slide {idx + 1} title — use plain words, avoid formulas or symbols",
                    key=f"sbs_title_{idx}",
                )
                s_content = st.text_area(
                    "Content",
                    value=slides_state[idx]["content"],
                    height=160,
                    max_chars=2000,
                    placeholder="Write the slide content in plain English. This becomes both the slide bullets and the spoken audio — avoid formulas and symbols, explain concepts in words instead.",
                    key=f"sbs_content_{idx}",
                )
                s_napkin = st.toggle(
                    "Generate Napkin diagram for this slide",
                    value=slides_state[idx]["use_napkin"],
                    key=f"sbs_napkin_{idx}",
                    help="Uses ~60-90 Napkin credits. The diagram appears in the right column; text stays in the left column — no overlaps.",
                )
                # Persist edits back to session state
                slides_state[idx] = {"title": s_title, "content": s_content, "use_napkin": s_napkin}
        st.session_state.sbs_slides = slides_state

        st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

        # Check if process just finished
        proc = st.session_state.get("gen_process")
        if proc and not proc.is_alive():
            result_data = _read_result()
            st.session_state.gen_process = None
            if result_data and result_data.get("status") == "ok":
                video_path = result_data["path"]
                if video_path and os.path.exists(video_path):
                    _append_gen_result({
                        "type": "slide_by_slide", "topic": sbs_topic or "Slide by Slide",
                        "videos": [{"path": video_path, "topic": sbs_topic or "Slide by Slide", "section": "Slide by Slide"}],
                    })
                    st.rerun()
            elif result_data:
                st.error(f"Generation failed: {result_data.get('error', 'Unknown error')}")

        # Video options
        st.markdown("""
        <div class="s-video-options">
            <div class="s-video-options-label">Video Options</div>
        </div>
        """, unsafe_allow_html=True)
        instructor_voice_sbs = st.selectbox("Instructor Voice", options=["Shivank Sir", "Anshuman Sir"], key="voice_sbs")
        is_shivank_sbs = (instructor_voice_sbs == "Shivank Sir")

        sbsc1, sbsc2 = st.columns(2)
        with sbsc1:
            sbs_scribble = st.toggle("Pen Annotations", value=st.session_state.gen_scribble, key="tog_scribble_sbs")
            st.session_state.gen_scribble = sbs_scribble
        with sbsc2:
            sbs_presenter = st.toggle(
                "Presenter Avatar",
                value=st.session_state.gen_presenter_overlay and is_shivank_sbs,
                key="tog_avatar_sbs",
                disabled=not is_shivank_sbs,
                help="Add Shivank Sir's avatar. Only available with Shivank Sir's voice.",
            )
            sbs_presenter = sbs_presenter and is_shivank_sbs
            st.session_state.gen_presenter_overlay = sbs_presenter

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        if _show_generation_status(f'"{sbs_topic}" — Slide by Slide'):
            pass
        else:
            if st.button("Generate Slide-by-Slide Video →", type="primary", key="gen_sbs"):
                if not sbs_topic.strip():
                    st.error("Please enter a video title.")
                elif not any(s["content"].strip() for s in slides_state):
                    st.error("Please add content to at least one slide.")
                elif not _get_llm_key():
                    st.error("Claude API key required. Set ANTHROPIC_API_KEY in .env")
                else:
                    from dashboard.pipeline_worker import run_slide_by_slide
                    el_k, _ = _get_el_creds()
                    selected_voice_id = VOICE_MAP[instructor_voice_sbs]
                    _start_process(run_slide_by_slide,
                                   (list(slides_state), el_k, selected_voice_id,
                                    _get_llm_key(), sbs_scribble, sbs_presenter))
                    st.rerun()

    # ── Document Video (Generic) ───────────────────────────────────────────────
    elif gen_mode == "Case Study Document":
        st.markdown("""
        <div class="s-section-head">
            <div class="s-form-title">Document Video</div>
            <div class="s-form-desc">Paste any document — case study, assignment, syllabus, notes — and tell the AI how you want the video structured.</div>
        </div>
        """, unsafe_allow_html=True)

        doc_topic = st.text_input("Video Title", placeholder="e.g. Driver Drowsiness Detection — Case Study", key="doc_t")

        st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

        doc_content = st.text_area(
            "Document Content",
            height=280,
            max_chars=25000,
            placeholder="Paste the full document text here (problem statement, approach, syllabus, assignment, paper, notes...)",
            key="doc_content"
        )

        doc_instructions = st.text_area(
            "Video Generation Instructions",
            height=140,
            max_chars=1000,
            placeholder=(
                'e.g. "Explain this document for beginner students."\n'
                'e.g. "This is an assignment — give hints but don\'t reveal the answers."\n'
                'e.g. "Cover only the key concepts, keep it to 5 slides."\n'
                'e.g. "This is a research paper — explain it in simple, intuitive terms."'
            ),
            key="doc_instructions"
        )

        st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

        # Check if process just finished
        proc = st.session_state.get("gen_process")
        if proc and not proc.is_alive():
            result_data = _read_result()
            st.session_state.gen_process = None
            if result_data and result_data.get("status") == "ok":
                video_path = result_data["path"]
                if video_path and os.path.exists(video_path):
                    _append_gen_result({
                        "type": "document", "topic": doc_topic,
                        "videos": [{"path": video_path, "topic": doc_topic, "section": "Document Video"}],
                    })
                    st.rerun()
            elif result_data:
                st.error(f"Generation failed: {result_data.get('error', 'Unknown error')}")

        # Video options panel
        st.markdown("""
        <div class="s-video-options">
            <div class="s-video-options-label">Video Options</div>
        </div>
        """, unsafe_allow_html=True)
        instructor_voice_doc = st.selectbox("Instructor Voice", options=["Shivank Sir", "Anshuman Sir"], key="voice_doc")
        is_shivank_doc = (instructor_voice_doc == "Shivank Sir")

        dvc1, dvc2, dvc3, dvc4 = st.columns(4)
        with dvc1:
            doc_scribble = st.toggle("Pen Annotations", value=st.session_state.gen_scribble, key="tog_scribble_doc")
            st.session_state.gen_scribble = doc_scribble
        with dvc2:
            st.toggle("Animations (Manim)", value=False, key="tog_anim_doc", disabled=True,
                      help="Animations not supported in Document pipeline")
        with dvc3:
            doc_lecture_eval = st.toggle(
                "Lecture Eval",
                value=st.session_state.gen_lecture_eval,
                key="tog_lecture_eval_doc",
                help="After slide-level evals, runs a full-lecture quality check. ~1 extra Claude call.",
            )
            st.session_state.gen_lecture_eval = doc_lecture_eval
        with dvc4:
            doc_presenter_overlay = st.toggle(
                "Presenter Avatar",
                value=st.session_state.gen_presenter_overlay and is_shivank_doc,
                key="tog_avatar_doc",
                disabled=not is_shivank_doc,
                help="Add Shivank Sir's avatar to the corner of each slide. Only available with Shivank Sir's voice.",
            )
            doc_presenter_overlay = doc_presenter_overlay and is_shivank_doc
            st.session_state.gen_presenter_overlay = doc_presenter_overlay

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        doc_max_slides = st.slider("Max Slides", min_value=2, max_value=40, value=10, step=1, key="slider_max_slides_doc")

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        if _show_generation_status(f'"{doc_topic}" — Document Video'):
            pass
        else:
            if st.button("Generate Document Video →", type="primary", key="gen_doc"):
                if not doc_topic.strip():
                    st.error("Please enter a video title.")
                elif not doc_content.strip():
                    st.error("Please paste your document content.")
                elif not doc_instructions.strip():
                    st.error("Please add instructions so the AI knows how to structure the video.")
                elif not _get_llm_key():
                    st.error("Claude API key required. Set ANTHROPIC_API_KEY in .env")
                else:
                    from dashboard.pipeline_worker import run_document
                    el_k, _ = _get_el_creds()
                    selected_voice_id = VOICE_MAP[instructor_voice_doc]
                    _start_process(run_document,
                                   (doc_topic, doc_content, doc_instructions,
                                    el_k, selected_voice_id, _get_llm_key(), doc_scribble, doc_max_slides,
                                    doc_lecture_eval, doc_presenter_overlay))
                    st.rerun()

    # ── Last-run log (persists until next generation starts) ─────────────────
    log_events = _read_log_events()
    if log_events and not st.session_state.get("gen_process"):
        with st.expander("Last Run — Generation Log", expanded=False):
            _render_log_section(log_events)

    # ── Generation history (persists until manually removed) ──────────────────
    _render_gen_history(context="generate")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: VIDEO LIBRARY
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "library":

    st.markdown("""
    <div class="s-eyebrow">Your Library</div>
    <div class="s-h1">Primer <b>Videos</b></div>
    <p class="s-subtitle">All your generated videos in one place.</p>
    """, unsafe_allow_html=True)
    st.markdown("<div class='s-spacer-sm'></div>", unsafe_allow_html=True)

# ── Library page CSS ──────────────────────────────────────────────────────────
    st.markdown("""
    <style>
    /* Library grid */
    .lib-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 16px;
        margin-top: 8px;
    }
    .lib-card {
        background: white;
        border: 1px solid var(--border);
        border-radius: 10px;
        overflow: hidden;
        transition: box-shadow 0.18s, border-color 0.18s, transform 0.15s;
        cursor: pointer;
    }
    .lib-card:hover {
        box-shadow: 0 6px 24px rgba(0,85,255,0.10);
        border-color: var(--blue);
        transform: translateY(-2px);
    }
    .lib-card.lib-active {
        border-color: var(--blue);
        box-shadow: 0 0 0 2px rgba(0,85,255,0.18), 0 6px 24px rgba(0,85,255,0.10);
    }
    .lib-thumb {
        background: linear-gradient(135deg, #011845 0%, #0a2d6e 100%);
        height: 150px;
        display: flex; align-items: center; justify-content: center;
        position: relative;
    }
    .lib-play-btn {
        width: 52px; height: 52px;
        border-radius: 50%;
        background: rgba(255,255,255,0.12);
        border: 2px solid rgba(255,255,255,0.5);
        display: flex; align-items: center; justify-content: center;
        color: white; font-size: 20px;
        backdrop-filter: blur(4px);
    }
    .lib-dur-badge {
        position: absolute; bottom: 10px; right: 12px;
        background: rgba(0,0,0,0.55);
        color: white; font-size: 10px; font-weight: 600;
        padding: 3px 8px; border-radius: 4px;
        letter-spacing: 0.5px;
    }
    .lib-type-badge {
        position: absolute; top: 10px; left: 12px;
        font-size: 9px; font-weight: 700;
        letter-spacing: 1.5px; text-transform: uppercase;
        padding: 3px 10px; border-radius: 3px;
    }
    .lib-body {
        padding: 14px 16px 16px;
    }
    .lib-title {
        font-size: 14px; font-weight: 600;
        color: var(--heading); line-height: 1.35;
        letter-spacing: -0.2px; margin-bottom: 6px;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .lib-meta {
        font-size: 11px; color: var(--light-muted);
        display: flex; align-items: center; gap: 6px;
    }
    .lib-meta-dot { color: #D1D5DB; }

    /* Player pane */
    .lib-player-wrap {
        background: white;
        border: 1px solid var(--border);
        border-radius: 12px;
        overflow: hidden;
        position: sticky;
        top: 16px;
    }
    .lib-player-header {
        background: var(--navy);
        padding: 20px 24px;
    }
    .lib-player-title {
        font-size: 17px; font-weight: 600;
        color: white; letter-spacing: -0.3px;
        margin-bottom: 4px;
        line-height: 1.3;
    }
    .lib-player-sub {
        font-size: 12px; color: rgba(255,255,255,0.45);
    }
    .lib-player-placeholder {
        background: linear-gradient(135deg, #0a1c3e 0%, #011845 100%);
        height: 280px;
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        gap: 12px;
    }
    .lib-placeholder-icon {
        font-size: 40px; opacity: 0.25; color: white;
    }
    .lib-placeholder-text {
        font-size: 14px; color: rgba(255,255,255,0.3);
        font-weight: 500;
    }
    .lib-filter-bar {
        display: flex; gap: 8px; flex-wrap: wrap;
        margin-bottom: 20px;
    }
    .lib-filter-chip {
        padding: 6px 16px;
        border-radius: 20px;
        font-size: 11px; font-weight: 600;
        letter-spacing: 0.8px; text-transform: uppercase;
        cursor: pointer;
        border: 1px solid var(--border);
        background: white;
        color: var(--muted);
        transition: all 0.15s;
    }
    .lib-filter-chip.active {
        background: var(--blue);
        color: white;
        border-color: var(--blue);
    }
    .lib-cat-label {
        font-size: 10px; font-weight: 700;
        letter-spacing: 2px; text-transform: uppercase;
        color: var(--muted);
        margin: 24px 0 10px 0;
        padding-bottom: 8px;
        border-bottom: 1px solid var(--border);
    }
    </style>
    """, unsafe_allow_html=True)

    all_videos = _scan_videos()

    if not all_videos:
        st.markdown("""
        <div class="s-empty">
            <div class="s-empty-icon">&#127902;</div>
            <div class="s-empty-title">No videos yet</div>
            <div class="s-empty-sub">Head to Generate Primer to create your first video.</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Generate Primer", type="primary"):
            st.session_state.page = "generate"
            st.rerun()
    else:
        # Group by category
        def _top_cat(cat_path):
            first = cat_path.split("/")[0] if cat_path else "general"
            return first.replace("_", " ").title()

        top_categories = {}
        for v in all_videos:
            top = _top_cat(v["category"])
            top_categories.setdefault(top, []).append(v)

        top_cat_names = list(top_categories.keys())

        # Layout: list left, player right
        left_col, right_col = st.columns([3, 2], gap="large")

        with left_col:
            # Filter chips using selectbox hidden, chips are cosmetic + selectbox is real
            if len(top_cat_names) > 1:
                selected_top = st.selectbox(
                    "Filter",
                    ["All"] + top_cat_names,
                    label_visibility="collapsed",
                    key="lib_filter",
                )
            else:
                selected_top = "All"

            show_tops = top_cat_names if selected_top == "All" else [selected_top]

            TYPE_BADGE_COLORS = {
                "single": ("rgba(0,85,255,0.15)", "#0055FF"),
                "primer": ("rgba(1,24,69,0.85)", "#FFFFFF"),
                "document": ("rgba(16,122,60,0.15)", "#1A7A3C"),
            }

            for top in show_tops:
                vids = top_categories[top]
                st.markdown(f'<div class="lib-cat-label">{top} &nbsp;·&nbsp; {len(vids)} video{"s" if len(vids) != 1 else ""}</div>', unsafe_allow_html=True)

                # Render in rows of 3 using columns
                chunk_size = 3
                for row_start in range(0, len(vids), chunk_size):
                    chunk = vids[row_start:row_start + chunk_size]
                    cols = st.columns(len(chunk))
                    for col, (i, vid) in zip(cols, enumerate(chunk, start=row_start)):
                        with col:
                            is_active = (
                                st.session_state.play_video and
                                st.session_state.play_video.get("path") == vid["path"]
                            )
                            active_cls = "lib-active" if is_active else ""
                            dur = _video_duration_str(vid["path"])

                            # Detect type from category
                            cat_lower = vid["category"].lower()
                            if "single" in cat_lower or "direct" in cat_lower:
                                badge_bg, badge_col = "rgba(0,85,255,0.15)", "#0055FF"
                                type_label = "Single"
                            elif "document" in cat_lower:
                                badge_bg, badge_col = "rgba(16,122,60,0.15)", "#1A7A3C"
                                type_label = "Doc"
                            else:
                                badge_bg, badge_col = "rgba(1,24,69,0.85)", "#FFFFFF"
                                type_label = "Primer"

                            st.markdown(f"""
                            <div class="lib-card {active_cls}">
                                <div class="lib-thumb">
                                    <div class="lib-type-badge" style="background:{badge_bg};color:{badge_col};">{type_label}</div>
                                    <div class="lib-play-btn">&#9654;</div>
                                    {f'<div class="lib-dur-badge">{dur}</div>' if dur else ''}
                                </div>
                                <div class="lib-body">
                                    <div class="lib-title">{vid['name']}</div>
                                    <div class="lib-meta">
                                        <span>{vid['size_mb']:.1f} MB</span>
                                    </div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                            btn_label = "▶ Now Playing" if is_active else "▶ Play"
                            if st.button(btn_label, key=f"lib_{top}_{i}", width='stretch'):
                                st.session_state.play_video = vid
                                st.rerun()

        with right_col:
            st.markdown('<div class="lib-player-wrap">', unsafe_allow_html=True)

            if st.session_state.play_video and os.path.exists(st.session_state.play_video["path"]):
                vid = st.session_state.play_video
                dur = _video_duration_str(vid["path"])
                meta_parts = [p for p in [vid["category"].title(), dur, f"{vid['size_mb']:.1f} MB"] if p]

                st.markdown(f"""
                <div class="lib-player-header">
                    <div class="lib-player-title">{vid['name']}</div>
                    <div class="lib-player-sub">{" &nbsp;·&nbsp; ".join(meta_parts)}</div>
                </div>
                """, unsafe_allow_html=True)

                with open(vid["path"], "rb") as f:
                    st.video(f.read())

                dl_col, _ = st.columns([1, 1])
                with dl_col:
                    with open(vid["path"], "rb") as f:
                        st.download_button(
                            "⬇ Download MP4",
                            f.read(),
                            file_name=os.path.basename(vid["path"]),
                            mime="video/mp4",
                            key="lib_dl",
                            width='stretch',
                        )
            else:
                st.markdown("""
                <div class="lib-player-placeholder">
                    <div class="lib-placeholder-icon">&#9654;</div>
                    <div class="lib-placeholder-text">Select a video to play</div>
                </div>
                """, unsafe_allow_html=True)

            st.markdown('</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: METRICS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "metrics":
    from utils.metrics import load_all

    st.markdown("""
    <div class="s-eyebrow">Pipeline Analytics</div>
    <div class="s-h1">Generation <b>Metrics</b></div>
    <p class="s-subtitle">Performance data across all video generation runs.</p>
    """, unsafe_allow_html=True)
    st.markdown("<div class='s-spacer-sm'></div>", unsafe_allow_html=True)

    runs = load_all()

    if not runs:
        st.info("No runs yet — generate a video to start collecting metrics.")
    else:
        ok_runs = [r for r in runs if r.get("status") == "success"]
        fail_runs = [r for r in runs if r.get("status") == "failed"]

        total_videos = len(ok_runs)
        avg_time = (sum(r.get("total_time_seconds", 0) for r in ok_runs) / total_videos) if total_videos else 0
        total_slides = sum(r.get("slides_generated", 0) for r in ok_runs)
        avg_duration = (sum(r.get("video_duration_seconds", 0) for r in ok_runs) / total_videos) if total_videos else 0

        # ── Summary metrics ────────────────────────────────────────────────────
        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        mc1.metric("Videos Generated", total_videos)
        mc2.metric("Avg Generation Time", f"{avg_time/60:.1f} min")
        mc3.metric("Total Slides Made", total_slides)
        mc4.metric("Avg Video Length", f"{avg_duration/60:.1f} min")
        mc5.metric("Failed Runs", len(fail_runs))

        st.divider()

        # ── Run history ────────────────────────────────────────────────────────
        st.subheader("Run History")

        import pandas as pd
        rows = []
        for r in reversed(runs[-30:]):
            status = r.get("status", "unknown")
            dur = r.get("video_duration_seconds", 0)
            dur_str = f"{int(dur//60)}:{int(dur%60):02d}" if dur else "—"
            t = r.get("total_time_seconds", 0)
            rows.append({
                "Status": "✅ OK" if status == "success" else "❌ FAIL",
                "Topic": r.get("topic", "—"),
                "Slides": r.get("slides_generated", 0),
                "Video Length": dur_str,
                "Gen Time": f"{t:.0f}s" if t else "—",
                "Timestamp": r.get("timestamp", "")[:16],
                "Error": r.get("error", "")[:80] if status == "failed" else "",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, width='stretch', hide_index=True)

        # ── Step breakdown ─────────────────────────────────────────────────────
        if ok_runs:
            st.divider()
            st.subheader("Step Breakdown (avg across successful runs)")

            step_keys = [
                ("slide_generation_seconds", "Slide Generation"),
                ("tts_generation_seconds", "Text-to-Speech"),
                ("video_assembly_seconds", "Video Assembly"),
                ("storage_seconds", "Storage"),
            ]
            total_avg = avg_time if avg_time else 1
            for key, label in step_keys:
                vals = [r.get(key, 0) for r in ok_runs if r.get(key) is not None]
                if not vals:
                    continue
                avg = sum(vals) / len(vals)
                pct = min(avg / total_avg, 1.0)
                col_label, col_val = st.columns([4, 1])
                col_label.caption(label)
                col_val.caption(f"{avg:.1f}s avg")
                st.progress(pct)


# ═══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="s-footer">
    <span>Scaler &middot; AI-Powered Learning &middot; Primer System v3.0</span>
</div>
""", unsafe_allow_html=True)
