from abc import ABC, abstractmethod


class BaseVideoAssembler(ABC):
    """
    Contract for video assembly.
    Takes slide images and audio files, produces a final MP4.
    """

    @abstractmethod
    def assemble(self, slide_image_paths: list[str], audio_paths: list[str], output_path: str) -> str:
        """
        Combines slide images and corresponding audio files into a single MP4.
        Each slide_image_paths[i] pairs with audio_paths[i].
        Returns path to final MP4.
        """
        pass
