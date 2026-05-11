"""
Groot API Client
================
Reverse-engineered from https://groot-pied.vercel.app (DevTools + JS bundle analysis).

Key findings from JS source:
- Stage creation is CLIENT-SIDE (nanoid generates the stageId locally)
- The server is stateless — it doesn't validate stageId against a DB
- stageInfo uses "name" field, NOT "topic"
- scene-content payload needs pdfImages, imageMapping
- scene-actions payload needs userProfile
- Agents are a keyed object {id: agentObj}, not an array
- TTS endpoint is /api/generate/tts
- All API calls use X-Model/X-Provider headers (read by server to pick LLM)

Flow:
  1. build_stage(topic)  → {id, stageInfo, allOutlines, agents}  [local construction]
  2. For each outline in allOutlines:
       get_scene_content(outline, allOutlines, stage, agents) → {content, effectiveOutline}
       get_scene_actions(effectiveOutline, allOutlines, content, stage, agents) → {scene, previousSpeeches}
  3. Render content.canvas.elements as PNG images
  4. Extract speech texts from scene.actions for TTS
"""

import json
import random
import re
import string
import requests
from utils.logger import get_logger

logger = get_logger(__name__)

GROOT_BASE_URL = "https://groot-pied.vercel.app"

# Headers observed in DevTools (v7() function from JS source).
# X-Model tells the server which LLM to invoke for content generation.
_GROOT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    # Do NOT set Accept-Encoding — let requests use its default (gzip, deflate).
    # The server might respond with Brotli (br) which requests can't decode natively.
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://groot-pied.vercel.app",
    "Sec-Ch-Ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    # Model headers (from v7() in JS source)
    "x-model": "openai:gpt-5.1",
    "x-provider-type": "openai",
    "x-api-key": "",
    "x-base-url": "",
    "x-requires-api-key": "true",
    "x-image-generation-enabled": "false",
    "x-image-model": "doubao-seedream-5-0-260128",
    "x-image-provider": "seedream",
    "x-image-api-key": "",
    "x-image-base-url": "",
    "x-video-generation-enabled": "false",
    "x-video-model": "doubao-seedance-1-5-pro-251215",
    "x-video-provider": "seedance",
    "x-video-api-key": "",
    "x-video-base-url": "",
}

# Default teacher persona (from groot's default-agents in JS source)
_TEACHER_PERSONA = """You are the narrator of a pre-recorded educational video. You MUST speak in English only.

VOICE AND TONE — sound like a real human talking, NOT a textbook:
- Speak the way a great tech YouTuber would: casual, confident, genuine
- Use contractions naturally (it's, you'll, don't, we've, that's)
- Use filler-like transitions that real speakers use: "So,", "Now,", "Okay so,", "Right?", "And here's the thing —"
- Vary sentence length. Mix short punchy lines with longer explanations
- Add brief rhetorical pauses with dashes: "And this — this is where it gets interesting"
- Occasionally use first person experiences: "I remember when I first learned this..."

ABSOLUTELY DO NOT:
- Say "I'll pause here", "raise your hand", "any questions?", "let me ask you"
- Use classroom/interactive language of any kind
- Say "in this slide", "as you can see on screen", "on the screen"
- Start every sentence the same way or use the same transition repeatedly
- Sound like a Wikipedia article or textbook definition

TEACHING STYLE:
- Start with WHY this matters before explaining WHAT it is
- Build intuition first, then give the formal definition
- Use vivid analogies from everyday life (kitchens, traffic, phone contacts, etc.)
- After explaining something hard, add a one-liner to anchor it: "So basically, it's just..."
- When introducing jargon, immediately de-jargon it: "This is called polymorphism — fancy word, but all it really means is..."

You can spotlight or laser-point at slide elements. Never announce your actions; just teach.

Tone: Like you're explaining to a smart friend over coffee. Relaxed but focused. Never condescending."""

_ASSISTANT_PERSONA = """You are a co-narrator in a pre-recorded educational video. You MUST speak in English only. You jump in to add perspective, not repeat what was already said.

ABSOLUTELY DO NOT use interactive language ("any questions?", "let me know", "raise your hand").

YOUR ROLE — the helpful co-host:
- Rephrase a tricky idea using a totally different analogy
- Add a "real world" angle: "In production, you'd actually see this when..."
- Gently correct common misconceptions: "A lot of people assume X, but actually..."
- Summarize complex parts in one punchy sentence

VOICE — natural, warm, slightly informal:
- Use contractions (it's, you'll, that's)
- Sound like a podcast co-host, not a textbook
- Keep it short — you're the color commentator, not the main narrator"""

