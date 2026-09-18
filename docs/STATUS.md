# Status & handoff

**As of 2026-09-18.** Phase 1 is **confirmed on real hardware** on the Windows 11
laptop. Phase 2 is now unblocked except for the axis mapping.

## Phase table

| Phase | Component | State |
|---|---|---|
| 1 | USB HID transport + packet decode | **Hardware-confirmed.** |
| 2 | Orientation filter (quaternion) | **Built and hardware-confirmed.** |
| 3 | 360° video renderer | Not started |
| 4 | Fullscreen + lens distortion on the DK1 display | Not started |

## What exists

| File | Purpose |
|---|---|
| `dk1/protocol.py` | Report layout, 21-bit unpacking, unit scaling, feature-report builders. Pure functions. |
| `dk1/device.py` | `Tracker` class: open by VID/PID, keep-alive thread, `reports()` / `samples()` iterators, `replay_raw()` for captures. Also `explain_invisible_device()`. |
| `dk1/orientation.py` | `OrientationFilter` (Mahony) and `calibrate()`. Pure math, no I/O. |
| `tools/dk1_probe.py` | Phase 1 diagnostic CLI. |
| `tools/dk1_orient.py` | Phase 2 driver: `--live` readout, `--replay` a capture through the filter. |
| `tools/dk1_display.py` | Phase 4 display probe: enumerates every video output and what is attached. |
| `tests/test_protocol.py` | Protocol test suite, no hardware required. |
| `tests/test_orientation.py` | Filter test suite, no hardware required. |

### `dk1_probe.py` commands

| Command | Purpose |
|---|---|
| `--list` | Enumerate HID devices, highlight VID 0x2833, print a power/cable checklist if absent. |
| `--sanity` | **The important one.** 5 s stationary capture; validates decode against physics. |
| `--live` | Scrolling accel/gyro/mag readout at 20 Hz. |
| `--raw N` | Hex dump N reports plus their decode. |
| `--record F --seconds S` | Write raw reports to a file for offline analysis. |

## What is actually verified

### On hardware, 2026-09-18

`--sanity` passed all five checks against the real tracker:

```
reports          4628  (926/s)
samples          4922  (984/s)
decode errors    0
dropped samples  0
temperature      30.29 C
|accel|          10.5834 m/s^2
|gyro|           0.04640 rad/s
|mag|            0.2583 gauss
```

Gravity coming back at the right order of magnitude confirms the 21-bit
unpacking *and* the `1e-4` scale on real bytes — the assumed report layout is
correct. The device enumerates as `VID 0x2833 PID 0x0001`,
`Oculus VR, Inc. / Tracker DK`, on HID usage page `0x03` usage `0x05`
(Head Tracker), bound to Microsoft's generic raw-HID driver. No driver install
and no Oculus SDK were needed.

Two numbers above are worth carrying into Phase 2:

- **`|accel|` is ~8% high** (10.58 vs 9.81). Expected: we ask for raw samples,
  not the device's factory calibration (`FLAG_USE_CALIBRATION` is not set). It
  does not affect the gravity *direction*, which is all the filter uses, so it is
  not a blocker — but do not use accelerometer magnitude as an absolute.
- **The gyro has a real bias of ~0.046 rad/s (≈2.6 °/s) at rest.** Left
  uncorrected that is ~150° of yaw drift per minute. Estimate and subtract it.

### Offline, with no hardware attached

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

**Still not verified:**

- **The DK1 display.** Never connected, and it is now the only unknown left that
  can invalidate a whole phase. See "Open risk" below.

## Environment on the Windows laptop

Already set up — `.venv` on Python 3.11 with `hidapi` and `numpy` installed:

```bat
.venv\Scripts\activate
python tools\dk1_probe.py --list
python tools\dk1_probe.py --sanity
```

> **The legacy Oculus runtime was installed on this machine and it claims the
> tracker exclusively.** While `OVRServer_x64.exe` runs, every attempt to open
> the HID path fails with Win32 error 32 (`ERROR_SHARING_VIOLATION`), and
> because hidapi opens each device just to enumerate it, the tracker vanishes
> from `--list` entirely rather than showing up as busy. It has been set to
> manual start, but if it ever comes back, in an elevated shell:
>
> ```bat
> sc config OVRService start= demand
> taskkill /F /IM OVRServer_x64.exe /IM OVRServiceLauncher.exe
> ```
>
> `--list` now detects this case and says so instead of blaming the power brick.
> Genuine absence really is almost always power: the tracker does not enumerate
> on USB bus power alone, so the control box needs its DC adapter.

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

