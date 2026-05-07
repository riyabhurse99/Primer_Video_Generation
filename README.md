# Scaler Primer — AI Video Lecture Generator

Automatically generates personalized primer video lectures for Scaler students.
Given a topic, the system produces a complete MP4 video with AI-generated slides and voice narration — no manual effort required.

---

## What It Does

1. Takes a topic (e.g. "Python Lists")
2. Generates AI-designed slides using Groot's API (backed by GPT)
3. Generates voice narration using ElevenLabs TTS
4. Merges slides + audio into a final MP4 video using FFmpeg
5. Stores the video and makes it available in the dashboard

---

## Folder Structure

```
Scaler_Primer/
│
├── dashboard/
│   └── app.py                  # Streamlit UI — the web dashboard
│
├── pipelines/
│   ├── direct.py               # Direct pipeline — topic → video (no Claude needed)
│   ├── generic.py              # Generic pipeline — course curriculum → videos (needs Claude)
│   └── dynamic.py              # Dynamic pipeline — student questionnaire → videos (needs Claude)
│
├── modules/
│   ├── groot/
│   │   ├── client.py           # HTTP client for Groot's API
│   │   ├── generator.py        # Slide generation manager
│   │   └── renderer.py         # Draws Groot's JSON elements as PNG images (Pillow)
│   │
│   ├── tts/
│   │   ├── elevenlabs.py       # ElevenLabs TTS (current — best quality)
│   │   ├── groot_tts.py        # Groot TTS via OpenAI (fallback)
│   │   └── gtts.py             # Google TTS (free fallback)
│   │
│   ├── video_assembler/
│   │   └── ffmpeg.py           # Merges slide PNGs + MP3 audio into MP4
│   │
│   ├── storage/
│   │   └── local.py            # Saves final video to local output folder
│   │
│   └── personalization/
│       └── claude.py           # Uses Claude to generate video plan (needs Anthropic key)
│
├── models/
│   └── schemas.py              # Data models (VideoScript, Slide, PrimerPlan, etc.)
│
├── config/
│   └── settings.py             # Reads .env file and exposes config values
│
├── utils/
│   ├── logger.py               # Logging setup
│   └── pptx_to_images.py       # PPTX → PNG converter (used by generic/dynamic pipelines)
│
├── .env                        # API keys and config (never commit this)
└── requirements.txt            # Python dependencies
```

---

## How It Works — The Flow

When you enter a topic and click Generate:

```
1. dashboard/app.py
   └── Creates tools (slide generator, TTS, video assembler, storage)
   └── Calls pipeline.run("Python Lists")

2. pipelines/direct.py
   └── Coordinates all steps

3. modules/groot/generator.py  →  generate_slides("Python Lists", images_dir)
   └── Calls client.build_stage()          [local, no API call]
   └── For each of 4 scenes:
         Calls client.get_scene_content()  [API call → slide elements JSON]
         Calls client.get_scene_actions()  [API call → narration text]
         Calls renderer.render_png()       [Pillow draws PNG from JSON]
   └── Returns (image_paths, narrations)

4. modules/tts/elevenlabs.py  →  generate_audio(narration, audio_path)
   └── POST to ElevenLabs API → saves MP3
   └── Repeated once per slide

5. modules/video_assembler/ffmpeg.py  →  assemble(images, audios, output)
   └── For each slide + audio → FFmpeg creates a short clip
   └── FFmpeg joins all clips → final MP4

6. modules/storage/local.py  →  save(video_path, destination)
   └── Copies MP4 to ./output/direct/

7. dashboard/app.py
   └── Shows video inline + download button
```

**Total API calls per video (4 slides):**
- 8 calls to Groot (scene-content + scene-actions × 4 scenes)
- 4 calls to ElevenLabs (one per slide narration)

---

## API Keys Required

| Key | Used For | Required? |
|---|---|---|
| `ELEVENLABS_API_KEY` | Voice narration | Yes |
| `ELEVENLABS_VOICE_ID` | Which voice to use | Yes |
| `ANTHROPIC_API_KEY` | Claude for video planning | Only for generic/dynamic pipelines |
| `GROOT_COOKIES` | Groot authentication | No — API works without it |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Install FFmpeg

```bash
brew install ffmpeg       # macOS
sudo apt install ffmpeg   # Ubuntu/Linux
```

### 3. Configure `.env`

```
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=your_voice_id_here
ANTHROPIC_API_KEY=your_key_here        # optional for now
GROOT_COOKIES=                          # leave empty
STORAGE_BACKEND=local
LOCAL_STORAGE_PATH=./output
USE_MOCKS=false
```

### 4. Run the dashboard

```bash
streamlit run dashboard/app.py
```

---

## Dashboard Pages

| Page | What It Does |
|---|---|
| **Generate Video** | Enter a topic → generate a full video (no Claude needed) |
| **Generate Primer** | Generate full course primer using Claude (needs Anthropic key) |
| **Video Library** | Browse and play all generated videos |
| **Module Tester** | Test each component independently (slides, audio, video assembly) |

---

## Key Technical Decisions

### Why Groot for Slides?
Groot (`groot-pied.vercel.app`) is a free AI classroom tool. We reverse-engineered its API to generate slides without any API key. It uses GPT internally (server-configured) to design slides and generate narrations.

### Why ElevenLabs for TTS?
ElevenLabs produces the most natural-sounding voice quality. Supports voice cloning — you can clone a specific person's voice with just 10 seconds of audio. We use `eleven_multilingual_v2` model with `language_code: "en"` to force English output.

### Why FFmpeg for Video Assembly?
FFmpeg is the industry standard for video processing. It measures each audio file's exact duration and holds the slide image on screen for precisely that duration, then concatenates all clips into one final video.

### Groot Stage — What It Is
Groot's server is stateless — it doesn't store anything between requests. So before generating slides, we build a "stage" object locally that contains the topic, all scene outlines, and agent personas. We send this with every API call so GPT has full context.

### Agents — What They Are
Groot uses two AI characters — a teacher and an assistant — who together write the narration. GPT generates dialogue between them. The agent list must be sent as an array (not a dict) because Groot's server calls `agents.find()` internally.

---

## Pipelines

### Direct Pipeline (`pipelines/direct.py`)
- Input: topic string
- No Claude needed
- Groot decides slide content and narration
- Used by the Generate Video page

### Generic Pipeline (`pipelines/generic.py`)
- Input: course curriculum
- Needs Claude API key
- Claude decides what sections and videos to create
- Groot generates slides for each video

### Dynamic Pipeline (`pipelines/dynamic.py`)
- Input: course curriculum + student questionnaire answers
- Needs Claude API key
- Claude identifies student's knowledge gaps and personalizes the video plan

---

## Future Work

- [ ] Get Anthropic API key → enable Generic and Dynamic pipelines
- [ ] Voice cloning — clone instructor's voice using ElevenLabs
- [ ] Metrics and evals — track generation time, quality scores, success rates
- [ ] Cloud storage — save videos to S3 instead of local disk
- [ ] Number of slides — expose as user-configurable input
