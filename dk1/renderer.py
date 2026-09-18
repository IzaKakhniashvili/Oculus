"""Equirectangular panorama renderer.

A fullscreen quad with a per-fragment view ray, not a UV sphere. There is no
mesh, no seam down the back, and no pole pinching, and the camera is a single
rotation matrix uniform. The whole renderer is one fragment shader.

Needs an OpenGL 3.3 core context, which the caller supplies -- so the same class
drives a window via glfw, or a headless framebuffer in the tests.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

import numpy as np

VERTEX_SHADER = """
#version 330

in vec2 position;
out vec2 ndc;

void main() {
    ndc = position;
    gl_Position = vec4(position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330

uniform sampler2D panorama;
uniform mat3 rotation;
uniform vec2 half_fov;   // tan of half the horizontal and vertical field of view

in vec2 ndc;
out vec4 colour;

const float TAU = 6.28318530717958647692;
const float PI  = 3.14159265358979323846;

void main() {
    // The ray through this pixel in the eye's frame. -Z is forward, matching
    // both OpenGL convention and the sensor frame the tracker reports in.
    vec3 ray = vec3(ndc.x * half_fov.x, ndc.y * half_fov.y, -1.0);
    vec3 d = normalize(rotation * ray);

    // Equirectangular lookup. Using -d.z rather than d.z puts u = 0.5 straight
    // ahead, which is where the seam must NOT be: with d.z the wrap lands dead
    // centre in the view.
    vec2 uv = vec2(atan(d.x, -d.z) / TAU + 0.5,
                   acos(clamp(d.y, -1.0, 1.0)) / PI);

    // OpenCV decodes to BGR, so swizzle here rather than converting every frame
    // on the CPU.
    colour = vec4(texture(panorama, uv).bgr, 1.0);
}
"""


def column_major(matrix: Sequence[float]) -> Tuple[float, ...]:
    """Transpose a row-major 3x3 for GLSL, which reads ``mat3`` column-major.

    Skipping this is a silent bug: the result is still a rotation, just the
    inverse one, so the view turns the wrong way and nothing errors.
    """
    m = tuple(matrix)
    if len(m) != 9:
        raise ValueError(f"expected 9 floats, got {len(m)}")
    return (m[0], m[3], m[6],
            m[1], m[4], m[7],
            m[2], m[5], m[8])


class PanoramaRenderer:
    """Draws an equirectangular frame, oriented by a rotation matrix."""

    def __init__(self, ctx, fov_deg: float = 90.0) -> None:
        self.ctx = ctx
        self.fov_deg = fov_deg

        self.program = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
        # Two triangles covering clip space. Cheaper to reason about than the
        # gl_VertexID tricks, and the cost is four vertices.
        quad = np.array([-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0], dtype="f4")
        self._vbo = ctx.buffer(quad.tobytes())
        self._vao = ctx.vertex_array(self.program, [(self._vbo, "2f", "position")])

        self.texture = None
        self._size = (0, 0)

    def upload(self, frame: np.ndarray) -> None:
        """Send one BGR frame to the GPU, (re)allocating if the size changed."""
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError(f"expected an HxWx3 BGR frame, got shape {frame.shape}")

        height, width = frame.shape[:2]
        if self.texture is None or self._size != (width, height):
            if self.texture is not None:
                self.texture.release()
            self.texture = self.ctx.texture((width, height), 3, dtype="f1")
            # Horizontal wrap must be seamless; vertical must not wrap, or the
            # sky bleeds into the ground at the poles.
            self.texture.repeat_x = True
            self.texture.repeat_y = False
            # Plain bilinear, no mipmaps. Mipmapping a 4K panorama was measured
            # and rejected: rebuilding the chain on every frame cost ~4% of the
            # frame rate and did not fix the dip that prompted it.
            self.texture.filter = (self.ctx.LINEAR, self.ctx.LINEAR)
            self._size = (width, height)

        # Write the array itself, not frame.tobytes(). At 3840x1920 a frame is
        # 22 MB, and tobytes() copies all of it on the render thread -- while
        # holding the GIL, so it stalls decoding too. ascontiguousarray is free
        # when the frame already is contiguous, which OpenCV's are.
        self.texture.write(np.ascontiguousarray(frame))

    def render(self, rotation: Sequence[float], aspect: float) -> None:
        """Draw one frame. ``rotation`` is 9 row-major floats, body to world."""
        if self.texture is None:
            return

        half_v = math.tan(math.radians(self.fov_deg) / 2.0)
        self.program["half_fov"].value = (half_v * aspect, half_v)
        self.program["rotation"].value = column_major(rotation)
        self.texture.use(0)
        self.program["panorama"].value = 0
        self._vao.render(mode=self.ctx.TRIANGLE_STRIP, vertices=4)

    def release(self) -> None:
        for resource in (self.texture, self._vao, self._vbo, self.program):
            if resource is not None:
                resource.release()
        self.texture = None