## Axis mapping — measured, Phase 2 unblocked

| Motion | Axis | Sign |
|---|---|---|
| Gravity, resting level | Y | + |
| Yaw — turn left | Y | + |
| Pitch — nod down | X | − |
| Roll — tilt right ear to shoulder | Z | − |

The sensor frame is X right, Y up, Z backward, right-handed — **identical to the
renderer's frame**, so the sensor-to-render remap is the identity. Full
derivation and method in [PROTOCOL.md](PROTOCOL.md#axis-mapping).

Measured from two captures of deliberate head motions, analysed offline. The
useful trick, worth reusing: derive the rotation axis from how the *gravity
direction* moved (`g₀ × g₁`) rather than by integrating the gyro, which smears
across axes over a 90° turn. Gravity gave a 100% pure axis where the gyro
integral gave 73%.

## Phase 2 as built, and what hardware said about it

`dk1/orientation.py` is the Mahony filter from the roadmap, plus three things the
hardware forced:

- **`calibrate()` measures gyro bias *and* the resting accelerometer magnitude.**
  The magnitude matters because this unit reads ~10.6 m/s² at rest, which eats
  most of the ±2 m/s² window used to decide whether the accelerometer is
  measuring gravity or movement. Gating against standard gravity would start
  rejecting valid samples.
- **The orientation is seeded from the first accelerometer reading** instead of
  starting level. Converging from level takes ~5 s at `kp=0.5`, and a headset is
  rarely level when playback starts. Verified on a real capture: pitch now reads
  its true −7° immediately rather than crawling there over three seconds.
- **`dt` is clamped to 50 ms** and the occurrences counted, as a backstop for the
  backlogged first report described below.

Replaying the recorded captures (`tools/dk1_orient.py --replay`):

| Measure | Result |
|---|---|
| Yaw reported for the left turn | +57°, against 55° from the independent gravity analysis |
| Estimated up vs measured gravity, at rest | **1.7° apart** |
| Yaw drift while stationary | **+0.21 °/s**, down from the 2.67 °/s raw bias |
| Accelerometer samples rejected as movement | 1.1% across a capture full of fast motion |
| `dt` clamps after dropping the first report | none |

The check worth keeping is the second one: **while the headset is at rest the
accelerometer can only be measuring gravity, so the filter's idea of up must
agree with it** — in any pose, regardless of what the headset was doing earlier.
That is a real invariant, unlike "did it come back to where it started", which
only means anything if the headset actually did.

### Use the quaternion, not the Euler angles

Resting flat on a desk is pitch ≈ −90°, exactly the Euler singularity, where yaw
and roll become the same rotation and both swing freely while the pose is
perfectly stable — observed live as yaw −124.7° with roll +126.0°, sum steady at
+1.3°. Nothing is wrong when this happens. **Phase 3 should take
`OrientationFilter.matrix` or `.quaternion`**; the Euler angles are for humans
reading a diagnostic, and `--live` flags the degenerate region.

## Observed on hardware, for any consumer of the sample stream

**Discard the first report after opening the device.** It arrives carrying the
firmware's whole accumulated backlog — `sample_count` of 20 and 87 were both
seen, against a steady-state value of 1. Our `dt` rule then hands the filter a
single sample covering 85 ms, which would inject a spurious lurch at startup.
The samples themselves are fine; it is the timing that is meaningless.
`sample_stream()` in `tools/dk1_orient.py` does this; copy it in Phase 3.

**The stream can be briefly silent right after another process releases the
device.** Immediately after the Oculus runtime was killed, one report arrived
and then nothing for 10 s; a second run was flawless at 926 reports/s. Treat an
initial silence as a reason to retry, not as a failure.

## Open risk: the display — investigated, not yet working

**Windows does not see the DK1 panel.** Probe it with:

```bat
python tools\dk1_display.py --list
python tools\dk1_display.py --modes
```

What the probe establishes, as of 2026-09-18 with the HDMI reportedly connected:

| Finding | Evidence |
|---|---|
| This laptop has exactly **one** external video output | `QueryDisplayConfig` reports 2 targets: 256 (internal panel) and 258. The 6 paths are those 2 targets × 3 desktop sources. |
| Nothing is attached to it | target 258 reports `targetAvailable = false` |
| Windows has **never** seen the DK1 | `HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY` holds one entry ever, `SDC4154`, the internal panel. No stale `OVR` entry. |
| It is not being hidden as an HMD | querying with `QDC_INCLUDE_HMD` returns the same two targets |
| Nothing is claiming it in Direct Mode | there is no Oculus display driver installed; `C:\Program Files (x86)\Oculus\Drivers` contains only `RiftSensorDriver` |

So this is **not** the EDID problem the roadmap anticipated, and not a mode-list
problem. The link is not coming up at all — Windows sees no sink on the cable.

Two things worth knowing before chasing it:

- **The one external output is reported as "DisplayPort", which does not mean the
  laptop lacks an HDMI socket.** Laptop HDMI ports are commonly a DP lane with an
  on-board converter, and Windows reports the lane, not the socket.
- **A working tracker does not prove the panel has power.** Both are fed by the
  control box's DC adapter, but the tracker enumerating only tells you the box is
  powered, not that the panel is being driven.

### The Oculus runtime never saw the display either — tested

Worth settling, because "the runtime detected it" is a reasonable thing to
conclude from what the runtime prints. The service was restarted and the display
re-probed with it running: **no change** — same two outputs, target 258 still
reporting nothing attached, no new monitor, no new registry entry.

Its own logs in `%LOCALAPPDATA%\Oculus\ServerLog_*.txt` say why:

```
[TrackingManager] HMD connected
[HMD] WARNING: Unable to change dynamic prediction mode setting
[HMD] WARNING: Unable to change low persistence mode setting
```

`TrackingManager` is the **USB tracker**. Across every server log there is not a
single mention of EDID, display detection, direct mode, extended mode, or 1280 —
the runtime detected the headset over USB and said "HMD connected" on that basis
alone. The two warnings are DK2-era features a DK1 does not have.

Two other things the investigation turned up:

- **The installed runtime is SDK 0.8.0.0**, from the PDB path inside
  `DirectDisplayConfig.exe`. DK1 support ended at 0.5.0.1 and Extended Mode was
  removed after 0.6, so this runtime cannot drive a DK1 display even in
  principle.
- **`DirectDisplayConfig.exe` is NVIDIA-only.** Its own strings are
  `"DirectDisplay compatible NVidia runtime and/or GPU detected"` / `"not
  detected. Skipping..."`. This laptop is AMD, so it has nothing to toggle.
- The logs are full of `{ERR-027} Deadlock detected`, so the runtime is also
  simply unstable with this device.

**Conclusion: the panel has never been driven on this machine, by us or by the
runtime.** Windows sees no sink on the cable, which makes this physical — cable
or panel — and not something software can reach.

### What has been ruled out on the machine side

Reported state: the control box LED is lit, the video cable is in the laptop's
own HDMI socket, and the tracker on the same control box works over USB.

| Ruled out | How |
|---|---|
| A second GPU owning the HDMI port | The machine is an ASUS Vivobook M3401QA with integrated Radeon only. No discrete GPU exists, present or hidden, so the one external output *is* the HDMI socket — reported as a DisplayPort lane, which is normal for laptop HDMI. |
| The GPU having seen the panel before | No cached EDID under `HKLM\SYSTEM\CurrentControlSet\Control\Video\{...}` for any connector. |
| Windows needing a nudge to extend | `DisplaySwitch.exe /extend` changed nothing; the output still reports nothing attached. |
| A hotplug event arriving but being ignored | `dk1_display.py --watch` polled for 50 s and saw no connector change state at all. |

The next step is not software. **Bisect the cable path with a known-good
display**: plug an ordinary monitor or TV into that same HDMI socket with the
same cable and run `--watch`. If it appears, the port and cable are fine and the
fault is in the DK1's video path; if it does not, the fault is the cable or the
socket, and the headset is irrelevant. Worth trying the control box's **DVI-D
input** too, since that is a separate signal path inside the box from its HDMI
input.

### If it does start being detected

The rest of Phase 4 then applies as written in [ROADMAP.md](ROADMAP.md): confirm
`--modes` offers 1280×800 @ 60 Hz, then pick that monitor in `glfw`. Until then,
Phase 3 should be built in a desktop window, which the roadmap recommends anyway.

## Next milestones

**Check the HDMI display.** It is the only thing left that can invalidate a whole
phase, and it is entirely independent of the software, so it should happen before
Phase 3 rather than after.

Then Phase 3, the renderer — see [ROADMAP.md](ROADMAP.md). The design decisions
are already made there, it now has a working orientation source to drive it, and
the roadmap is explicit that it should be built in a desktop window first.
Debugging a renderer while wearing a headset showing a warped image is miserable.
