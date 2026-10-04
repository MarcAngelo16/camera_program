"""Transcode a finished recording chunk to H.264, then hand it to upload.py.

The vendor SDK can only write "MSCV" (a per-frame, motion-JPEG-style
compression) or fully uncompressed -- there's no hook to use a better codec
during capture. MSCV has none of H.264's inter-frame compression, so a chunk
can be 10-20x larger than it needs to be for mostly-static content like a
padel court. Re-encoding after the fact, on the already-finished file, gets
that efficiency back without touching the live capture path at all.

Runs in the background so it never blocks the recording loop or the next
chunk starting. If ffmpeg is missing or the transcode fails for any reason,
falls back to uploading the original file untouched -- we'd rather ship an
oversized chunk than silently lose one.
"""

import logging
import os
import subprocess
import threading
from collections import deque

from .upload import upload_sync

logger = logging.getLogger(__name__)

TRANSCODE_TIMEOUT_SECONDS = 300
HISTORY_SIZE = 20  # recent chunks kept for the debug panel, newest first

_history_lock = threading.Lock()
_history = deque(maxlen=HISTORY_SIZE)


def _record_history(filename, original_mb, transcoded_mb, uploaded, error=None):
    with _history_lock:
        _history.appendleft({
            "filename": filename,
            "original_mb": original_mb,
            "transcoded_mb": transcoded_mb,
            "ratio": (original_mb / transcoded_mb) if transcoded_mb else None,
            "uploaded": uploaded,
            "error": error,
        })


def segment_history() -> list:
    with _history_lock:
        return list(_history)


def transcode_and_upload_async(path: str, filename: str) -> None:
    threading.Thread(target=_transcode_and_upload, args=(path, filename), daemon=True).start()


def _transcode_and_upload(path: str, filename: str) -> None:
    # Already running in a background thread (see transcode_and_upload_async),
    # so there's no need for upload itself to also be async here -- using the
    # blocking upload_sync lets us know whether it actually succeeded before
    # deciding what's safe to delete locally.
    root, _ext = os.path.splitext(filename)
    out_filename = f"{root}.mp4"
    out_path = os.path.join(os.path.dirname(path), out_filename)

    original_mb = os.path.getsize(path) / (1024 * 1024)

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", path,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                out_path,
            ],
            check=True,
            capture_output=True,
            timeout=TRANSCODE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        detail = e.stderr.decode(errors="replace")[-500:] if isinstance(e, subprocess.CalledProcessError) else str(e)
        logger.error("transcode failed for %s, uploading original instead: %s", filename, detail)
        # Original is the only copy that exists -- only delete it once its
        # upload is confirmed, same rule as the transcoded-success path.
        ok = upload_sync(path, filename)
        if ok:
            try:
                os.remove(path)
            except OSError:
                pass
        _record_history(filename, original_mb, None, ok, error=f"transcode failed: {detail}")
        return

    transcoded_mb = os.path.getsize(out_path) / (1024 * 1024)
    logger.info(
        "transcoded %s -> %s (%.1f MB -> %.1f MB)", filename, out_filename, original_mb, transcoded_mb
    )

    # The original's only job was to survive long enough to produce a valid
    # transcode -- ffmpeg already reported success, so it's done its job.
    # Deleting it now (not waiting for the upload too) is what actually
    # bounds local storage: this is the large MSCV file, and holding it
    # through the upload step as well would defeat the point of
    # transcoding promptly in the first place.
    try:
        os.remove(path)
    except OSError:
        pass

    ok = upload_sync(out_path, out_filename)
    if ok:
        try:
            os.remove(out_path)
        except OSError:
            pass
    else:
        logger.error(
            "upload of transcoded %s failed; keeping it locally until it can be retried",
            out_filename,
        )
    _record_history(filename, original_mb, transcoded_mb, ok, error=None if ok else "upload failed")
