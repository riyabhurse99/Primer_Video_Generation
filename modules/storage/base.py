from abc import ABC, abstractmethod


class BaseStorage(ABC):
    """
    Contract for storage.
    Local storage for prototype, S3 for production.
    Swapping storage = swapping implementation class only.
    """

    @abstractmethod
    def save(self, file_path: str, destination: str) -> str:
        """
        Saves a file to storage.
        Returns the accessible URL or path of the stored file.
        """
        pass
