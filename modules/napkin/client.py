"""
Napkin.ai API Client
====================
Converts text (slide content) into visual diagrams (PNG).

Flow: POST /v1/visual → poll /v1/visual/{id}/status → download file URL.
Files expire 30 minutes after generation, so we always save locally.

API key: get from app.napkin.ai → Account Settings → Developers.

Correct field names (confirmed from official docs):
  submit body: { "content": ..., "format": "png", "number_of_visuals": 1 }
  submit response: { "id": "...", "status": "pending" }
  status response: { "status": "completed", "generated_files": [{ "url": "..." }] }
"""

import time
import requests
from utils.logger import get_logger

logger = get_logger(__name__)

NAPKIN_BASE_URL = "https://api.napkin.ai"
_POLL_INTERVAL = 5       # seconds between status checks
_POLL_TIMEOUT  = 180     # seconds before giving up


class NapkinAPIClient:

    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    # ── Public ────────────────────────────────────────────────────────────────

    def generate_visual(self, text: str, output_path: str) -> str:
        """
        Full flow: submit text → poll until done → download PNG to output_path.
        Returns output_path on success. Raises on any failure.
        """
        request_id = self._submit(text)
        logger.info(f"  Napkin submitted: {request_id}")

        file_url = self._poll(request_id)
        self._download(file_url, output_path)
        logger.info(f"  Napkin visual saved: {output_path}")
        return output_path

    # ── Internal ──────────────────────────────────────────────────────────────

    def _submit(self, text: str) -> str:
        resp = self.session.post(
            f"{NAPKIN_BASE_URL}/v1/visual",
            json={
                "content": text,
                "format": "png",
                "number_of_visuals": 1,
                "width": 900,              # match the right-column size in the renderer
                "height": 900,
                "orientation": "auto",     # let Napkin pick best layout for the content
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        request_id = data.get("id")        # "id", NOT "request_id"
        if not request_id:
            raise RuntimeError(f"Napkin: no id in submit response: {data}")
        return request_id

    def _poll(self, request_id: str) -> str:
        """Poll until completed. Returns the download URL of the first generated file."""
        deadline = time.time() + _POLL_TIMEOUT
        while time.time() < deadline:
            resp = self.session.get(
                f"{NAPKIN_BASE_URL}/v1/visual/{request_id}/status",
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "")

            if status == "completed":
                return self._extract_file_url(data, request_id)

            if status == "failed":
                err = data.get("error") or data
                raise RuntimeError(f"Napkin generation failed: {err}")

            time.sleep(_POLL_INTERVAL)

        raise TimeoutError(f"Napkin request {request_id} timed out after {_POLL_TIMEOUT}s")

    def _extract_file_url(self, data: dict, request_id: str) -> str:
        """Extract the download URL from a completed status response."""
        files = data.get("generated_files") or []   # "generated_files", NOT "files"
        if not files:
            raise RuntimeError(f"Napkin {request_id}: completed but no generated_files in response: {data}")
        first = files[0]
        url = first.get("url") if isinstance(first, dict) else first
        if not url:
            raise RuntimeError(f"Napkin {request_id}: no url in generated_files entry: {first}")
        return url

    def _download(self, url: str, output_path: str) -> None:
        resp = self.session.get(url, timeout=60)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(resp.content)
