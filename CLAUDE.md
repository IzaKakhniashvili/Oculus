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

**One blocker is left, and it is a deadlock, not a dead cable.** The panel *does*
work: with a legacy Oculus runtime running, Windows detects the HDMI display and a
demo scene rendered on the headset (observed 2026-09-21). Kill the runtime and the
panel goes dark again — but **the runtime holds the tracker exclusively**, so the
two halves are mutually exclusive:

| Runtime running | Runtime killed |
|---|---|
| Panel detected, video renders | Panel dark |
| Tracker locked (Win32 error 32) | Tracker ours, streams normally |

`tools/dk1_player.py` needs both at once and can currently have either.

**An earlier version of this file concluded the opposite** — that the hotplug
signal never reaches the GPU, that the causes are physical, and that no software
fix exists. That was drawn from probes taken with no runtime running and it is
**wrong**. The physical path is proven good: socket, cable, control box, receiver,
ribbon and panel all work. Don't buy a cable, adapter or splitter.

The useful lead: `dk1_probe.py` already opens the HID device and already sends the
keep-alive, and the panel still stays dark for us — so the runtime is not merely
holding a session open, it **sends something specific that we do not**. We send
exactly two feature reports, 2 and 8; anything else in the runtime's USB traffic is
the answer. Find it with a capture and we can light the panel ourselves. Full
handoff in
[docs/STATUS.md](docs/STATUS.md#open-problem-the-display-needs-the-runtime-and-the-runtime-takes-the-tracker).

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
 disappears from `--list` rather than appearing as busy. **As of 2026-09-22 it is
 back to `Automatic` and running**, so it claims the tracker on every boot —
 check it before concluding the hardware is missing. `explain_invisible_device()` in `device.py` exists to
 tell this apart from a genuinely absent device — don't let a future change
 collapse the two cases back into "check your power brick". **This is no longer
 a nuisance — it is the project's open problem**, because the runtime is also
 currently the only thing that lights the panel. See gotcha 13.
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
13. **The panel and the tracker are currently mutually exclusive.** A legacy
 Oculus runtime is the only thing that makes Windows detect the HDMI display,
 and that same runtime claims the tracker exclusively. Killing it frees the
 tracker and kills the panel. Don't write code that assumes it can have both
 until the USB capture in [docs/STATUS.md](docs/STATUS.md) says how the runtime
 turns the display on. Two escape routes are open: send the same command
 ourselves (preferred — keeps the project runtime-free), or read the tracker
 through the Windows Raw Input API, which is fed by the HID class driver rather
 than by an exclusive file handle. `protocol.py` is pure bytes-in, so a second
 transport slots in beside hidapi without touching the decode or the filter.

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
