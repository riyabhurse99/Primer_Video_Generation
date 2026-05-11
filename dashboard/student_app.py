"""
Scaler Primer — Student Portal (Demo)
======================================
Production-ready student-facing dashboard for the Scaler Primer AI system.
Run with: streamlit run dashboard/student_app.py
"""

import sys
import os
import json
import time as _time

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
            return msg.content[0].text
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


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════════════════
if "page" not in st.session_state:
    st.session_state.page = "dashboard"
if "play_video" not in st.session_state:
    st.session_state.play_video = None
if "last_gen_result" not in st.session_state:
    st.session_state.last_gen_result = None
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
    --bg: #FCFCFC;
    --text: #0B1529;
    --heading: #101E37;
    --muted: #696969;
    --light-muted: #848484;
    --panel: #F6F6F6;
    --ice: #E9F1FF;
    --border: #CAC0C0;
    --border-light: #D1D1D1;
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
    font-size: 11px; font-weight: 600;
    letter-spacing: 2.5px; text-transform: uppercase;
    color: var(--light-muted);
    margin: 0 0 8px 0;
}
.s-h1 {
    font-size: 36px; font-weight: 700;
    color: var(--heading);
    letter-spacing: -0.8px; line-height: 1.15;
    margin: 0 0 8px 0;
}
.s-h1 b { color: var(--blue); font-weight: 700; }
.s-h2 {
    font-size: 13px; font-weight: 600;
    letter-spacing: 2px; text-transform: uppercase;
    color: var(--text);
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border-light);
    margin: 0 0 20px 0;
}
.s-subtitle {
    font-size: 15px;
    color: var(--muted);
    letter-spacing: -0.2px;
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
}
.s-form-title {
    font-size: 20px; font-weight: 700;
    color: var(--heading);
    letter-spacing: -0.3px;
    margin: 0 0 4px 0;
}
.s-form-desc {
    font-size: 14px; color: var(--muted);
    letter-spacing: -0.2px;
    margin: 0 0 28px 0;
}
.s-divider { height: 1px; background: var(--border-light); margin: 24px 0; }
.s-form-label {
    font-size: 12px; font-weight: 600;
    letter-spacing: 1px; text-transform: uppercase;
    color: var(--text); margin-bottom: 8px;
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

/* ── Info banner ── */
.s-info {
    background: var(--ice);
    border-left: 3px solid var(--blue);
    padding: 14px 20px;
    font-size: 13px; color: var(--text);
    letter-spacing: -0.2px;
    margin-bottom: 20px;
}
.s-success {
    background: #E6F7EE;
    border-left: 3px solid #1A7A3C;
    padding: 14px 20px;
    font-size: 13px; color: var(--text);
    margin-bottom: 20px;
}
.s-error {
    background: #FEF2F2;
    border-left: 3px solid #DC2626;
    padding: 14px 20px;
    font-size: 13px; color: var(--text);
    margin-bottom: 20px;
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
    border-radius: 0 !important;
    background: var(--bg) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 14px !important;
    color: var(--text) !important;
    padding: 10px 14px !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--blue) !important;
    box-shadow: 0 0 0 1px var(--blue) !important;
}
div[data-testid="stSelectbox"] > div {
    border-radius: 0 !important;
}
.stButton > button,
.stButton button,
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-secondary"] {
    background: var(--blue) !important; color: white !important;
    border: none !important; border-radius: 0 !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 12px !important; font-weight: 600 !important;
    letter-spacing: 1px !important; text-transform: uppercase !important;
    padding: 7px 20px !important; height: auto !important; min-height: 0 !important;
    line-height: 1.4 !important;
    transition: background 0.15s !important;
}
.stButton > button:hover,
.stButton button:hover,
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-secondary"]:hover { background: var(--cta) !important; }
.stDownloadButton > button {
    background: transparent !important; color: var(--blue) !important;
    border: 1px solid var(--blue) !important; border-radius: 0 !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 11px !important; font-weight: 600 !important;
    letter-spacing: 1px !important; text-transform: uppercase !important;
    padding: 7px 20px !important; height: auto !important; min-height: 0 !important;
}
.stDownloadButton > button:hover {
    background: var(--blue) !important; color: white !important;
}
label { font-size: 12px !important; font-weight: 600 !important; color: var(--text) !important; letter-spacing: 0.5px !important; }
h1, h2, h3 { font-family: 'Plus Jakarta Sans', sans-serif !important; color: var(--heading) !important; }
.stRadio > div { gap: 0 !important; }
.stRadio label { font-weight: 500 !important; }

