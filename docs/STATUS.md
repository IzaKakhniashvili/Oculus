# Status & handoff

**As of 2026-09-18.** Phases 1 to 3 are built and confirmed on hardware: turning
the real headset turns a 360° video at 90 fps, in a window. The one thing left
between that and watching it *in* the headset is the DK1 panel, which has never
been detected — and that is now the only open problem in the project.

## Phase table

| Phase | Component | State |
|---|---|---|
| 1 | USB HID transport + packet decode | **Hardware-confirmed.** |
| 2 | Orientation filter (quaternion) | **Built and hardware-confirmed.** |
| 3 | 360° video renderer | **Built and hardware-confirmed.** Live head motion drives 360° video at 90 fps. |
| 4 | Fullscreen + lens distortion on the DK1 display | Blocked — the panel is not detected. |

## What exists

| File | Purpose |
|---|---|
| `dk1/protocol.py` | Report layout, 21-bit unpacking, unit scaling, feature-report builders. Pure functions. |
| `dk1/device.py` | `Tracker` class: open by VID/PID, keep-alive thread, `reports()` / `samples()` iterators, `replay_raw()` for captures. Also `explain_invisible_device()`. |
| `dk1/orientation.py` | `OrientationFilter` (Mahony) and `calibrate()`. Pure math, no I/O. |
| `dk1/renderer.py` | `PanoramaRenderer`: fullscreen-quad equirectangular shader, aimed by a rotation matrix. Takes a caller-supplied GL context. |
| `dk1/video.py` | Threaded decode (`VideoSource`), the one-slot frame handoff (`LatestFrame`), and `synthetic_panorama()`. OpenCV imported softly. |
| `tools/dk1_probe.py` | Phase 1 diagnostic CLI. |
| `tools/dk1_orient.py` | Phase 2 driver: `--live` readout, `--replay` a capture through the filter. |
| `tools/dk1_player.py` | Phase 3 player: `--live`, `--replay`, or mouse-driven with no hardware. |
| `tools/make_test_video.py` | Writes an equirectangular test clip, for exercising decode without a real 360° video. |
| `tools/dk1_display.py` | Phase 4 display probe: enumerates every video output and what is attached. |
| `tests/test_protocol.py` | Protocol test suite, no hardware required. |
| `tests/test_orientation.py` | Filter test suite, no hardware required. |
| `tests/test_video.py` | Frame-handoff and test-pattern suite, no hardware and no OpenCV required. |
| `tests/test_renderer.py` | Runs the real shader offscreen and asserts on pixels. Skips if the machine has no usable GL. |

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

## Phase 3 as built

`tools/dk1_player.py` draws an equirectangular frame through the shader in
`dk1/renderer.py`, aimed by the Phase 2 filter:

```bat
python tools\dk1_player.py                              :: test pattern, mouse look
python tools\dk1_player.py --video clip.mp4 --live      :: the real thing
python tools\dk1_player.py --video clip.mp4 --replay axis_capture.bin
```

Measured with a 2048×1024 clip, both replayed against `axis_capture.bin` and run
live off the headset:

| Measure | Result |
|---|---|
| Render rate | **90 fps**, the vsync ceiling of the internal panel |
| Decode rate | 30 fps, matching the source |
| Frames dropped | **1**, at startup, in every run |
| View follows recorded head motion | yes — yaw tracked 0° → +58° in step with `--replay` |
| View follows **live** head motion | **yes** — yaw swung +11° → +59° → −41° with the headset in hand, pitch and roll responding, no stutter |

