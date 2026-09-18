"""Video frames for the panorama renderer.

Decoding happens on its own thread. A blocking ``VideoCapture.read()`` in the
render loop stutters, and 4K equirectangular frames are not cheap to decode, so
the render thread must never wait on one.

The handoff deliberately keeps only the **newest** frame. If the renderer falls
behind, the right thing for video playback is to skip ahead rather than queue up
stale frames and drift further behind real time, so late frames are dropped and
counted.

OpenCV is imported softly, the same way hidapi is in :mod:`dk1.device`: the
synthetic test panorama and the frame-handoff policy must stay usable and
testable on a machine with no video stack installed.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - environment dependent
    cv2 = None  # type: ignore[assignment]

_CV2_MISSING = (
    "OpenCV is not installed, so video files cannot be decoded. Install it with:\n"
    "    pip install opencv-python"
)


class LatestFrame:
    """A one-slot handoff that keeps the newest value and counts what it drops.

    Separate from the decoding so the policy can be tested without a video file
    or an OpenCV build.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame = None
        self._fresh = False
        self.delivered = 0
        self.dropped = 0

    def put(self, frame) -> None:
        with self._lock:
            if self._fresh:
                # The consumer never came for the previous one.
                self.dropped += 1
            self._frame = frame
            self._fresh = True

    def get(self):
        """The newest frame if it has not been taken yet, else ``None``."""
        with self._lock:
            if not self._fresh:
                return None
            self._fresh = False
            self.delivered += 1
            return self._frame

    def peek(self):
        """The newest frame whether or not it is fresh -- for redraws."""
        with self._lock:
            return self._frame


class VideoSource:
    """Decodes a video file on a background thread, newest frame wins."""

    def __init__(self, path: str, loop: bool = True) -> None:
        self.path = path
        self.loop = loop
        self.frames = LatestFrame()

        self.width = 0
        self.height = 0
        self.fps = 0.0
        self.frame_count = 0
        self.frames_decoded = 0

        self._capture = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def open(self) -> "VideoSource":
        if cv2 is None:
            raise ImportError(_CV2_MISSING)
        capture = cv2.VideoCapture(self.path)
        if not capture.isOpened():
            raise RuntimeError(f"Could not open video: {self.path}")

        self._capture = capture
        self.width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        # Some containers report nonsense; 30 is a safer guess than 0 or 1000.
        self.fps = fps if 1.0 < fps < 240.0 else 30.0

        self._thread = threading.Thread(target=self._decode_loop, name="dk1-video", daemon=True)
        self._thread.start()
        return self

    def _decode_loop(self) -> None:
        period = 1.0 / self.fps
        next_due = time.perf_counter()
        while not self._stop.is_set():
            ok, frame = self._capture.read()
            if not ok:
                if not self.loop:
                    return
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            self.frames_decoded += 1
            self.frames.put(frame)

            # Pace to the source frame rate, but never accumulate a backlog of
            # sleep debt if decoding itself fell behind.
            next_due += period
            now = time.perf_counter()
            if next_due < now:
                next_due = now
            elif self._stop.wait(next_due - now):
                return

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> "VideoSource":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()


def synthetic_panorama(width: int = 2048, height: int = 1024) -> np.ndarray:
    """An equirectangular test pattern, BGR, with recognisable landmarks.

    Useful before any video file exists: the four cardinal directions get
    distinct colours, so "is forward actually forward and is up actually up" is
    answerable by looking, and by a test sampling known pixels.

    Layout, in equirectangular convention -- u=0.5 is forward, v=0 is up:

        forward  green      right  red
        back     blue       left   yellow
        zenith   white      nadir  dark grey
    """
    image = np.zeros((height, width, 3), dtype=np.uint8)

    # A coarse checkerboard so rotation and pole pinching are visible at all.
    cols = (np.arange(width) * 24 // width) % 2
    rows = (np.arange(height) * 12 // height) % 2
    checker = (cols[None, :] ^ rows[:, None]).astype(np.uint8)
    image[:] = np.where(checker[..., None] == 1, 70, 40)

    # Horizon, and a brighter band above it so up/down is unambiguous.
    horizon = height // 2
    image[horizon - 2 : horizon + 2, :] = (200, 200, 200)
    image[: horizon // 4, :] = np.clip(image[: horizon // 4, :] + 60, 0, 255)

    # Cardinal markers, as vertical bars centred on each direction.
    bar = max(4, width // 64)
    marks = {
        0.5: (0, 200, 0),  # forward: green
        0.75: (0, 0, 220),  # right:   red
        0.0: (220, 0, 0),  # back:    blue
        0.25: (0, 220, 220),  # left:    yellow
    }
    for u, colour in marks.items():
        centre = int(u * width) % width
        for offset in range(-bar, bar + 1):
            image[horizon - height // 8 : horizon + height // 8, (centre + offset) % width] = colour

    # Poles, so a wrong vertical mapping is obvious rather than subtle.
    image[: height // 32, :] = (255, 255, 255)
    image[-height // 32 :, :] = (25, 25, 25)
    return image
