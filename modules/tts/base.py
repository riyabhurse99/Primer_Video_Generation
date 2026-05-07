from abc import ABC, abstractmethod


class BaseTTS(ABC):
    """
    Contract for Text-to-Speech.
    Any TTS provider (ElevenLabs, OpenAI TTS, AWS Polly) must implement this.
    Swapping TTS provider = swapping implementation class only.
    """

    @abstractmethod
    def generate_audio(self, text: str, output_path: str) -> str:
        """
        Converts text to speech and saves the audio file.
        Returns the path to the generated audio file (MP3).
        """
        pass
