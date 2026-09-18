"""Orientation filter tests. These run anywhere -- no headset, no hidapi.

Where possible these check physics rather than restating the implementation: a
constant gyro rate must integrate to the right angle, gravity must pull a wrong
estimate toward the measured vertical, and a rotation followed by its inverse
must come back to where it started.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1.orientation import (  # noqa: E402
    Calibration,
    NotStationary,
    OrientationFilter,
    calibrate,
)
from dk1.protocol import Sample  # noqa: E402

LEVEL_ACCEL = (0.0, 9.80665, 0.0)  # Y up, as measured on hardware
NO_ACCEL = (0.0, 0.0, 0.0)  # magnitude 0 is rejected -> pure gyro integration


def check(label, got, want):
    status = "ok  " if got == want else "FAIL"
    print(f"  [{status}] {label}: got {got!r}, want {want!r}")
    return got == want


def approx(label, got, want, tol=1e-6):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: got {tuple(round(g, 6) for g in got)!r}, "
          f"want {tuple(round(w, 6) for w in want)!r}")
    return ok


failures = 0


def run(name, fn):
    global failures
    print(f"\n{name}")
    if not fn():
        failures += 1


def spin(filt, gyro, seconds, accel=NO_ACCEL, dt=0.001):
    for _ in range(int(round(seconds / dt))):
        filt.update(Sample(accel=accel, gyro=gyro, dt=dt))


def estimated_up(filt):
    """Where the filter thinks world up is, in body coordinates.

    Computed here from the quaternion rather than read off the filter's own
    ``estimated_up``, so that a bug in that property cannot hide behind itself.
    """
    w, x, y, z = filt.quaternion
    return (2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x))


def _angle(a, b):
    """Angle between two unit-ish vectors, radians."""
    na = math.sqrt(sum(c * c for c in a)) or 1.0
    nb = math.sqrt(sum(c * c for c in b)) or 1.0
    dot = sum(x * y for x, y in zip(a, b)) / (na * nb)
    return math.acos(max(-1.0, min(1.0, dot)))


def test_gyro_integration():
    """A constant rate must integrate to rate x time, exactly."""
    ok = True
    filt = OrientationFilter()
    spin(filt, (0.0, 1.0, 0.0), 0.6)
    ok &= approx("1.0 rad/s about Y for 0.6s -> yaw", (filt.euler[0],), (0.6,), tol=1e-6)

    filt = OrientationFilter()
    spin(filt, (0.5, 0.0, 0.0), 0.4)
    ok &= approx("0.5 rad/s about X for 0.4s -> pitch", (filt.euler[1],), (0.2,), tol=1e-6)

    filt = OrientationFilter()
    spin(filt, (0.0, 0.0, -0.25), 1.2)
    ok &= approx("-0.25 rad/s about Z for 1.2s -> roll", (filt.euler[2],), (-0.3,), tol=1e-6)
    return ok


def test_rotation_and_back():
    """90 degrees out and 90 degrees back must return to the start pose."""
    ok = True
    filt = OrientationFilter()
    rate = math.pi / 2  # 90 deg in one second
    spin(filt, (0.0, rate, 0.0), 1.0)
    ok &= approx("after +90 deg yaw", (math.degrees(filt.euler[0]),), (90.0,), tol=1e-3)
    spin(filt, (0.0, -rate, 0.0), 1.0)
    ok &= approx("back to start", filt.quaternion, (1.0, 0.0, 0.0, 0.0), tol=1e-6)
    return ok


def test_euler_signs_match_measured_axes():
    """The signs must match the axis mapping measured on hardware.

    Sensor frame is X right, Y up, Z backward and right-handed, so turning left
    is +Y, nodding down is -X and tilting the right ear down is -Z.
    """
    ok = True
    filt = OrientationFilter()
    spin(filt, (0.0, 1.0, 0.0), 0.3)  # turn left
    ok &= check("turn left -> yaw positive", filt.euler[0] > 0.2, True)

    filt = OrientationFilter()
    spin(filt, (-1.0, 0.0, 0.0), 0.3)  # nod down
    ok &= check("nod down -> pitch negative", filt.euler[1] < -0.2, True)

    filt = OrientationFilter()
    spin(filt, (0.0, 0.0, -1.0), 0.3)  # right ear to shoulder
    ok &= check("roll right -> roll negative", filt.euler[2] < -0.2, True)
    return ok


def test_gravity_corrects_a_wrong_estimate():
    """The decisive check: gravity must pull the estimate toward the measured
    vertical, not away from it. A sign error here diverges to the opposite pole
    while still producing plausible-looking quaternions.

    Seeding makes the estimate right from the first sample, so to test the
    correction at all the estimate has to be knocked wrong first -- which is
    what accumulated gyro error does in real use.
    """
    ok = True
    filt = OrientationFilter(kp=0.5)
    filt.update(Sample(accel=LEVEL_ACCEL, gyro=(0.0, 0.0, 0.0), dt=0.001))

    # Half a second of unseen rotation: the accelerometer is ignored (magnitude
    # zero), so the estimate pitches away while the headset is really level.
    spin(filt, (1.0, 0.0, 0.0), 0.5, accel=NO_ACCEL)
    wrong_by = math.degrees(_angle(estimated_up(filt), (0.0, 1.0, 0.0)))
    ok &= check("estimate knocked off by gyro-only motion", wrong_by > 25.0, True)

    # Now let it see gravity again, stationary and level.
    spin(filt, (0.0, 0.0, 0.0), 20.0, accel=LEVEL_ACCEL)
    err = math.degrees(_angle(estimated_up(filt), (0.0, 1.0, 0.0)))
    print(f"  [info] up estimate was {wrong_by:.1f} deg off, ended {err:.3f} deg off")
    ok &= check("converged back to measured gravity", err < 0.5, True)
    ok &= approx("pitch and roll recovered", filt.euler[1:], (0.0, 0.0), tol=0.01)
    return ok


def test_seeds_from_the_first_accelerometer_reading():
    """Starting level and converging wastes seconds reporting a wrong pose, and
    a headset is rarely level when playback starts."""
    ok = True
    tilt = math.radians(30.0)
    accel = (0.0, 9.80665 * math.cos(tilt), 9.80665 * math.sin(tilt))

    filt = OrientationFilter()
    filt.update(Sample(accel=accel, gyro=(0.0, 0.0, 0.0), dt=0.001))
    want = tuple(c / 9.80665 for c in accel)
    ok &= approx("up correct after ONE sample", estimated_up(filt), want, tol=1e-6)
    ok &= approx("pitch correct after one sample", (filt.euler[1] * 180 / math.pi,),
                 (-30.0,), tol=1e-3)
    # The shortest arc onto world up introduces no yaw, and nothing measures it.
    ok &= approx("seeding invents no yaw", (filt.euler[0],), (0.0,), tol=1e-9)
    return ok


def test_gravity_cannot_fix_yaw():
    """Gravity anchors pitch and roll only. Yaw error must survive untouched --
    this is why a recentre key exists."""
    ok = True
    filt = OrientationFilter(kp=0.5)
    spin(filt, (0.0, 1.0, 0.0), 0.5, accel=LEVEL_ACCEL)  # yaw away from zero
    yawed = filt.euler[0]
    spin(filt, (0.0, 0.0, 0.0), 10.0, accel=LEVEL_ACCEL)  # sit still for 10 s
    ok &= approx("yaw unchanged by gravity", (filt.euler[0],), (yawed,), tol=1e-3)
    ok &= approx("pitch stays level", (filt.euler[1],), (0.0,), tol=1e-3)
    ok &= approx("roll stays level", (filt.euler[2],), (0.0,), tol=1e-3)
    return ok


def test_accel_rejected_during_hard_movement():
    """When the accelerometer is measuring movement rather than gravity, the
    correction has to be dropped."""
    ok = True
    filt = OrientationFilter()
    spin(filt, (0.0, 0.0, 0.0), 0.1, accel=LEVEL_ACCEL)
    ok &= check("level 1 g accepted", filt.rejected_accel, 0)

    filt = OrientationFilter()
    spin(filt, (0.0, 0.0, 0.0), 0.1, accel=(0.0, 25.0, 0.0))  # 2.5 g
    ok &= check("2.5 g rejected", filt.rejected_accel, 100)

    # An 8% high resting magnitude is what this unit actually reports, so it
    # must not be mistaken for movement.
    filt = OrientationFilter(calibration=Calibration((0.0, 0.0, 0.0), 10.58, 1000))
    spin(filt, (0.0, 0.0, 0.0), 0.1, accel=(0.0, 10.58, 0.0))
    ok &= check("calibrated resting magnitude accepted", filt.rejected_accel, 0)
    return ok


def test_backlogged_first_sample_is_clamped():
    """The first report after opening claims tens of milliseconds in one sample.
    Integrating it whole would lurch the view."""
    ok = True
    filt = OrientationFilter()
    filt.update(Sample(accel=NO_ACCEL, gyro=(0.0, 1.0, 0.0), dt=0.085))
    ok &= check("clamped", filt.clamped_samples, 1)
    # One step of the first-order update gives 2*atan(w*dt/2), not exactly w*dt.
    # That is 40 urad short here, and irrelevant next to the 35 ms of bogus
    # rotation the clamp just threw away.
    ok &= approx("rotation limited to MAX_DT", (filt.euler[0],), (0.05,), tol=1e-4)
    ok &= check("far below the unclamped 0.085", filt.euler[0] < 0.06, True)

    filt = OrientationFilter()
    filt.update(Sample(accel=NO_ACCEL, gyro=(0.0, 1.0, 0.0), dt=0.0))
    ok &= check("zero dt ignored", filt.updates, 0)
    return ok


def test_recentre():
    ok = True
    filt = OrientationFilter()
    spin(filt, (0.0, 1.0, 0.0), 1.0)  # 1 rad of yaw
    spin(filt, (0.3, 0.0, 0.0), 1.0)  # and 0.3 rad of pitch
    before = filt.euler
    filt.recentre()
    after = filt.euler
    ok &= approx("yaw zeroed", (after[0],), (0.0,), tol=1e-6)
    ok &= approx("pitch preserved", (after[1],), (before[1],), tol=1e-6)
    ok &= approx("roll preserved", (after[2],), (before[2],), tol=1e-6)

    # The offset is a fixed world-frame rotation, so later motion reads relative
    # to it. Check that from level: the gyro measures rotation about the *body*
    # axes, so turning about body Y while pitched is not a pure world yaw, and
    # comparing against the raw rate there would be testing the wrong thing.
    filt = OrientationFilter()
    spin(filt, (0.0, 1.0, 0.0), 1.0)
    filt.recentre()
    spin(filt, (0.0, 1.0, 0.0), 0.5)
    ok &= approx("yaw measured from the new zero", (filt.euler[0],), (0.5,), tol=1e-6)
    return ok


def test_gyro_bias_is_subtracted():
    """Bias is the main source of yaw drift; measured at ~0.04 rad/s on this unit."""
    ok = True
    bias = (-0.0404, 0.0230, 0.0094)
    filt = OrientationFilter(calibration=Calibration(bias, 9.80665, 1000))
    spin(filt, bias, 60.0)  # a full minute of nothing but bias
    drift = max(abs(a) for a in filt.euler)
    ok &= approx("60 s of pure bias -> no rotation", (drift,), (0.0,), tol=1e-9)

    # And for contrast, what leaving it in would have cost.
    filt = OrientationFilter()
    spin(filt, bias, 60.0)
    print(f"  [info] uncorrected, the same minute drifts "
          f"{math.degrees(max(abs(a) for a in filt.euler)):.0f} deg")
    ok &= check("uncorrected bias drifts badly", math.degrees(abs(filt.euler[0])) > 60, True)
    return ok


def test_calibrate():
    ok = True
    bias = (-0.0404, 0.0230, 0.0094)
    still = [Sample(accel=(0.0, 10.58, 0.0), gyro=bias, dt=0.001) for _ in range(2000)]
    cal = calibrate(still, seconds=1.0)
    ok &= check("stopped at 1 s of samples", cal.samples, 1000)
    ok &= approx("bias recovered", cal.gyro_bias, bias, tol=1e-9)
    ok &= approx("resting magnitude recovered", (cal.gravity,), (10.58,), tol=1e-9)

    moving = [
        Sample(accel=(0.0, 10.58, 0.0), gyro=(math.sin(i / 50.0), 0.0, 0.0), dt=0.001)
        for i in range(1000)
    ]
    try:
        calibrate(moving)
        print("  [FAIL] moving headset: should have raised")
        ok = False
    except NotStationary:
        print("  [ok  ] moving headset: rejected")

    try:
        calibrate([])
        print("  [FAIL] empty input: should have raised")
        ok = False
    except NotStationary:
        print("  [ok  ] empty input: rejected")
    return ok


def test_matrix_is_a_rotation():
    """The renderer gets a matrix, so it had better be orthonormal."""
    ok = True
    filt = OrientationFilter()
    spin(filt, (0.3, 0.7, -0.2), 2.0)
    m = filt.matrix
    rows = [m[0:3], m[3:6], m[6:9]]
    for i, row in enumerate(rows):
        ok &= approx(f"row {i} unit length", (math.sqrt(sum(c * c for c in row)),), (1.0,))
    for i, j in ((0, 1), (0, 2), (1, 2)):
        dot = sum(a * b for a, b in zip(rows[i], rows[j]))
        ok &= approx(f"rows {i},{j} orthogonal", (dot,), (0.0,))
    return ok


run("gyro integration", test_gyro_integration)
run("rotation out and back", test_rotation_and_back)
run("euler signs match the measured axis mapping", test_euler_signs_match_measured_axes)
run("gravity corrects a wrong estimate", test_gravity_corrects_a_wrong_estimate)
run("seeds from the first accelerometer reading", test_seeds_from_the_first_accelerometer_reading)
run("gravity cannot fix yaw", test_gravity_cannot_fix_yaw)
run("accelerometer rejection during movement", test_accel_rejected_during_hard_movement)
run("backlogged first sample is clamped", test_backlogged_first_sample_is_clamped)
run("recentre", test_recentre)
run("gyro bias subtraction", test_gyro_bias_is_subtracted)
run("calibration from stationary samples", test_calibrate)
run("matrix is a rotation", test_matrix_is_a_rotation)

print("\n" + ("ALL PASS" if failures == 0 else f"{failures} TEST GROUP(S) FAILED"))
sys.exit(1 if failures else 0)
