from abc import ABC, abstractmethod
from models.schemas import CurriculumInput, QuestionnaireInput, PrimerPlan


class BasePersonalization(ABC):
    """
    Contract for the personalization module.
    Any LLM (Claude, GPT-4, Gemini) must implement these two methods.
    Swapping the LLM = swapping the implementation class, nothing else changes.
    """

    @abstractmethod
    def generate_generic_plan(self, input: CurriculumInput) -> PrimerPlan:
        """
        Reads course curriculum + group level.
        Decides sections, number of videos, topics, depth.
        Returns a full PrimerPlan.
        """
        pass

    @abstractmethod
    def generate_dynamic_plan(self, input: QuestionnaireInput) -> PrimerPlan:
        """
        Reads student questionnaire answers + group level.
        Identifies gaps, decides personalized sections and videos.
        Returns a full PrimerPlan.
        """
        pass
