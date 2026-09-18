# Status & handoff

**As of 2026-09-18.** Phase 1 written on macOS; hardware is on a Windows 11
laptop and has not yet been exercised.

## Phase table

| Phase | Component | State |
|---|---|---|
| 1 | USB HID transport + packet decode | **Built. Unit-tested. Not yet hardware-confirmed.** |
| 2 | Orientation filter (quaternion) | Not started |
| 3 | 360° video renderer | Not started |
| 4 | Fullscreen + lens distortion on the DK1 display | Not started |

## What exists

| File | Purpose |
|---|---|
| `dk1/protocol.py` | Report layout, 21-bit unpacking, unit scaling, feature-report builders. Pure functions. |
| `dk1/device.py` | `Tracker` class: open by VID/PID, keep-alive thread, `reports()` / `samples()` iterators, `replay_raw()` for captures. |
| `tools/dk1_probe.py` | Diagnostic CLI. |
| `tests/test_protocol.py` | Protocol test suite, no hardware required. |

### `dk1_probe.py` commands

| Command | Purpose |
|---|---|
| `--list` | Enumerate HID devices, highlight VID 0x2833, print a power/cable checklist if absent. |
| `--sanity` | **The important one.** 5 s stationary capture; validates decode against physics. |
| `--live` | Scrolling accel/gyro/mag readout at 20 Hz. |
| `--raw N` | Hex dump N reports plus their decode. |
| `--record F --seconds S` | Write raw reports to a file for offline analysis. |

## What is actually verified

Verified by running on macOS, with **no hardware**:

- 4,913 pack/unpack round-trips across the full 21-bit signed range, including
  values straddling every byte boundary in the packing.
- Field independence — a value in one slot doesn't bleed into the other two.
- Full report layout: sample count, timestamp, command ID, temperature,
  magnetometer, per-sample accel/gyro at the correct offsets.
- Unit scaling: −98066 counts → −9.8066 m/s².
- Dropped-sample timing: `sample_count=10` yields 3 samples whose `dt` still
  sums to exactly 10 ms.
- Malformed input (short, long, wrong report ID) is rejected.
- Feature-report byte encoding for keep-alive and sensor-config.
- End-to-end replay → integration: 1.0 rad/s over 0.6 s integrates to exactly
  0.6 rad.
- `--help` and the missing-hidapi path fail cleanly with no traceback.

**Not verified — everything requiring the headset:**

- That the device enumerates at VID 0x2833 / PID 0x0001 on this unit.
- That `send_feature_report` is accepted and the keep-alive actually works.
- That reports arrive at the expected rate.
- That the real byte layout matches the assumed one. The decode is derived from
  the documented DK1 format; only hardware can confirm it.
- **Sensor axis orientation and signs.** Which physical axis is yaw vs pitch vs
  roll, and their polarity, is unknown until observed. Phase 2 blocks on this.

## Do this first

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python tools\dk1_probe.py --list
```

Expect `VID 0x2833  PID 0x0001`.

> **Nothing found?** Almost always power. The DK1 tracker does not enumerate on
> USB bus power alone — the control box needs its DC adapter, and the blue LED
> should be lit. Then try a different USB port.

Then, headset flat on a desk and untouched for 5 seconds:

```bat
python tools\dk1_probe.py --sanity
```

### Interpreting `--sanity`

| Check | Meaning if it fails |
|---|---|
| `\|accel\| ≈ 9.81 m/s²` | **The decisive check.** A stationary accelerometer reads exactly 1 g. Wrong magnitude ⇒ the 21-bit unpacking or the 1e-4 scale is wrong. Everything downstream is invalid until this passes. |
| `\|gyro\| ≈ 0 at rest` | Either the headset moved, or gyro bytes are being read at the wrong offset. |
| sample rate > 200/s | USB trouble, or the keep-alive isn't being accepted and the stream is timing out. |
| 0 decode errors | Malformed reports — check report length and ID. |
| temperature 10–60 °C | Offset 6 is being read wrong. |

If anything fails, **capture rather than guess**:

```bat
python tools\dk1_probe.py --record capture.bin --seconds 10
```

Move the headset through yaw, pitch and roll while it records. The file is just
concatenated 62-byte reports and can be decoded on any machine.

## Then: determine the axis mapping

Phase 2 cannot be written correctly without this, and it takes two minutes with
`--live`. Hold the headset in its normal worn orientation and record, for each
motion, **which axis moves and in which direction**:

| Motion | Axis? | Sign? |
|---|---|---|
| Resting level (gravity) | | |
| Yaw — turn left | | |
| Pitch — nod down | | |
| Roll — tilt right ear to shoulder | | |

Write the answers into `docs/PROTOCOL.md` under "Axis mapping". They determine
the remap from sensor frame to the renderer's frame (Y up, −Z forward).

## Open risk

**The DK1 display is the largest unknown in the project**, and it is independent
of all the tracker work. Worth testing early rather than discovering at Phase 4:
plug the HDMI in and check whether Windows detects a 1280×800 @ 60 Hz display.
The DK1's EDID is unusual. If Windows won't offer the mode, that is a bigger
problem than anything in the software, so find out now.

## Next milestones

See [ROADMAP.md](ROADMAP.md). Phase 2 is the orientation filter; the design
decisions are already made there, and it is blocked only on the axis mapping
above.
