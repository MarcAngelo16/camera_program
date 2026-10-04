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
from datetime import datetime

from . import mvsdk

RETRY_INTERVAL_SECONDS = 5


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

        self._last_error = None

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

        with self._lock:
            self._hCamera = hCamera
            self._capability = capability
            self._mono = mono
            self._dev_info = dev_info
            self._rgb_buffer = rgb_buffer
            self._last_error = None

    # -------------------------------------------------------------- status

    def status(self):
        with self._lock:
            if self._hCamera is None:
                return {
                    "connected": False,
                    "recording": False,
                    "last_error": self._last_error,
                }
            return {
                "connected": True,
                "recording": self._recording,
                "current_file": self._current_recording_file,
                "recording_started_at": self._recording_started_at,
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

    # ---------------------------------------------------------------- grab

    def _grab_and_process(self):
        """Grab one frame and run it through the ISP into self._rgb_buffer.
        Returns the frame head. Raises on timeout/error."""
        hCamera = self._hCamera
        pRawData, frame_head = mvsdk.CameraGetImageBuffer(hCamera, 2000)
        try:
            mvsdk.CameraImageProcess(hCamera, pRawData, self._rgb_buffer, frame_head)
        finally:
            mvsdk.CameraReleaseImageBuffer(hCamera, pRawData)
        return frame_head

    # ------------------------------------------------------------ snapshot

    def snapshot(self, filename: str | None = None) -> str:
        with self._lock:
            hCamera = self._require_camera()
            if filename is None:
                filename = f"snapshot_{datetime.now():%Y%m%d_%H%M%S}.jpg"
            path = os.path.join(self.storage_dir, filename)

            frame_head = self._grab_and_process()
            mvsdk.CameraSaveImage(
                hCamera, path, self._rgb_buffer, frame_head, mvsdk.FILE_JPG, 100
            )
            return filename

    # ------------------------------------------------------------ record

    def start_recording(self, filename: str | None = None, frame_rate: int = 25) -> str:
        with self._lock:
            hCamera = self._require_camera()
            if self._recording:
                raise RuntimeError("a recording is already in progress")

            if filename is None:
                filename = f"recording_{datetime.now():%Y%m%d_%H%M%S}.avi"
            path = os.path.join(self.storage_dir, filename)

            mvsdk.CameraInitRecord(
                hCamera,
                1,  # MSCV-compressed; 0 would be uncompressed
                path,
                True,  # split file if it exceeds 2GB
                90,  # quality factor
                frame_rate,
            )

            # With split-on-2GB enabled, the SDK always writes the first
            # segment as "<name>-1.<ext>" instead of "<name>.<ext>", even
            # when the recording never grows large enough to split. Track
            # the name it actually wrote, not the one we asked for, or
            # every lookup (download, upload-to-central) 404s.
            root, ext = os.path.splitext(filename)
            filename = f"{root}-1{ext}"

            self._recording = True
            self._current_recording_file = filename
            self._recording_started_at = datetime.now().isoformat()
            self._record_stop_event.clear()
            self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
            self._record_thread.start()
            return filename

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
            mvsdk.CameraStopRecord(hCamera)
            filename = self._current_recording_file
            self._recording = False
            self._current_recording_file = None
            self._recording_started_at = None
            self._record_thread = None
            return filename
