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

## 4. Factual correctness checking in slide eval
**File:** `utils/evals.py` — `_build_slide_eval_prompt`, `_SLIDE_QUALITY_CRITERIA`

The current slide eval checks relevance and quality (clarity, level-appropriateness) but has no factual correctness dimension. For CS/ML topics, Claude can confidently generate wrong time complexities, incorrect algorithm steps, wrong API behaviours, or hallucinated library details — and the eval would not catch it.

The naive fix is adding a CORRECTNESS dimension (1–5) to the slide eval with a hard rule that any factual error caps the score at 2. This is better than nothing but has a fundamental limitation: **Claude evaluating Claude for factual correctness is unreliable.** The evaluator and the generator share the same training data and the same hallucinations — if Claude hallucinated that Dijkstra runs in O(n²), there is a real chance the evaluator also "knows" it as O(n²) and flags nothing.

Options to consider (in order of reliability):
- **RAG-based generation**: ground narration generation in verified reference material (textbooks, official docs) so hallucinations are less likely in the first place
- **Separate stronger model for fact-checking**: use a different model with a focused fact-checking prompt, ideally one with web search/tool access to verify claims
- **Human review gate**: for technically sensitive topics (algorithms, complexity bounds, ML math), flag for a subject-matter expert before shipping
- **Correctness dimension as a weak signal**: add it to the eval as a rough filter — it won't catch subtle errors but will catch obvious ones. Document clearly that it is not a reliable guarantee.

Decision needed: what is the acceptable risk level for factual errors in student-facing content, and which approach fits the production timeline?

---

## 5. Inter-rater reliability check for the eval judge
**File:** `utils/evals.py` — offline experiment, not a code change

LLM judges are noisy — the same narration evaluated twice can score differently. Before trusting these scores in production at scale, run an offline variance check:

1. Pick a sample of ~20 narrations across all four levels (5 per level), covering a range of quality (some clearly good, some clearly bad, some borderline).
2. Run `run_slide_evals` on each narration 5 times independently (separate `call_llm` calls, no caching).
3. Compute variance in `quality_score` across the 5 runs for each narration.
4. **Threshold**: if `quality_score` varies by more than ±1 point across runs on more than 20% of samples, the rubric is under-specified and needs more concrete anchors.

This tells you whether the current rubric produces reliable scores or just noisy ones that happen to look plausible. Do this before using eval scores as hard gates in production.

---

## 6. Human ground truth calibration
**File:** `utils/evals.py` — process task, not a code change

The eval is currently "LLM judges LLM" with no external anchor. You cannot know whether the judge is strict-correct (matching human expectations) or strict-wrong (systematically miscalibrated) without human labels.

Recommended process:
1. Hand-score 30–50 narrations yourself across all levels and topics. Score each on the same QUALITY (1–5) dimension the judge uses.
2. Compare your scores to judge scores using Pearson correlation or mean absolute error.
3. **Threshold**: if Pearson r < 0.70, the judge's scores don't correlate reliably with human judgment and the rubric needs rework.
4. Look specifically at systematic bias: does the judge consistently score 1 point higher than you? Lower? That tells you whether to shift the pass threshold.

This is the only way to know if the eval system is actually measuring what it claims to measure.

---

## 7. Narration-visual match (architectural gap)
**File:** `utils/evals.py` — `_build_slide_eval_prompt`, `run_slide_evals`

The slide eval receives only narration text — it has no access to the actual slide content (bullets, diagrams, code blocks) that Groot generated. This means the eval cannot catch cases where the narration drifts away from the visual: a narration that accurately explains topic X may still be wrong for this specific slide if the slide shows something different.

The fix requires passing slide content alongside narrations through the eval pipeline:
- `run_slide_evals(topic, narrations, level, call_llm)` needs a `slide_contents: list` param
- `_build_slide_eval_prompt` would include each slide's content alongside its narration
- The judge could then check: "does the narration describe what's on this slide?"

This is a medium-effort change that requires the pipeline to store and forward slide content from Groot all the way through to the eval call. Currently that data is not preserved after `generate_slides()` returns.

Decision needed: is the narration-visual drift a real observed problem, or theoretical? If it's theoretical, defer. If instructors are seeing slides where the audio doesn't match the visuals, prioritise this.

---

## 8. Reverse-engineer openmaic slide generation prompts
**Goal:** Figure out how openmaic generates the slides and capture all the prompts needed to get them, so that we can directly talk to OpenAI and remove Groot from the loop entirely. This will significantly improve reliability by removing an external undocumented dependency.

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
