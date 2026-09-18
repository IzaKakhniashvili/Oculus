# DK1 360° Player

A small, self-contained driver and 360° video player for the Oculus Rift DK1,
with no dependency on any Oculus runtime. Reads the tracker's raw USB HID
packets directly and renders an equirectangular video onto the inside of a
sphere on the headset's HDMI display.

**Target machine:** Windows 11 (the laptop the DK1 is plugged into).
The code is OS-portable; only display enumeration is platform-specific.

## Status

| Phase | Component | State |
|---|---|---|
| 1 | USB HID transport + packet decode | **done, confirmed on hardware** |
| 2 | Orientation filter (quaternion) | **done, confirmed on hardware** |
| 3 | 360° video renderer | not started |
| 4 | Fullscreen output on the DK1 display | not started |

## Setup (on the Windows 11 laptop)

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Python 3.10+ recommended. No Oculus runtime, no SDK, no driver install.

## Phase 1: verify the tracker

Run these in order.

**1. Is the headset visible on USB?**

```bat
python tools\dk1_probe.py --list
```

Expect a device at `VID 0x2833 PID 0x0001`.

> If nothing shows up, the usual cause is power: the DK1 tracker does **not**
> enumerate on USB bus power alone. The control box needs its DC adapter
> plugged in, and the blue LED should be lit. The other cause is a process
> holding the device exclusively — usually a legacy Oculus runtime — which
> `--list` detects and reports separately.

**2. Is the packet decode correct?**

```bat
python tools\dk1_probe.py --sanity
```

Put the headset on a desk and leave it still. This checks the decode against
physics rather than against itself: a stationary accelerometer must read one
`g`, so if `|accel|` comes back at ~9.81 m/s², the 21-bit unpacking and the
1e-4 scaling are both confirmed correct on real hardware.

**3. Watch it move.**

```bat
python tools\dk1_probe.py --live
```

Tilt the headset and confirm the accelerometer axes respond sensibly.

## Phase 2: watch the orientation

```bat
python tools\dk1_orient.py --live
```

Hold the headset still for a second while the gyro bias is measured, then move
it. `r` recentres the heading, `q` quits.

To check the filter without the headset, run a recorded capture through it — the
same code, real samples, and it will tell you whether its idea of "up" still
agrees with measured gravity:

```bat
python tools\dk1_orient.py --replay capture.bin
```

## If something looks wrong

Capture real packets and they can be analyzed anywhere, on any machine:

```bat
python tools\dk1_probe.py --record capture.bin --seconds 10
```

Move the headset through yaw, pitch and roll while it records. The file is just
concatenated 62-byte reports; `dk1.device.replay_raw()` re-decodes it offline.

## Layout

```
dk1/protocol.py     wire format: 21-bit unpacking, report layout, feature reports
                    pure functions, no I/O, fully unit-tested
dk1/device.py       hidapi transport, keep-alive thread, report/sample iterators
dk1/orientation.py  Mahony filter: samples in, orientation quaternion out
tools/dk1_probe.py  Phase 1 diagnostic CLI
tools/dk1_orient.py Phase 2: live orientation, or replay a capture through it
tests/              run anywhere, no hardware needed
```

Run the tests with:

```bash
python tests/test_protocol.py
python tests/test_orientation.py
```

## Documentation

| Document | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Project context and conventions; auto-loaded by Claude Code |
| [docs/STATUS.md](docs/STATUS.md) | What's built, what's verified, exactly what to do next |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | Full DK1 tracker protocol reference |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phases 2–4 with design decisions already made |

## Known limitations

- **Yaw will drift.** Phase 2's filter corrects pitch and roll against gravity,
  but yaw has no absolute reference without magnetometer calibration. Expect
  slow rotation; a recentre key is the practical fix.
- **The DK1 display is the other unknown.** 1280×800 @ 60 Hz over HDMI/DVI.
  Windows should treat it as an ordinary extended display, but its EDID is
  unusual and mode selection sometimes needs a nudge. Worth testing early.
- **Lens distortion is mandatory, not cosmetic.** The DK1's lenses need a barrel
  pre-warp or the image is unusable. See
  [docs/ROADMAP.md](docs/ROADMAP.md#4b-barrel-distortion--this-is-not-optional).
