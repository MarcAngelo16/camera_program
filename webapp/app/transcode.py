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

from .upload import upload_sync

logger = logging.getLogger(__name__)

TRANSCODE_TIMEOUT_SECONDS = 300


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
        # Original is the only copy that exists -- never delete it here,
        # regardless of whether this upload succeeds; upload_sync itself
        # may retry/surface failures via last_upload_stats() for visibility.
        upload_sync(path, filename)
        return

    logger.info("transcoded %s -> %s", filename, out_filename)
    if upload_sync(out_path, out_filename):
        # Only delete the original now that the smaller replacement is
        # confirmed to have reached central storage -- this is the one
        # moment it's actually safe to free the local disk space that
        # chunking was built to bound in the first place.
        try:
            os.remove(path)
        except OSError:
            pass
    else:
        logger.error(
            "upload of transcoded %s failed; keeping original %s locally until it can be retried",
            out_filename, filename,
        )
