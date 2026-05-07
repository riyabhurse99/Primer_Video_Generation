import json
import time
import requests
from models.schemas import VideoScript
from modules.slide_generator.base import BaseSlideGenerator
from config.settings import OPENMAIC_BASE_URL, OPENMAIC_API_KEY
from utils.logger import get_logger

logger = get_logger(__name__)

# OpenMAIC has a 50K character input limit — we chunk if needed
MAX_CHARS = 45000


class OpenMAICSlideGenerator(BaseSlideGenerator):

    def __init__(self):
        self.base_url = OPENMAIC_BASE_URL
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENMAIC_API_KEY}"
        }

    def _build_outline(self, video_script: VideoScript) -> str:
        outline = f"Topic: {video_script.topic}\nDepth: {video_script.depth}\n\nSlides:\n"
        for i, slide in enumerate(video_script.slides, 1):
            outline += f"\nSlide {i}: {slide.title}\n"
            for point in slide.content:
                outline += f"  - {point}\n"
        return outline

    def _submit_job(self, outline: str) -> str:
        payload = {"topic": outline, "duration": 15}
        response = requests.post(
            f"{self.base_url}/api/generate-classroom",
            headers=self.headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        return response.json()["job_id"]

    def _poll_until_done(self, job_id: str, timeout: int = 1800) -> dict:
        start = time.time()
        while time.time() - start < timeout:
            response = requests.get(
                f"{self.base_url}/api/job-status/{job_id}",
                headers=self.headers,
                timeout=10
            )
            data = response.json()
            if data["status"] == "complete":
                return data
            if data["status"] == "failed":
                raise RuntimeError(f"OpenMAIC job {job_id} failed: {data.get('error')}")
            time.sleep(15)
        raise TimeoutError(f"OpenMAIC job {job_id} timed out after {timeout}s")

    def _download_pptx(self, job_id: str, output_path: str) -> str:
        response = requests.get(
            f"{self.base_url}/api/export-pptx/{job_id}",
            headers=self.headers,
            timeout=60
        )
        response.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(response.content)
        return output_path

    def generate(self, video_script: VideoScript, output_path: str) -> str:
        logger.info(f"Generating slides via OpenMAIC for: {video_script.topic}")
        outline = self._build_outline(video_script)

        if len(outline) > MAX_CHARS:
            logger.warning("Content exceeds OpenMAIC limit — truncating to fit")
            outline = outline[:MAX_CHARS]

        job_id = self._submit_job(outline)
        logger.info(f"OpenMAIC job submitted: {job_id}")

        self._poll_until_done(job_id)
        logger.info(f"OpenMAIC job complete: {job_id}")

        return self._download_pptx(job_id, output_path)
