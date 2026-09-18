"""Wire format for the Oculus Rift DK1 tracker (VID 0x2833, PID 0x0001).

Pure functions only -- no I/O, no hidapi. Everything here is testable without
the headset attached, which is the point: the 21-bit packing below is the one
place where a silent bug would corrupt every downstream number.

Layout of the 62-byte input report (report ID 1):

    offset  size  field
    ------  ----  ---------------------------------------------
    0       1     report ID (always 1)
    1       1     sample count (may exceed 3 if samples dropped)
    2       2     timestamp, uint16 LE, wraps
    4       2     last command ID, uint16 LE
    6       2     temperature, int16 LE, 0.01 degC
    8       48    3 x TrackerSample, 16 bytes each
    56      6     magnetometer X/Y/Z, int16 LE each

Each 16-byte TrackerSample is two 8-byte blocks -- accelerometer then
gyroscope -- and each block holds three 21-bit signed integers (63 of the 64
bits are used, the low bit of the last byte is padding).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Tuple

VENDOR_ID = 0x2833
PRODUCT_ID_DK1 = 0x0001
PRODUCT_ID_DK2 = 0x0021

REPORT_ID_TRACKER = 1
REPORT_LENGTH = 62

# Feature reports used to configure / keep the sensor streaming.
FEATURE_SENSOR_CONFIG = 2
FEATURE_KEEP_ALIVE = 8

# Raw counts -> physical units. The DK1 firmware reports everything in units
# of 1e-4, except temperature which is 1e-2.
ACCEL_SCALE = 1e-4  # -> m/s^2
GYRO_SCALE = 1e-4  # -> rad/s
MAG_SCALE = 1e-4  # -> gauss
TEMP_SCALE = 1e-2  # -> degrees C

# The IMU samples at 1 kHz; each report carries 1-3 of those samples.
SAMPLE_PERIOD = 1e-3  # seconds

MAX_SAMPLES_PER_REPORT = 3

# SensorConfig flag bits, from the original LibOVR sensor implementation.
FLAG_RAW_MODE = 0x01
FLAG_CALIBRATION_TEST = 0x02
FLAG_USE_CALIBRATION = 0x04
FLAG_AUTO_CALIBRATION = 0x08
FLAG_MOTION_KEEP_ALIVE = 0x10
FLAG_COMMAND_KEEP_ALIVE = 0x20
FLAG_SENSOR_COORDINATES = 0x40


def _sign_extend_21(value: int) -> int:
    """Interpret a 21-bit two's-complement field as a signed int."""
    return value - 0x200000 if value & 0x100000 else value


def unpack_3x21(block: bytes) -> Tuple[int, int, int]:
    """Decode three 21-bit signed integers packed into 8 bytes.

    Bit budget, most significant first:
        x = byte0[8] byte1[8] byte2[7:3]        -> 8 + 8 + 5 = 21
        y = byte2[2:0] byte3[8] byte4[8] byte5[7:6] -> 3 + 8 + 8 + 2 = 21
        z = byte5[5:0] byte6[8] byte7[7:1]      -> 6 + 8 + 7 = 21
    """
    if len(block) != 8:
        raise ValueError(f"expected an 8-byte block, got {len(block)}")

    x = (block[0] << 13) | (block[1] << 5) | ((block[2] & 0xF8) >> 3)
    y = ((block[2] & 0x07) << 18) | (block[3] << 10) | (block[4] << 2) | ((block[5] & 0xC0) >> 6)
    z = ((block[5] & 0x3F) << 15) | (block[6] << 7) | (block[7] >> 1)

    return _sign_extend_21(x), _sign_extend_21(y), _sign_extend_21(z)


def pack_3x21(x: int, y: int, z: int) -> bytes:
    """Inverse of :func:`unpack_3x21`. Only used by tests and the replay tool."""
    xu, yu, zu = x & 0x1FFFFF, y & 0x1FFFFF, z & 0x1FFFFF
    return bytes(
        (
            (xu >> 13) & 0xFF,
            (xu >> 5) & 0xFF,
            ((xu << 3) & 0xF8) | ((yu >> 18) & 0x07),
            (yu >> 10) & 0xFF,
            (yu >> 2) & 0xFF,
            ((yu << 6) & 0xC0) | ((zu >> 15) & 0x3F),
            (zu >> 7) & 0xFF,
            (zu << 1) & 0xFE,
        )
    )


