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

The one untested thing left is the **DK1 display**, which has never been plugged
in, and it is the only remaining unknown that can invalidate a whole phase. Do
that before building the Phase 3 renderer.

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
tools/dk1_probe.py   Phase 1 CLI (--list --sanity --live --raw --record)
tools/dk1_orient.py  Phase 2 CLI (--live --replay)
tests/               protocol + filter tests; run anywhere, no hardware needed
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
- **hidapi is imported softly** in `dk1/device.py` (`hid = None` on ImportError,
  `_require_hid()` at the call site). This is deliberate: replaying a recorded
  capture must work on machines without the binding. Don't "fix" it into a
  hard top-level import.
- Run the tests after touching `protocol.py` or `orientation.py`:

```bat
python tests\test_protocol.py
python tests\test_orientation.py
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

## Verification style

The protocol decode is validated **against physics, not against itself** — a
stationary accelerometer must read 1 g, so `--sanity` checks `|accel| ≈ 9.81`.
Prefer this kind of check to assertions that merely restate the implementation.

When something looks wrong on hardware, capture rather than guess:

```bat
python tools\dk1_probe.py --record capture.bin --seconds 10
```

The file is concatenated raw 62-byte reports; `dk1.device.replay_raw()` decodes
it offline on any machine.
