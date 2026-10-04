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

Transcoding can be turned off at runtime (set_transcode_enabled) to compare
original-vs-transcoded video quality/size directly -- uploaded filenames are
always labeled "_original" or "_h264" so the two are never ambiguous in the
recordings list, regardless of which mode produced a given file.
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

_enabled = True  # plain bool; GIL makes single reads/writes atomic enough here

_history_lock = threading.Lock()
_history = deque(maxlen=HISTORY_SIZE)


def set_transcode_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def is_transcode_enabled() -> bool:
    return _enabled


def _record_history(filename, original_mb, transcoded_mb, uploaded, mode, error=None):
    with _history_lock:
        _history.appendleft({
            "filename": filename,
            "original_mb": original_mb,
            "transcoded_mb": transcoded_mb,
            "ratio": (original_mb / transcoded_mb) if transcoded_mb else None,
            "uploaded": uploaded,
            "mode": mode,  # "transcoded" | "skipped" (toggle off) | "transcode_failed"
            "error": error,
        })


def segment_history() -> list:
    with _history_lock:
        return list(_history)


def transcode_and_upload_async(path: str, filename: str) -> None:
    threading.Thread(target=_transcode_and_upload, args=(path, filename), daemon=True).start()


def _upload_labeled_original(path: str, filename: str, mode: str, error: str | None) -> None:
    """Upload the untranscoded file, labeled unambiguously as "_original" --
    used both when transcoding is turned off and when ffmpeg itself fails.
    Only the upload's target filename changes; the local file is untouched
    until a confirmed upload makes it safe to delete."""
    root, ext = os.path.splitext(filename)
    labeled_name = f"{root}_original{ext}"
    original_mb = os.path.getsize(path) / (1024 * 1024)

    ok = upload_sync(path, labeled_name)
    if ok:
        try:
            os.remove(path)
        except OSError:
            pass
    _record_history(filename, original_mb, None, ok, mode, error=error if not ok else None)


def _transcode_and_upload(path: str, filename: str) -> None:
    # Already running in a background thread (see transcode_and_upload_async),
    # so there's no need for upload itself to also be async here -- using the
    # blocking upload_sync lets us know whether it actually succeeded before
    # deciding what's safe to delete locally.
    if not _enabled:
        _upload_labeled_original(path, filename, mode="skipped", error=None)
        return

    root, ext = os.path.splitext(filename)
    out_filename = f"{root}_h264.mp4"
    out_path = os.path.join(os.path.dirname(path), f"{root}.mp4")

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
        _upload_labeled_original(path, filename, mode="transcode_failed", error=detail)
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
    _record_history(
        filename, original_mb, transcoded_mb, ok, mode="transcoded",
        error=None if ok else "upload failed",
    )