A real 3840×1920 clip (NASA's public-domain Webb cleanroom 360° footage) performs
the same: **90 fps, zero dropped frames**, live-tracked, through pitch to +81° and
roll to ±32°. So 4K equirectangular playback needs none of the upload
optimisations the roadmap held in reserve.

One caveat learned the hard way, since it wasted a round of investigation: **this
repository sits in a OneDrive folder.** A run immediately after downloading a
27 MB video into it dipped to 50 fps, which looked convincingly like a
motion-related problem. It was sync contention. The USB stream measured 943
reports/s with a worst gap of 2.3 ms under deliberate motion, an identical run
driven by `--replay` held 90 fps through the same rotation, and the dip never
reproduced. Treat a one-off dip as suspect until it happens twice.

**Calibration retries rather than failing.** The first live attempt died on
`NotStationary` because the headset was in someone's hand for the opening second,
and a player that gives up permanently for that reason is useless. It now waits
for a still second, up to 60 attempts — bounded, because an exhausted replay file
also fails every attempt, instantly, and must not spin.

Confirmation that the Euler warning in this document is real, seen live: with the
headset flat on the desk the readout sat at pitch −89.3°, yaw −118.9°, roll
+119.5° for thirty seconds while the headset did not move at all. Yaw and roll sum
to a steady +0.6°. Nothing is wrong; the renderer takes `.matrix` and is
unaffected.

Two bugs in the roadmap's shader sketch were found and are written up in
[ROADMAP.md](ROADMAP.md#phase-3--360-renderer--done): the horizontal lookup needs
`atan(d.x, -d.z)`, and the rotation matrix must be transposed on its way into the
uniform. **Neither is visible in a still frame** — both render a perfectly
plausible panorama that is simply aimed wrong — which is why
`tests/test_renderer.py` renders the actual shader into an offscreen buffer and
asserts on pixels. `moderngl.create_standalone_context()` works on this AMD
laptop, so that costs nothing and needs no window.

The test pattern exists for the same reason. `synthetic_panorama()` paints the
cardinal directions and both poles in distinct colours, so the assertions can read
"turned left shows the left marker" and a vertical flip or a transpose fails
loudly instead of looking fine.

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

## Resolved: the tracker dropped off USB, and came back

Worth keeping, because it will happen again and it looks alarming.

Mid-session the tracker stopped enumerating entirely, having previously delivered
4628 reports at 926/s with zero errors. Windows reported **both** of its device
nodes as ghosts:

```
HID\VID_2833&PID_0001\7&36F0DBC&0&0000     Present: False   Status: Unknown
USB\VID_2833&PID_0001\61M6I3TGQS37         Present: False   Status: Unknown
```

The only USB devices actually present are the laptop's internal Bluetooth and
webcam. The Oculus runtime was stopped at the time, so this is not the sharing
violation described above — that case leaves the device present and merely
un-openable, and reports Win32 error 32. **This reports error 2, on a path that no
longer exists.** The Config Utility said "Oculus Rift Removed" at around the same
time, which is the same event seen from the other side.

How to tell the three cases apart, since they look similar from `--list`:

| Symptom | Meaning |
|---|---|
| Device present, open fails with **error 32** | Another process holds it — almost always `OVRServer_x64.exe`. |
| Device present, open fails with **error 2** | The node is a ghost; the hardware has gone. |
| Device absent from PnP entirely | Never connected on this machine. |

`Get-PnpDevice | Where-Object { $_.InstanceId -match 'VID_2833' }` is the quickest
check — look at `Present`, not at `Status`.

**Reseating the USB cable and the DC adapter brought it straight back**, Status OK
on both nodes, and it has streamed perfectly since. So the fault is a connection
that works loose, not a dead tracker. If it disappears again, reseat before
investigating anything.

That it recovers so easily also weakens the theory that the DC adapter is starving
the panel: the same box now runs the tracker indefinitely without trouble. The
display and the USB dropout look like two separate problems after all, and only
the display is still open.

## Open problem: the display, and why it cannot be turned on in software

**Windows does not see the DK1 panel at all.** Probe it with:

```bat
python tools\dk1_display.py --list
python tools\dk1_display.py --modes
```

### The mechanism, so the conclusion is not just an assertion

To the PC, the DK1 is an **ordinary external monitor**. It is not a VR device with
a driver: the control box contains an HDMI receiver wired to a 1280×800 LCD, and it
presents itself exactly as a small desktop display would. There is no Oculus
software in the path and, on a DK1, there never was — Direct Mode arrived with the
DK2.

Bringing any monitor up happens in a fixed order, and each step depends on the one
before it:

1. **Hotplug Detect.** The sink pulls a dedicated pin on the connector high. This
   is how the GPU learns anything is plugged in at all. It is an electrical
   signal, not a negotiation.
2. **EDID.** Only once HPD is asserted does the driver read the monitor's EDID
   over the DDC/I²C lines to learn its supported timings.
3. **A display device, then a mode.** Windows creates a monitor device from that
   EDID, adds a video *target* with a sink attached, and only then can anything be
   rendered to it.

**Our probe shows the chain breaking at step 1.** The single external output
reports `targetAvailable = false` — no sink detected — and
`HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY` has only ever contained the internal
panel, so no EDID was ever read and no monitor device was ever created, at any
point in this machine's history.

That is why this cannot be fixed in software, and specifically why the two usual
tricks do not apply:

- **A registry EDID override** (`EDID_OVERRIDE`) fixes a monitor that reports a
  *bad* EDID. It has to be attached to an existing monitor device instance, which
  only exists after steps 1 and 2 succeed. Here there is nothing to attach it to —
  nothing is being misread, because nothing is being read.
- **Forcing a mode** requires a display path with a sink on it. There is no sink.
  Some NVIDIA drivers can force output on a connector regardless; this laptop is
  AMD-only and its driver exposes no equivalent, and `DisplaySwitch.exe /extend`
  correspondingly changed nothing.

So the fault is upstream of every layer software can reach: **the hotplug signal is
not arriving at the GPU.** Everything below is the evidence for that, and the
remaining causes are all physical.

**One variable is still untested, and it is the cheapest one.** Nothing else has
ever been plugged into that HDMI socket during this investigation, so "the socket
works" is an assumption, not a finding. A known-good monitor on that port splits
the problem in half in a single test — see the next step below.

### Ruling out a software cause

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

### The remaining causes, all physical

Since the hotplug signal is not reaching the GPU, something between the panel and
the socket is not carrying it. In rough order of likelihood:

| Cause | Why it is plausible | How to test it |
|---|---|---|
| **The cable** | The most common DK1 failure by a wide margin, and the failure is often invisible: a cable can carry power and picture conductors fine while the HPD or DDC lines are broken, or be damaged only at a strain point. | Try a different HDMI cable. |
| **The laptop's HDMI socket** | Never verified. Nothing else has been plugged into it during this investigation, so its working is an assumption. | Plug in any monitor or TV. |
| **The control box's HDMI input** | DK1 boxes commonly lose one input while keeping the other, since HDMI and DVI-D are separate signal paths inside the box. | Use the DVI-D input with an HDMI-to-DVI-D cable. |
| **The box's video receiver or the ribbon to the panel** | A lit LED and a working tracker prove only that the box has *power*. The USB tracker and the video receiver are independent circuits; the tracker working says nothing about the receiver being alive. | Try the DK1 on another computer. If no machine detects it on either input with a known-good cable, the box or panel is dead. |

**Do the socket test first.** Plugging an ordinary monitor or TV into that same
HDMI socket with that same cable, with `dk1_display.py --watch` running, splits the
problem in half for one minute's work:

- **It appears** → the socket and cable are both fine, and the fault is inside the
  DK1's video path. Move on to the DVI-D input.
- **It does not appear** → the fault is the cable or the socket, and the headset is
  irrelevant to the problem. Swap the cable and repeat.

### If it does start being detected

The rest of Phase 4 then applies as written in [ROADMAP.md](ROADMAP.md): confirm
`--modes` offers 1280×800 @ 60 Hz, then `python tools\dk1_player.py --video
clip.mp4 --live --fullscreen --monitor N`. The player already takes a monitor
index and toggles fullscreen, so the only work left in 4a is choosing the right
index. 4b, the barrel distortion and the stereo pair, is still unwritten.

## Next milestones

**One hardware problem is left, and it is the display.** Everything else works:
the tracker streams, the filter holds, and the player draws 360° video aimed by
real head motion at 90 fps.

1. **Bisect the video path.** Plug an ordinary monitor or TV into that same HDMI
   socket with the same cable and run `dk1_display.py --watch`. If it appears, the
   port and cable are fine and the fault is in the DK1's video path; if not, the
   headset is irrelevant. Try the control box's **DVI-D input** too — a separate
   signal path inside the box from its HDMI input.

2. **Phase 4b, the lens distortion**, which does not need the display. The barrel
   warp and the stereo pair are a post-process on an offscreen texture and can be
   developed and inspected in a window like everything else, so this is the
   obvious software task while the display is stuck. Constants are in
   [ROADMAP.md](ROADMAP.md#4b-barrel-distortion--this-is-not-optional).

Phase 4a is nearly nothing once the panel is detected: the player already takes
`--monitor N` and `--fullscreen`, so only picking the right index remains.
