import json
import re
import anthropic
from models.schemas import (
    CurriculumInput, QuestionnaireInput,
    PrimerPlan, Section, VideoScript, Slide
)
from modules.personalization.base import BasePersonalization
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from utils.logger import get_logger

logger = get_logger(__name__)


GENERIC_SYSTEM_PROMPT = """
You are an expert curriculum designer for Scaler Academy.
You will receive a course curriculum and a student group level (basic, intermediate, or advanced).

Your job:
1. Decide what sections are needed for the primer based on the curriculum
2. Decide how many videos per section (keep it focused — 2 to 5 videos per section)
3. For each video, generate a complete script with slides and narration

Rules:
- Basic group: start from scratch, use simple analogies, real-world examples, slower pace
- Intermediate group: assume basic awareness, focus on filling gaps, moderate pace
- Advanced group: skip fundamentals, go deep, use technical language, faster pace
- Each slide narration should be detailed enough to be spoken aloud (minimum 4-5 sentences)
- Keep each video between 8 to 15 minutes

Return ONLY valid JSON in this exact format:
{
  "sections": [
    {
      "name": "section name",
      "videos": [
        {
          "topic": "video topic",
          "depth": "beginner|intermediate|advanced",
          "estimated_duration_minutes": 10,
          "slides": [
            {
              "title": "slide title",
              "content": ["bullet point 1", "bullet point 2"],
              "narration": "full narration text spoken by the instructor for this slide"
            }
          ]
        }
      ]
    }
  ]
}
"""

DYNAMIC_SYSTEM_PROMPT = """
You are an expert educator for Scaler Academy.
You will receive a course curriculum, a student's questionnaire answers, and their group level.

Your job:
1. Understand what the course curriculum covers — the student needs to be ready for this
2. Identify specific knowledge gaps from the student's answers relative to the curriculum
3. Decide which sections and topics to cover to address those gaps
4. Generate a complete personalized primer plan with scripts and narration

Rules:
- Focus only on what the student actually needs — do not cover what they already know well
- Only generate content relevant to the upcoming course curriculum
- Tailor examples to what the student mentioned in their answers
- Match depth to group level but adjust further based on answers
- Each slide narration should be detailed enough to be spoken aloud (minimum 4-5 sentences)

Return ONLY valid JSON in this exact format:
{
  "sections": [
    {
      "name": "section name",
      "videos": [
        {
          "topic": "video topic",
          "depth": "beginner|intermediate|advanced",
          "estimated_duration_minutes": 10,
          "slides": [
            {
              "title": "slide title",
              "content": ["bullet point 1", "bullet point 2"],
              "narration": "full narration text spoken by the instructor for this slide"
            }
          ]
        }
      ]
    }
  ]
}
"""


class ClaudePersonalization(BasePersonalization):

    def __init__(self):
        import os as _os
        api_key = ANTHROPIC_API_KEY
        if not api_key:
            try:
                import streamlit as st
                api_key = st.secrets["ANTHROPIC_API_KEY"]
            except Exception:
                pass
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Add it to .env locally or "
                "Streamlit Cloud secrets (Settings → Secrets)."
            )
        # We pass the api_key explicitly to ensure the client authenticates correctly.
        self.client = anthropic.Anthropic(api_key=api_key)

    def _parse_response(self, raw: str, course: str, group_level: str) -> PrimerPlan:
        # Strip markdown code fences Claude sometimes wraps around JSON
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

        if not cleaned:
            logger.error(f"Empty response from Claude. Raw:\n{raw[:300]}")
            raise ValueError("Claude returned an empty response")

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Last resort: extract the first {...} block
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                data = json.loads(match.group())
            else:
                logger.error(f"Could not parse JSON. Raw response:\n{raw[:500]}")
                raise
        sections = []
        for s in data["sections"]:
            videos = []
            for v in s["videos"]:
                slides = [Slide(**slide) for slide in v["slides"]]
                videos.append(VideoScript(
                    topic=v["topic"],
                    depth=v["depth"],
                    estimated_duration_minutes=v["estimated_duration_minutes"],
                    slides=slides
                ))
            sections.append(Section(name=s["name"], videos=videos))
        return PrimerPlan(course=course, group_level=group_level, sections=sections)

    def generate_generic_plan(self, input: CurriculumInput) -> PrimerPlan:
        logger.info(f"Generating generic plan — course={input.course}, level={input.group_level}")
        user_message = f"""
Course: {input.course}
Group Level: {input.group_level}
Curriculum:
{json.dumps(input.curriculum, indent=2)}
"""
        response = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=GENERIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )
        if response.stop_reason == "max_tokens":
            logger.warning("Generic plan response was truncated (max_tokens hit) — JSON may be incomplete")
        return self._parse_response(response.content[0].text, input.course, input.group_level)

    def generate_dynamic_plan(self, input: QuestionnaireInput) -> PrimerPlan:
        logger.info(f"Generating dynamic plan — course={input.course}, level={input.group_level}")
        qna_text = "\n".join([f"Q: {q.question}\nA: {q.answer}" for q in input.questions_and_answers])
        user_message = f"""
Course: {input.course}
Group Level: {input.group_level}
Course Curriculum:
{json.dumps(input.curriculum, indent=2)}

Student Questionnaire Answers:
{qna_text}
"""
        response = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=DYNAMIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )
        if response.stop_reason == "max_tokens":
            logger.warning("Dynamic plan response was truncated (max_tokens hit) — JSON may be incomplete")
        return self._parse_response(response.content[0].text, input.course, input.group_level)