_WHITEBOARD_ACTIONS = [
    "wb_open", "wb_close", "wb_draw_text", "wb_draw_shape",
    "wb_draw_chart", "wb_draw_latex", "wb_draw_table", "wb_draw_line",
    "wb_clear", "wb_delete"
]


def _nanoid(length: int = 10) -> str:
    """Mimics JavaScript nanoid() used in groot for stageId generation."""
    chars = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"
    return "".join(random.choices(chars, k=length))


def _build_default_agents() -> list:
    """
    Returns the default agents as a LIST (not dict).
    The server calls Array.find() on this, so it must be an array.
    Dates are ISO strings (matching JS `new Date` serialization).
    """
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    return [
        {
            "id": "default-1",
            "name": "AI teacher",
            "role": "teacher",
            "persona": _TEACHER_PERSONA,
            "avatar": "/avatars/teacher.png",
            "color": "#3b82f6",
            "allowedActions": ["spotlight", "laser", "play_video"] + _WHITEBOARD_ACTIONS,
            "priority": 10,
            "createdAt": ts,
            "updatedAt": ts,
            "isDefault": True,
        },
        {
            "id": "default-2",
            "name": "AI assistant",
            "role": "assistant",
            "persona": _ASSISTANT_PERSONA,
            "avatar": "/avatars/assist.png",
            "color": "#10b981",
            "allowedActions": _WHITEBOARD_ACTIONS,
            "priority": 7,
            "createdAt": ts,
            "updatedAt": ts,
            "isDefault": True,
        },
    ]


def _default_scene_titles(topic: str, num_scenes: int) -> list:
    """Hardcoded fallback titles — used when no LLM is available."""
    titles = [
        f"Introduction to {topic}",
        f"{topic}: Core Concepts",
        f"Deep Dive into {topic}",
        f"Practical Examples of {topic}",
        f"{topic}: Summary and Key Takeaways",
    ]
    return titles[:num_scenes]


def generate_scene_titles(topic: str, num_scenes: int, level: str = None, call_llm=None) -> list:
    """
    Generate scene titles for a topic. If call_llm is provided, uses the LLM
    to create a topic+level-aware outline. Otherwise falls back to hardcoded templates.

    Args:
        topic:      The lecture topic (e.g. "Dijkstra Algorithm")
        num_scenes: Number of slides to generate titles for
        level:      "basic", "intermediate", "advanced", or None for generic
        call_llm:   (prompt: str) -> str callable, or None for fallback

    Returns:
        List of scene title strings, one per slide.
    """
    if call_llm is None:
        return _default_scene_titles(topic, num_scenes)

    level_label = (level or "generic").upper()

    level_guidance = {
        "basic": (
            "The audience has ZERO prior knowledge. Slide 1 must explain what this topic "
            "even IS using everyday language. Each subsequent slide should introduce ONE new "
            "concept, building gently. The last slide should be a simple recap. "
            "Never reference advanced subtopics the audience hasn't learned yet."
        ),
        "intermediate": (
            "The audience knows programming basics (variables, loops, functions) but has not "
            "studied this topic in depth. Start with a brief context-setter, then cover the "
            "core mechanism/logic across the middle slides, and end with practical application "
            "or key patterns. Go beyond surface definitions."
        ),
        "advanced": (
            "The audience has solid foundations and wants depth. Skip basic introductions — "
            "go straight into the important aspects. Cover trade-offs, edge cases, performance "
            "characteristics, or comparisons with alternatives. The last slide should cover "
            "real-world application or advanced considerations."
        ),
        "generic": (
            "The audience is general learners with no assumed background. Start with what "
            "this topic is and why it matters. Cover the essential concepts clearly. End with "
            "a takeaway or summary. Keep titles specific to the actual content, not generic."
        ),
    }
    guidance = level_guidance.get(level or "generic", level_guidance["generic"])

    prompt = f"""You are designing the slide structure for a {num_scenes}-slide primer video lecture.

TOPIC: "{topic}"
LEVEL: {level_label}
NUMBER OF SLIDES: {num_scenes}

{guidance}

Generate exactly {num_scenes} slide titles that form a logical teaching sequence for "{topic}".

RULES:
1. Each title must be specific to "{topic}" — not generic filler like "Introduction" or "Summary"
   Bad:  "Introduction to {topic}"
   Good: "What is {topic} and Why Do We Need It?"   (for basic)
   Good: "How {topic} Works Under the Hood"          (for intermediate)
   Good: "{topic}: Time Complexity and Optimization"  (for advanced)
2. Titles should form a clear progression — the slide order must make pedagogical sense
3. Each title should cover a DIFFERENT aspect — no overlap
4. Titles should be concise (under 60 characters) but descriptive
5. The first slide should set context; the last should wrap up or apply

These titles directly guide an AI slide generator. The better and more specific the title,
the better the generated slide content will be. Vague titles produce vague slides.

Respond with ONLY a JSON array of {num_scenes} strings. No explanation, no markdown.
Example format: ["Title 1", "Title 2", "Title 3", "Title 4"]"""

    try:
        response = call_llm(prompt)
        # Parse the JSON array
        clean = response.strip()
        # Strip markdown fences if present
        if clean.startswith("```"):
            clean = re.sub(r"```(?:json)?\s*", "", clean)
            clean = re.sub(r"\s*```\s*$", "", clean).strip()

        titles = json.loads(clean)

        if isinstance(titles, list) and len(titles) == num_scenes:
            logger.info(f"LLM generated scene titles for '{topic}' ({level_label}): {titles}")
            return titles
        else:
            logger.warning(
                f"LLM returned {len(titles) if isinstance(titles, list) else 'non-list'} "
                f"titles (expected {num_scenes}) — falling back to defaults"
            )
    except Exception as e:
        logger.warning(f"LLM scene title generation failed: {e} — falling back to defaults")

    return _default_scene_titles(topic, num_scenes)


