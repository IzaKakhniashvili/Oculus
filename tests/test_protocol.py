"""Protocol decode tests. These run anywhere -- no headset, no hidapi."""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1 import protocol as p  # noqa: E402


def check(label, got, want):
    status = "ok  " if got == want else "FAIL"
    print(f"  [{status}] {label}: got {got!r}, want {want!r}")
    return got == want


def approx(label, got, want, tol=1e-9):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: got {got!r}, want {want!r}")
    return ok


failures = 0


def run(name, fn):
    global failures
    print(f"\n{name}")
    if not fn():
        failures += 1


def test_bit_width():
    """Every 21-bit value must survive a pack/unpack round trip."""
    ok = True
    # Boundaries of the 21-bit signed range, plus values that straddle every
    # byte split in the packing.
    interesting = [0, 1, -1, 7, -7, 255, 256, -256, 1023, 4095, 65535, -65536,
                   0xFFFFF, -0xFFFFF, 0xFFFFF, -0x100000, 0x0FFFFF]
    for x in interesting:
        for y in interesting:
            for z in interesting:
                got = p.unpack_3x21(p.pack_3x21(x, y, z))
                if got != (x, y, z):
                    print(f"  [FAIL] round trip {(x, y, z)} -> {got}")
                    ok = False
    print(f"  [{'ok  ' if ok else 'FAIL'}] {len(interesting) ** 3} round trips")
    ok &= check("min 21-bit", p.unpack_3x21(p.pack_3x21(-0x100000, 0, 0))[0], -0x100000)
    ok &= check("max 21-bit", p.unpack_3x21(p.pack_3x21(0x0FFFFF, 0, 0))[0], 0x0FFFFF)
    return ok


def test_field_independence():
    """A value in one slot must not bleed into the other two."""
    ok = True
    ok &= check("x only", p.unpack_3x21(p.pack_3x21(0x0FFFFF, 0, 0)), (0x0FFFFF, 0, 0))
    ok &= check("y only", p.unpack_3x21(p.pack_3x21(0, 0x0FFFFF, 0)), (0, 0x0FFFFF, 0))
    ok &= check("z only", p.unpack_3x21(p.pack_3x21(0, 0, 0x0FFFFF)), (0, 0, 0x0FFFFF))
    ok &= check("all negative ones", p.unpack_3x21(p.pack_3x21(-1, -1, -1)), (-1, -1, -1))
    return ok


def build_report(sample_count=3, timestamp=1234, command_id=7, temp_raw=2500,
                 mag=(100, -200, 300), samples=None):
    """Synthesize a 62-byte report the way the firmware would."""
    if samples is None:
        samples = [((0, 0, -9810), (0, 0, 0))] * 3
    buf = bytearray(p.REPORT_LENGTH)
    buf[0] = p.REPORT_ID_TRACKER
    buf[1] = sample_count
    struct.pack_into("<HH", buf, 2, timestamp, command_id)
    struct.pack_into("<h", buf, 6, temp_raw)
    for i, (accel, gyro) in enumerate(samples[:3]):
        base = 8 + i * 16
        buf[base : base + 8] = p.pack_3x21(*accel)
        buf[base + 8 : base + 16] = p.pack_3x21(*gyro)
    struct.pack_into("<hhh", buf, 56, *mag)
    return bytes(buf)


def test_report_layout():
    ok = True
    raw = build_report(
        sample_count=2,
        timestamp=40000,
        command_id=9,
        temp_raw=3012,
        mag=(1000, -2000, 3000),
        samples=[((100, 200, -98100), (10, -20, 30)), ((101, 201, -98101), (11, -21, 31))],
    )
    ok &= check("report length", len(raw), 62)
    r = p.parse_tracker_report(raw)
    ok &= check("sample_count", r.sample_count, 2)
    ok &= check("timestamp", r.timestamp, 40000)
    ok &= check("last_command_id", r.last_command_id, 9)
    ok &= approx("temperature", (r.temperature,), (30.12,), tol=1e-6)
    ok &= approx("mag", r.mag, (0.1, -0.2, 0.3), tol=1e-9)
    ok &= check("samples decoded", len(r.samples), 2)
    # 1e-4 scaling: -98100 counts -> -9.81 m/s^2
    ok &= approx("sample0 accel", r.samples[0].accel, (0.01, 0.02, -9.81), tol=1e-9)
    ok &= approx("sample0 gyro", r.samples[0].gyro, (0.001, -0.002, 0.003), tol=1e-9)
    ok &= approx("sample1 accel", r.samples[1].accel, (0.0101, 0.0201, -9.8101), tol=1e-9)
    return ok


def test_dropped_sample_timing():
    """When the firmware drops samples, elapsed time must still be accounted."""
    ok = True
    r = p.parse_tracker_report(build_report(sample_count=3))
    ok &= check("no drop -> 3 samples", len(r.samples), 3)
    ok &= check("no drop -> dropped==0", r.dropped_samples, 0)
    ok &= approx("no drop -> dt", (r.samples[0].dt,), (0.001,), tol=1e-12)
    ok &= approx("no drop -> total dt", (sum(s.dt for s in r.samples),), (0.003,), tol=1e-12)

    # 10 samples elapsed, only 3 survive: first must absorb the missing time.
    r = p.parse_tracker_report(build_report(sample_count=10))
    ok &= check("drop -> still 3 samples", len(r.samples), 3)
    ok &= check("drop -> dropped count", r.dropped_samples, 7)
    ok &= approx("drop -> first dt absorbs gap", (r.samples[0].dt,), (0.008,), tol=1e-12)
    ok &= approx("drop -> later dt normal", (r.samples[1].dt,), (0.001,), tol=1e-12)
    ok &= approx("drop -> total dt", (sum(s.dt for s in r.samples),), (0.010,), tol=1e-12)
    return ok


def test_rejects_bad_input():
    ok = True
    for bad, label in [(b"\x01" * 61, "short report"), (b"\x01" * 63, "long report")]:
        try:
            p.parse_tracker_report(bad)
            print(f"  [FAIL] {label}: should have raised")
            ok = False
        except ValueError:
            print(f"  [ok  ] {label}: rejected")
    try:
        p.parse_tracker_report(bytes([9]) + b"\x00" * 61)
        print("  [FAIL] wrong report ID: should have raised")
        ok = False
    except ValueError:
        print("  [ok  ] wrong report ID: rejected")
    return ok


def test_feature_reports():
    ok = True
    ka = p.build_keep_alive(command_id=0x1234, interval_ms=10000)
    ok &= check("keep-alive length", len(ka), 5)
    ok &= check("keep-alive bytes", ka.hex(), "0834121027")
    cfg = p.build_sensor_config(command_id=1, flags=0x30, packet_interval=0, sample_rate=1000)
    ok &= check("sensor-config length", len(cfg), 7)
    ok &= check("sensor-config bytes", cfg.hex(), "020100300 0e803".replace(" ", ""))
    return ok


run("21-bit pack/unpack round trip", test_bit_width)
run("field independence", test_field_independence)
run("full report layout + scaling", test_report_layout)
run("dropped-sample timing", test_dropped_sample_timing)
run("malformed input rejection", test_rejects_bad_input)
run("feature report encoding", test_feature_reports)

print("\n" + ("ALL PASS" if failures == 0 else f"{failures} TEST GROUP(S) FAILED"))
sys.exit(1 if failures else 0)
