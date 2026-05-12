# Critical Code Review — Primer Video Generation

Reviewer notes: deep read of `pipelines/`, `modules/`, `utils/`, `dashboard/app.py`, `config/settings.py`, `models/schemas.py`, `main.py`, `requirements.txt`. Calling out real bugs, design issues, security holes, and pragmatic fixes — in roughly descending priority.

---

## P0 — Real bugs / will bite in production

### 1. `client.get_scene_content` signature mismatch with caller
**File:** `modules/groot/client.py` line 367, `modules/groot/generator.py` line 100
The method declares `agents: dict` but `_build_default_agents()` returns a `list`, and the docstring of `client.py` itself says agents must be a list. The caller in `generator.py` passes the list. Type hint is misleading — minor — but more importantly the same parameter on `get_scene_actions` is also `agents: dict` (line 414). Won't crash, but anyone reading the code or running mypy will get confused fast.

**Fix:** change both type hints to `agents: list`. Same for `previous_speeches: list = None` description block.

---

### 2. `extract_speeches` reads from a list that is never assigned
**File:** `modules/groot/generator.py` lines 122–124
```python
actions = actions_resp.get("scene", {}).get("actions", [])
speeches = GrootAPIClient.extract_speeches(actions)
```
Fine so far. But you also do:
```python
previous_speeches = actions_resp.get("previousSpeeches", previous_speeches)
```
The Groot API returns `previousSpeeches` in the response, but the field shape isn't documented anywhere in the client. If the server returns the speeches in a different structure (e.g. `{role, text}` objects vs raw strings), the next `get_scene_actions` call will silently send malformed `previousSpeeches`, which is a recipe for "scene 2 onwards looks weird and we don't know why". No defensive normalization.

**Fix:** define a strict shape for `previous_speeches` (list of `{agentId, text}` dicts is what the Groot JS source uses), and normalize the response before storing it. Also log a warning if the type doesn't match expectation.

---

### 3. Eval+improve loop runs **before** TTS but after a metric is sent saying `tts_provider="elevenlabs"` — and that string is hardcoded
**File:** `pipelines/direct.py` line 116
```python
tts_provider="elevenlabs",
```
But the dashboard and `main.py` both pick TTS dynamically (gtts/elevenlabs/mock). The metric is just lying when you're running gTTS. The metrics dashboard then says "elevenlabs" for runs that used the free Google TTS. That destroys the value of the metric.

**Fix:** thread the actual TTS backend name through. E.g.
```python
tts_provider=type(self.tts).__name__
```
or set it explicitly when wiring the pipeline.

---

### 4. `eval_and_improve` runs slide-level eval on **every retry** but lecture-level eval also runs every retry — wasted LLM calls
**File:** `utils/evals.py` lines 606–614
You call `run_evals(...)` inside the retry loop, and `run_evals` runs *both* slide and lecture evals. Lecture eval doesn't drive any retry decision (only `needs_regeneration` from slide-eval does). So on a 3-retry run with all slides flagged, you make 3 lecture-eval calls instead of 1. At ~$0.01–0.03 per call that's wasted money and ~2–4× the latency.

**Fix:** split `run_evals` into `run_slide_evals` and `run_lecture_eval`. Loop slide-eval; run lecture-eval **once** at the end.

---

### 5. `eval_retries = attempt` set after `for` loop — wrong on the "all passed first try" path
**File:** `utils/evals.py` lines 620–653
If all slides pass on attempt 1, you `break` out, then set `final_result["eval_retries"] = attempt` where `attempt == 1`. That reads as "1 retry" but actually means "1 attempt, 0 retries". Off-by-one in metric naming.

**Fix:** rename the field to `eval_attempts` (which is what it actually counts) or set `final_result["eval_retries"] = attempt - 1`.

---

