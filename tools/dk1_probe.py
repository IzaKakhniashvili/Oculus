#!/usr/bin/env python3
"""DK1 tracker diagnostic.

Phase-1 tool: confirm the headset enumerates, that reports arrive, and that the
21-bit decode is actually correct on real hardware.

    python tools/dk1_probe.py --list              # what HID devices exist
    python tools/dk1_probe.py --sanity            # IS THE DECODE RIGHT? start here
    python tools/dk1_probe.py --live              # scrolling sensor readout
    python tools/dk1_probe.py --raw 5             # hex dump of 5 reports
    python tools/dk1_probe.py --record cap.bin --seconds 10

The sanity check is the important one: it leans on the fact that a stationary
accelerometer must read 1 g. If the scaling or bit-packing were wrong, that
magnitude would come out as nonsense rather than ~9.81 m/s^2.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1 import protocol as p  # noqa: E402

GRAVITY = 9.80665


def cmd_list() -> int:
    from dk1.device import enumerate_devices

    devices = enumerate_devices()
    if not devices:
        print("No HID devices visible at all. On Windows this usually means a")
        print("permissions problem or a missing hidapi DLL.")
        return 1

    oculus = [d for d in devices if d.get("vendor_id") == p.VENDOR_ID]
    print(f"{len(devices)} HID device(s) present; {len(oculus)} from Oculus (VID 0x2833)\n")

    if oculus:
        for d in oculus:
            print(f"  VID 0x{d['vendor_id']:04x}  PID 0x{d['product_id']:04x}"
                  f"  {d.get('manufacturer_string') or '?'} / {d.get('product_string') or '?'}")
            print(f"    path: {d.get('path')}")
        print("\nLooks good -- next run:  python tools/dk1_probe.py --sanity")
        return 0

    print("  No Oculus device found.")
    print("\nChecklist:")
    print("  1. Is the DC power adapter plugged into the control box?")
    print("     The DK1 tracker does NOT enumerate on USB bus power alone.")
    print("  2. Is the USB cable in a port that works (try a different one)?")
    print("  3. Is the blue LED on the control box lit?")
    print("\nOther devices seen, in case the VID differs on your unit:")
    for d in devices[:15]:
        print(f"  VID 0x{d['vendor_id']:04x} PID 0x{d['product_id']:04x}"
              f"  {d.get('product_string') or '?'}")
    return 1


def cmd_raw(count: int, pid: int) -> int:
    from dk1.device import Tracker

    with Tracker(product_id=pid) as t:
        print(f"Dumping {count} raw report(s). Expect 62 bytes starting with 0x01.\n")
        seen = 0
        deadline = time.time() + 10
        while seen < count and time.time() < deadline:
            raw = t.read_raw(timeout_ms=500)
            if raw is None:
                continue
            seen += 1
            print(f"--- report {seen} ({len(raw)} bytes) ---")
            for off in range(0, len(raw), 16):
                chunk = raw[off : off + 16]
                print(f"  {off:04x}  {chunk.hex(' ')}")
            r = p.parse_tracker_report(raw)
            print(f"  decoded: samples={r.sample_count} ts={r.timestamp} "
                  f"temp={r.temperature:.2f}C mag={tuple(round(v, 4) for v in r.mag)}")
            for i, s in enumerate(r.samples):
                print(f"    [{i}] accel={tuple(round(v, 3) for v in s.accel)} "
                      f"gyro={tuple(round(v, 4) for v in s.gyro)}")
            print()
        if seen == 0:
            print("No reports arrived within 10s -- the device opened but is silent.")
            return 1
    return 0


def cmd_sanity(pid: int, seconds: float) -> int:
    """Validate the decode against known physics. Keep the headset STILL."""
    from dk1.device import Tracker

    print("=" * 62)
    print("DK1 DECODE SANITY CHECK")
    print("=" * 62)
    print(f"Put the headset on a flat surface and DO NOT MOVE IT for {seconds:g}s.\n")
    for n in (3, 2, 1):
        print(f"  starting in {n}...", end="\r", flush=True)
        time.sleep(1)
    print("  sampling...        ")

    accel_mags, gyro_mags, temps = [], [], []
    mag_mags = []
    n_samples = 0
    start = time.time()

    with Tracker(product_id=pid) as t:
        for report in t.reports(timeout_ms=200):
            temps.append(report.temperature)
            mag_mags.append(math.sqrt(sum(v * v for v in report.mag)))
            for s in report.samples:
                accel_mags.append(math.sqrt(sum(v * v for v in s.accel)))
                gyro_mags.append(math.sqrt(sum(v * v for v in s.gyro)))
                n_samples += 1
            if time.time() - start >= seconds:
                break
        elapsed = time.time() - start
        reports_read, decode_errors, dropped = t.reports_read, t.decode_errors, t.dropped_samples

    if n_samples == 0:
        print("\nFAIL: no samples received.")
        return 1

    mean_accel = sum(accel_mags) / len(accel_mags)
    mean_gyro = sum(gyro_mags) / len(gyro_mags)
    mean_temp = sum(temps) / len(temps)
    mean_mag = sum(mag_mags) / len(mag_mags)
    sample_rate = n_samples / elapsed
    report_rate = reports_read / elapsed

    print(f"\n  elapsed          {elapsed:.2f} s")
    print(f"  reports          {reports_read}  ({report_rate:.0f}/s)")
    print(f"  samples          {n_samples}  ({sample_rate:.0f}/s)")
    print(f"  decode errors    {decode_errors}")
    print(f"  dropped samples  {dropped}")
    print(f"  temperature      {mean_temp:.2f} C")
    print(f"  |accel|          {mean_accel:.4f} m/s^2   (expect ~{GRAVITY:.2f})")
    print(f"  |gyro|           {mean_gyro:.5f} rad/s    (expect ~0 at rest)")
    print(f"  |mag|            {mean_mag:.4f} gauss    (expect ~0.2-0.6)")

    print("\n" + "-" * 62)
    checks = []

    # The decisive one. A stationary accelerometer reads exactly 1 g; if the
    # bit unpacking or the 1e-4 scale were wrong this would be far off.
    ok_accel = abs(mean_accel - GRAVITY) < 1.5
    checks.append(("accelerometer magnitude ~= 1 g", ok_accel,
                   "DECODE CONFIRMED" if ok_accel else
                   f"got {mean_accel:.3f}, expected ~{GRAVITY:.2f} -- decode or scale is wrong"))

    ok_gyro = mean_gyro < 0.15
    checks.append(("gyroscope near zero at rest", ok_gyro,
                   "ok" if ok_gyro else f"got {mean_gyro:.3f} rad/s -- was it moving, or is the decode off?"))

    ok_rate = sample_rate > 200
    checks.append(("sample rate healthy", ok_rate,
                   f"{sample_rate:.0f}/s" if ok_rate else f"only {sample_rate:.0f}/s -- USB or keep-alive trouble"))

    ok_errors = decode_errors == 0
    checks.append(("no decode errors", ok_errors, "ok" if ok_errors else f"{decode_errors} malformed reports"))

    ok_temp = 10 < mean_temp < 60
    checks.append(("temperature plausible", ok_temp,
                   f"{mean_temp:.1f} C" if ok_temp else f"{mean_temp:.1f} C is out of range -- check offset 6"))

    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {label:<32} {detail}")

    passed = all(ok for _, ok, _ in checks)
    print("-" * 62)
    if passed:
        print("\nAll checks passed. The protocol layer is verified against hardware.")
        print("Next: python tools/dk1_probe.py --live   (then we build the orientation filter)")
    else:
        print("\nSomething is off. Capture a sample for offline analysis:")
        print("  python tools/dk1_probe.py --record capture.bin --seconds 10")
    return 0 if passed else 1


def cmd_live(pid: int) -> int:
    from dk1.device import Tracker

    print("Live sensor readout -- move the headset and watch the numbers. Ctrl+C to stop.\n")
    last_draw = 0.0
    try:
        with Tracker(product_id=pid) as t:
            for report in t.reports(timeout_ms=200):
                now = time.time()
                if now - last_draw < 0.05:  # 20 Hz is plenty for eyeballing
                    continue
                last_draw = now
                s = report.samples[-1]
                ax, ay, az = s.accel
                gx, gy, gz = s.gyro
                mx, my, mz = report.mag
                sys.stdout.write(
                    f"\raccel {ax:+7.2f} {ay:+7.2f} {az:+7.2f}  |  "
                    f"gyro {gx:+7.3f} {gy:+7.3f} {gz:+7.3f}  |  "
                    f"mag {mx:+6.3f} {my:+6.3f} {mz:+6.3f}  |  "
                    f"{report.temperature:5.1f}C  drop {t.dropped_samples}"
                )
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n\nStopped.")
    return 0


def cmd_record(path: str, seconds: float, pid: int) -> int:
    from dk1.device import Tracker

    print(f"Recording raw reports to {path} for {seconds:g}s.")
    print("Move the headset through yaw, pitch and roll so the capture is useful.\n")
    count = 0
    start = time.time()
    with open(path, "wb") as fh:
        def write_raw(raw: bytes) -> None:
            nonlocal count
            fh.write(raw)
            count += 1

        with Tracker(product_id=pid, on_raw=write_raw) as t:
            for _ in t.reports(timeout_ms=200):
                elapsed = time.time() - start
                if int(elapsed * 2) % 2 == 0:
                    sys.stdout.write(f"\r  {elapsed:5.1f}s  {count} reports")
                    sys.stdout.flush()
                if elapsed >= seconds:
                    break

    size = os.path.getsize(path)
    print(f"\n\nWrote {count} reports, {size} bytes to {path}")
    print("This file can be replayed or analyzed on any machine.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Oculus DK1 tracker diagnostic")
    ap.add_argument("--list", action="store_true", help="enumerate HID devices")
    ap.add_argument("--sanity", action="store_true", help="validate the decode against physics")
    ap.add_argument("--live", action="store_true", help="scrolling sensor readout")
    ap.add_argument("--raw", type=int, metavar="N", help="hex dump N reports")
    ap.add_argument("--record", metavar="FILE", help="save raw reports for offline analysis")
    ap.add_argument("--seconds", type=float, default=5.0, help="duration for --sanity / --record")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=p.PRODUCT_ID_DK1,
                    help="USB product ID (default 0x0001 = DK1)")
    args = ap.parse_args()

    try:
        if args.list:
            return cmd_list()
        if args.raw is not None:
            return cmd_raw(args.raw, args.pid)
        if args.sanity:
            return cmd_sanity(args.pid, args.seconds)
        if args.record:
            return cmd_record(args.record, args.seconds, args.pid)
        if args.live:
            return cmd_live(args.pid)
        ap.print_help()
        return 0
    except ImportError as exc:
        print(f"{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        print(f"\nError: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
