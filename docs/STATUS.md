# Status & handoff

**As of 2026-09-22 evening.** Phases 1 to 3 are confirmed on hardware. The Oculus
runtime has been **uninstalled**, the DK1 panel enumerates as an ordinary monitor
(`EDID vendor OVR`, `Rift DK`, 1280×800 @ 60 Hz) **with the tracker still ours**,
and Phase 4b — stereo split plus barrel warp — has been run live off the headset.
The remaining gap is Phase 4a: Windows is **mirroring** the laptop onto the DK1
instead of giving it its own 1280×800 surface.

> Earlier versions of this document said the panel was undetectable, then that it
> only lit under the runtime. Both were measured in the wrong state. Uninstalling
> the runtime released the display. See
> [the display section](#resolved-the-oculus-software-was-hiding-the-panel--uninstalling-it-released-the-display).

## Phase table

| Phase | Component | State |
|---|---|---|
| 1 | USB HID transport + packet decode | **Hardware-confirmed.** |
| 2 | Orientation filter (quaternion) | **Built and hardware-confirmed.** |
| 3 | 360° video renderer | **Built and hardware-confirmed.** Live head motion drives 360° video at 90 fps. |
| 4a | Fullscreen on the DK1 as its own monitor | Panel enumerates; desktop is still **mirrored**, not extended. |
| 4b | Stereo split + barrel pre-warp | **Built and live-confirmed.** Same monocular video in both eyes, DK1 lens offset, LibOVR K. |

## What exists

| File | Purpose |
|---|---|
| `dk1/protocol.py` | Report layout, 21-bit unpacking, unit scaling, feature-report builders. Pure functions. |
| `dk1/device.py` | `Tracker` class: open by VID/PID, keep-alive thread, `reports()` / `samples()` iterators, `replay_raw()` for captures. Also `explain_invisible_device()`. |
| `dk1/orientation.py` | `OrientationFilter` (Mahony) and `calibrate()`. Pure math, no I/O. |
| `dk1/renderer.py` | `PanoramaRenderer`: fullscreen-quad equirectangular shader. `render()` is the Phase 3 mono path; `render_stereo()` draws both eyes. |
| `dk1/optics.py` | DK1 screen and lens numbers (LibOVR defaults). Pure math, no I/O. |
| `dk1/video.py` | Threaded decode (`VideoSource`), the one-slot frame handoff (`LatestFrame`), and `synthetic_panorama()`. OpenCV imported softly. |
| `tools/dk1_probe.py` | Phase 1 diagnostic CLI. |
| `tools/dk1_orient.py` | Phase 2 driver: `--live` readout, `--replay` a capture through the filter. |
| `tools/dk1_player.py` | Player: `--live`, `--replay`, `--stereo`, `--no-distortion`. `s` toggles stereo, `d` the warp. |
| `tests/test_optics.py` | Lens-offset and viewport-split suite. No hardware, no GL. |
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

## Phase 4b as built — stereo split and barrel warp

The same monocular panorama is drawn twice, once per eye. That is what the
roadmap specified for 360° video: true stereo needs over/under source footage,
which we do not have.

```bat
python tools\dk1_player.py --video nasa_webb_360.mp4 --live --stereo
```

`s` toggles the split, `d` toggles the warp. `--no-distortion` starts with the
split only, for judging it on a monitor.

| What | How |
|---|---|
| Viewports | left `(0,0,w/2,h)`, right `(w/2,0,w-w/2,h)` — 640×800 on a 1280×800 window |
| Lens centre | LibOVR `XCenterOffset` ≈ **0.151976**. Left eye `+offset`, right `-offset`, so the optical axis goes through the lens, not the middle of each half |
| FOV | physical: `tan(half H) = (H_SCREEN/4)/EYE_TO_SCREEN`, same for V |
| Warp | `r' = r · (1 + 0.22 r² + 0.24 r⁴)` about the lens centre, in the same fragment shader as the equirect lookup. Not a post-process on an offscreen texture — for 360° video there is no scene to render first |

The numbers live in `dk1/optics.py` (pure, tested) and the draw path in
`PanoramaRenderer.render_stereo()`. Mono `render()` is unchanged: centred lens,
identity K. A test asserts that path still matches the original Phase 3 pixels.

**Live, 2026-09-22.** NASA Webb 3840×1920, `--live --stereo`, tracker free after
the uninstall. One session ran **17,079 frames** (~4 min) and quit cleanly. Yaw
swung through a full circle with pitch and roll following. Frame rate sat around
60–75 fps with the DK1 attached and mirroring (vs 90 fps on the laptop alone) —
same cost already noted under the mirror finding, not a stereo regression.

A YouTube-style circular black mask around each eye was tried the same evening
and **reverted**. It punched holes in the half-rectangles; on this headset the
filled halves were better. Do not put it back without a new reason. Chromatic
aberration is still unwritten.

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
display and the USB dropout look like two separate problems after all. The
display is no longer hidden; the dropout still happens and still reseats.

## Resolved: the Oculus software was hiding the panel — uninstalling it released the display

**Solved 2026-09-22.** The Oculus runtime was uninstalled, and the DK1 panel
immediately enumerated as an ordinary monitor **with the tracker still ours**:

```
[ACTIVE] target 50331905  DVI       display attached: yes
                 EDID vendor OVR  product 0x0001   Rift DK
                 driving 60.00 Hz
```

`1280 x 800 @ 60 Hz` is in its mode list, `dk1_probe.py --list` sees the tracker at
the same moment, and `tools/dk1_player.py --video ... --live` played 360° video
tracked by real head motion with the panel attached. **The deadlock is gone and the
display is no longer a blocker.**

The working theory below was right: the Oculus software had the DK1's EDID on a
hide-this-display list honoured by the GPU driver, so the panel was excluded before
Windows ever listed it. That is why no probe in any *runtime* state could see it,
and why stopping the service never brought it back — the exclusion outlived the
service and only went away with the software itself.

**Two earlier conclusions in this project were wrong, and both are worth
remembering as a pattern.** First, that the fault was physical — cable, socket, or
the DK1's own video path — drawn entirely from probes taken in one state. Second,
that the runtime was *required* to light the panel, which inverted cause and
effect: the runtime was the thing suppressing it. In both cases the measurements
were sound and the inference reached past them.

### What remains: the panel is mirrored, not extended

The one piece not yet working. The DK1 currently duplicates the laptop screen
rather than having its own surface:

- `[System.Windows.Forms.Screen]::AllScreens` reports a single desktop screen.
- `glfw.get_monitors()` returns two monitors **both** reporting 2880×1800 at
  position (0,0) — the signature of mirroring, not two distinct surfaces.
- `DisplaySwitch.exe /extend` ran without error and changed nothing.

So the headset shows a scaled copy of the laptop display instead of its native
1280×800. `--fullscreen --monitor N` cannot target a distinct surface while that
is true. **Stereo and the warp do not need that surface** — they were built and
run in a 1280×800 window the same evening. Phase 4a's remaining work is getting
Windows to *extend* onto the DK1 so that window can go fullscreen on the panel
at 1280×800 @ 60 Hz.

Also measured, so it is not mistaken for a regression: with the DK1 attached and
mirroring, the player runs at **45–60 fps with 9 dropped frames**, against a flat
90 fps when no second display was attached. Expected — the GPU is driving a 60 Hz
output alongside the 90 Hz internal panel and scaling 2880×1800 down to 1280×800,
and the two refresh rates no longer divide evenly. Tracking itself was unaffected.

### How it was found — kept because the reasoning is the lesson

### What actually happens

A legacy Oculus runtime was installed and started. **Windows detected the HDMI
display, and a demo scene rendered on the headset** (reported 2026-09-21).

> **Confirmed 2026-09-22.** The full Oculus demo scene was seen rendering inside
> the headset. The panel, the cable, the socket, the control box and its video
> receiver are all proven good beyond any doubt. Nothing physical needs buying
> or replacing.
>
> **And a probe run in that same state still reported nothing attached** — two
> outputs, target 258 empty, and **nothing revealed by `QDC_INCLUDE_HMD`
> either.** Pixels were reaching a panel Windows says does not exist.
>
> That narrows the mechanism sharply rather than deepening the mystery.
> `QDC_INCLUDE_HMD` is a *Microsoft* mechanism, for headsets the OS itself knows
> about. It does not reveal a display that the **GPU vendor's driver** has taken
> out of the OS display list below that level — which is exactly how Oculus
> Direct Mode worked in the 0.6–0.8 era: through NVAPI on NVIDIA, and AMD's own
> direct-display path on AMD. **This laptop is AMD-only**, so that is the path to
> suspect, and it also explains why `DirectDisplayConfig.exe` being NVIDIA-only
> told us nothing.
>
> **Working theory:** the Oculus software has the DK1's EDID on a "this is an
> HMD, hide it" list honoured by the AMD driver, which would explain why the
> panel never appears as a desktop monitor in *any* runtime state — it is
> excluded before the OS ever lists it. The exclusion is persistent, which is
> why stopping the service does not bring it back.
>
> **The test: uninstall the Oculus software completely, reboot, hot-plug the
> headset, re-probe.** If the panel returns as an ordinary extended monitor, the
> deadlock breaks outright — we get the display *and* the tracker, which is the
> whole game.

| | Runtime running | Runtime killed | **Runtime uninstalled** |
|---|---|---|---|
| DK1 panel | detected, renders | not detected | **enumerates as a monitor, 1280×800 @ 60 Hz** |
| Tracker | locked — open fails with Win32 error 32 | ours, ~926 reports/s |

Two consequences.

**The physical path is proven good.** The laptop's HDMI socket, the cable, the
control box's HDMI input, its video receiver, the ribbon and the panel all work —
a picture appeared on them. The hardware bisection this document used to
recommend is moot, and no cable, adapter or splitter needs buying.

**That deadlock is closed.** After uninstall, the table's third column is the
machine's current state: panel enumerates, tracker is ours. The paragraphs below
this heading describe the *pre-uninstall* investigation and are kept as the
record of how we got here, not as open work.

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
- **Recorded 2026-09-22, and it complicates the story.** The only Oculus runtime
  installed is **`Oculus Runtime 0.8.0.0-public-release-117061`**, with
  `OVRServiceLauncher.exe` reporting file version `0.8.0.0.117061`. Alongside it,
  `Oculus Rift Sensor Driver 1.0.14.0`, installed 2026-09-11. **There is no
  DK1-era runtime on the machine** — so the supposition above, that a second and
  older runtime had been installed and was the one lighting the panel, is not
  supported. Whatever put the demo on the panel did so with 0.8.0.0, the very
  version that dropped DK1 display support at 0.5.0.1. Either that history is
  wrong, or 0.8.0.0 retains a path nobody documented.

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

These rows are the **runtime-off / pre-uninstall** baseline. After uninstall the
panel *does* enumerate (see the resolved section above). Do not re-run this list
expecting it to still be true.

### A third output appeared and vanished, 2026-09-22 — possibly the DK1's connector

Two `--list` runs nineteen minutes apart, with nothing deliberately plugged or
unplugged in between:

| Time | Reported |
|---|---|
| 20:40 | **9 paths over 3 outputs** — 256 internal, **257 `DVI`**, 258 `DisplayPort` |
| 20:59 | 6 paths over 2 outputs — the usual 256 and 258. Target 257 gone. |

The 20:40 run used the pre-fix probe; the 20:59 run used the corrected one and its
`QDC_INCLUDE_HMD` diff was empty. But the flag is not what differs here — the
extra output was in the plain `QDC_ALL_PATHS` answer both times, present in one and
absent in the other.

**It came back after uninstall as the DK1 itself.** `--list` then reported
`target 50331905  DVI`, `EDID vendor OVR`, `Rift DK`, driving 60 Hz. Target 257
was the same connector appearing and dropping while the hide-list was still
installed. No longer a mystery.

A same-state probe was also run at 21:12 with `OVRService` Running and the tracker
present, and it returned the plain two-output baseline with an empty
`QDC_INCLUDE_HMD` diff. Per the confirmed finding above, **that says nothing about
whether the panel was lit** — the demo was seen rendering in exactly that kind of
probe-negative state. Recorded only so it is not mistaken later for evidence that
the service had stopped working.

### State the machine was left in, 2026-09-22 evening

- **`OVRService` is gone** — not registered. No Oculus processes. Leftover files
  may still sit under `C:\Program Files (x86)\Oculus`.
- **Panel enumerates:** `EDID vendor OVR`, `Rift DK`, 1280×800 @ 60 Hz, reported
  as DVI target 50331905. Desktop is still mirrored, not extended.
- **Tracker is ours** when the cable is seated. It dropped off USB once during
  this session (ghost nodes, error 2); reseating USB and the DC adapter brought
  it back.
- Do not reinstall the runtime to "fix" the display — that is what hid it.

### What to do next

**1. Extend the desktop onto the DK1.** `DisplaySwitch.exe /extend` already failed
once. Try Settings → Display, identify the `Rift DK`, set it to Extend, and force
1280×800 @ 60 Hz. Then:

```bat
python tools\dk1_display.py --list
python tools\dk1_player.py --video nasa_webb_360.mp4 --live --stereo --fullscreen --monitor N
```

`glfw.get_monitors()` must show a second size, not two copies of 2880×1800 at
(0,0). That is the remaining 4a work.

**2. Chromatic aberration**, if the warp looks right through the lenses but the
edges fringing. Sample R/G/B at the LibOVR radii. Not started.

USB capture and Raw Input were the leads when we still thought the runtime had to
stay installed. They are not the next step anymore.

## Next milestones

**The deadlock is closed. Stereo is in.** What is left is getting a dedicated
1280×800 surface on the panel.

1. **Extend, not mirror**, then `--live --stereo --fullscreen --monitor N`.
2. **Chromatic aberration** once the warp has been judged through the lenses.
3. Do not reinstall the Oculus runtime. It hid the panel; uninstalling released it.