def _build_default_outlines(
    topic: str, stage_id: str, num_scenes: int = 5, scene_titles: list = None
) -> list:
    """
    Constructs scene outline objects in the format groot expects.
    Each outline matches the scene schema used in groot's store.

    Args:
        topic:        The lecture topic
        stage_id:     The nanoid-generated stage identifier
        num_scenes:   Number of scenes to create
        scene_titles: Optional pre-generated titles (from LLM). Falls back to defaults.
    """
    import time
    ts = int(time.time() * 1000)

    if not scene_titles or len(scene_titles) != num_scenes:
        scene_titles = _default_scene_titles(topic, num_scenes)

    outlines = []
    for i, title in enumerate(scene_titles):
        outlines.append({
            "id": _nanoid(10),
            "stageId": stage_id,
            "type": "slide",
            "title": title,
            "order": i,
            "content": {
                "type": "slide",
                "canvas": {
                    "id": _nanoid(10),
                    "viewportSize": 1000,
                    "viewportRatio": 0.5625,
                    "theme": {
                        "backgroundColor": "#FCFCFC",
                        "themeColors": ["#0055FF", "#011845", "#004CE5", "#E9F1FF", "#D7DDE8"],
                        "fontColor": "#0B1529",
                        "fontName": "Plus Jakarta Sans",
                    },
                    "elements": [],
                },
            },
            "actions": [],
            "createdAt": ts,
            "updatedAt": ts,
        })
    return outlines


