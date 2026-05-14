from abc import ABC, abstractmethod
from models.schemas import VideoScript


class BaseSlideGenerator(ABC):
    """
    Contract for slide generation.
    Any slide tool (OpenMAIC, Gamma, custom) must implement this.
    Swapping slide generator = swapping implementation class only.
    """

    @abstractmethod
    def generate(self, video_script: VideoScript, output_path: str, reserve_corner: bool = False) -> str:
        """
        Takes a VideoScript and generates a PPTX file.
        Returns the path to the generated PPTX file.
        """
        pass
