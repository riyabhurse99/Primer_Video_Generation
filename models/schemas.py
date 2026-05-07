from pydantic import BaseModel
from typing import Optional


# ─── INPUT SCHEMAS ─────────────────────────────────────────────────────────────

class CurriculumInput(BaseModel):
    """Input for Generic Primer Pipeline."""
    course: str                          # e.g. "AIML"
    group_level: str                     # "basic" | "intermediate" | "advanced"
    curriculum: dict                     # raw curriculum data from Scaler


class QnA(BaseModel):
    """Single question-answer pair from student."""
    question: str
    answer: str


class QuestionnaireInput(BaseModel):
    """Input for Dynamic Primer Pipeline."""
    course: str
    group_level: str                     # from Scaler's grouping
    curriculum: dict                     # course curriculum — Claude needs this to identify gaps
    questions_and_answers: list[QnA]


# ─── PIPELINE INTERNAL SCHEMAS ─────────────────────────────────────────────────

class Slide(BaseModel):
    """Single slide inside a video."""
    title: str
    content: list[str]                   # bullet points shown on slide
    narration: str                       # full text spoken by TTS for this slide


class VideoScript(BaseModel):
    """Complete script for one video."""
    topic: str
    depth: str                           # "beginner" | "intermediate" | "advanced"
    estimated_duration_minutes: int
    slides: list[Slide]


class Section(BaseModel):
    """One section containing multiple videos."""
    name: str
    videos: list[VideoScript]


class PrimerPlan(BaseModel):
    """Full primer plan — multiple sections, each with multiple videos."""
    course: str
    group_level: str
    sections: list[Section]


# ─── OUTPUT SCHEMAS ────────────────────────────────────────────────────────────

class GeneratedVideo(BaseModel):
    """Metadata of a successfully generated video."""
    section: str
    topic: str
    video_path: str
    duration_seconds: Optional[float] = None


class PrimerOutput(BaseModel):
    """Final output of the full primer generation."""
    course: str
    group_level: str
    primer_type: str                     # "generic" | "dynamic"
    videos: list[GeneratedVideo]
