# DK1 360° Player — project context

Custom driver + 360° video player for the **Oculus Rift DK1**, with **no Oculus
runtime or SDK**. Reads raw USB HID packets from the tracker, fuses them into an
orientation, and renders an equirectangular video on the headset's HDMI display.

## Read this first

**Phase 1 is confirmed on hardware** as of 2026-09-18 — `--sanity` passes all
five checks on the real tracker, so the report layout, the 21-bit unpacking, the
unit scaling and the keep-alive are all proven, not assumed.

The **axis mapping is measured** and written up in `docs/PROTOCOL.md`. The sensor
frame turns out to be X right, Y up, Z backward and right-handed — the same frame
the renderer wants — so the sensor-to-render remap is the identity.

**Phase 2 is also built and confirmed on hardware.** `dk1/orientation.py` holds
pitch and roll to within 1.7° of measured gravity and drifts 0.21 °/s in yaw.

**Phase 3 is built and confirmed on hardware.** `tools/dk1_player.py` plays an
equirectangular video at 90 fps in a window, aimed by real head motion — verified
live off the headset as well as against recorded captures.

**One blocker is left: Windows does not see the DK1 panel, and no software can
change that.** To the PC the DK1 is an ordinary external monitor — there is no
driver in the path, and Direct Mode arrived with the DK2. A monitor comes up in a
fixed order: hotplug detect, then EDID over DDC, then a display device and a mode.
This laptop's one external output reports `targetAvailable = false` and the monitor
history has only ever held the internal panel, so **the chain breaks at the first
step** — the hotplug signal is not reaching the GPU, nothing was ever misread, and
an EDID override has no detected monitor to attach to. Probe with `python
tools\dk1_display.py --list`. The Oculus runtime never detected it either; its "HMD
connected" refers to the USB tracker. The remaining causes are all physical: the
cable, the socket, or the DK1's own video path. **Don't spend another session
looking for a software fix** — the cheapest untested variable is the socket, so
plug any ordinary monitor into it first.

**The tracker can also drop off USB**, as it did once mid-session — both device
nodes reporting `Present: False`. Reseating the USB cable and the DC adapter
brought it straight back, so reseat before investigating. Note this is *not* the
runtime holding the device: that case leaves it present and returns Win32 error
32, whereas a ghost node returns error 2.

See [docs/STATUS.md](docs/STATUS.md) for the full handoff, including the measured
hardware numbers and the startup quirks.

## Layout

```
dk1/protocol.py      wire format: 21-bit unpacking, report layout, feature
                     reports. Pure functions, no I/O, fully unit-tested.
dk1/device.py        hidapi transport, keep-alive thread, report iterators,
                     offline capture replay, blocked-device diagnosis.
dk1/orientation.py   Mahony filter and calibrate(). Pure math, no I/O, so it
                     can be driven from a recorded capture anywhere.
dk1/renderer.py      equirectangular shader on a fullscreen quad. Takes a GL
                     context from the caller, so it also runs headless.
dk1/video.py         threaded decode, newest-frame handoff, test pattern.
tools/dk1_probe.py   Phase 1 CLI (--list --sanity --live --raw --record)
tools/dk1_orient.py  Phase 2 CLI (--live --replay)
tools/dk1_player.py  Phase 3 CLI (--video --live --replay --fullscreen)
tools/make_test_video.py  writes an equirectangular test clip
tools/dk1_display.py display probe (--list --modes); ctypes QueryDisplayConfig,
                     no new dependency
tests/               run anywhere, no hardware needed; the renderer tests skip
                     themselves if the machine has no usable OpenGL
docs/                status, protocol reference, roadmap
```

## Conventions

- **Python 3.10+, stdlib-first.** Dependencies are deliberately minimal; see
  `requirements.txt`. Phase 1 needs only `hidapi`.
- **Never add a dependency on the Oculus SDK, LibOVR, or any Oculus runtime.**
  Avoiding them is the entire point of the project — they are unsupported on
  Windows 11.
- **`dk1/protocol.py` and `dk1/orientation.py` stay pure.** No I/O, no threads,
  no hidapi. They must remain runnable and testable on a machine with no headset
  attached — that is what makes `--replay` of a capture possible anywhere.
- **The filter uses plain floats, not numpy.** It runs once per 1 kHz sample,
  where per-call array overhead would dwarf the dozen operations it needs.
- **hidapi is imported softly** in `dk1/device.py`, and **OpenCV is imported
  softly** in `dk1/video.py` (`= None` on ImportError, required at the call
  site). This is deliberate: replaying a recorded capture must work on machines
  without the binding, and the frame-handoff policy must be testable without a
  video stack. Don't "fix" either into a hard top-level import.
