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
import time

import requests

logger = logging.getLogger(__name__)

UPLOAD_URL = os.environ.get("UPLOAD_URL")
UPLOAD_TIMEOUT_SECONDS = 300

_last_upload_lock = threading.Lock()
_last_upload = {"filename": None, "duration_s": None, "mb": None, "mbps": None, "error": None}


def upload_async(path: str, filename: str) -> None:
    if not UPLOAD_URL:
        return
    threading.Thread(target=_upload, args=(path, filename), daemon=True).start()


def _upload(path: str, filename: str) -> None:
    # Upload is bandwidth/IO-bound, not CPU-bound, so duration + throughput
    # (not CPU%) is the meaningful cost to report for "sending to server."
    size_mb = os.path.getsize(path) / (1024 * 1024)
    t0 = time.perf_counter()
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                UPLOAD_URL, files={"file": (filename, f)}, timeout=UPLOAD_TIMEOUT_SECONDS
            )
        resp.raise_for_status()
        duration = time.perf_counter() - t0
        with _last_upload_lock:
            _last_upload.update(
                filename=filename,
                duration_s=duration,
                mb=size_mb,
                mbps=size_mb / duration if duration > 0 else None,
                error=None,
            )
        logger.info("uploaded %s to central storage (%.1fs, %.1f MB)", filename, duration, size_mb)
    except Exception as e:
        with _last_upload_lock:
            _last_upload.update(
                filename=filename, duration_s=None, mb=size_mb, mbps=None, error=str(e)
            )
        logger.error("failed to upload %s to central storage: %s", filename, e)


def last_upload_stats() -> dict:
    with _last_upload_lock:
        return dict(_last_upload)
