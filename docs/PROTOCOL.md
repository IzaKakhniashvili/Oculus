# DK1 tracker protocol reference

Everything needed to talk to the DK1 tracker without the Oculus SDK.
Implemented in `dk1/protocol.py`; this document explains *why*.

## Device

| | |
|---|---|
| Vendor ID | `0x2833` (Oculus VR) |
| Product ID | `0x0001` (DK1 tracker; DK2 is `0x0021`) |
| Transport | USB HID, interrupt IN |
| Input report ID | `1` |
| Report length | 62 bytes, **including** the leading report-ID byte |
| Report rate | ~500 Hz, each carrying 1–3 samples |
| IMU sample rate | 1000 Hz |
| Sensor | InvenSense MPU-6000 + separate magnetometer |

> **Power:** the tracker does not enumerate on USB bus power alone. The control
> box needs its DC adapter connected.

hidapi returns the report ID as byte 0 because the device uses report IDs, so
all offsets below include it.

## Input report layout

| Offset | Size | Type | Field |
|---|---|---|---|
| 0 | 1 | u8 | Report ID — always `1` |
| 1 | 1 | u8 | Sample count — **may exceed 3** |
| 2 | 2 | u16 LE | Timestamp, wraps |
| 4 | 2 | u16 LE | Last command ID |
| 6 | 2 | i16 LE | Temperature, 0.01 °C |
| 8 | 48 | — | 3 × sample, 16 bytes each |
| 56 | 2 | i16 LE | Magnetometer X |
| 58 | 2 | i16 LE | Magnetometer Y |
| 60 | 2 | i16 LE | Magnetometer Z |

Each 16-byte sample is two 8-byte blocks:

| Offset in sample | Content |
|---|---|
| 0–7 | Accelerometer, 3 × 21-bit packed |
| 8–15 | Gyroscope, 3 × 21-bit packed |

### Scaling

| Quantity | Factor | Result unit |
|---|---|---|
| Accelerometer | `1e-4` | m/s² |
| Gyroscope | `1e-4` | rad/s |
| Magnetometer | `1e-4` | gauss |
| Temperature | `1e-2` | °C |

## The 21-bit packing

This is the single most error-prone part of the project. Three 21-bit
two's-complement integers are packed into 8 bytes — 63 of the 64 bits are used,
and the fields straddle byte boundaries unevenly.

Most-significant bit first:

```
x = byte0[7:0] byte1[7:0] byte2[7:3]                 →  8 + 8 + 5 = 21
y = byte2[2:0] byte3[7:0] byte4[7:0] byte5[7:6]      →  3 + 8 + 8 + 2 = 21
z = byte5[5:0] byte6[7:0] byte7[7:1]                 →  6 + 8 + 7 = 21
```

Bit 0 of byte 7 is padding.

```python
x = (b[0] << 13) | (b[1] << 5)  | ((b[2] & 0xF8) >> 3)
y = ((b[2] & 0x07) << 18) | (b[3] << 10) | (b[4] << 2) | ((b[5] & 0xC0) >> 6)
z = ((b[5] & 0x3F) << 15) | (b[6] << 7)  | (b[7] >> 1)
```

Then sign-extend each from 21 bits — the sign bit is `0x100000`:

```python
value - 0x200000 if value & 0x100000 else value
```

A bug here does **not** crash. It yields plausible-looking numbers that drift
wrong, which is why `tests/test_protocol.py` round-trips the full signed range
and `--sanity` cross-checks against gravity.

## Two things that cause silent drift

### 1. `sample_count` can exceed 3

Only three samples fit in a report. If the firmware counted more, the extras
were **dropped** — but that time still elapsed, and if it isn't integrated the
orientation quietly under-rotates.

The convention (inherited from LibOVR) is that the first surviving sample
absorbs the gap:

```
if sample_count > 3:
    first sample dt = (sample_count - 2) × 1 ms
    remaining samples dt = 1 ms
else:
    every sample dt = 1 ms
```

So `sample_count = 10` yields 3 samples with `dt` of 8 ms, 1 ms, 1 ms — summing
to the full 10 ms elapsed. `TrackerReport.dropped_samples` exposes the count so
drops can be monitored rather than ignored.

### 2. The keep-alive is mandatory

The tracker stops streaming if the host goes quiet. Send feature report 8
periodically — `dk1/device.py` uses a daemon thread at **half** the advertised
interval so a single dropped packet can't cause a timeout.

## Feature reports (output)