- Run the matching tests after touching any of these:

```bat
python tests\test_protocol.py
python tests\test_orientation.py
python tests\test_video.py
python tests\test_renderer.py
```

## Gotchas already discovered — don't rediscover these

1. **`sample_count` can exceed 3.** Only three samples fit in a report. If the
   firmware counted more, the rest were dropped and that elapsed time must
   still be integrated, or orientation silently under-rotates. The first
   surviving sample absorbs the gap (`parse_tracker_report` handles this).
2. **The keep-alive is mandatory.** The tracker stops streaming if the host goes
   quiet. A daemon thread resends feature report 8 at half the interval.
3. **The DK1 does not enumerate on USB bus power alone.** The control box needs
   its DC adapter. A "missing" device is usually an unplugged power brick.
4. **Two different PyPI packages both import as `hid`** with incompatible APIs
   (`hidapi` by apmorton → `hid.device()`; `hid` by trezor → `hid.Device()`).
   `device.py` detects which is installed. Prefer `hidapi` — its Windows wheel
   bundles the DLL.
5. **Yaw will drift.** Gravity anchors pitch and roll; nothing anchors yaw
 without magnetometer calibration. Planned fix is a recentre key, not a
 fight with the magnetometer. Measured gyro bias at rest is ~0.046 rad/s,
 about 150° of yaw per minute, so subtract an estimated bias.
6. **A legacy Oculus runtime on this machine claims the tracker exclusively.**
 While `OVRServer_x64.exe` runs, opening the HID path fails with Win32 error
 32, and since hidapi opens devices merely to enumerate them, the tracker
 disappears from `--list` rather than appearing as busy. `OVRService` is set
 to manual start now. `explain_invisible_device()` in `device.py` exists to
 tell this apart from a genuinely absent device — don't let a future change
 collapse the two cases back into "check your power brick".
7. **Ignore the first report after opening.** It carries the firmware's
 accumulated backlog (`sample_count` up to 87 observed), so its first sample's
 `dt` can be tens of milliseconds and will jolt the orientation filter.
 `sample_stream()` in `tools/dk1_orient.py` does this.
8. **Take orientation as a quaternion or matrix, never Euler angles.** A headset
 resting on a desk is pitch ≈ −90°, exactly the Euler singularity, where yaw
 and roll describe the same rotation and both swing wildly while the pose is
 perfectly stable. `OrientationFilter.euler` exists for humans reading a
 diagnostic; the renderer gets `.matrix`.
9. **The resting accelerometer magnitude is ~10.6 m/s², not 9.81**, because we
 read raw samples rather than the firmware's factory calibration. Anything
 that gates on "is this close to 1 g" must use a measured reference —
 `calibrate()` returns one — or it will reject valid samples.
10. **The rotation matrix must be transposed on its way into the shader.** The
 filter reports row-major; GLSL reads `mat3` column-major. `column_major()` in
 `renderer.py` does it. Getting this wrong applies the inverse rotation, which
 still renders a convincing panorama and only reveals itself as the view
 turning the wrong way.
11. **The equirectangular lookup is `atan(d.x, -d.z)`.** With `d.z`, `u = 0.5`
 does not land straight ahead and the panorama's wrap sits dead centre in the
 view — a seam directly in front of you. The roadmap's original sketch had
 this wrong.
12. **"Device present but won't open" has two different causes**, and the Win32
 error tells them apart: **32** means another process holds it (the runtime),
 **2** means the node is a ghost and the hardware is gone. Check
 `Present`, not `Status`, in `Get-PnpDevice`.

## Verification style

The protocol decode is validated **against physics, not against itself** — a
stationary accelerometer must read 1 g, so `--sanity` checks `|accel| ≈ 9.81`.
Prefer this kind of check to assertions that merely restate the implementation.

The renderer follows the same rule: `tests/test_renderer.py` runs the **real
shader** in a standalone GL context and asserts on pixels, because every mistake
that matters here still produces a plausible panorama that is merely aimed wrong.
`synthetic_panorama()` colours the cardinal directions and both poles so the
assertions can read "turned left shows the left marker". Re-implementing the
projection in Python and comparing would have tested nothing.

When something looks wrong on hardware, capture rather than guess:

```bat
python tools\dk1_probe.py --record capture.bin --seconds 10
```

The file is concatenated raw 62-byte reports; `dk1.device.replay_raw()` decodes
it offline on any machine.
