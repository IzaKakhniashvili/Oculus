"""USB HID transport for the DK1 tracker.

Wraps whichever hidapi binding is installed, runs the read loop on its own
thread, and keeps the sensor awake. Decoding lives in :mod:`dk1.protocol`.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Iterator, List, Optional

from . import protocol as p

# hidapi is imported softly: replaying a capture and re-decoding it offline
# must work on a machine that has never seen the headset, so only the code
# paths that actually touch USB are allowed to require the binding.
try:
    import hid  # type: ignore
except ImportError:  # pragma: no cover - environment dependent
    hid = None  # type: ignore[assignment]

_HIDAPI_MISSING = (
    "No hidapi binding found. Install it with:\n"
    "    pip install hidapi\n"
    "(the 'hidapi' package bundles the DLL on Windows and imports as 'hid')"
)


def _require_hid():
    if hid is None:
        raise ImportError(_HIDAPI_MISSING)
    return hid


# Two different PyPI packages both import as `hid` and have different APIs.
# Detect which one we got rather than guessing.
def _uses_device_class() -> bool:
    return hasattr(hid, "device")


class TrackerNotFound(RuntimeError):
    pass


def enumerate_devices() -> List[dict]:
    """All HID devices the system can see."""
    return list(_require_hid().enumerate())


def find_oculus_devices() -> List[dict]:
    """Just the Oculus ones."""
    return [d for d in enumerate_devices() if d.get("vendor_id") == p.VENDOR_ID]


class Tracker:
    """A connected DK1 tracker.

    Use as a context manager::

        with Tracker() as t:
            for report in t.reports():
                ...
    """

    def __init__(
        self,
        vendor_id: int = p.VENDOR_ID,
        product_id: int = p.PRODUCT_ID_DK1,
        keep_alive_interval_ms: int = 10_000,
        on_raw: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.keep_alive_interval_ms = keep_alive_interval_ms
        self.on_raw = on_raw

        self._dev = None
        self._keep_alive_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._command_id = 0

        # Diagnostics worth surfacing rather than hiding.
        self.reports_read = 0
        self.decode_errors = 0
        self.dropped_samples = 0

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> "Tracker":
        hid_mod = _require_hid()
        if _uses_device_class():
            dev = hid_mod.device()
            try:
                dev.open(self.vendor_id, self.product_id)
            except (OSError, IOError) as exc:
                raise TrackerNotFound(self._not_found_message()) from exc
        else:  # trezor-style binding
            try:
                dev = hid_mod.Device(vid=self.vendor_id, pid=self.product_id)
            except Exception as exc:  # noqa: BLE001 - binding raises its own type
                raise TrackerNotFound(self._not_found_message()) from exc

        self._dev = dev
        self._configure()
        self._start_keep_alive()
        return self

    def _not_found_message(self) -> str:
        # Runs inside an exception handler -- must never raise, or it would
        # mask the actual open() failure.
        try:
            seen = find_oculus_devices()
        except Exception:  # noqa: BLE001
            seen = []
        if seen:
            listed = ", ".join(f"PID 0x{d['product_id']:04x}" for d in seen)
            return (
                f"Could not open VID 0x{self.vendor_id:04x} PID 0x{self.product_id:04x}, "
                f"but an Oculus device is present ({listed}). "
                "Pass the matching --pid, or close any other software holding the device."
            )
        return (
            f"No device with VID 0x{self.vendor_id:04x} found. "
            "Check the USB cable and that the control box has DC power -- "
            "the DK1 tracker does not enumerate on USB power alone."
        )

    def _configure(self) -> None:
        """Put the sensor in streaming mode and prime the keep-alive."""
        self._send_feature(p.build_sensor_config(command_id=self._next_command_id()))
        self._send_feature(
            p.build_keep_alive(
                command_id=self._next_command_id(),
                interval_ms=self.keep_alive_interval_ms,
            )
        )

    def _next_command_id(self) -> int:
        self._command_id = (self._command_id + 1) & 0xFFFF
        return self._command_id

    def _send_feature(self, payload: bytes) -> None:
        assert self._dev is not None
        self._dev.send_feature_report(payload)

    def _start_keep_alive(self) -> None:
        # Resend at half the advertised interval so a single dropped packet
        # never lets the stream time out.
        period = max(0.5, self.keep_alive_interval_ms / 2000.0)

        def loop() -> None:
            while not self._stop.wait(period):
                try:
                    self._send_feature(
                        p.build_keep_alive(
                            command_id=self._next_command_id(),
                            interval_ms=self.keep_alive_interval_ms,
                        )
                    )
                except Exception:  # noqa: BLE001 - device may vanish mid-loop
                    return

        self._keep_alive_thread = threading.Thread(target=loop, name="dk1-keepalive", daemon=True)
        self._keep_alive_thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._keep_alive_thread is not None:
            self._keep_alive_thread.join(timeout=1.0)
            self._keep_alive_thread = None
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001
                pass
            self._dev = None

    def __enter__(self) -> "Tracker":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- reading -----------------------------------------------------------

    def read_raw(self, timeout_ms: int = 100) -> Optional[bytes]:
        """One raw report, or None on timeout."""
        if self._dev is None:
            raise RuntimeError("Tracker is not open")
        data = self._dev.read(p.REPORT_LENGTH, timeout_ms)
        if not data:
            return None
        raw = bytes(data)
        if self.on_raw is not None:
            self.on_raw(raw)
        return raw

    def reports(self, timeout_ms: int = 100) -> Iterator[p.TrackerReport]:
        """Decoded reports, indefinitely.

        Malformed reports are counted and skipped rather than raising -- a
        single bad USB frame should not kill a render loop.
        """
        while not self._stop.is_set():
            raw = self.read_raw(timeout_ms)
            if raw is None:
                continue
            self.reports_read += 1
            try:
                report = p.parse_tracker_report(raw)
            except ValueError:
                self.decode_errors += 1
                continue
            self.dropped_samples += report.dropped_samples
            yield report

    def samples(self, timeout_ms: int = 100) -> Iterator[p.Sample]:
        """Flattened 1 kHz sample stream -- what the orientation filter wants."""
        for report in self.reports(timeout_ms):
            for sample in report.samples:
                yield sample


def replay_raw(path: str, realtime: bool = False) -> Iterator[p.TrackerReport]:
    """Re-decode a capture produced by ``dk1_probe.py --record``.

    Lets a capture taken on the headset machine be analyzed anywhere.
    """
    with open(path, "rb") as fh:
        while True:
            raw = fh.read(p.REPORT_LENGTH)
            if len(raw) < p.REPORT_LENGTH:
                return
            try:
                report = p.parse_tracker_report(raw)
            except ValueError:
                continue
            if realtime:
                time.sleep(sum(s.dt for s in report.samples))
            yield report
