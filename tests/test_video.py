"""Video handoff tests. These run anywhere -- no video file, no OpenCV.

The frame handoff is deliberately separable from decoding so that its policy can
be checked here: the renderer must always get the newest frame, and frames the
renderer was too slow to collect must be counted rather than queued.
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1.video import LatestFrame, synthetic_panorama  # noqa: E402

failures = 0


def run(name, fn):
    global failures
    print(f"\n{name}")
    if not fn():
        failures += 1


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: got {got!r}, want {want!r}")
    return ok


def test_newest_frame_wins():
    ok = True
    slot = LatestFrame()
    ok &= check("nothing to collect at first", slot.get(), None)

    slot.put("a")
    slot.put("b")
    slot.put("c")
    ok &= check("collects the newest, not the oldest", slot.get(), "c")
    ok &= check("two frames counted as dropped", slot.dropped, 2)
    ok &= check("one frame counted as delivered", slot.delivered, 1)
    return ok


def test_a_collected_frame_is_not_redelivered():
    """The render loop calls get() every frame; it must be able to tell a new
    frame from the one it already has, or it re-uploads the same texture."""
    ok = True
    slot = LatestFrame()
    slot.put("a")
    ok &= check("first collection returns the frame", slot.get(), "a")
    ok &= check("second collection reports nothing new", slot.get(), None)
    ok &= check("collecting twice is not a drop", slot.dropped, 0)
    return ok


def test_peek_survives_collection():
    """Redraws -- a window resize, or video paused -- need the last frame again
    without it counting as new."""
    ok = True
    slot = LatestFrame()
    slot.put("a")
    slot.get()
    ok &= check("peek still sees the last frame", slot.peek(), "a")
    ok &= check("peek is not a delivery", slot.delivered, 1)
    return ok


def test_concurrent_put_and_get_lose_nothing():
    """Every frame must be either delivered or counted as dropped. If the lock
    is wrong, totals silently disagree under contention."""
    ok = True
    slot = LatestFrame()
    total = 2000

    def produce():
        for i in range(total):
            slot.put(i)

    producer = threading.Thread(target=produce)
    producer.start()
    while producer.is_alive():
        slot.get()
    producer.join()
    slot.get()

    accounted = slot.delivered + slot.dropped
    print(f"  [info] delivered {slot.delivered}, dropped {slot.dropped}")
    ok &= check("every frame is accounted for", accounted, total)
    return ok


def test_synthetic_panorama_landmarks():
    """The test pattern is what the renderer tests assert against, so its own
    landmarks must be where they claim to be. Stored BGR."""
    ok = True
    pano = synthetic_panorama(1024, 512)
    ok &= check("shape is 2:1 equirectangular", pano.shape, (512, 1024, 3))
    ok &= check("8-bit", pano.dtype.name, "uint8")

    horizon = 256
    ok &= check("forward is green at u=0.5", tuple(int(c) for c in pano[horizon, 512]), (0, 200, 0))
    ok &= check("right is red at u=0.75", tuple(int(c) for c in pano[horizon, 768]), (0, 0, 220))
    ok &= check("back is blue at u=0", tuple(int(c) for c in pano[horizon, 0]), (220, 0, 0))
    ok &= check("left is yellow at u=0.25", tuple(int(c) for c in pano[horizon, 256]), (0, 220, 220))
    ok &= check("top row is the white zenith",
                tuple(int(c) for c in pano[0, 0]), (255, 255, 255))
    ok &= check("bottom row is the dark nadir",
                tuple(int(c) for c in pano[-1, 0]), (25, 25, 25))
    ok &= check("poles differ, so a vertical flip is detectable",
                pano[0, 0, 0] != pano[-1, 0, 0], True)
    return ok


run("newest frame wins", test_newest_frame_wins)
run("a collected frame is not redelivered", test_a_collected_frame_is_not_redelivered)
run("peek survives collection", test_peek_survives_collection)
run("concurrent put and get lose nothing", test_concurrent_put_and_get_lose_nothing)
run("synthetic panorama landmarks", test_synthetic_panorama_landmarks)

print("\n" + ("ALL PASS" if failures == 0 else f"{failures} TEST GROUP(S) FAILED"))
sys.exit(1 if failures else 0)
