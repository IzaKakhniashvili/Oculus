"""DK1 display and lens geometry. Pure numbers, no I/O, no OpenGL.

Canonical LibOVR defaults for the DK1. The lenses sit closer together than the
centres of the two 640-pixel halves, so each eye's projection is shifted toward
the nose. Skipping that offset puts the seam between the eyes in the wrong place
and the image looks like it is sliding outward.

These numbers are what Phase 4b's stereo split is built from. Distortion K is
here too so the warp and the split share one source of truth.
"""

from __future__ import annotations

from typing import Tuple

# Physical screen, metres. The panel is one 1280x800 LCD, not two.
H_SCREEN = 0.14976
V_SCREEN = 0.0936
EYE_TO_SCREEN = 0.041
LENS_SEPARATION = 0.0635
IPD = 0.064
RESOLUTION = (1280, 800)

# Radial barrel pre-warp. The lenses apply the inverse (pincushion).
DISTORTION_K: Tuple[float, float, float, float] = (1.0, 0.22, 0.24, 0.0)
IDENTITY_K: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)


def projection_center_offset() -> float:
    """NDC x of the lens centre relative to an eye-viewport centre.

    Positive means the lens is to the right of that viewport's centre. The left
    eye uses ``+offset``, the right eye ``-offset``, so both optical axes point
    through the lenses rather than through the middle of each half-screen.

    Equals LibOVR's ``Distortion.XCenterOffset`` (~0.152).
    """
    view_center = H_SCREEN / 4.0
    shift = view_center - LENS_SEPARATION / 2.0
    return 4.0 * shift / H_SCREEN


def half_fov_tan() -> Tuple[float, float]:
    """``(tan(half horizontal FOV), tan(half vertical FOV))`` for one eye.

    Taken from the physical half-screen and the eye-to-screen distance, so the
    projection matches what the lenses actually see rather than an arbitrary
    90-degree guess.
    """
    return (H_SCREEN / 4.0 / EYE_TO_SCREEN, V_SCREEN / 2.0 / EYE_TO_SCREEN)


def eye_viewports(width: int, height: int) -> Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]:
    """``(left, right)`` viewports as ``(x, y, w, h)`` for a framebuffer."""
    if width < 2:
        raise ValueError(f"stereo needs a width of at least 2, got {width}")
    half = width // 2
    return (0, 0, half, height), (half, 0, width - half, height)


def lens_center_ndc(eye: str) -> Tuple[float, float]:
    """Lens centre in the eye viewport's NDC. ``eye`` is ``'left'`` or ``'right'``."""
    offset = projection_center_offset()
    if eye == "left":
        return (offset, 0.0)
    if eye == "right":
        return (-offset, 0.0)
    raise ValueError(f"eye must be 'left' or 'right', got {eye!r}")