### 6. Generic and Dynamic pipelines silently swallow per-video failures, but `GeneratedVideo.video_path` becomes `None`
**File:** `pipelines/generic.py` line 90, `pipelines/dynamic.py` line 79
When `_generate_single_video` returns `None` (no image/narration pairs), the calling loop goes:
```python
generated_videos.append(GeneratedVideo(
    section=section.name, topic=video_script.topic,
    video_path=video_path  # could be None!
))
```
`GeneratedVideo.video_path: str` will fail Pydantic validation when `video_path is None` (it's not Optional). Either the append crashes or — worse, depending on Pydantic version — it stores literal `"None"` string. Either way, downstream code consuming `videos` is unsafe.

**Fix:** either
```python
if video_path is None:
    logger.warning(f"Skipping {video_script.topic} from output — generation returned None")
    continue
```
or make `video_path` Optional and add a `status` field.

---

### 7. `pptx_to_images` Pillow fallback always recreates `_make_clip` clips with `force_original_aspect_ratio=decrease,pad...` but Groot already produces 1920×1080
**File:** `modules/video_assembler/ffmpeg.py` line 39
The `-vf` filter pads/scales every input. Groot images are already 1920×1080 PNGs (per `renderer.py`), so the filter is a no-op cost. Fine. But the LibreOffice path produces images at 150 DPI which gives ~2475×1391 — those *do* get scaled. Inconsistent input sizes mean you can't use stream-copy concat reliably across pipelines. (Direct works because all inputs are 1920×1080 from one source. Generic/Dynamic with PPTX may hit re-encoding mismatch.)

**Fix:** explicitly resize *before* calling `_make_clip`, or set a fixed `-r 30` framerate for all clips so concat-copy is safe.

---

### 8. `_concat_clips` uses `-c copy` — will fail silently if clips have different codecs/parameters
**File:** `modules/video_assembler/ffmpeg.py` line 61
Stream copy concat requires identical codec parameters across all input clips. If even one clip happens to be encoded slightly differently (e.g. due to ffmpeg version differences across slides), the concat succeeds but produces a corrupt MP4. There is no validation step.

**Fix:**
- Either standardize all clips with explicit `-r`, `-pix_fmt`, `-profile:v`, `-level`, `-s 1920x1080` then `-c copy` works.
- Or use `-c:v libx264 -c:a aac` on the concat (re-encode) — slower but bulletproof.

---

### 9. Race condition: `temp_dir` is shared across concurrent generations
**File:** `pipelines/direct.py` line 44
```python
video_temp_dir = os.path.join(self.temp_dir, f"direct_{safe_topic}")
```
If two users in Streamlit hit "Generate Video" with the same topic at the same time, they collide on the same temp dir, overwrite each other's slides and audio, and one (or both) videos will be wrong. Streamlit's session model makes this a real concern even with one user opening two tabs.

**Fix:** include a uuid suffix in the temp dir, like the assembler already does (`assembly_{uuid}`):
```python
job_id = uuid.uuid4().hex[:8]
video_temp_dir = os.path.join(self.temp_dir, f"direct_{safe_topic}_{job_id}")
```

---

### 10. `_get_audio_duration` doesn't validate that `streams[0]` is the audio stream
**File:** `modules/video_assembler/ffmpeg.py` line 23
`ffprobe -show_streams` returns all streams. For a pure-audio MP3 there's only one, so it works today. But if anyone ever wraps audio in a container with metadata streams, this picks the wrong one and the duration is junk.

**Fix:** filter to audio:
```python
audio_streams = [s for s in data["streams"] if s.get("codec_type") == "audio"]
if not audio_streams:
    raise RuntimeError(f"No audio stream in {audio_path}")
return float(audio_streams[0]["duration"])
```

---

### 11. The Anthropic `_parse_response` assumes `response.content[0].text` — breaks on multi-block responses
**File:** `modules/personalization/claude.py` line 131
With newer Anthropic SDKs, `response.content` can be a list of `TextBlock`/`ToolUseBlock` etc. If Claude ever returns a thinking block first or even just wraps the JSON in two blocks, this crashes or grabs the wrong content.

**Fix:**
```python
text = "".join(b.text for b in response.content if hasattr(b, "text"))
return self._parse_response(text, ...)
```

Also: the code calls `json.loads(raw)` directly with no markdown fence stripping. Claude in practice often wraps JSON in ```json fences. You already have a robust `_parse_json` helper in `utils/evals.py` — reuse it here.

---

## P1 — Security / config / data hygiene

### 12. ElevenLabs API key is read from `st.secrets` AND env, but logging always reveals partial voice_id
**File:** `modules/tts/elevenlabs.py` line 22
```python
voice_id={'SET:' + self.voice_id[:6] + '...' if self.voice_id else 'EMPTY'}
```
Voice IDs aren't catastrophically secret, but logging the first 6 chars on every init is unnecessary leakage and lands in cloud logs. Likewise `api_key='SET' if ... else 'EMPTY'` is fine, but the pattern is worth standardizing.

**Fix:** log only `SET`/`EMPTY`. Remove the prefix display.

---

### 13. `s3.py` uses keys from settings and no explicit `STS`/role-based fallback — and bucket isn't validated
**File:** `modules/storage/s3.py`
- No check for `AWS_S3_BUCKET` being non-empty. `boto3.upload_file(file, "", dest)` will fail with a confusing error.
- Hardcodes `region_name=AWS_REGION` even when not set. The default `"ap-south-1"` is fine for you in Bangalore, but is silently wrong for everyone else.
- Doesn't set `ACL` or any kind of expiration. The returned URL `https://{bucket}.s3.{region}.amazonaws.com/{key}` only works if the bucket allows public reads. If it doesn't, the URL is useless and the caller has no idea.

**Fix:** validate bucket; optionally use `presigned URL` if private; handle the common "bucket exists but in different region" error.

---

### 14. `os.chdir(PROJECT_ROOT)` in `dashboard/app.py`
**File:** `dashboard/app.py` line 14
Changing the process working directory in a Streamlit app is fragile — it persists across requests and breaks if Streamlit reloads the script. This also breaks any relative path the user might pass in via the UI (e.g. uploaded curriculum file).

**Fix:** use absolute paths everywhere (you mostly already do via `os.path.join`). Drop the `os.chdir`.

---

### 15. `config/settings.py` reads env at import time → values are frozen
The settings module reads `os.getenv` and `st.secrets` once on import. If the user updates `.env` during a Streamlit session, nothing picks it up. Worse, the dashboard imports `ELEVENLABS_API_KEY` directly from the module *and* re-reads `st.secrets` inside the button handler — inconsistent and confusing.

**Fix:** make `_get` a function used at call sites, not constants. Or wrap settings in a `Settings()` class that re-reads each call. Or at least pick one pattern.

---

### 16. `GROOT_COOKIES` is silently allowed empty even though the JS source likely uses cookies for rate limiting
**File:** README + client. The README says "API works without it" — true today, but groot-pied is a third-party site and the entire pipeline depends on it. There is **no fallback** for when groot rate-limits, returns 401, or shuts down. The DirectPipeline has no degraded mode.

**Fix:** wrap groot calls with a circuit-breaker; on persistent failure, fall back to (a) the LLM directly via `call_llm` for slide content, plus (b) `render_fallback_slide` for visuals. Right now you'd just produce a video full of error placeholders.

---

### 17. Hardcoded user-agent and `Sec-Ch-Ua` headers will go stale fast
**File:** `modules/groot/client.py` lines 45–55
Pinning Chrome 147 / macOS 10.15.7 looks suspicious to a CDN (Vercel definitely fingerprints UA combos). When Vercel ships a bot-protection update, this UA is the first thing they'll flag.

**Fix:** rotate among a small pool of recent UAs, or use a maintained library like `fake-useragent`. Even better: stop spoofing browser headers entirely — call yourself something honest like `ScalerPrimer/1.0` and accept that you may need real auth eventually.

---

### 18. The whole reverse-engineered Groot dependency is a legal / continuity risk that isn't called out anywhere except in passing
README says "We reverse-engineered its API." There's no discussion of:
- Terms-of-service compliance
- What happens when the upstream changes their API contract
- Attribution of where slide content is generated

For a Scaler-shipped product this needs an explicit "build vs. buy vs. host" decision, not a footnote. This is about the **only** piece of the system that's not under your control, and it's the central piece. Worth flagging to the team.

---

## P2 — Design / architecture smells

### 19. `GrootSlideGenerator.generate()` (the BaseSlideGenerator interface) writes proxy files that are decoded by `pptx_to_images` via filename heuristics
**File:** `modules/groot/generator.py` line 67, `utils/pptx_to_images.py` line 153
```python
with open(output_path, "w") as f:
    f.write(f"groot_proxy:{png_list_path}")
```
Then `pptx_to_images` does `if os.path.exists(png_list_path)` and reads it. This is filename-based polymorphism — fragile, hard to test, and breaks the abstraction the `BaseSlideGenerator` is supposed to provide. The whole point of the base class is "any slide tool returns a PPTX." Now we have a "PPTX or pretend-PPTX-but-actually-PNG-list" duality that every consumer has to know about.

**Fix:** change `BaseSlideGenerator.generate` to return a structured result:
```python
@dataclass
class SlideArtifacts:
    images: list[str]
    narrations: list[str] | None  # narrations may not always be available
    pptx_path: str | None
```
Then `pptx_to_images` becomes a private helper to `MockSlideGenerator` only. No more proxy files.

---

### 20. `direct.py` and `generic.py` and `dynamic.py` duplicate ~90% of the same logic (pair narrations with images, generate audio, assemble, save)
The three pipelines diverge only in:
- where the slide content comes from
- how narrations are picked (groot vs. video_script)
- the storage path

Everything else (TTS loop, video assembly, storage save, error handling, metrics) is copy-pasted. With three copies, fixes will only land in the one you remember.

**Fix:** extract a `_assemble_one_video(images, narrations, output_destination, temp_dir)` helper. Each pipeline only does the part that differs.

---

### 21. Pydantic schemas use bare `list[QnA]`, `list[Slide]` — Python 3.9 compat will break
**File:** `models/schemas.py`
The `list[Foo]` syntax is 3.9+ for type hints in non-runtime contexts but Pydantic uses these at runtime. On Python 3.8 these crash at import time. Your `requirements.txt` doesn't pin a Python version.

**Fix:** add `python_requires=">=3.10"` (or whatever you need) to a setup file, OR fall back to `List[QnA]` from `typing`. Pin a python version in the README.

---

### 22. `generate_scene_titles` parses LLM output as JSON via regex stripping — brittle
**File:** `modules/groot/client.py` lines 233–253
You handle the markdown fence case but not "the LLM responded with a numbered list because it ignored the instruction." Same issue as the Anthropic client — your eval module already has a robust `_parse_json`. Reuse it everywhere.

---

### 23. `_default_scene_titles` returns 5 titles but `num_scenes` is variable
**File:** `modules/groot/client.py` line 156
```python
return titles[:num_scenes]
```
If `num_scenes > 5`, you silently get fewer titles than asked. The caller (`_build_default_outlines` line 274) checks `len(scene_titles) != num_scenes` and falls back to defaults… which is the same broken function. Infinite loop of fallback that gives 5 titles for any request of >5 scenes.

**Fix:** pad with generic "{topic}: Part N" titles up to `num_scenes`.

---

### 24. `MockTTS` uses libmp3lame at q=9 (lowest quality) — and ffmpeg might not have lame compiled in
**File:** `modules/tts/mock.py` line 32
Some minimal ffmpeg builds don't include lame. The mock then fails at runtime with no fallback. For a *mock* TTS this is silly — it doesn't need to be MP3.

**Fix:** generate a WAV (always works) or, even simpler, generate a tiny silent file with `pydub` + `numpy`. No subprocess, no ffmpeg dependency.

---

### 25. No retry / backoff anywhere for HTTP calls
**File:** every TTS, Groot, ElevenLabs call
Vercel cold starts, ElevenLabs concurrency limits, transient 429s — none have retry logic. Single transient blip = whole video failed.

**Fix:** wrap `requests.post` calls with `tenacity` or `urllib3.Retry` — exponential backoff on 429/5xx/timeouts.

---

### 26. `requirements.txt` has `moviepy` and `pdf2image` but nothing imports them
Just dead deps. Slows installs and bloats Docker images unnecessarily.

**Fix:** remove. Run `pip-audit` or `pipdeptree` to surface unused.

---

### 27. `requirements.txt` doesn't pin `ffprobe` / `ffmpeg` / `libreoffice` / `poppler` — system deps invisible
`packages.txt` has only `ffmpeg`. The `pptx_to_images` LibreOffice path silently degrades on systems without LibreOffice or `pdftoppm`. The fallback path produces visibly worse output. Users won't realize they're getting the bad path.

**Fix:** at startup, log a banner showing which optional system tools were found, e.g.:
```
[startup] LibreOffice: FOUND (/usr/bin/libreoffice)
[startup] pdftoppm:    NOT FOUND — pptx → png will use Pillow fallback
[startup] ffmpeg:      FOUND
```

---

### 28. `streamlit.metric` is used to display metrics that include error strings
**File:** `dashboard/app.py` Metrics page
Run history dataframe shows full `Error` column. If errors contain stack traces or API key fragments that leaked into exception messages (which happens), they're now rendered in the dashboard.

**Fix:** sanitize error text to first ~200 chars and strip anything that looks like a token (`sk_...`, `xi_...`, etc.).

---

### 29. `LocalStorage.save` uses `shutil.copy2` — duplicates large MP4s when the source is already in the project tree
For a multi-video Generic/Dynamic run, you generate to `./temp/...mp4` then copy to `./output/...mp4`. Disk usage doubles for the duration of the run. On a 512 MB Streamlit Cloud instance with multiple sessions, this matters.

**Fix:** `shutil.move` (rename) instead. The temp file is by definition disposable.

---

### 30. The `ask_user_input_v0` style of dashboard isn't here, but the dashboard buttons don't disable themselves while a job is running
A user can spam-click "Generate Video" and trigger N concurrent pipelines. With an external API like ElevenLabs, this drains your quota fast.

**Fix:** use `st.session_state` to track `is_running` and disable buttons. Standard Streamlit pattern.

---

## P3 — Minor / nits

- **`utils/logger.py`**: every `get_logger` call risks `addHandler` running twice in a Streamlit reload, leading to duplicated log lines. Guard with `logger.handlers` check (already done) but also set `logger.propagate = False` to stop double-logging through the root logger.
- **`renderer.py` `_get_background_color`** always returns white. Either remove the function or actually parse the `bg-color` from elements. Dead code is worse than no code.
- **`renderer.py` line 282**: empty-line spacing uses `int(line_h * 0.4)` — magic number. Pull to a named constant.
- **`renderer.py` HTML parsing**: re-implements a tag parser. For ~150 lines of regex you could just `pip install beautifulsoup4` and have correct, edge-case-safe parsing in 20 lines.
- **`config/settings.py` line 26** (`CLAUDE_MODEL = "claude-sonnet-4-6"`): valid identifier, but hardcoded in a constant with no override. Move to `_get("CLAUDE_MODEL") or "claude-sonnet-4-6"` so ops can change it without a code deploy.
- **`pipelines/dynamic.py` line 58**: `script_narrations = _json.load(_f)` is loaded then immediately overwritten on line 68 if `png_list` exists. Reads as if narrations matter, then they don't. Restructure.
- **`main.py` lines 71, 118**: `os.path.join(course, "curriculum.json")` looks for curriculum at `./AIML/curriculum.json` — a directory inside `course/` that isn't documented anywhere. Should be `data/curricula/{course}.json` or similar, with the path explicit in config.
- **README**: claims "8 calls to Groot (scene-content + scene-actions × 4 scenes)" — but the default in `generate_slides` is `num_scenes=4`, and `_run` uses whatever is passed. The README hard-codes the math.
- **`Programs/a.txt`**: empty file in the repo. Remove.
- **No tests anywhere.** Not even a smoke test that imports each module. For a pipeline with this many failure modes (network, file system, subprocess, LLM) tests are non-negotiable. Even a single `pytest tests/test_pipeline_smoke.py` that runs `USE_MOCKS=true` end-to-end would catch most regressions.

---

## Top-5 fixes I'd ship first

1. **Fix the `tts_provider` hardcode in `direct.py`** — your metrics dashboard is currently lying. (P0 #3)
2. **Add uuid to temp dirs** to prevent concurrent-run collisions. (P0 #9)
3. **Split slide-eval and lecture-eval** so the retry loop doesn't waste 60–70% of LLM cost. (P0 #4)
4. **Replace the proxy-file polymorphism** with a structured `SlideArtifacts` return type. (P2 #19) — this single change makes the next 3 pipelines you'll write in the future much easier.
5. **Add retry/backoff to all HTTP calls** (Groot, ElevenLabs, Anthropic). (P2 #25) — single biggest reliability win.

Beyond that, the **bigger strategic question** the team should think about: this whole thing depends on a free, reverse-engineered third-party API (Groot). That's fine for a prototype, but if this is going to ship to actual Scaler students, the slide-generation path needs an owned, supported alternative — either a paid Groot-equivalent (Gamma, Tome), or build it yourself directly via the LLM + Pillow path you already have most of the pieces for.
