"""Orientation estimation from the DK1's IMU samples.

A Mahony complementary filter: integrate the gyroscope for responsiveness, and
pull the result back toward gravity so pitch and roll cannot drift. Yaw has no
absolute reference and will drift regardless -- see :meth:`OrientationFilter.recentre`.

Pure math, no I/O and no hidapi, so it can be driven from a recorded capture via
:func:`dk1.device.replay_raw` on any machine. Plain floats rather than numpy:
this runs once per 1 kHz sample, where per-call array overhead would dominate the
dozen arithmetic operations it actually needs.

Frames
------
The sensor frame is **X right, Y up, Z backward, right-handed**, measured on
hardware -- see ``docs/PROTOCOL.md``. That is already the renderer's frame
(Y up, -Z forward), so nothing here remaps axes. Quaternions are
``(w, x, y, z)`` and rotate body vectors into world.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Tuple

from .protocol import Sample

Vec3 = Tuple[float, float, float]
Quat = Tuple[float, float, float, float]

# Standard gravity. Only a fallback: this unit reports ~10.58 m/s^2 at rest
# because we read raw samples rather than the firmware's factory calibration,
# so prefer a magnitude measured by calibrate().
STANDARD_GRAVITY = 9.80665

# World up. The whole point of the accelerometer term is to keep the estimate's
# idea of this direction aligned with the measured one.
WORLD_UP: Vec3 = (0.0, 1.0, 0.0)


class NotStationary(RuntimeError):
    """Raised when calibration data has too much motion in it to be useful."""


@dataclass(frozen=True)
class Calibration:
    """What a stationary measurement tells us about this particular unit."""

    gyro_bias: Vec3  # rad/s, subtract from every gyro reading
    gravity: float  # m/s^2 the accelerometer reports at rest
    samples: int


def calibrate(samples: Iterable[Sample], seconds: float = 1.0) -> Calibration:
    """Measure gyro bias and resting accelerometer magnitude. Keep it still.

    The bias is worth real effort: measured at ~0.04 rad/s on this unit, which
    is 150 degrees of yaw drift per minute if left in. It also moves with
    temperature, by ~16% between runs minutes apart, so it must be measured at
    startup rather than hard-coded.
    """
    n = 0
    gyro_sum = [0.0, 0.0, 0.0]
    accel_sum = 0.0
    gyro_min = [math.inf] * 3
    gyro_max = [-math.inf] * 3
    elapsed = 0.0

    for sample in samples:
        for k in range(3):
            gyro_sum[k] += sample.gyro[k]
            gyro_min[k] = min(gyro_min[k], sample.gyro[k])
            gyro_max[k] = max(gyro_max[k], sample.gyro[k])
        accel_sum += _norm(sample.accel)
        elapsed += sample.dt
        n += 1
        if elapsed >= seconds:
            break

    if n == 0:
        raise NotStationary("no samples to calibrate from")

    # Rest noise is ~0.01 rad/s rms on this unit, so a spread past 0.2 rad/s
    # means the headset was moving and the "bias" would be a motion average.
    spread = max(gyro_max[k] - gyro_min[k] for k in range(3))
    if spread > 0.2:
        raise NotStationary(
            f"gyro varied by {spread:.3f} rad/s during calibration -- "
            "the headset has to be still and untouched"
        )

    return Calibration(
        gyro_bias=(gyro_sum[0] / n, gyro_sum[1] / n, gyro_sum[2] / n),
        gravity=accel_sum / n,
        samples=n,
    )


class OrientationFilter:
    """Fuses gyro and accelerometer into an orientation quaternion.

    ``kp`` is the only tuning constant: it sets how hard gravity pulls the
    estimate back. Higher tracks truth faster but lets linear acceleration tilt
    the horizon; lower drifts more.
    """

    # A report read straight after opening the device carries the firmware's
    # accumulated backlog -- sample_count of 87 has been seen against a
    # steady-state 1 -- so its first sample claims to cover tens of
    # milliseconds. Integrating that lurches the orientation, so clamp it and
    # count it rather than trusting it.
    MAX_DT = 0.05

    # Gravity is only worth trusting when the accelerometer is measuring
    # gravity. During a fast head movement it measures the movement, and
    # correcting toward it actively harms the estimate.
    ACCEL_TOLERANCE = 2.0  # m/s^2 away from the resting magnitude

    def __init__(self, kp: float = 0.5, calibration: Calibration | None = None) -> None:
        self.kp = kp
        self.gyro_bias: Vec3 = calibration.gyro_bias if calibration else (0.0, 0.0, 0.0)
        self.gravity: float = calibration.gravity if calibration else STANDARD_GRAVITY

        self._q: Quat = (1.0, 0.0, 0.0, 0.0)
        self._yaw_offset = 0.0
        self._seeded = False

        # Diagnostics worth surfacing rather than hiding.
        self.updates = 0
        self.clamped_samples = 0
        self.rejected_accel = 0

    # -- state -------------------------------------------------------------

    @property
    def quaternion(self) -> Quat:
        """Orientation as ``(w, x, y, z)``, body to world, including recentring."""
        if self._yaw_offset == 0.0:
            return self._q
        half = -self._yaw_offset / 2.0
        return _mul((math.cos(half), 0.0, math.sin(half), 0.0), self._q)

    @property
    def euler(self) -> Vec3:
        """``(yaw, pitch, roll)`` in radians.

        Signs follow the right-hand rule in the sensor frame, so: yaw positive
        turning left, pitch positive nodding up, roll positive tilting the
        *left* ear down. Negate at the call site if you want another convention.
        """
        w, x, y, z = self.quaternion
        yaw = math.atan2(2.0 * (x * z + w * y), 1.0 - 2.0 * (x * x + y * y))
        pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * x - y * z))))
        roll = math.atan2(2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z))
        return yaw, pitch, roll

    @property
    def estimated_up(self) -> Vec3:
        """Where the filter believes world up is, in body coordinates.

        Comparing this against the measured accelerometer direction is the one
        check that must hold whenever the headset is at rest, whatever pose it
        is in, so it is the useful thing to assert against a real capture.
        """
        w, x, y, z = self._q
        return (2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x))

    @property
    def matrix(self) -> Tuple[float, ...]:
        """Rotation as 9 floats, row-major -- what the Phase 3 shader wants."""
        w, x, y, z = self.quaternion
        return (
            1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y),
            2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x),
            2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y),
        )

    def recentre(self) -> None:
        """Call the current heading zero. The practical answer to yaw drift."""
        w, x, y, z = self._q
        self._yaw_offset = math.atan2(2.0 * (x * z + w * y), 1.0 - 2.0 * (x * x + y * y))

    # -- the filter --------------------------------------------------------

    def update(self, sample: Sample) -> None:
        """Advance the estimate by one 1 kHz sample."""
        dt = sample.dt
        if dt <= 0.0:
            return
        if dt > self.MAX_DT:
            dt = self.MAX_DT
            self.clamped_samples += 1

        wx = sample.gyro[0] - self.gyro_bias[0]
        wy = sample.gyro[1] - self.gyro_bias[1]
        wz = sample.gyro[2] - self.gyro_bias[2]

        accel_mag = _norm(sample.accel)
        if abs(accel_mag - self.gravity) <= self.ACCEL_TOLERANCE and accel_mag > 0.0:
            ax = sample.accel[0] / accel_mag
            ay = sample.accel[1] / accel_mag
            az = sample.accel[2] / accel_mag

            # Start from the pose the accelerometer implies rather than from
            # level. Converging from identity takes seconds at kp=0.5, during
            # which the reported pitch and roll are simply wrong -- and a
            # headset is rarely being held exactly level when playback starts.
            # Yaw is left at zero because nothing measures it.
            if not self._seeded:
                self._q = _shortest_arc((ax, ay, az), WORLD_UP)
                self._seeded = True

            # Where the estimate thinks up is, in body coordinates: the second
            # row of the rotation matrix, which is R^T applied to world up.
            w, x, y, z = self._q
            ux = 2.0 * (x * y + w * z)
            uy = 1.0 - 2.0 * (x * x + z * z)
            uz = 2.0 * (y * z - w * x)

            # Cross product of measured against estimated up: zero when they
            # agree, and otherwise the rotation that would close the gap.
            wx += self.kp * (ay * uz - az * uy)
            wy += self.kp * (az * ux - ax * uz)
            wz += self.kp * (ax * uy - ay * ux)
        else:
            self.rejected_accel += 1

        # q += 0.5 * q (x) (0, omega) * dt
        w, x, y, z = self._q
        half = 0.5 * dt
        self._q = _normalise(
            (
                w + half * (-x * wx - y * wy - z * wz),
                x + half * (w * wx + y * wz - z * wy),
                y + half * (w * wy - x * wz + z * wx),
                z + half * (w * wz + x * wy - y * wx),
            )
        )
        self.updates += 1

    def update_all(self, samples: Iterable[Sample]) -> None:
        for sample in samples:
            self.update(sample)


# -- small vector/quaternion helpers ---------------------------------------


def _norm(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _normalise(q: Quat) -> Quat:
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n == 0.0:
        return (1.0, 0.0, 0.0, 0.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def _shortest_arc(a: Vec3, b: Vec3) -> Quat:
    """Unit quaternion rotating unit vector ``a`` onto unit vector ``b``.

    The shortest of the infinitely many rotations that do it, which is what we
    want when seeding: it adds no yaw beyond what is unavoidable.
    """
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    if dot < -0.999999:
        # Antiparallel: the rotation is 180 degrees about any perpendicular axis.
        axis = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
        cross = (
            a[1] * axis[2] - a[2] * axis[1],
            a[2] * axis[0] - a[0] * axis[2],
            a[0] * axis[1] - a[1] * axis[0],
        )
        return _normalise((0.0, cross[0], cross[1], cross[2]))
    return _normalise(
        (
            1.0 + dot,
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )
    )


def _mul(a: Quat, b: Quat) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )
