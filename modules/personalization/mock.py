from models.schemas import (
    CurriculumInput, QuestionnaireInput,
    PrimerPlan, Section, VideoScript, Slide
)
from modules.personalization.base import BasePersonalization
from utils.logger import get_logger

logger = get_logger(__name__)


def _make_mock_slides(topic: str) -> list[Slide]:
    return [
        Slide(
            title=f"Introduction to {topic}",
            content=[
                f"What is {topic}?",
                "Why does it matter?",
                "Real-world applications"
            ],
            narration=(
                f"Welcome to this session on {topic}. "
                f"In this video, we are going to cover the fundamentals of {topic} "
                f"and understand why it is an essential concept for this course. "
                f"By the end of this session, you will have a clear understanding "
                f"of what {topic} is and where it is applied in the real world."
            )
        ),
        Slide(
            title=f"Core Concepts of {topic}",
            content=[
                "Concept 1 — definition and explanation",
                "Concept 2 — how it works",
                "Concept 3 — common patterns"
            ],
            narration=(
                f"Now let us dive into the core concepts of {topic}. "
                f"The first thing to understand is what it is at its most basic level. "
                f"Think of it like this — imagine you are organising items in a room. "
                f"Each concept in {topic} is like a specific rule for how those items "
                f"are arranged, accessed, and modified. Let us go through each one carefully."
            )
        ),
        Slide(
            title=f"Practical Example",
            content=[
                "Step-by-step walkthrough",
                "Common mistakes to avoid",
                "Best practices"
            ],
            narration=(
                f"Let us now look at a practical example of {topic} in action. "
                f"One of the most common mistakes beginners make is skipping this step. "
                f"We will walk through this example step by step so you can see "
                f"exactly how {topic} works in a real scenario. "
                f"Pay close attention to the best practices highlighted here — "
                f"these will save you a lot of time later."
            )
        ),
        Slide(
            title="Summary and Next Steps",
            content=[
                "Key takeaways from this session",
                "What comes next in the course",
                "Practice exercise"
            ],
            narration=(
                f"Great work making it to the end of this session on {topic}. "
                f"Let us quickly recap what we covered. "
                f"We started with the definition, moved to the core concepts, "
                f"and then walked through a practical example. "
                f"In the next session, we will build on this foundation "
                f"and go deeper into more advanced aspects of the topic."
            )
        )
    ]


class MockPersonalization(BasePersonalization):
    """
    Returns hardcoded mock data.
    Used when API keys are not yet available.
    Replace with ClaudePersonalization once keys are ready.
    """

    def generate_generic_plan(self, input: CurriculumInput) -> PrimerPlan:
        logger.info(f"[MOCK] Generating generic plan — course={input.course}, level={input.group_level}")
        return PrimerPlan(
            course=input.course,
            group_level=input.group_level,
            sections=[
                Section(
                    name="Python Basics",
                    videos=[
                        VideoScript(
                            topic="Variables and Data Types",
                            depth="beginner",
                            estimated_duration_minutes=10,
                            slides=_make_mock_slides("Variables and Data Types")
                        ),
                        VideoScript(
                            topic="Loops and Conditionals",
                            depth="beginner",
                            estimated_duration_minutes=12,
                            slides=_make_mock_slides("Loops and Conditionals")
                        )
                    ]
                ),
                Section(
                    name="SQL Fundamentals",
                    videos=[
                        VideoScript(
                            topic="SELECT Queries and Filtering",
                            depth="beginner",
                            estimated_duration_minutes=10,
                            slides=_make_mock_slides("SELECT Queries and Filtering")
                        )
                    ]
                )
            ]
        )

    def generate_dynamic_plan(self, input: QuestionnaireInput) -> PrimerPlan:
        logger.info(f"[MOCK] Generating dynamic plan — course={input.course}, level={input.group_level}")
        return PrimerPlan(
            course=input.course,
            group_level=input.group_level,
            sections=[
                Section(
                    name="Targeted Python Review",
                    videos=[
                        VideoScript(
                            topic="Functions and Scope (Gap Identified)",
                            depth="intermediate",
                            estimated_duration_minutes=8,
                            slides=_make_mock_slides("Functions and Scope")
                        )
                    ]
                )
            ]
        )
