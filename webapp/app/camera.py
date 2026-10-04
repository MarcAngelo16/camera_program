"""
Thin controller around the MindVision MVSDK for a single camera.

Responsibilities:
- Hold one camera handle open for the lifetime of the process.
- Retry enumeration/init in the background until a camera appears on the network.
- Run a grab loop only while a recording is active (no live preview needed).
- Expose start/stop recording and one-shot snapshot.
"""

import os
import threading
import time
from collections import deque
from datetime import datetime

import psutil

from . import mvsdk
from .transcode import transcode_and_upload_async

RETRY_INTERVAL_SECONDS = 5
STATS_WINDOW = 100  # frames kept for the rolling ISP-time / fps averages
DEFAULT_CHUNK_SECONDS = 60

_process = psutil.Process(os.getpid())
_process.cpu_percent(interval=None)  # prime it; first real call below is meaningful


class CameraNotReady(Exception):
    pass


class CameraController:
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

        self._lock = threading.Lock()
        self._hCamera = None
        self._capability = None
        self._mono = False
        self._dev_info = None

        self._rgb_buffer = None

        self._recording = False
        self._record_thread = None
        self._record_stop_event = threading.Event()
        self._current_recording_file = None
        self._recording_started_at = None

        # A "recording" is actually a series of short chunks under the hood
        # (see start_recording) -- this is invisible to the API/website,
        # which only ever sees one continuous Start-to-Stop recording.
        self._session_id = None
        self._segment_index = 0
        self._segment_started_at_mono = None
        self._chunk_seconds = DEFAULT_CHUNK_SECONDS
        self._frame_rate = 25
        self._session_total_bytes = 0

        self._last_error = None
        self._current_resolution = None
        self._frame_buffer_bytes = None

        self._isp_times_ms = deque(maxlen=STATS_WINDOW)
        self._frame_timestamps = deque(maxlen=STATS_WINDOW)
        self._frames_processed = 0

        mvsdk.CameraSdkInit(1)

        self._retry_thread = threading.Thread(target=self._init_retry_loop, daemon=True)
        self._retry_thread.start()

    # ---------------------------------------------------------------- init

    def _init_retry_loop(self):
        while True:
            with self._lock:
                already_open = self._hCamera is not None
            if already_open:
                time.sleep(RETRY_INTERVAL_SECONDS)
                continue

            try:
                self._try_init()
            except Exception as e:
                self._last_error = str(e)

            time.sleep(RETRY_INTERVAL_SECONDS)

    def _try_init(self):
        devices = mvsdk.CameraEnumerateDevice()
        if len(devices) == 0:
            self._last_error = "no camera found"
            return

        dev_info = devices[0]
        hCamera = mvsdk.CameraInit(dev_info, -1, -1)

        capability = mvsdk.CameraGetCapability(hCamera)
        mono = capability.sIspCapacity.bMonoSensor != 0

        max_w = capability.sResolutionRange.iWidthMax
        max_h = capability.sResolutionRange.iHeightMax
        buffer_size = max_w * max_h * 3
        rgb_buffer = mvsdk.CameraAlignMalloc(buffer_size, 16)

        mvsdk.CameraSetIspOutFormat(
            hCamera,
            mvsdk.CAMERA_MEDIA_TYPE_MONO8 if mono else mvsdk.CAMERA_MEDIA_TYPE_RGB8,
        )
        mvsdk.CameraPlay(hCamera)

        current_res = mvsdk.CameraGetImageResolution(hCamera)

        with self._lock:
            self._hCamera = hCamera
            self._capability = capability
            self._mono = mono
            self._dev_info = dev_info
            self._rgb_buffer = rgb_buffer
            self._frame_buffer_bytes = buffer_size
            self._last_error = None
            self._current_resolution = {
                "width": current_res.iWidth,
                "height": current_res.iHeight,
                "description": current_res.GetDescription() or "default",
            }

    # -------------------------------------------------------------- status

    def status(self):
        with self._lock:
            if self._hCamera is None:
                return {
                    "connected": False,
                    "recording": False,
                    "last_error": self._last_error,
                }
            current_file_size_bytes = None
            if self._recording and self._current_recording_file:
                try:
                    current_file_size_bytes = os.path.getsize(
                        os.path.join(self.storage_dir, self._current_recording_file)
                    )
                except OSError:
                    pass
            return {
                "connected": True,
                "recording": self._recording,
                "current_file": self._current_recording_file,
                "current_file_size_bytes": current_file_size_bytes,
                "session_id": self._session_id,
                "segment_index": self._segment_index,
                # Sum of already-finished chunks this session; add
                # current_file_size_bytes for the live running total.
                "session_total_bytes": self._session_total_bytes,
                "recording_started_at": self._recording_started_at,
                "current_resolution": self._current_resolution,
                "device": {
                    "product_name": self._dev_info.GetProductName(),
                    "friendly_name": self._dev_info.GetFriendlyName(),
                    "serial_number": self._dev_info.GetSn(),
                    "port_type": self._dev_info.GetPortType(),
                },
            }

    def _require_camera(self):
        if self._hCamera is None:
            raise CameraNotReady("camera is not connected")
        return self._hCamera

    def supported_media_types(self):
        """Every output format this specific camera unit actually reports
        supporting, straight from its own capability list -- not from SDK
        documentation, which only says what *some* MindVision models can
        do, not what this one does. Used to check for real hardware
        H.264/H.265 output (CAMERA_MEDIA_TYPE_H264_MV / _H265_MV): if either
        appears here, this camera can encode in hardware and the whole
        MSCV-then-transcode pipeline becomes unnecessary; if not, it can't,
        regardless of what the SDK generally supports."""
        with self._lock:
            cap = self._capability
            if cap is None:
                return []
            return [
                {
                    "index": cap.pMediaTypeDesc[i].iIndex,
                    "description": cap.pMediaTypeDesc[i].GetDescription() or None,
                    "media_type": cap.pMediaTypeDesc[i].iMediaType,
                    "media_type_hex": f"0x{cap.pMediaTypeDesc[i].iMediaType:08X}",
                }
                for i in range(cap.iMediaTypeDesc)
            ]

    # ---------------------------------------------------------- resolution

    def resolution_presets(self):
        with self._lock:
            cap = self._capability
            if cap is None:
                return []
            return [
                {
                    "index": cap.pImageSizeDesc[i].iIndex,
                    "description": cap.pImageSizeDesc[i].GetDescription() or None,
                    "width": cap.pImageSizeDesc[i].iWidth,
                    "height": cap.pImageSizeDesc[i].iHeight,
                }
                for i in range(cap.iImageSizeDesc)
            ]

    def set_resolution(self, index: int):
        with self._lock:
            hCamera = self._require_camera()
            if self._recording:
                raise RuntimeError("cannot change resolution while recording")

            cap = self._capability
            chosen = None
            for i in range(cap.iImageSizeDesc):
                preset = cap.pImageSizeDesc[i]
                if preset.iIndex == index:
                    chosen = preset
                    break
            if chosen is None:
                raise ValueError(f"unknown resolution index {index}")

            mvsdk.CameraSetImageResolution(hCamera, chosen)
            self._current_resolution = {
                "width": chosen.iWidth,
                "height": chosen.iHeight,
                "description": chosen.GetDescription() or None,
            }

    # ---------------------------------------------------------------- grab

    def _grab_and_process(self):
        """Grab one frame and run it through the ISP into self._rgb_buffer.
        Returns the frame head. Raises on timeout/error."""
        hCamera = self._hCamera
        pRawData, frame_head = mvsdk.CameraGetImageBuffer(hCamera, 2000)
        try:
            t0 = time.perf_counter()
            mvsdk.CameraImageProcess(hCamera, pRawData, self._rgb_buffer, frame_head)
            isp_ms = (time.perf_counter() - t0) * 1000
        finally:
            mvsdk.CameraReleaseImageBuffer(hCamera, pRawData)
        self._isp_times_ms.append(isp_ms)
        self._frame_timestamps.append(time.monotonic())
        self._frames_processed += 1
        return frame_head

    def stats(self):
        """Resource usage: time spent in the SDK's ISP step per frame (the
        raw-sensor-to-RGB conversion, done on CPU), the frame rate that's
        actually being achieved, and overall process CPU/RAM. Useful for
        judging whether a given machine (e.g. a Raspberry Pi) can keep up."""
        isp_times = list(self._isp_times_ms)
        timestamps = list(self._frame_timestamps)

        isp_ms_avg = sum(isp_times) / len(isp_times) if isp_times else None

        achieved_fps = None
        if len(timestamps) >= 2:
            achieved_fps = (len(timestamps) - 1) / (timestamps[-1] - timestamps[0])

        # ISP cost in isolation: time per frame times frames/sec = fraction
        # of one core continuously busy doing *just* that native call --
        # independent of whatever else the process happens to be doing
        # (serving status polls, etc), unlike whole-process CPU%.
        isp_cpu_load_cores = None
        if isp_ms_avg is not None and achieved_fps is not None:
            isp_cpu_load_cores = (isp_ms_avg / 1000) * achieved_fps

        # psutil reports CPU% relative to one core (100% = one core fully
        # busy), which is meaningless without knowing how many cores the
        # machine has -- e.g. 100% means "maxed out" on a single-core Pi
        # but is nothing on a many-core laptop. Report both the raw percent
        # and how many cores that actually amounts to.
        cpu_percent = _process.cpu_percent(interval=None)
        cpu_count = os.cpu_count() or 1

        # Camera-side settings that cap achievable fps independently of CPU:
        # a frame-speed tier (Low/Normal/High, set once at init, defaults to
        # whatever the hardware ships with since nothing here calls
        # CameraSetFrameSpeed) and exposure time (longer exposure under dim
        # lighting directly reduces max fps; auto-exposure picks this for you).
        frame_speed_index = None
        frame_speed_max_index = None
        ae_enabled = None
        exposure_time_us = None
        with self._lock:
            hCamera = self._hCamera
            cap = self._capability
        if hCamera is not None:
            try:
                frame_speed_index = mvsdk.CameraGetFrameSpeed(hCamera)
                frame_speed_max_index = cap.iFrameSpeedDesc - 1 if cap else None
                ae_enabled = bool(mvsdk.CameraGetAeState(hCamera))
                exposure_time_us = mvsdk.CameraGetExposureTime(hCamera)
            except Exception:
                pass

        return {
            "frame_speed_index": frame_speed_index,
            "frame_speed_max_index": frame_speed_max_index,
            "ae_enabled": ae_enabled,
            "exposure_time_us": exposure_time_us,
            "frames_processed": self._frames_processed,
            "isp_ms_avg": isp_ms_avg,
            "isp_ms_last": isp_times[-1] if isp_times else None,
            "achieved_fps": achieved_fps,
            "isp_cpu_load_cores": isp_cpu_load_cores,
            # Fixed at startup (max_width * max_height * 3 bytes), reused
            # every frame -- not a live/growing number, so it's the honest
            # answer to "how much RAM does capture need," unlike total RSS
            # which also includes the whole Python/FastAPI runtime.
            "frame_buffer_mb": (
                self._frame_buffer_bytes / (1024 * 1024) if self._frame_buffer_bytes else None
            ),
            "process_cpu_percent": cpu_percent,
            "process_cores_used": cpu_percent / 100,
            "cpu_count": cpu_count,
            "process_rss_mb": _process.memory_info().rss / (1024 * 1024),
        }

    def _resolution_tag(self) -> str:
        if not self._current_resolution:
            return ""
        return f"_{self._current_resolution['width']}x{self._current_resolution['height']}"

    # ------------------------------------------------------------ snapshot

    def snapshot(self, filename: str | None = None) -> str:
        with self._lock:
            hCamera = self._require_camera()
            if filename is None:
                filename = f"snapshot_{datetime.now():%Y%m%d_%H%M%S}{self._resolution_tag()}.jpg"
            path = os.path.join(self.storage_dir, filename)

            frame_head = self._grab_and_process()
            mvsdk.CameraSaveImage(
                hCamera, path, self._rgb_buffer, frame_head, mvsdk.FILE_JPG, 100
            )
            return filename

    # ------------------------------------------------------------ record
    #
    # A "recording" is actually a back-to-back series of short chunks (see
    # DEFAULT_CHUNK_SECONDS): the grab loop runs continuously for the whole
    # session, but every chunk_seconds it closes the current chunk and opens
    # a new one, handing the finished chunk off to transcode_and_upload_async
    # (MSCV -> H.264, then uploaded) in the background. This keeps local
    # storage bounded to roughly one chunk's worth of MSCV data regardless
    # of how long the overall recording runs -- a full match's raw MSCV
    # output would otherwise be too large to even hold locally on a
    # storage-constrained edge box. None of this is visible through the
    # API: start_recording/stop_recording still look like one continuous
    # recording from the outside.

    def _segment_filename(self) -> str:
        self._segment_index += 1
        return f"match_{self._session_id}_seg{self._segment_index:03d}{self._resolution_tag()}.avi"

    def _open_segment(self, hCamera):
        filename = self._segment_filename()
        path = os.path.join(self.storage_dir, filename)

        mvsdk.CameraInitRecord(
            hCamera,
            1,  # MSCV-compressed; 0 would be uncompressed
            path,
            True,  # split file if it exceeds 2GB
            90,  # quality factor
            self._frame_rate,
        )

        # With split-on-2GB enabled, the SDK always writes the first
        # segment as "<name>-1.<ext>" instead of "<name>.<ext>", even
        # when the recording never grows large enough to split. Track
        # the name it actually wrote, not the one we asked for, or every
        # lookup (this chunk's size, transcode input path) 404s.
        root, ext = os.path.splitext(filename)
        filename = f"{root}-1{ext}"

        self._current_recording_file = filename
        self._segment_started_at_mono = time.monotonic()

    def _close_segment(self, hCamera):
        mvsdk.CameraStopRecord(hCamera)
        finished_file = self._current_recording_file
        finished_path = os.path.join(self.storage_dir, finished_file)

        try:
            self._session_total_bytes += os.path.getsize(finished_path)
        except OSError:
            pass

        transcode_and_upload_async(finished_path, finished_file)

    def start_recording(self, frame_rate: int = 25, chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> str:
        with self._lock:
            hCamera = self._require_camera()
            if self._recording:
                raise RuntimeError("a recording is already in progress")

            self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._segment_index = 0
            self._chunk_seconds = chunk_seconds
            self._frame_rate = frame_rate
            self._session_total_bytes = 0
            self._recording_started_at = datetime.now().isoformat()

            self._open_segment(hCamera)

            self._recording = True
            self._record_stop_event.clear()
            self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
            self._record_thread.start()
            return self._session_id

    def _record_loop(self):
        hCamera = self._hCamera
        while not self._record_stop_event.is_set():
            try:
                frame_head = self._grab_and_process()
            except mvsdk.CameraException as e:
                if e.error_code == mvsdk.CAMERA_STATUS_TIME_OUT:
                    continue
                self._last_error = str(e)
                break
            try:
                mvsdk.CameraPushFrame(hCamera, self._rgb_buffer, frame_head)
            except Exception as e:
                self._last_error = str(e)
                break

            if time.monotonic() - self._segment_started_at_mono >= self._chunk_seconds:
                with self._lock:
                    if self._record_stop_event.is_set():
                        break
                    self._close_segment(hCamera)
                    self._open_segment(hCamera)

    def stop_recording(self) -> str:
        with self._lock:
            if not self._recording:
                raise RuntimeError("no recording is in progress")
            hCamera = self._require_camera()

            self._record_stop_event.set()
            thread = self._record_thread

        if thread is not None:
            thread.join(timeout=10)

        with self._lock:
            self._close_segment(hCamera)
            session_id = self._session_id
            self._recording = False
            self._current_recording_file = None
            self._recording_started_at = None
            self._record_thread = None
            return session_id
