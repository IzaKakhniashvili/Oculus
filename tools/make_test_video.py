#!/usr/bin/env python3
"""Write a short equirectangular test clip.

    python tools/make_test_video.py clip.mp4 --seconds 10

Exists so the player's decode path can be checked without hunting down a real
360 degree video: the static test pattern proves the projection but not that
frames are arriving, and a clip that is obviously moving does both. The pattern
is the same one the renderer tests assert against, with a marker orbiting the
horizon and a clock hand at the zenith, so a stalled decode, a frozen frame or a
vertical flip all show up by eye.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1.video import synthetic_panorama  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="write an equirectangular test clip")
    ap.add_argument("output", help="output file, e.g. clip.mp4")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--width", type=int, default=2048, help="height is half this")
    args = ap.parse_args()

    try:
        import cv2
        import numpy as np
    except ImportError:
        print("Needs OpenCV: pip install opencv-python")
        return 1

    width = args.width
    height = width // 2
    base = synthetic_panorama(width, height)

    writer = cv2.VideoWriter(args.output, cv2.VideoWriter_fourcc(*"mp4v"),
                             args.fps, (width, height))
    if not writer.isOpened():
        print(f"Could not open {args.output} for writing.")
        return 1

    total = int(args.seconds * args.fps)
    horizon = height // 2
    radius = max(6, width // 96)

    for i in range(total):
        frame = base.copy()
        phase = i / total

        # A marker orbiting the horizon: makes the playback direction obvious and
        # lets you check that turning your head tracks it smoothly.
        cx = int(phase * width) % width
        cv2.circle(frame, (cx, horizon), radius, (255, 255, 255), -1)
        cv2.circle(frame, (cx, horizon), radius, (0, 0, 0), 2)

        # A hand sweeping near the zenith, where equirectangular distortion is
        # worst -- if the vertical mapping is wrong this lands at your feet.
        angle = phase * 2 * math.pi
        top = height // 8
        hx = int((0.5 + 0.12 * math.cos(angle)) * width)
        hy = int(top + 0.06 * height * math.sin(angle))
        cv2.line(frame, (int(0.5 * width), top), (hx, hy), (0, 255, 255), 4)

        cv2.putText(frame, f"{i + 1}/{total}", (int(0.42 * width), horizon + height // 6),
                    cv2.FONT_HERSHEY_SIMPLEX, width / 1400.0, (255, 255, 255), 2)
        writer.write(frame)

    writer.release()
    size_mb = os.path.getsize(args.output) / 1e6
    print(f"wrote {args.output}: {width}x{height}, {total} frames at {args.fps:g} fps "
          f"({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
