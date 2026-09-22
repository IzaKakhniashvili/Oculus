# Roadmap — Phases 2 to 4

Design decisions already made, so the next session implements rather than
re-deliberates. Deviate where hardware says otherwise.

---

## Phase 2 — Orientation filter — **DONE**

Built as `dk1/orientation.py` and confirmed on hardware; see
[STATUS.md](STATUS.md) for the measured results and the three places the
implementation deviates from the plan below. The axis mapping it was blocked on
is measured and written up in [PROTOCOL.md](PROTOCOL.md#axis-mapping) — it turned
out to be the identity.

The rest of this section is the original design, kept because it still describes
what the code does.

### Approach — Mahony complementary filter

Chosen over Madgwick and over a plain Kalman filter: ~30 lines, one tuning
constant, no matrix algebra, and at 1000 Hz the accuracy difference is
irrelevant for video playback.

Per sample:

1. **Integrate the gyro** into the quaternion:
   `q += 0.5 · q ⊗ (0, ω) · dt`, then normalise.
2. **Correct against gravity.** Normalise the accelerometer vector `â`.
   Compute the expected up direction in the body frame, `ĝ = Rᵀ · (0,1,0)`.
   The error is `e = â × ĝ`.
3. **Feed the error back** into the gyro term: `ω′ = ω + Kp · e`.

Start with `Kp ≈ 0.5`. Higher tracks faster but lets linear acceleration tilt
the horizon; lower drifts more.

Reject the accelerometer correction when `| |a| − 9.81 | > ~2 m/s²` — during
rapid head movement the accelerometer measures motion, not gravity, and trusting
it then actively harms the estimate.

### Proposed API

```python
class OrientationFilter:
    def __init__(self, kp: float = 0.5): ...
    def update(self, sample: Sample) -> None:   # one 1 kHz sample
    @property
    def quaternion(self) -> tuple[float, float, float, float]:
    @property
    def euler(self) -> tuple[float, float, float]:   # yaw, pitch, roll (rad)
    def recentre(self) -> None:                       # zero current yaw
```

`numpy` is already in `requirements.txt`. Keep the filter free of I/O so it can
be tested against a recorded capture via `replay_raw()`.

### Yaw drift

Gravity anchors pitch and roll absolutely. **Nothing anchors yaw.** It will
rotate slowly and forever.

The magnetometer could anchor it, but DK1 mag calibration is per-environment,
degrades near monitors and desks, and is a project in itself. **Decision: ship a
recentre key, don't fight the magnetometer.** Revisit only if drift proves
intolerable in use. A gyro bias estimate (average the gyro while stationary and
subtract it) removes most of the drift for a fraction of the effort.

### How to verify

- Replay a recorded capture; orientation must return near its start after the
  headset returns to its start pose.
- Stationary headset: pitch and roll must stay fixed indefinitely; yaw may drift.
- Rotate 90° physically, confirm ~90° reported.

---

## Phase 3 — 360° renderer — **DONE**

Built as `dk1/renderer.py`, `dk1/video.py` and `tools/dk1_player.py`, and
verified at 90 fps against a replayed hardware capture. The design below was
followed as written except for two details in the shader sketch, both of which
were wrong and are corrected in the code:

- **The horizontal lookup must be `atan(d.x, -d.z)`, not `atan(d.x, d.z)`.**
  With `d.z`, `u = 0.5` does not land straight ahead: the panorama's wrap sits
  dead centre in the view, putting a seam directly in front of you.
- **The rotation matrix must be transposed before it becomes a uniform.** The
  filter reports row-major and GLSL reads `mat3` column-major. Skipping this
  applies the inverse rotation, which still renders a convincing panorama and
  only gives itself away as the view turning the wrong way.

Both are invisible in a still frame, which is why the tests render the real
shader offscreen and assert on pixels rather than trusting inspection.

**Goal:** equirectangular video on screen, camera rotated by the filter.

**Stack:** `moderngl` + `glfw` for GL context and window, `opencv-python` for
video decode.

### Taking orientation from Phase 2

```python
filt = OrientationFilter(calibration=calibrate(stream))
for sample in stream:          # stream must skip the first report
    filt.update(sample)
rotation = filt.matrix         # 9 floats, row-major, ready as a uniform
```

Use `.matrix` or `.quaternion`, **never `.euler`** — resting flat on a desk is
pitch ≈ −90°, the Euler singularity, where yaw and roll swing freely while the
pose is stable. The sensor frame already matches the render frame, so no remap.

The filter wants all 1000 samples/s, but the renderer only needs the pose once
per frame, so run `update()` wherever the samples are read and let the render
loop sample `.matrix` whenever it draws. It is a cheap property read.

### Use a fullscreen quad, not a sphere mesh

The original plan was an inverted UV sphere. A **fullscreen quad with
per-fragment ray direction** is strictly better here:

- No mesh, no vertex buffer, no UV seam artifact down the back.
- No pole pinching, which is where sphere tessellation looks worst.
- Exactly one fragment shader; the "camera" is just a rotation matrix uniform.

Vertex stage emits a full-screen triangle. The fragment shader builds a view ray
from the pixel's NDC position and the FOV, rotates it by the orientation matrix,
then samples the equirectangular texture:

```glsl
vec3 d = normalize(rotation * ray);        // rotation uniform is transposed
vec2 uv = vec2(atan(d.x, -d.z) / 6.2831853 + 0.5,
               acos(clamp(d.y, -1.0, 1.0)) / 3.1415927);
colour = texture(video, uv);
```

Set the texture to `GL_CLAMP_TO_EDGE` vertically and `GL_REPEAT` horizontally so
the horizontal wrap is seamless.

### Video decode

Decode on a **separate thread** — a blocking `cv2.VideoCapture.read()` in the
render loop will stutter. Hand frames to the GL thread through a small bounded
queue; drop frames rather than blocking when the renderer falls behind.

Upload with a persistently-mapped PBO or double-buffered texture if 4K frames
prove too slow to upload each frame. Don't optimise this before measuring.

Measured, and it does not need optimising. Both a 2048×1024 clip and a 3840×1920
one hold a steady 90 fps — the vsync ceiling on this panel — while decoding and
while tracking live head motion, with one dropped frame, at startup. **No PBO, no
double buffering.** The one upload change worth making was passing the numpy array
straight to `Texture.write()` instead of `frame.tobytes()`, which was copying 22 MB
per frame on the render thread while holding the GIL.

The queue ended up as a **one-slot latest-frame handoff** rather than a bounded
queue: for playback the right response to falling behind is to skip ahead, and a
queue of any depth adds latency instead.

Two things were tried and rejected, so they don't need retrying:

- **Mipmapping the panorama.** Plausible — a 4K texture is minified roughly 3:1
  into the window — but rebuilding the chain every frame cost ~4% of the frame
  rate and fixed nothing. Plain bilinear.
- **Blaming a frame-rate dip on motion.** A dip to 50 fps during a live run
  looked like it correlated with head movement. It did not: the USB stream
  measured 943 reports/s with a worst gap of 2.3 ms and no gap over 20 ms during
  deliberate motion, an identical run driven by `--replay` held 90 fps through the
  same rotation, and the dip never reproduced. It was OneDrive uploading a video
  that had just been downloaded into the project folder. **This repository lives in
  a synced folder; treat any one-off performance dip as suspect until it
  reproduces.**

OpenCV gives BGR — either swizzle in the shader or convert on upload. Shader is
cheaper.

---

## Phase 4 — Display output and lens distortion

Two separate problems. 4b is done; 4a is now "extend the desktop", not "find
the panel".

### 4a. Getting a fullscreen window onto the DK1 — panel enumerates, still mirrored

**This section anticipated the wrong problem twice.** It originally assumed the
panel would be detected and that its unusual EDID might not offer 1280×800 @
60 Hz. It was then rewritten to say Windows never detects the panel, then that
the runtime was required to light it. All three are wrong.

What actually happens: **uninstalling Oculus runtime 0.8.0.0 released the
panel.** It enumerates as `EDID vendor OVR` / `Rift DK` at 1280×800 @ 60 Hz,
and the tracker is ours at the same time. The runtime was *hiding* the display
via the AMD driver's hide-this-EDID list. Do not reinstall it. Full reasoning
in [STATUS.md](STATUS.md).

The remaining 4a work is Windows: the desktop is still *mirrored* onto the DK1
instead of extended. `DisplaySwitch.exe /extend` did not change that.
`--fullscreen` and `--monitor N` are already in `tools/dk1_player.py`; they
cannot target a 1280×800 surface until the display is extended.

### 4b. Barrel distortion and stereo — **DONE**, confirmed live

Built as `dk1/optics.py` plus `render_stereo()` in `dk1/renderer.py`. Confirmed
live with `--video nasa_webb_360.mp4 --live --stereo` (17079 frames, ~4 min).
A YouTube-style circular black mask around each lens centre was tried and
reverted — filled 640×800 halves looked better.

The DK1's lenses apply strong pincushion distortion. The image is
**pre-distorted with the inverse (barrel) warp** so the lenses cancel it, in
the same shader as the equirect lookup (not a render-to-texture post-process).
`--no-distortion` / `d` leave identity K for A/B.

Stereo is two viewports of 640×800, each with its own lens-centre offset, both
showing the same monocular 360° image. True stereoscopic 360 needs over/under
source footage and is not written. Chromatic aberration is not written.

Canonical DK1 optical constants, now in `dk1/optics.py` — LibOVR defaults,
verified only as numbers, not against a measured lens:

| Parameter | Value |
|---|---|
| Horizontal screen size | 0.14976 m |
| Vertical screen size | 0.0936 m |
| Vertical screen centre | 0.0468 m |
| Eye to screen distance | 0.041 m |
| Lens separation distance | 0.0635 m |
| Interpupillary distance | 0.064 m (per-user) |
| Resolution | 1280 × 800 |
| Distortion K | `[1.0, 0.22, 0.24, 0.0]` |
| Chromatic aberration | `[0.996, −0.004, 1.014, 0.0]` (unused) |

The distortion is a radial polynomial about the lens centre:

```
r′ = r · (K₀ + K₁r² + K₂r⁴ + K₃r⁶)
```

---

## Suggested order

1. ~~Confirm Phase 1 on hardware (`--sanity`).~~ Done.
2. ~~Measure the axis mapping.~~ Done — it is the identity.
3. ~~Check the HDMI display works.~~ Done — it enumerates as `OVR` / `Rift DK`
   after uninstalling the runtime. The remaining work is extending the
   desktop, not lighting the panel. See [STATUS.md](STATUS.md).
4. ~~Phase 2 filter, verified against a recorded capture.~~ Done.
5. ~~Phase 3 renderer in a normal desktop window first.~~ Done, verified against a
   replayed capture **and driven live off the headset** at 90 fps.
6. ~~Phase 4b — stereo split and barrel warp.~~ Done, confirmed live. Chromatic
   aberration is the leftover piece of 4b.
7. **Phase 4a — extend the desktop onto the DK1**, then
   `--live --stereo --fullscreen --monitor N`.

Steps 5 and 6 were deliberately separated: debugging a renderer while wearing a
headset with a warped image is miserable. Get it correct on a monitor first.
