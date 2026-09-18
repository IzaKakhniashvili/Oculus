#!/usr/bin/env python3
"""Orientation filter driver: live readout, or replay of a recorded capture.

    python tools/dk1_orient.py --replay capture.bin   # verify offline, anywhere
    python tools/dk1_orient.py --live                 # needs the headset

Replay is the honest test of the filter. It runs the same code over real
recorded samples and checks the two things that must hold: pitch and roll are
anchored by gravity and must come back to where they started once the headset
does, and yaw is not anchored by anything and is only allowed to drift slowly.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1.orientation import NotStationary, OrientationFilter, calibrate  # noqa: E402

DEG = 180.0 / math.pi


def sample_stream(reports, skip_first: bool = True):
    """Flatten reports into samples, dropping the backlogged first one.

    The first report after opening carries the firmware's accumulated backlog,
    so its leading sample claims tens of milliseconds and would jolt the filter.
    """
    for i, report in enumerate(reports):
        if i == 0 and skip_first:
            continue
        for sample in report.samples:
            yield sample


def cmd_replay(path: str, kp: float) -> int:
    from dk1.device import replay_raw

    stream = sample_stream(replay_raw(path))
    try:
        cal = calibrate(stream, seconds=1.0)
    except NotStationary as exc:
        print(f"Cannot calibrate from this capture: {exc}")
        print("Record one that starts with a second of stillness.")
        return 1

    print(f"calibrated from {cal.samples} samples")
    print(f"  gyro bias   ({cal.gyro_bias[0]:+.5f}, {cal.gyro_bias[1]:+.5f}, "
          f"{cal.gyro_bias[2]:+.5f}) rad/s   = {_norm(cal.gyro_bias) * DEG:.2f} deg/s")
    print(f"  resting |a| {cal.gravity:.3f} m/s^2\n")

    filt = OrientationFilter(kp=kp, calibration=cal)

    t = 0.0
    rows, next_row = [], 0.0
    start = start_accel = None
    still_end = 0.0  # end of the opening stationary stretch
    still_yaw = []
    recent = deque(maxlen=200)  # last 0.2 s, for judging "at rest"

    for sample in stream:
        filt.update(sample)
        t += sample.dt
        recent.append(sample)

        if start is None:
            start, start_accel = filt.euler, _unit(sample.accel)

        # The opening stretch lasts until the headset first actually moves.
        if still_end == 0.0:
            rate = _norm(tuple(sample.gyro[k] - cal.gyro_bias[k] for k in range(3)))
            if rate > 0.35:
                still_end = t
            else:
                still_yaw.append((t, filt.euler[0]))

        if t >= next_row:
            rows.append((t, filt.euler))
            next_row += 0.5

    if start is None:
        print("Capture contained no samples.")
        return 1

    end = filt.euler
    end_accel = _unit(_mean_accel(recent))
    at_rest = _norm(_mean_gyro(recent, cal.gyro_bias)) < 0.1

    print("    t     yaw    pitch    roll   (degrees)")
    for t_row, (yaw, pitch, roll) in rows:
        print(f"  {t_row:5.1f}  {yaw * DEG:+7.1f} {pitch * DEG:+7.1f} {roll * DEG:+7.1f}")

    print(f"\n  samples {filt.updates}   dt clamped {filt.clamped_samples}"
          f"   accel rejected {filt.rejected_accel}"
          f" ({100.0 * filt.rejected_accel / max(1, filt.updates):.1f}%)")
    print(f"  stationary opening {still_end:.1f}s;  headset "
          f"{'is' if at_rest else 'is NOT'} at rest at the end")

    drift = 0.0
    if len(still_yaw) > 1 and still_yaw[-1][0] > still_yaw[0][0]:
        drift = (still_yaw[-1][1] - still_yaw[0][1]) / (still_yaw[-1][0] - still_yaw[0][0]) * DEG

    print("\n" + "-" * 62)
    checks = []

    # The invariant that holds in any pose: while the headset is at rest, the
    # only thing the accelerometer can be measuring is gravity, so the filter's
    # idea of which way is up must agree with it. This is the real test, and it
    # does not care whether the headset was put back where it started.
    if at_rest:
        err = _angle_between(filt.estimated_up, end_accel) * DEG
        checks.append(("estimate agrees with measured gravity", err < 2.0, f"{err:.2f} deg apart"))

    # Only meaningful if the headset really did come back, which the
    # accelerometer can say independently of the filter.
    returned = _angle_between(start_accel, end_accel) * DEG
    if at_rest and returned < 5.0:
        for name, i in (("pitch", 1), ("roll", 2)):
            delta = abs(end[i] - start[i]) * DEG
            checks.append((f"{name} came back with the headset", delta < 3.0,
                           f"{delta:.1f} deg from start"))
    else:
        print(f"  [skip]  pose-return checks: the headset ended {returned:.0f} deg")
        print("          from where it started, so there is nothing to compare")

    checks.append(("yaw drift while stationary", abs(drift) < 2.0, f"{drift:+.2f} deg/s"))
    checks.append(("no dt clamping", filt.clamped_samples == 0,
                   "none" if filt.clamped_samples == 0 else f"{filt.clamped_samples} samples"))

    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {label:<38} {detail}")
    print("-" * 62)
    return 0 if all(ok for _, ok, _ in checks) else 1


def cmd_live(pid: int, kp: float, seconds: float = 0.0) -> int:
    from dk1.device import Tracker

    try:
        import msvcrt  # Windows only; the keys are a convenience, not a feature
    except ImportError:
        msvcrt = None

    print("Hold the headset still for one second while the gyro bias is measured.\n")
    with Tracker(product_id=pid) as tracker:
        stream = sample_stream(tracker.reports(timeout_ms=200))
        try:
            cal = calibrate(stream, seconds=1.0)
        except NotStationary as exc:
            print(f"Calibration failed: {exc}")
            return 1

        print(f"gyro bias {_norm(cal.gyro_bias) * DEG:.2f} deg/s, "
              f"resting |a| {cal.gravity:.2f} m/s^2")
        print("r = recentre, q = quit\n" if msvcrt else "Ctrl+C to stop\n")

        filt = OrientationFilter(kp=kp, calibration=cal)
        last_draw = 0.0
        deadline = time.time() + seconds if seconds > 0 else None
        try:
            for sample in stream:
                filt.update(sample)
                now = time.time()
                if deadline and now >= deadline:
                    break
                if now - last_draw < 0.05:  # 20 Hz is plenty for eyeballing
                    continue
                last_draw = now

                if msvcrt and msvcrt.kbhit():
                    key = msvcrt.getch().lower()
                    if key == b"q":
                        break
                    if key == b"r":
                        filt.recentre()

                yaw, pitch, roll = (a * DEG for a in filt.euler)
                # Looking straight up or down, yaw and roll describe the same
                # rotation and both swing wildly while the pose is perfectly
                # stable. Say so, or it reads as a bug.
                note = "  <- near gimbal, yaw/roll degenerate" if abs(pitch) > 80.0 else ""
                sys.stdout.write(
                    f"\ryaw {yaw:+7.1f}  pitch {pitch:+7.1f}  roll {roll:+7.1f}   "
                    f"accel rejected {100.0 * filt.rejected_accel / max(1, filt.updates):4.1f}%"
                    f"{note}   "
                )
                sys.stdout.flush()
        except KeyboardInterrupt:
            pass
    print("\n\nStopped.")
    return 0


def _norm(v) -> float:
    return math.sqrt(sum(c * c for c in v))


def _unit(v):
    n = _norm(v) or 1.0
    return tuple(c / n for c in v)


def _mean_accel(samples):
    n = len(samples) or 1
    return tuple(sum(s.accel[k] for s in samples) / n for k in range(3))


def _mean_gyro(samples, bias):
    n = len(samples) or 1
    return tuple(sum(s.gyro[k] - bias[k] for s in samples) / n for k in range(3))


def _angle_between(a, b) -> float:
    a, b = _unit(a), _unit(b)
    return math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)))))


def main() -> int:
    ap = argparse.ArgumentParser(description="DK1 orientation filter")
    ap.add_argument("--replay", metavar="FILE", help="run a recorded capture through the filter")
    ap.add_argument("--live", action="store_true", help="live yaw/pitch/roll from the headset")
    ap.add_argument("--kp", type=float, default=0.5, help="filter gain (default 0.5)")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop --live after this long (default: run until 'q')")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x0001, help="USB product ID")
    args = ap.parse_args()

    try:
        if args.replay:
            return cmd_replay(args.replay, args.kp)
        if args.live:
            return cmd_live(args.pid, args.kp, args.seconds)
        ap.print_help()
        return 0
    except ImportError as exc:
        print(f"{exc}")
        return 1
    except FileNotFoundError as exc:
        print(f"\nError: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        print(f"\nError: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
