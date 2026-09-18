# DK1 360° Player — project context

Custom driver + 360° video player for the **Oculus Rift DK1**, with **no Oculus
runtime or SDK**. Reads raw USB HID packets from the tracker, fuses them into an
orientation, and renders an equirectangular video on the headset's HDMI display.

## Read this first

This project was scaffolded on a **Mac**, but the DK1 is plugged into **this
Windows 11 machine**. That means:

- Everything in Phase 1 is written and unit-tested, but **nothing has ever
  touched real hardware**. Treat every USB code path as unproven.
- **The first task in this session is to run the hardware check** and report the
  result. Do not start Phase 2 before Phase 1 is confirmed:

```bat
python tools\dk1_probe.py --list
python tools\dk1_probe.py --sanity
```

See [docs/STATUS.md](docs/STATUS.md) for the full handoff, including what to do
if those fail.

## Layout

```
dk1/protocol.py      wire format: 21-bit unpacking, report layout, feature
                     reports. Pure functions, no I/O, fully unit-tested.
dk1/device.py        hidapi transport, keep-alive thread, report iterators,
                     offline capture replay.
tools/dk1_probe.py   diagnostic CLI (--list --sanity --live --raw --record)
tests/               protocol tests; run anywhere, no hardware needed
docs/                status, protocol reference, roadmap
```

## Conventions

- **Python 3.10+, stdlib-first.** Dependencies are deliberately minimal; see
  `requirements.txt`. Phase 1 needs only `hidapi`.
- **Never add a dependency on the Oculus SDK, LibOVR, or any Oculus runtime.**
  Avoiding them is the entire point of the project — they are unsupported on
  Windows 11.
- **`dk1/protocol.py` stays pure.** No I/O, no threads, no hidapi. It must remain
  runnable and testable on a machine with no headset attached.
- **hidapi is imported softly** in `dk1/device.py` (`hid = None` on ImportError,
  `_require_hid()` at the call site). This is deliberate: replaying a recorded
  capture must work on machines without the binding. Don't "fix" it into a
  hard top-level import.
- Run the tests after touching `protocol.py`:

```bat
python tests\test_protocol.py
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
   fight with the magnetometer.

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
