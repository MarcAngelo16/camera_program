"""Push a finished recording/snapshot from the edge box to central storage.

Controlled entirely by the UPLOAD_URL env var: unset (the default, e.g. on
the central server itself) means "don't forward anywhere, this is already
where files should live." Set on an edge box to the central server's
/api/upload endpoint (e.g. http://10.10.0.1:8001/api/upload, reachable over
the WireGuard tunnel) and finished files get copied there in the background
so they're downloadable even after the edge box goes offline.
"""

import logging
import os
import threading

import requests

logger = logging.getLogger(__name__)

UPLOAD_URL = os.environ.get("UPLOAD_URL")
UPLOAD_TIMEOUT_SECONDS = 300


def upload_async(path: str, filename: str) -> None:
    if not UPLOAD_URL:
        return
    threading.Thread(target=_upload, args=(path, filename), daemon=True).start()


def _upload(path: str, filename: str) -> None:
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                UPLOAD_URL, files={"file": (filename, f)}, timeout=UPLOAD_TIMEOUT_SECONDS
            )
        resp.raise_for_status()
        logger.info("uploaded %s to central storage", filename)
    except Exception as e:
        logger.error("failed to upload %s to central storage: %s", filename, e)
