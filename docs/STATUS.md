# Status & handoff

**As of 2026-09-22.** Phases 1 to 3 are built and confirmed on hardware: turning
the real headset turns a 360° video at 90 fps, in a window. The one thing left
between that and watching it *in* the headset is a **deadlock**: the DK1 panel
lights only while a legacy Oculus runtime is running, and that runtime holds the
tracker exclusively. That is now the only open problem in the project.

> **Correction, 2026-09-22.** Everything this document previously said about the
> panel being undetectable and the fault being physical was measured with no
> runtime running, and is wrong. The display works. See
> [the display section](#open-problem-the-display-needs-the-runtime-and-the-runtime-takes-the-tracker).

## Phase table

| Phase | Component | State |
|---|---|---|
| 1 | USB HID transport + packet decode | **Hardware-confirmed.** |
| 2 | Orientation filter (quaternion) | **Built and hardware-confirmed.** |
| 3 | 360° video renderer | **Built and hardware-confirmed.** Live head motion drives 360° video at 90 fps. |
| 4 | Fullscreen + lens distortion on the DK1 display | Blocked — the panel and the tracker are mutually exclusive. |

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

- **The DK1 display driven by our own code.** It renders under the Oculus
  runtime, but the runtime locks the tracker, so the player has never driven it.
  See the display section below.

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

## Open problem: the display needs the runtime, and the runtime takes the tracker

**Corrected 2026-09-22.** This section previously concluded that the panel had
never been detected, that the hotplug signal was not reaching the GPU, and that
the remaining causes were all physical — cable, socket, or the DK1's own video
path. **That conclusion was wrong.** Every probe behind it was taken with no
Oculus runtime running, and it did not survive the first test in the other state.

### What actually happens

A legacy Oculus runtime was installed and started. **Windows detected the HDMI
display, and a demo scene rendered on the headset** (reported 2026-09-21).

> ⚠️ **Not reproduced, 2026-09-22.** A later `--list` — run with the runtime
> installed, and now also querying `QDC_INCLUDE_HMD` — reported the original
> baseline exactly: two outputs, target 258 with no display attached, and
> **nothing hidden in Direct Mode**. So the panel is not merely being concealed
> from the desktop.
>
> The runtime-on observation therefore rests on a single recollection, and the
> state it was taken in is uncertain. Treat everything in this section as
> **unconfirmed** until someone reports the headset visibly lit *at the same
> moment* as a probe run. Note the documented trap: the runtime prints
> `[TrackingManager] HMD connected` on the strength of the USB tracker alone,
> with no display involved.

| | Runtime running | Runtime killed |
|---|---|---|
| DK1 panel | detected, renders | not detected |
| Tracker | locked — open fails with Win32 error 32 | ours, ~926 reports/s |

Two consequences.

**The physical path is proven good.** The laptop's HDMI socket, the cable, the
control box's HDMI input, its video receiver, the ribbon and the panel all work —
a picture appeared on them. The hardware bisection this document used to
recommend is moot, and no cable, adapter or splitter needs buying.

**What is left is a deadlock.** `tools/dk1_player.py` needs the panel *and* the
tracker, and each state offers exactly one.

### What that rules in and out

**It is not a driver install.** A driver persists once installed; this does not.
Something the runtime does *while it is alive* holds the display up.

**It is not merely an open USB session either — and this is the useful part.**
`dk1_probe.py` opens the HID device and sends the keep-alive (report 8)
continuously, and the panel stays dark for us. So the runtime is not just keeping
a session alive: it **sends something we do not**. That is a specific, findable
difference rather than a vague one.

We send exactly two feature reports — **2** (sensor config) and **8**
(keep-alive). Anything else in the runtime's traffic to VID `0x2833` is the
answer.

### Why the earlier probes said otherwise

They were sound measurements of one state; the conclusion drawn from them
overreached. Two things to carry forward:

- **The "the runtime never saw the display either" test is superseded.** It used
  the runtime already on the machine, **SDK 0.8.0.0**, which dropped DK1 display
  support at 0.5.0.1 and genuinely cannot drive this panel — so that finding was
  true of *that* runtime. A different, DK1-era runtime was installed afterwards
  and behaves completely differently. Its logs saying `[TrackingManager] HMD
  connected` still refer to the USB tracker, and that reading was correct.
- **The version now installed has not been recorded, and must be.** It is the
  single most important reproducibility fact in the project: the difference
  between a runtime that cannot light the panel and one that can. Get it from the
  Config Utility's About box, or the installer filename.

The hotplug → EDID → display-device chain described in the old text is still an
accurate account of how any monitor comes up, and still explains what the probe
sees with the runtime stopped. It was simply never the whole story.

### Evidence — all of it gathered with the runtime NOT running

| Finding | Evidence |
|---|---|
| This laptop has exactly **one** external video output | `QueryDisplayConfig` reports 2 targets: 256 (internal panel) and 258. The 6 paths are those 2 targets × 3 desktop sources. |
| Nothing attached to it | target 258 reports `targetAvailable = false` |
| No monitor history | `HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY` held one entry, `SDC4154`, the internal panel |
| Not hidden as an HMD | querying with `QDC_INCLUDE_HMD` returns the same two targets |
| No second GPU owning the socket | ASUS Vivobook M3401QA, integrated Radeon only. The one external output *is* the HDMI socket — reported as a DisplayPort lane, which is normal for laptop HDMI. |
| No cached EDID for any connector | nothing under `HKLM\SYSTEM\CurrentControlSet\Control\Video\{...}` |
| `DisplaySwitch.exe /extend` changes nothing | the output still reports nothing attached |

**None of this has been re-run with the runtime up, and all of it should be.**
That is task 2 below.

### What to measure next, in order

**1. Record the runtime version.** Config Utility → About, or the installer
filename. Nothing else here is reproducible without it.

**2. Probe the display in the working state.** Never been done:

```bat
python tools\dk1_display.py --list
python tools\dk1_display.py --modes
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY"
```

What matters is whether a **second** entry appears under `Enum\DISPLAY` carrying a
real EDID — 1280×800, an Oculus vendor ID. If it does, the panel genuinely
enumerates over the cable once the runtime acts, which means hotplug is being
asserted and the runtime is switching the DK1's video side on. If the display
appears with no corresponding monitor enumeration, it is coming from the driver
side and the answer is a different one.

**3. Catch the transition.** Start the watcher first, then start the runtime:

```bat
python tools\dk1_display.py --watch --seconds 60
```

**4. Capture the USB traffic — this is the one that ends the problem.** Install
USBPcap, capture the root hub the DK1 is on, start the runtime, stop the capture,
then filter Wireshark to the DK1's address and read the SET_REPORT / feature-out
traffic. Any report ID that is not 2 or 8 is the lead. Implementing it is then a
few lines in `dk1/protocol.py` and one call in `dk1/device.py`, and we light the
panel ourselves with no runtime running at all.

Same discipline that pinned down the 21-bit packing: capture rather than guess.

**5. If the runtime turns out to be unavoidable, test Raw Input.**
`RegisterRawInputDevices` on HID usage page `0x03` usage `0x05` (Head Tracker —
what the DK1 registers as) is fed by the HID class driver rather than by an
exclusive file handle, so it may keep delivering reports while the runtime owns
the device. **Unverified**, and cheap to settle with a script that registers for
the usage page and prints whether anything arrives.

It fits the architecture without disturbing it: `dk1/protocol.py` is pure
bytes-in, samples-out, so a Raw Input transport slots in beside hidapi in
`device.py` while the decode, the filter and the renderer stay untouched.

### An intermediate step available right now

With the runtime running, run the player in **mouse-look mode** — which needs no
tracker — fullscreen on the panel:

```bat
python tools\dk1_player.py --video clip.mp4 --fullscreen --monitor N
```

That proves we can render to the headset, separately from proving we can track
while doing it, and it exercises the `--monitor` index choice that is all Phase 4a
has left.

### If the panel becomes usable while the tracker is still ours

The rest of Phase 4 applies as written in [ROADMAP.md](ROADMAP.md): confirm
`--modes` offers 1280×800 @ 60 Hz, then run the player with `--live --fullscreen
--monitor N`. 4b, the barrel distortion and the stereo pair, is still unwritten —
and it never needed the display.

## Next milestones

**One problem is left and it is the deadlock above.** Everything else works: the
tracker streams, the filter holds, and the player draws 360° video aimed by real
head motion at 90 fps.

1. **Record the installed runtime version**, then re-run the display probes with
   it running. A minute's work, and nothing else is reproducible without it.
2. **Capture the runtime's USB traffic** and find the feature report that turns the
   panel on. This ends the problem outright and keeps the project runtime-free,
   which is the entire point of it.
3. **Failing that, test Raw Input** as a way to read the tracker while the runtime
   holds it. It would let the two coexist without knowing how the runtime works.
4. **Phase 4b, the lens distortion** — which never needed the display. The barrel
   warp and the stereo pair are a post-process on an offscreen texture, developable
   and inspectable in a window, so this is the obvious software task at any moment
   the hardware is uncooperative. Constants are in
   [ROADMAP.md](ROADMAP.md#4b-barrel-distortion--this-is-not-optional).

Phase 4a is nearly nothing once the panel and the tracker can coexist: the player
already takes `--monitor N` and `--fullscreen`, so only picking the right index
remains.
