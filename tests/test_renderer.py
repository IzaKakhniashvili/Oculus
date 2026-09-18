"""Renderer tests. These run the real shader, offscreen, and check pixels.

No window and no headset needed -- moderngl can make a standalone context. If
this machine has no usable GL at all the whole group is skipped rather than
failed, the same way the protocol tests do not require hidapi.

The useful property to test is the one that is easy to get wrong and impossible
to notice: whether "forward" in the video lands in front of you. A transposed
rotation matrix, a flipped vertical axis or the wrong argument order in atan all
still render a plausible-looking panorama, just pointing somewhere else. So the
test pattern colours the cardinal directions and the poles, and the assertions
are "looking left shows the left marker".
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1.orientation import OrientationFilter  # noqa: E402
from dk1.protocol import Sample  # noqa: E402
from dk1.renderer import PanoramaRenderer, column_major  # noqa: E402
from dk1.video import synthetic_panorama  # noqa: E402

# What synthetic_panorama() paints, as RGB once the shader has un-swizzled BGR.
FORWARD = (0, 200, 0)
RIGHT = (220, 0, 0)
BACK = (0, 0, 220)
LEFT = (220, 220, 0)
ZENITH = (255, 255, 255)

IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)
# Row-major, body to world.
YAW_LEFT_90 = (0, 0, 1, 0, 1, 0, -1, 0, 0)  # Ry(+90), and +Y is a left turn
YAW_RIGHT_90 = (0, 0, -1, 0, 1, 0, 1, 0, 0)  # Ry(-90)
YAW_180 = (-1, 0, 0, 0, 1, 0, 0, 0, -1)
PITCH_UP_90 = (1, 0, 0, 0, 0, -1, 0, 1, 0)  # Rx(+90), and +X is nodding up

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


def near_colour(label, got, want, tol=40):
    ok = all(abs(int(g) - int(w)) <= tol for g, w in zip(got, want))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: got RGB{tuple(int(c) for c in got)}, "
          f"want ~RGB{want}")
    return ok


def test_column_major():
    ok = True
    ok &= check("identity is unchanged", column_major(IDENTITY), IDENTITY)
    ok &= check("rows become columns",
                column_major((1, 2, 3, 4, 5, 6, 7, 8, 9)),
                (1, 4, 7, 2, 5, 8, 3, 6, 9))
    try:
        column_major((1, 2, 3))
        print("  [FAIL] wrong length: should have raised")
        ok = False
    except ValueError:
        print("  [ok  ] wrong length: rejected")
    return ok


class Offscreen:
    """A standalone GL context plus a framebuffer, as a context manager."""

    def __init__(self, size=(256, 256)):
        self.size = size

    def __enter__(self):
        import moderngl

        self.ctx = moderngl.create_standalone_context()
        self.fbo = self.ctx.simple_framebuffer(self.size)
        self.fbo.use()
        self.renderer = PanoramaRenderer(self.ctx, fov_deg=60.0)
        self.renderer.upload(synthetic_panorama(1024, 512))
        return self

    def __exit__(self, *exc_info):
        self.renderer.release()
        self.fbo.release()
        self.ctx.release()

    def centre_pixel(self, rotation):
        """Render with this orientation and read the pixel dead ahead."""
        import numpy as np

        self.fbo.clear(0.0, 0.0, 0.0, 1.0)
        self.renderer.render(rotation, aspect=self.size[0] / self.size[1])
        raw = np.frombuffer(self.fbo.read(components=3), dtype=np.uint8)
        image = raw.reshape((self.size[1], self.size[0], 3))
        return image[self.size[1] // 2, self.size[0] // 2]


def test_looking_directions():
    """Each orientation must show the marker painted in that direction."""
    ok = True
    with Offscreen() as gl:
        ok &= near_colour("level and forward -> forward marker",
                          gl.centre_pixel(IDENTITY), FORWARD)
        ok &= near_colour("turned left -> left marker",
                          gl.centre_pixel(YAW_LEFT_90), LEFT)
        ok &= near_colour("turned right -> right marker",
                          gl.centre_pixel(YAW_RIGHT_90), RIGHT)
        ok &= near_colour("turned around -> back marker",
                          gl.centre_pixel(YAW_180), BACK)
        ok &= near_colour("looking up -> zenith",
                          gl.centre_pixel(PITCH_UP_90), ZENITH)
    return ok


def test_filter_drives_the_renderer():
    """End to end: the filter's own matrix must aim the view the same way.

    This is what catches a transpose. The filter reports body-to-world row-major
    and GLSL reads mat3 column-major, so getting it wrong turns the view the
    opposite way -- which looks completely normal until you move your head.
    """
    ok = True
    with Offscreen() as gl:
        # A quarter turn to the left, integrated from gyro samples exactly as
        # the live player would. Accelerometer magnitude zero, so no gravity
        # correction interferes with a known rotation.
        filt = OrientationFilter()
        rate = math.pi / 2
        for _ in range(1000):
            filt.update(Sample(accel=(0.0, 0.0, 0.0), gyro=(0.0, rate, 0.0), dt=0.001))

        yaw_deg = math.degrees(filt.euler[0])
        print(f"  [info] filter reports yaw {yaw_deg:.1f} deg")
        ok &= check("filter turned 90 deg left", 89.0 < yaw_deg < 91.0, True)
        ok &= near_colour("view shows what is on the left",
                          gl.centre_pixel(filt.matrix), LEFT)
    return ok


def test_wrap_modes():
    """The two texture wrap settings, each checked by a consequence of it.

    Vertical clamping is visible at the poles. At v=0 a linear sample sits half
    a texel above the first row, so it blends with whatever lies beyond: with
    clamping that is the white zenith again and the result stays pure white,
    whereas wrapping would pull in the dark nadir row and come back grey. So an
    exactly-white zenith is evidence of clamping, not just of aiming up.

    Horizontal wrapping is checked by turning a full circle: u leaves [0, 1] and
    must land back on the same pixels rather than clamping to the edge column.
    """
    ok = True
    with Offscreen(size=(64, 64)) as gl:
        zenith = gl.centre_pixel(PITCH_UP_90)
        ok &= check("zenith is pure white, so v is clamped not wrapped",
                    tuple(int(c) for c in zenith), ZENITH)

        base = gl.centre_pixel(_yaw_matrix(math.radians(30.0)))
        wrapped = gl.centre_pixel(_yaw_matrix(math.radians(30.0 + 360.0)))
        ok &= check("a full turn samples identical pixels",
                    tuple(int(c) for c in base), tuple(int(c) for c in wrapped))
    return ok


def _yaw_matrix(angle: float):
    c, s = math.cos(angle), math.sin(angle)
    return (c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c)


run("column-major conversion", test_column_major)

try:
    import moderngl

    moderngl.create_standalone_context().release()
    have_gl = True
except Exception as exc:  # noqa: BLE001 - any GL failure means skip, not fail
    have_gl = False
    print(f"\n[skip] no usable OpenGL context here ({exc}); shader tests skipped")

if have_gl:
    run("looking in each direction", test_looking_directions)
    run("the filter aims the view", test_filter_drives_the_renderer)
    run("texture wrap modes", test_wrap_modes)

print("\n" + ("ALL PASS" if failures == 0 else f"{failures} TEST GROUP(S) FAILED"))
sys.exit(1 if failures else 0)