/* ── Footer ── */
.s-footer {
    border-top: 1px solid var(--border-light);
    padding: 20px 0;
    text-align: center;
    margin-top: 60px;
}
.s-footer span {
    font-size: 11px; color: var(--light-muted);
    letter-spacing: 1.5px; text-transform: uppercase;
}

/* ── Nav row (first stHorizontalBlock on the page) ── */
div[data-testid="stHorizontalBlock"]:first-of-type {
    background: var(--navy) !important;
    margin: 0 -1rem !important;
    padding: 0 24px !important;
    gap: 0 !important;
    border-bottom: 4px solid var(--blue) !important;
    min-height: 60px !important;
    align-items: center !important;
}
div[data-testid="stHorizontalBlock"]:first-of-type > div {
    padding: 0 !important;
    display: flex !important;
    align-items: center !important;
}
div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button,
div[data-testid="stHorizontalBlock"]:first-of-type .stButton button,
div[data-testid="stHorizontalBlock"]:first-of-type [data-testid="stBaseButton-secondary"],
div[data-testid="stHorizontalBlock"]:first-of-type [data-testid="stBaseButton-primary"] {
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
div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button:hover,
div[data-testid="stHorizontalBlock"]:first-of-type .stButton button:hover,
div[data-testid="stHorizontalBlock"]:first-of-type [data-testid="stBaseButton-secondary"]:hover,
div[data-testid="stHorizontalBlock"]:first-of-type [data-testid="stBaseButton-primary"]:hover {
    color: white !important;
    background: transparent !important;
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
_nc_logo, _nc_sp, _nc1, _nc2, _nc3 = st.columns([2, 4, 1, 1, 1])
with _nc_logo:
    st.markdown(
        f'<img src="data:image/png;base64,{_logo_b64}" class="s-nav-logo" alt="Scaler" '
        f'style="height:28px;filter:brightness(0) invert(1);margin-top:16px">',
        unsafe_allow_html=True,
    )
with _nc1:
    if st.button("Dashboard", key="_nav1", use_container_width=True):
        st.session_state.page = "dashboard"
        st.rerun()
with _nc2:
    if st.button("Generate", key="_nav2", use_container_width=True):
        st.session_state.page = "generate"
        st.rerun()
with _nc3:
    if st.button("Library", key="_nav3", use_container_width=True):
        st.session_state.page = "library"
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

    # ── Recent videos ──
    if all_videos:
        st.markdown("<div class='s-spacer'></div>", unsafe_allow_html=True)
        st.markdown('<div class="s-h2">Recently Generated</div>', unsafe_allow_html=True)

        cols = st.columns(3)
        for i, vid in enumerate(all_videos[:6]):
            with cols[i % 3]:
                dur = _video_duration_str(vid["path"])
                st.markdown(f"""
                <div class="s-vcard">
                    <div class="s-vcard-thumb">
                        <div class="s-vcard-play">&#9654;</div>
                        {'<div class="s-vcard-dur">' + dur + '</div>' if dur else ''}
                    </div>
                    <div class="s-vcard-body">
                        <div class="s-vcard-cat">{vid['category'].upper()}</div>
                        <div class="s-vcard-title">{vid['name']}</div>
                        <div class="s-vcard-meta">{vid['size_mb']:.1f} MB &middot; AI Generated</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                if st.button("Watch", key=f"dw_{i}"):
                    st.session_state.play_video = vid
                    st.session_state.page = "library"
                    st.rerun()
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
    <p class="s-subtitle">Our AI builds personalized video lectures tailored to your exact learning needs.</p>
    """, unsafe_allow_html=True)
    st.markdown("<div class='s-spacer-sm'></div>", unsafe_allow_html=True)

    gen_mode = st.radio(
        "mode",
        ["Single Topic", "Personalized Primer", "Case Study Document"],
        horizontal=True,
        label_visibility="collapsed",
        key="gen_mode_radio",
    )

    st.markdown("<div class='s-spacer-sm'></div>", unsafe_allow_html=True)

    # ── Single Topic ──────────────────────────────────────────────────────────
    if gen_mode == "Single Topic":
        st.markdown("""
        <div class="s-form">
            <div class="s-form-title">Quick Topic Video</div>
            <div class="s-form-desc">Enter any topic and get a complete lecture video with slides, narration, and AI voice.</div>
        """, unsafe_allow_html=True)

        c1, c2 = st.columns([3, 1])
        with c1:
            topic = st.text_input("Topic", placeholder="e.g. Binary Search, SQL Joins, Neural Networks", label_visibility="collapsed")
        with c2:
            level = st.selectbox("Level", ["basic", "intermediate", "advanced"], label_visibility="collapsed")

        st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

        if st.button("Generate Video", type="primary", key="gen_single"):
            if not topic.strip():
                st.markdown('<div class="s-error">Please enter a topic.</div>', unsafe_allow_html=True)
            else:
                from pipelines.direct import DirectPipeline
                from modules.groot.generator import GrootSlideGenerator
                from modules.tts.elevenlabs import ElevenLabsTTS
                from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
                from modules.storage.local import LocalStorage

                el_k, el_v = _get_el_creds()
                pipeline = DirectPipeline(
                    slide_generator=GrootSlideGenerator(cookies=GROOT_COOKIES),
                    tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
                    video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
                    storage=LocalStorage(base_path=OUTPUT_DIR),
                    temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
                    call_llm=_get_call_llm(),
                )
                os.makedirs(TEMP_DIR, exist_ok=True)
                os.makedirs(OUTPUT_DIR, exist_ok=True)

                with st.spinner(f"Generating primer for \"{topic}\"..."):
                    video_path = pipeline.run(topic, level=level)

                if video_path and os.path.exists(video_path):
                    st.session_state.last_gen_result = {
                        "type": "single",
                        "topic": topic,
                        "videos": [{"path": video_path, "topic": topic, "section": "Single Topic"}],
                    }
                    st.rerun()
                else:
                    st.markdown('<div class="s-error">Generation failed. Please try again.</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

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
            <div class="s-form">
                <div class="s-form-title">Personalized Primer</div>
                <div class="s-form-desc">We'll ask you a few questions to understand your background. Our AI adapts each question based on your answers.</div>
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

            st.markdown('</div>', unsafe_allow_html=True)

        # ── Steps 1-N: Adaptive questions ─────────────────────────────────────
        elif 1 <= st.session_state.qa_step <= MAX_QUESTIONS:
            course = st.session_state.qa_course
            level = st.session_state.qa_level
            topics = st.session_state.qa_topics
            step = st.session_state.qa_step
            current_q = st.session_state.qa_questions[step - 1]

            st.markdown(f"""
            <div class="s-form">
                <div class="s-form-title">Background Assessment</div>
                <div class="s-form-desc">{course} Program &middot; Question {step} of {MAX_QUESTIONS}</div>
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

            st.markdown('</div>', unsafe_allow_html=True)

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

            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            bc1, bc2 = st.columns([1, 3])
            with bc1:
                if st.button("← Retake", key="qa_retake"):
                    st.session_state.qa_step = 0
                    st.session_state.qa_questions = []
                    st.session_state.qa_answers = []
                    st.rerun()
            with bc2:
                if st.button("Generate My Primer →", type="primary", key="gen_primer"):
                    from models.schemas import QuestionnaireInput, QnA
                    from pipelines.dynamic import DynamicPrimerPipeline
                    from modules.groot.generator import GrootSlideGenerator
                    from modules.tts.elevenlabs import ElevenLabsTTS
                    from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
                    from modules.storage.local import LocalStorage
                    from modules.personalization.claude import ClaudePersonalization

                    curriculum = {"course": course, "topics": topics}
                    qna_list = [QnA(question=q, answer=a) for q, a in qa_pairs]

                    el_k, el_v = _get_el_creds()
                    pipeline = DynamicPrimerPipeline(
                        personalization=ClaudePersonalization(),
                        slide_generator=GrootSlideGenerator(cookies=GROOT_COOKIES),
                        tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
                        video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
                        storage=LocalStorage(base_path=OUTPUT_DIR),
                        temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
                    )
                    questionnaire = QuestionnaireInput(
                        course=course, group_level=level,
                        curriculum=curriculum, questions_and_answers=qna_list,
                    )
                    os.makedirs(TEMP_DIR, exist_ok=True)
                    os.makedirs(OUTPUT_DIR, exist_ok=True)

                    with st.spinner("Analyzing your profile and generating primers..."):
                        result = pipeline.run(questionnaire, student_id="student_demo_001")

                    sections = {}
                    for v in result.videos:
                        sections.setdefault(v.section, []).append({
                            "path": v.video_path, "topic": v.topic, "section": v.section,
                        })
                    st.session_state.last_gen_result = {
                        "type": "primer",
                        "topic": f"{course} — Personalized Primer",
                        "sections": sections,
                        "videos": [{"path": v.video_path, "topic": v.topic, "section": v.section} for v in result.videos],
                        "total": len(result.videos),
                    }
                    # Reset questionnaire for next time
                    st.session_state.qa_step = 0
                    st.session_state.qa_questions = []
                    st.session_state.qa_answers = []
                    st.rerun()

    # ── Case Study Document ───────────────────────────────────────────────────
    else:
        st.markdown("""
        <div class="s-form">
            <div class="s-form-title">Case Study Video</div>
            <div class="s-form-desc">Paste your case study document. Our AI will structure it into a detailed, guided video walkthrough.</div>
        """, unsafe_allow_html=True)

        doc_topic = st.text_input("Case Study Name", value="Driver Drowsiness Detection System", key="doc_t")

        st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        with c1:
            problem = st.text_area("Problem Statement", height=200, placeholder="Paste the problem statement...", key="doc_p")
        with c2:
            dataset = st.text_area("Dataset Description", height=200, placeholder="Paste dataset details...", key="doc_d")

        approach = st.text_area("Approach / Methodology", height=200, placeholder="Paste the approach document...", key="doc_a")

        st.markdown('<div class="s-divider"></div>', unsafe_allow_html=True)

        if st.button("Generate Case Study Video", type="primary", key="gen_doc"):
            if not problem.strip() or not approach.strip():
                st.markdown('<div class="s-error">Please provide at least the Problem Statement and Approach.</div>', unsafe_allow_html=True)
            else:
                call_llm = _get_call_llm(max_tokens=16000)
                if not call_llm:
                    st.markdown('<div class="s-error">Claude API key required. Set ANTHROPIC_API_KEY in .env</div>', unsafe_allow_html=True)
                else:
                    from pipelines.document import DocumentPipeline
                    from modules.tts.elevenlabs import ElevenLabsTTS
                    from modules.video_assembler.ffmpeg import FFmpegVideoAssembler
                    from modules.storage.local import LocalStorage

                    el_k, el_v = _get_el_creds()
                    pipeline = DocumentPipeline(
                        tts=ElevenLabsTTS(api_key=el_k, voice_id=el_v),
                        video_assembler=FFmpegVideoAssembler(temp_dir=TEMP_DIR),
                        storage=LocalStorage(base_path=OUTPUT_DIR),
                        call_llm=call_llm, temp_dir=TEMP_DIR, output_dir=OUTPUT_DIR,
                    )
                    os.makedirs(TEMP_DIR, exist_ok=True)
                    os.makedirs(OUTPUT_DIR, exist_ok=True)

                    with st.spinner(f"Generating case study video for \"{doc_topic}\"..."):
                        video_path = pipeline.run(
                            topic=doc_topic,
                            problem_statement=problem,
                            dataset_description=dataset or "(Not provided)",
                            approach_document=approach,
                        )

                    if video_path and os.path.exists(video_path):
                        st.session_state.last_gen_result = {
                            "type": "document",
                            "topic": doc_topic,
                            "videos": [{"path": video_path, "topic": doc_topic, "section": "Case Study"}],
                        }
                        st.rerun()
                    else:
                        st.markdown('<div class="s-error">Generation failed. Check logs.</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    # ── Show generation results ───────────────────────────────────────────────
    result = st.session_state.last_gen_result
    if result:
        st.markdown("<div class='s-spacer'></div>", unsafe_allow_html=True)

        total = len(result.get("videos", []))
        st.markdown(f"""
        <div class="s-success">
            Generated <strong>{total} video{'s' if total != 1 else ''}</strong> for
            <strong>{result['topic']}</strong>
        </div>
        """, unsafe_allow_html=True)

        # If sections exist (primer), show grouped by section
        sections = result.get("sections")
        if sections:
            for section_name, vids in sections.items():
                st.markdown(f"""
                <div class="s-section-divider">
                    <span>{section_name}</span>
                    <span class="s-section-count">{len(vids)} video{'s' if len(vids) != 1 else ''}</span>
                </div>
                """, unsafe_allow_html=True)

                for i, vid in enumerate(vids):
                    dur = _video_duration_str(vid["path"]) if os.path.exists(vid["path"]) else ""
                    st.markdown(f"""
                    <div class="s-result-card">
                        <div class="s-result-num">{i + 1}</div>
                        <div class="s-result-body">
                            <div class="s-result-title">{vid['topic']}</div>
                            <div class="s-result-sub">{dur + ' &middot; ' if dur else ''}AI Generated</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    if os.path.exists(vid["path"]):
                        with st.expander(f"Watch: {vid['topic']}"):
                            with open(vid["path"], "rb") as f:
                                st.video(f.read())
                            with open(vid["path"], "rb") as f:
                                st.download_button(
                                    "Download",
                                    f.read(),
                                    file_name=os.path.basename(vid["path"]),
                                    mime="video/mp4",
                                    key=f"dl_s_{section_name}_{i}",
                                )
        else:
            # Single or document — show video directly
            for i, vid in enumerate(result.get("videos", [])):
                if os.path.exists(vid["path"]):
                    st.markdown('<div class="s-player-frame">', unsafe_allow_html=True)
                    with open(vid["path"], "rb") as f:
                        st.video(f.read())
                    st.markdown('</div>', unsafe_allow_html=True)

                    dur = _video_duration_str(vid["path"])
                    size_mb = os.path.getsize(vid["path"]) / (1024 * 1024)
                    st.markdown(f"""
                    <div class="s-player-info">
                        <div class="s-player-title">{vid['topic']}</div>
                        <div class="s-player-sub">{dur + ' &middot; ' if dur else ''}{size_mb:.1f} MB &middot; AI Generated</div>
                    </div>
                    """, unsafe_allow_html=True)

                    with open(vid["path"], "rb") as f:
                        st.download_button(
                            "Download MP4",
                            f.read(),
                            file_name=os.path.basename(vid["path"]),
                            mime="video/mp4",
                            key=f"dl_v_{i}",
                        )

        # Clear results button
        if st.button("Clear Results", key="clear_gen"):
            st.session_state.last_gen_result = None
            st.rerun()


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

    all_videos = _scan_videos()

    if not all_videos:
        st.markdown("""
        <div class="s-empty">
            <div class="s-empty-icon">&#9654;</div>
            <div class="s-empty-title">No videos yet</div>
            <div class="s-empty-sub">Head to Generate Primer to create your first video.</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Generate Primer", type="primary"):
            st.session_state.page = "generate"
            st.rerun()
    else:
        # Group by category
        categories = {}
        for v in all_videos:
            categories.setdefault(v["category"], []).append(v)

        # Category filter
        cat_names = list(categories.keys())
        if len(cat_names) > 1:
            selected_cat = st.radio(
                "Filter",
                ["All"] + cat_names,
                horizontal=True,
                label_visibility="collapsed",
            )
        else:
            selected_cat = "All"

        st.markdown("<div class='s-spacer-sm'></div>", unsafe_allow_html=True)

        # Two-column: list + player
        left_col, right_col = st.columns([2, 3])

        with left_col:
            show_cats = cat_names if selected_cat == "All" else [selected_cat]
            for cat in show_cats:
                vids = categories[cat]
                st.markdown(f"""
                <div class="s-section-divider">
                    <span>{cat.upper()}</span>
                    <span class="s-section-count">{len(vids)}</span>
                </div>
                """, unsafe_allow_html=True)

                for i, vid in enumerate(vids):
                    is_active = (st.session_state.play_video and
                                 st.session_state.play_video.get("path") == vid["path"])
                    style = "border-left: 3px solid #0055FF;" if is_active else ""
                    dur = _video_duration_str(vid["path"])

                    st.markdown(f"""
                    <div class="s-vcard" style="{style}">
                        <div class="s-vcard-body" style="padding:14px 18px">
                            <div class="s-vcard-cat">{vid['category'].upper()}</div>
                            <div class="s-vcard-title" style="font-size:13px">{vid['name']}</div>
                            <div class="s-vcard-meta">{dur + ' &middot; ' if dur else ''}{vid['size_mb']:.1f} MB</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button("Play", key=f"lib_{cat}_{i}"):
                        st.session_state.play_video = vid
                        st.rerun()

        with right_col:
            if st.session_state.play_video and os.path.exists(st.session_state.play_video["path"]):
                vid = st.session_state.play_video
                st.markdown('<div class="s-player-frame">', unsafe_allow_html=True)
                with open(vid["path"], "rb") as f:
                    st.video(f.read())
                st.markdown('</div>', unsafe_allow_html=True)

                dur = _video_duration_str(vid["path"])
                st.markdown(f"""
                <div class="s-player-info">
                    <div class="s-player-title">{vid['name']}</div>
                    <div class="s-player-sub">{vid['category'].title()} &middot; {dur + ' &middot; ' if dur else ''}{vid['size_mb']:.1f} MB</div>
                </div>
                """, unsafe_allow_html=True)

                with open(vid["path"], "rb") as f:
                    st.download_button(
                        "Download MP4",
                        f.read(),
                        file_name=os.path.basename(vid["path"]),
                        mime="video/mp4",
                        key="lib_dl",
                    )
            else:
                st.markdown("""
                <div class="s-empty" style="margin-top:20px">
                    <div class="s-empty-icon">&#9654;</div>
                    <div class="s-empty-title">Select a video</div>
                    <div class="s-empty-sub">Click Play on any video from the list.</div>
                </div>
                """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="s-footer">
    <span>Scaler &middot; AI-Powered Learning &middot; Primer System v3.0</span>
</div>
""", unsafe_allow_html=True)
