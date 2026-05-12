# Future TODOs

## 1. Act on lecture-level eval failures
**File:** `utils/evals.py` — `eval_and_improve`

Currently the lecture-level eval runs once at the end and records a score, but nothing acts on a bad result. If `pass == false` (overall score < 3.5), the video still ships.

Options to consider:
- Surface the lecture eval verdict clearly in the Streamlit dashboard so the user can decide to regenerate
- Trigger a full pipeline re-run automatically if `pass == false` (costly — ~$0.90+ per run)
- At minimum, add a visible warning when lecture eval fails

Decision needed: is this a user-facing gate or an internal metric?

---

## 2. Add Groot fallback when API goes down
**File:** `pipelines/direct.py`, `modules/groot/client.py`

The entire DirectPipeline depends on Groot for slide content and narrations. There is no degraded mode — if Groot rate-limits, returns persistent errors, or goes offline, the run fails completely with no recovery.

Options to consider:
- On persistent Groot failure, fall back to generating slide content directly via the LLM (`call_llm`) + `render_fallback_slide` for visuals
- Add a circuit-breaker so repeated failures fast-fail instead of burning retries
- Surface a clear user-facing error message distinguishing "Groot down" from other failures

---

## 3. Prevent concurrent pipeline runs from the dashboard
**File:** `dashboard/app.py`

No button locking exists while a video generation job is running. A user can click "Generate Video" multiple times and trigger parallel pipelines, each consuming ElevenLabs quota and LLM calls.

Fix: use `st.session_state` to set an `is_running` flag when a job starts, disable the generate button while it's set, and clear it when the job completes or fails. Standard Streamlit pattern.

---

## Not Necessary (low/no real-world impact)

- **#1** — Type hint `agents: dict` should be `agents: list` in `groot/client.py`. Cosmetic only, no runtime impact.
- **#2** — `previousSpeeches` shape validation in `generator.py`. Theoretical risk but no observed failures.
- **#7** — Inconsistent image sizes (Groot vs LibreOffice). Only matters if PPTX pipeline is used; Groot path is fine.
- **#8** — ffmpeg `-c copy` concat may produce corrupt MP4 if clips differ. Not observed in practice with current setup.
- **#10** — ffprobe `streams[0]` assumption. Works fine for plain MP3 files, always will in current usage.
- **#12** — Partial voice_id in logs. Cosmetic, not a real security risk.
- **#13** — S3 bucket validation. Only relevant if S3 storage is used.
- **#14** — `os.chdir()` in `app.py`. Fragile in theory but not causing issues since absolute paths are used throughout.
- **#15** — Settings frozen at import time. Not a problem since `.env` is never hot-reloaded mid-session.
- **#17** — Hardcoded Chrome 147 UA headers in Groot client. Low priority for now — retry logic (#25) already helps with transient blocks.
- **#19** — Proxy file polymorphism (`groot_proxy:` hack). Works fine, architectural cleanup only.
- **#20** — 90% code duplication across pipelines. Maintenance concern only, not causing bugs.
- **#21** — Python 3.8 compat (`list[Foo]` syntax). Running on 3.10+, irrelevant.
- **#22** — Brittle JSON parsing in multiple places. Already handled well enough with current regex stripping.
- **#24** — MockTTS depends on `libmp3lame`. Dev-only, not production.
- **#26** — Dead deps (`moviepy`, `pdf2image`). Easy cleanup but no functional impact.
- **#27** — No startup banner for system tools. Nice-to-have, not critical.
- **P3 nits** — Double log lines, dead `_get_background_color`, magic numbers, HTML regex parsing, `CLAUDE_MODEL` hardcoded, `Programs/a.txt` empty file, no tests.
