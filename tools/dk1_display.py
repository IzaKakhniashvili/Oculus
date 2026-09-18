#!/usr/bin/env python3
"""Display-side probe: is the DK1's panel visible to Windows at all?

Phase 4 needs a fullscreen window on the headset's 1280x800 @ 60 Hz display, and
the DK1's EDID is unusual enough that the first question is not "which monitor
index" but "does Windows see a panel on that cable".

    python tools/dk1_display.py --list     # every video connector, and what is on it
    python tools/dk1_display.py --modes     # resolutions each attached display offers

--list is the useful one when nothing shows up. It asks the display driver for
*all* paths rather than only the active ones, so a connector with a monitor
attached but switched off still appears, and a connector with nothing on it is
reported as empty rather than silently omitted. That distinguishes "the DK1 is
not plugged in / not powered / the cable is dead" from "Windows sees it but will
not offer the mode", which need completely different fixes.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes

if not sys.platform.startswith("win"):
    print("This probe is Windows-only; display enumeration is platform-specific.")
    sys.exit(1)

user32 = ctypes.WinDLL("user32", use_last_error=True)

QDC_ALL_PATHS = 1
QDC_ONLY_ACTIVE_PATHS = 2
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
DISPLAYCONFIG_PATH_ACTIVE = 0x1
ERROR_SUCCESS = 0

# DISPLAYCONFIG_VIDEO_OUTPUT_TECHNOLOGY
OUTPUT_TECHNOLOGY = {
    0: "other",
    1: "VGA",
    2: "S-Video",
    3: "composite",
    4: "component",
    5: "DVI",
    6: "HDMI",
    7: "LVDS",
    8: "D Jpn",
    9: "SDI",
    10: "DisplayPort",
    11: "DisplayPort (embedded)",
    12: "UDI",
    13: "UDI (embedded)",
    14: "SDTV dongle",
    15: "Miracast",
    16: "indirect wired",
    17: "indirect virtual",
    0x80000000: "internal panel",
}

# The DK1 reports itself with this EDID vendor code.
DK1_EDID_VENDOR = "OVR"
DK1_WIDTH, DK1_HEIGHT, DK1_REFRESH = 1280, 800, 60


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.DWORD), ("Denominator", wintypes.DWORD)]


class PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", wintypes.DWORD),
        ("modeInfoIdx", wintypes.DWORD),
        ("statusFlags", wintypes.DWORD),
    ]


class PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", wintypes.DWORD),
        ("modeInfoIdx", wintypes.DWORD),
        ("outputTechnology", wintypes.DWORD),
        ("rotation", wintypes.DWORD),
        ("scaling", wintypes.DWORD),
        ("refreshRate", RATIONAL),
        ("scanLineOrdering", wintypes.DWORD),
        ("targetAvailable", wintypes.BOOL),
        ("statusFlags", wintypes.DWORD),
    ]


class PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", PATH_SOURCE_INFO),
        ("targetInfo", PATH_TARGET_INFO),
        ("flags", wintypes.DWORD),
    ]


class MODE_INFO(ctypes.Structure):
    # Opaque: the mode union is only needed for the active signal, and
    # EnumDisplaySettings reports resolutions more readably.
    _fields_ = [("blob", ctypes.c_byte * 64)]


class DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("size", wintypes.DWORD),
        ("adapterId", LUID),
        ("id", wintypes.DWORD),
    ]


class TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DEVICE_INFO_HEADER),
        ("flags", wintypes.DWORD),
        ("outputTechnology", wintypes.DWORD),
        ("edidManufactureId", wintypes.WORD),
        ("edidProductCodeId", wintypes.WORD),
        ("connectorInstance", wintypes.DWORD),
        ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
        ("monitorDevicePath", wintypes.WCHAR * 128),
    ]


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


def decode_edid_vendor(packed: int) -> str:
    """Three-letter EDID manufacturer code from its packed 16-bit form.

    Five bits per letter, and the word arrives byte-swapped relative to how the
    letters read. Sanity-checkable: the panel in this laptop must come out "SDC".
    """
    swapped = ((packed & 0xFF) << 8) | (packed >> 8)
    letters = [
        ((swapped >> 10) & 0x1F),
        ((swapped >> 5) & 0x1F),
        (swapped & 0x1F),
    ]
    if any(v < 1 or v > 26 for v in letters):
        return "?"
    return "".join(chr(ord("A") + v - 1) for v in letters)


def query_paths(flags: int = QDC_ALL_PATHS):
    """Every display path the driver knows about, active or not."""
    n_path = wintypes.UINT()
    n_mode = wintypes.UINT()
    err = user32.GetDisplayConfigBufferSizes(flags, ctypes.byref(n_path), ctypes.byref(n_mode))
    if err != ERROR_SUCCESS:
        raise OSError(f"GetDisplayConfigBufferSizes failed: {err}")

    paths = (PATH_INFO * n_path.value)()
    modes = (MODE_INFO * n_mode.value)()
    err = user32.QueryDisplayConfig(
        flags,
        ctypes.byref(n_path),
        ctypes.byref(paths),
        ctypes.byref(n_mode),
        ctypes.byref(modes),
        None,
    )
    if err != ERROR_SUCCESS:
        raise OSError(f"QueryDisplayConfig failed: {err}")
    return paths[: n_path.value]


def target_name(path) -> TARGET_DEVICE_NAME:
    info = TARGET_DEVICE_NAME()
    info.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
    info.header.size = ctypes.sizeof(TARGET_DEVICE_NAME)
    info.header.adapterId = path.targetInfo.adapterId
    info.header.id = path.targetInfo.id
    user32.DisplayConfigGetDeviceInfo(ctypes.byref(info.header))
    return info


def cmd_list() -> int:
    paths = query_paths(QDC_ALL_PATHS)

    # One video output can appear on several paths, once per desktop source it
    # could be driven from. The target id is what actually identifies the
    # connector, so collapse on that.
    outputs = {}
    for path in paths:
        target = path.targetInfo.id
        entry = outputs.setdefault(target, {"active": False})
        entry["tech"] = OUTPUT_TECHNOLOGY.get(path.targetInfo.outputTechnology,
                                              f"0x{path.targetInfo.outputTechnology:08x}")
        entry["available"] = bool(path.targetInfo.targetAvailable)
        entry["active"] |= bool(path.flags & DISPLAYCONFIG_PATH_ACTIVE)
        info = target_name(path)
        entry["vendor"] = decode_edid_vendor(info.edidManufactureId)
        entry["product"] = info.edidProductCodeId
        entry["name"] = info.monitorFriendlyDeviceName or "(no EDID name)"
        if path.targetInfo.refreshRate.Denominator:
            entry["refresh"] = (path.targetInfo.refreshRate.Numerator
                                / path.targetInfo.refreshRate.Denominator)

    print(f"{len(paths)} path(s) over {len(outputs)} video output(s)\n")

    dk1 = None
    for target, e in sorted(outputs.items()):
        flag = "ACTIVE" if e["active"] else "      "
        print(f"  [{flag}] target {target}  {e['tech']:<22} "
              f"display attached: {'yes' if e['available'] else 'NO'}")
        if e["available"]:
            print(f"                 EDID vendor {e['vendor']}  product 0x{e['product']:04x}"
                  f"   {e['name']}")
            if e.get("refresh"):
                print(f"                 driving {e['refresh']:.2f} Hz")
        if e["vendor"] == DK1_EDID_VENDOR:
            dk1 = e["name"]

    external = [e for e in outputs.values() if e["tech"] != "internal panel"]
    occupied = [e for e in external if e["available"]]

    print()
    if dk1:
        print(f"Found EDID vendor {DK1_EDID_VENDOR} -- that is the DK1 ({dk1}).")
        print("Next: python tools/dk1_display.py --modes   (look for 1280x800 @ 60 Hz)")
        return 0

    print("No DK1 display found (nothing reports EDID vendor 'OVR').")
    print()

    if not external:
        print("This machine reports no external video output at all, only the internal")
        print("panel. Nothing can be plugged in until that changes -- check whether the")
        print("GPU drivers are installed properly.")
        return 1

    if not occupied:
        print(f"{len(external)} external output(s) exist, but Windows reports no display")
        print("attached to any of them. It is not seeing a sink on the cable, so this is")
        print("not an EDID or mode problem yet -- the link is not coming up at all.")
        print()
        print("Worth knowing: a laptop's physical HDMI port is often reported as")
        print("'DisplayPort' here, because internally it is a DP lane with a converter.")
        print("So the label does not tell you which socket to use.")
        print()
        print("Checklist, in the order most likely to be the cause:")
        print("  1. Is the DK1 control box's DC adapter plugged in? The panel needs it.")
        print("     A working tracker only proves the box has power, not that the")
        print("     panel is being driven.")
        print("  2. Is the cable in the control box's HDMI *input*, and is the headset's")
        print("     own captive cable seated in the box?")
        print("  3. If you are going through a USB-C adapter or dock, it must support")
        print("     DP alt mode. Many USB-C ports carry no video at all.")
        print("  4. Power the box first, then plug the video cable, then press Win+P")
        print("     to force a redetect.")
        return 1

    print("A display is attached to an external output, but it is not the DK1:")
    for e in occupied:
        print(f"  {e['tech']}: EDID vendor {e['vendor']}  {e['name']}")
    print("If that is the headset, its EDID is being misread -- that is the DK1")
    print("EDID problem proper, and the mode list is the next place to look.")
    return 1


def cmd_modes() -> int:
    """Resolutions each attached display offers, and whether 1280x800@60 is there."""
    adapter = 0
    found_any = False
    rc = 1
    while True:
        dev = DISPLAY_DEVICE()
        dev.cb = ctypes.sizeof(DISPLAY_DEVICE)
        if not user32.EnumDisplayDevicesW(None, adapter, ctypes.byref(dev), 0):
            break
        adapter += 1
        if not dev.StateFlags & 0x1:  # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
            print(f"{dev.DeviceName}  {dev.DeviceString}  -- not attached to the desktop")
            continue

        found_any = True
        print(f"\n{dev.DeviceName}  {dev.DeviceString}")

        modes = set()
        i = 0
        while True:
            dm = DEVMODE()
            dm.dmSize = ctypes.sizeof(DEVMODE)
            if not user32.EnumDisplaySettingsExW(dev.DeviceName, i, ctypes.byref(dm), 0):
                break
            modes.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
            i += 1

        wanted = (DK1_WIDTH, DK1_HEIGHT, DK1_REFRESH)
        for mode in sorted(modes, reverse=True):
            mark = "  <-- DK1 native" if mode == wanted else ""
            print(f"    {mode[0]:>5} x {mode[1]:<5} @ {mode[2]:>3} Hz{mark}")
        if wanted in modes:
            print(f"    -> offers {DK1_WIDTH}x{DK1_HEIGHT} @ {DK1_REFRESH} Hz")
            rc = 0

    if not found_any:
        print("No display is attached to the desktop.")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="DK1 display probe")
    ap.add_argument("--list", action="store_true", help="every video connector and what is on it")
    ap.add_argument("--modes", action="store_true", help="modes each attached display offers")
    args = ap.parse_args()

    try:
        if args.list:
            return cmd_list()
        if args.modes:
            return cmd_modes()
        ap.print_help()
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        print(f"\nError: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