### Keep-alive — report `8`, 5 bytes

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | Report ID = `8` |
| 1 | 2 | Command ID, u16 LE |
| 3 | 2 | Keep-alive interval ms, u16 LE (default 10000) |

### Sensor config — report `2`, 7 bytes

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | Report ID = `2` |
| 1 | 2 | Command ID, u16 LE |
| 3 | 1 | Flags |
| 4 | 1 | Packet interval |
| 5 | 2 | Sample rate, u16 LE |

Flag bits:

| Bit | Name |
|---|---|
| `0x01` | Raw mode |
| `0x02` | Calibration test |
| `0x04` | Use calibration |
| `0x08` | Auto calibration |
| `0x10` | Motion keep-alive |
| `0x20` | Command keep-alive |
| `0x40` | Sensor coordinates |

We send `0x30` (motion + command keep-alive) at 1000 Hz.

## Axis mapping

**Measured on hardware, 2026-09-18**, from two recorded captures of the headset
held in its normal worn orientation:

| Motion | Axis | Sign |
|---|---|---|
| Gravity, resting level | Y | + (a level headset reads +1 g on Y) |
| Yaw — turn left | Y | + |
| Pitch — nod down | X | − |
| Roll — tilt right ear to shoulder | Z | − |

So the sensor frame is **X right, Y up, Z backward**, and it is **right-handed**:
rotations follow the right-hand rule about each axis.

### The remap to the render frame is the identity

The renderer's frame is Y up, −Z forward (OpenGL convention), which is X right,
Y up, Z backward — **the same frame the sensor already reports in.** No axis
swap, no sign flip, no permutation matrix. Phase 2 can integrate the gyro and
use the accelerometer as measured.

This is a convenient result and therefore worth distrusting on sight, so both
independent derivations are recorded below.

### How it was determined

Two things make this measurable without a rig:

**Gravity identifies the vertical axis.** A stationary accelerometer reads +1 g
along whichever axis points *up* (it measures the supporting force, not the
field). Level and worn, the DK1 reads `(+0.05, +0.97, +0.24)` normalised — so +Y
is up.

**Gravity also identifies the rotation axis, and does it better than the gyro.**
Integrating the gyro as a vector is only valid while the rotation stays on one
axis; across a 90° turn it smears across all three. The direction of a fixed
world vector does not care: if the measured up-direction moves from `g₀` to `g₁`,
it swung about `g₀ × g₁` exactly, and the body turned the opposite way. On the
recorded nods this gave a **100% pure** X axis where the gyro integral reported
only 73%. Prefer it for any future axis work.

The three motions then separate cleanly:

- **Yaw is the one where gravity does not move.** Turning left produced 55° about
  +Y, 99% pure, with the gravity direction fixed to within 8°. Only rotation
  about the vertical axis can leave gravity unchanged.
- **Nodding down moved up from +Y toward +Z**, about −X. Nodding down tips
  world-up toward the *back* of the head, so **+Z points backward** and −Z is
  forward.
- **Tilting the right ear down moved up from +Y toward −X**, about −Z, 96% pure
  by gyro and 95% by gravity. Up moving toward the body's left is what tilting
  right does, so this also re-confirms **+X is right**.

Right-handedness follows independently from both the yaw and the pitch: for a
body rotating at ω, a fixed world vector in body coordinates obeys
`v̇ = −ω × v`, and ω = −X̂ predicts up moving +Y → +Z, which is what the
accelerometer recorded.

### Gyro bias

Measured at rest in two separate captures:

```
(-0.0404, +0.0230, +0.0094) rad/s    |bias| 0.0474  (2.72 deg/s)
(-0.0335, +0.0193, +0.0088) rad/s    |bias| 0.0397  (2.27 deg/s)
```

Consistent in direction, but the magnitude moved ~16% between runs a few minutes
apart — it varies with temperature. **Estimate it at startup from a stationary
average; do not hard-code these numbers.** Noise about the bias is only
0.01 rad/s rms, so a one-second average is plenty.

## Verification approach

The decode is checked against **physics rather than against itself**. A
stationary accelerometer must read exactly 1 g, so if `|accel| ≈ 9.81 m/s²`
comes back from real hardware, the bit-unpacking *and* the 1e-4 scaling are both
confirmed. A test that merely re-implemented the shifts would pass even if the
layout were wrong.

`tests/test_protocol.py` covers the round-trip, field independence, layout,
scaling, drop timing, malformed input, and feature-report encoding — all without
hardware.
