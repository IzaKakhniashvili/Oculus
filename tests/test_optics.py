"""DK1 optics tests. Pure numbers -- no headset, no OpenGL."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1.optics import (  # noqa: E402
    H_SCREEN,
    LENS_SEPARATION,
    eye_viewports,
    lens_center_ndc,
    projection_center_offset,
)

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


def approx(label, got, want, tol=1e-5):
    ok = abs(got - want) <= tol
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: got {got:.6f}, want {want:.6f}")
    return ok


def test_offset_matches_libovr():
    """LibOVR publishes Distortion.XCenterOffset = 0.151976 for the DK1.

    That number is not a magic constant -- it is (H/4 - lens_sep/2) expressed
    as a fraction of one eye's half-width. If we drift from it, the optical
    axis misses the lens.
    """
    ok = True
    view_center = H_SCREEN / 4.0
    shift = view_center - LENS_SEPARATION / 2.0
    expected = 4.0 * shift / H_SCREEN
    ok &= approx("matches the derived LibOVR value", projection_center_offset(), expected)
    ok &= approx("is the published 0.151976", projection_center_offset(), 0.151976, tol=1e-5)
    ok &= check("lenses sit closer together than the half-screen centres",
                LENS_SEPARATION < H_SCREEN / 2.0, True)
    return ok


def test_eyes_are_mirror_images():
    ok = True
    left = lens_center_ndc("left")
    right = lens_center_ndc("right")
    ok &= check("left lens is to the right of its viewport centre (toward the nose)",
                left[0] > 0.0, True)
    ok &= check("right lens is to the left of its viewport centre (toward the nose)",
                right[0] < 0.0, True)
    ok &= approx("offsets are equal and opposite", left[0] + right[0], 0.0)
    ok &= check("neither is shifted vertically", (left[1], right[1]), (0.0, 0.0))
    try:
        lens_center_ndc("middle")
        print("  [FAIL] bad eye name: should have raised")
        ok = False
    except ValueError:
        print("  [ok  ] bad eye name: rejected")
    return ok


def test_viewports_split_the_framebuffer():
    ok = True
    left, right = eye_viewports(1280, 800)
    ok &= check("left eye is the left 640", left, (0, 0, 640, 800))
    ok &= check("right eye is the right 640", right, (640, 0, 640, 800))
    ok &= check("the two halves cover the full width",
                left[2] + right[2], 1280)
    odd_left, odd_right = eye_viewports(641, 800)
    ok &= check("an odd width still covers every column",
                odd_left[2] + odd_right[2], 641)
    try:
        eye_viewports(1, 800)
        print("  [FAIL] width 1: should have raised")
        ok = False
    except ValueError:
        print("  [ok  ] width 1: rejected")
    return ok


run("offset matches LibOVR", test_offset_matches_libovr)
run("eyes are mirror images", test_eyes_are_mirror_images)
run("viewports split the framebuffer", test_viewports_split_the_framebuffer)

print("\n" + ("ALL PASS" if failures == 0 else f"{failures} TEST GROUP(S) FAILED"))
sys.exit(1 if failures else 0)
