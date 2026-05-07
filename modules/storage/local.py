import os
import shutil
from modules.storage.base import BaseStorage
from utils.logger import get_logger

logger = get_logger(__name__)


class LocalStorage(BaseStorage):

    def __init__(self, base_path: str = "./output"):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

    def save(self, file_path: str, destination: str) -> str:
        dest_path = os.path.join(self.base_path, destination)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(file_path, dest_path)
        logger.info(f"Saved to local storage: {dest_path}")
        return dest_path