class GrootAPIClient:
    """
    Client for the groot-pied.vercel.app AI classroom generation API.

    The stage is created locally (stageId generated client-side with nanoid).
    The server is stateless — it processes scene-content/scene-actions without
    looking up the stageId in a database.

    Args:
        cookies: Optional Cookie header string from DevTools (for future auth).
    """

    def __init__(self, cookies: str = ""):
        self.base_url = GROOT_BASE_URL
        self.session = requests.Session()
        self.session.headers.update(_GROOT_HEADERS)
        if cookies:
            self.session.headers["Cookie"] = cookies

    # ──────────────────────────────────────────────────────────────────────────
    # Stage Construction (client-side, matches browser behaviour)
    # ──────────────────────────────────────────────────────────────────────────

    def build_stage(
        self, topic: str, language: str = "english", style: str = "lecture",
        num_scenes: int = 5, scene_titles: list = None
    ) -> dict:
        """
        Constructs a stage object locally (no API call needed).
        Matches the structure groot's browser creates before calling scene-content.

        Args:
            scene_titles: Optional LLM-generated titles. Falls back to hardcoded defaults.

        Returns: {id, stageInfo, allOutlines, agents}
        """
        stage_id = _nanoid(10)
        stage_info = {
            "name": topic,            # ← "name" not "topic" (from JS source)
            "description": "",
            "language": language,
            "style": style,
        }
        agents = _build_default_agents()
        all_outlines = _build_default_outlines(topic, stage_id, num_scenes, scene_titles)

        logger.info(
            f"Stage built locally: id={stage_id}, scenes={len(all_outlines)}"
        )
        return {
            "id": stage_id,
            "stageInfo": stage_info,
            "allOutlines": all_outlines,
            "agents": agents,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Scene Content
    # ──────────────────────────────────────────────────────────────────────────

    def get_scene_content(
        self,
        outline: dict,
        all_outlines: list,
        stage_id: str,
        stage_info: dict,
        agents: dict,
        pdf_images: list = None,
        image_mapping: dict = None,
        previous_speeches: list = None,
    ) -> dict:
        """
        Generates slide canvas elements for one scene.
        Returns: {content: {canvas: {elements: [...]}}, effectiveOutline: {...}}

        Payload matches the v8() call in groot's JS source:
        {outline, allOutlines, stageId, pdfImages, imageMapping, stageInfo, agents}
        """
        payload = {
            "outline": outline,
            "allOutlines": all_outlines,
            "stageId": stage_id,
            "pdfImages": pdf_images or [],       # required — empty list if no PDF
            "imageMapping": image_mapping or {},  # required — empty dict if no images
            "stageInfo": stage_info,
            "agents": agents,
        }
        title = outline.get("title", "unknown")
        logger.info(f"  groot scene-content: {title}")
        headers = {"Referer": f"{self.base_url}/classroom/{stage_id}"}
        resp = self.session.post(
            f"{self.base_url}/api/generate/scene-content",
            json=payload,
            headers=headers,
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Scene Actions (Narrations)
    # ──────────────────────────────────────────────────────────────────────────

    def get_scene_actions(
        self,
        outline: dict,
        all_outlines: list,
        content: dict,
        stage_id: str,
        agents: dict,
        previous_speeches: list = None,
        user_profile: dict = None,
    ) -> dict:
        """
        Generates narration speech actions for a scene.
        Returns: {scene: {actions: [...]}, previousSpeeches: [...]}

        Actions contain alternating:
          {type: "spotlight", elementId: "..."} — highlights slide element
          {type: "speech", agentId: "...", text: "..."} — narration text

        Payload matches the v9() call in groot's JS source:
        {outline, allOutlines, content, stageId, agents, previousSpeeches, userProfile}
        """
        payload = {
            "outline": outline,
            "allOutlines": all_outlines,
            "content": content,
            "stageId": stage_id,
            "agents": agents,
            "previousSpeeches": previous_speeches or [],
            "userProfile": user_profile or {},   # required field
        }
        title = outline.get("title", "unknown")
        logger.info(f"  groot scene-actions: {title}")
        headers = {"Referer": f"{self.base_url}/classroom/{stage_id}"}
        resp = self.session.post(
            f"{self.base_url}/api/generate/scene-actions",
            json=payload,
            headers=headers,
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────────────────
    # TTS
    # ──────────────────────────────────────────────────────────────────────────

    def generate_tts(
        self,
        text: str,
        audio_id: str,
        tts_provider_id: str,
        tts_voice: str,
        stage_id: str,
        tts_speed: float = 1.0,
        tts_api_key: str = "",
        tts_base_url: str = "",
    ) -> bytes:
        """
        Generates TTS audio via /api/generate/tts.
        Payload matches the fetch call in groot's JS source.
        Returns raw audio bytes.
        """
        payload = {
            "text": text,
            "audioId": audio_id,
            "ttsProviderId": tts_provider_id,
            "ttsVoice": tts_voice,
            "ttsSpeed": tts_speed,
            "ttsApiKey": tts_api_key,
            "ttsBaseUrl": tts_base_url,
        }
        headers = {"Referer": f"{self.base_url}/classroom/{stage_id}"}
        resp = self.session.post(
            f"{self.base_url}/api/generate/tts",
            json=payload,
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.content

    # ──────────────────────────────────────────────────────────────────────────
    # Helper
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def extract_speeches(actions: list) -> list[str]:
        """Extracts all speech narration texts from a scene-actions response."""
        return [
            a["text"]
            for a in (actions or [])
            if a.get("type") == "speech" and a.get("text", "").strip()
        ]