@dataclass(frozen=True)
class Sample:
    """One 1 kHz IMU sample, in physical units."""

    accel: Tuple[float, float, float]  # m/s^2
    gyro: Tuple[float, float, float]  # rad/s
    dt: float  # seconds this sample covers


@dataclass(frozen=True)
class TrackerReport:
    """One decoded 62-byte USB report."""

    sample_count: int
    timestamp: int
    last_command_id: int
    temperature: float  # degrees C
    mag: Tuple[float, float, float]  # gauss
    samples: List[Sample]

    @property
    def dropped_samples(self) -> int:
        """How many 1 kHz samples the firmware discarded before we read it."""
        return max(0, self.sample_count - MAX_SAMPLES_PER_REPORT)


def parse_tracker_report(buf: bytes) -> TrackerReport:
    """Decode a raw 62-byte input report.

    ``buf`` must include the leading report-ID byte, which is what hidapi
    hands back for a device that uses report IDs.
    """
    if len(buf) != REPORT_LENGTH:
        raise ValueError(f"expected {REPORT_LENGTH} bytes, got {len(buf)}")
    if buf[0] != REPORT_ID_TRACKER:
        raise ValueError(f"expected report ID {REPORT_ID_TRACKER}, got {buf[0]}")

    sample_count = buf[1]
    timestamp, last_command_id = struct.unpack_from("<HH", buf, 2)
    (temperature_raw,) = struct.unpack_from("<h", buf, 6)
    mag_raw = struct.unpack_from("<hhh", buf, 56)

    # Only three samples ever fit in the report. If the firmware counted more,
    # the extras were dropped and the first surviving sample has to account for
    # the elapsed time -- otherwise gyro integration loses rotation silently.
    usable = min(sample_count, MAX_SAMPLES_PER_REPORT)
    first_dt = (sample_count - 2) * SAMPLE_PERIOD if sample_count > MAX_SAMPLES_PER_REPORT else SAMPLE_PERIOD

    samples: List[Sample] = []
    for i in range(usable):
        base = 8 + i * 16
        ax, ay, az = unpack_3x21(buf[base : base + 8])
        gx, gy, gz = unpack_3x21(buf[base + 8 : base + 16])
        samples.append(
            Sample(
                accel=(ax * ACCEL_SCALE, ay * ACCEL_SCALE, az * ACCEL_SCALE),
                gyro=(gx * GYRO_SCALE, gy * GYRO_SCALE, gz * GYRO_SCALE),
                dt=first_dt if i == 0 else SAMPLE_PERIOD,
            )
        )

    return TrackerReport(
        sample_count=sample_count,
        timestamp=timestamp,
        last_command_id=last_command_id,
        temperature=temperature_raw * TEMP_SCALE,
        mag=tuple(v * MAG_SCALE for v in mag_raw),  # type: ignore[arg-type]
        samples=samples,
    )


def build_keep_alive(command_id: int = 0, interval_ms: int = 10_000) -> bytes:
    """Feature report that stops the tracker from going quiet.

    The DK1 halts its stream if it hasn't heard from the host within
    ``interval_ms``. Resend well inside that window.
    """
    return struct.pack("<BHH", FEATURE_KEEP_ALIVE, command_id, interval_ms)


def build_sensor_config(
    command_id: int = 0,
    flags: int = FLAG_MOTION_KEEP_ALIVE | FLAG_COMMAND_KEEP_ALIVE,
    packet_interval: int = 0,
    sample_rate: int = 1000,
) -> bytes:
    """Feature report selecting stream mode and rate."""
    return struct.pack("<BHBBH", FEATURE_SENSOR_CONFIG, command_id, flags, packet_interval, sample_rate)
