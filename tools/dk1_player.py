#!/usr/bin/env python3
"""360 degree video player, aimed by the DK1's tracker.

    python tools/dk1_player.py                          # test pattern, mouse look
    python tools/dk1_player.py --live                   # aimed by the headset
    python tools/dk1_player.py --video clip.mp4 --live  # the actual thing
    python tools/dk1_player.py --video clip.mp4 --replay capture.bin

Runs in a normal desktop window. That is deliberate for Phase 3: every part of
this -- decode, upload, the equirectangular projection, the tracker feeding the
camera -- is easier to judge on a monitor than through a headset showing a
pre-warped image. Phase 4 adds fullscreen output, the barrel distortion and the
stereo pair on top, and none of it changes what is here.

Without --live or --replay the view is mouse-driven, so the renderer can be
worked on with no headset attached at all.

Keys:
    r          recentre -- call the current heading straight ahead
    s          toggle stereo (two eye viewports)
    d          toggle barrel distortion (only matters in stereo)
    f          toggle fullscreen on the current monitor
    space      pause and resume the video
    q / esc    quit
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dk1.orientation import NotStationary, OrientationFilter, calibrate  # noqa: E402
from dk1.renderer import PanoramaRenderer  # noqa: E402
from dk1.video import VideoSource, synthetic_panorama  # noqa: E402

DEG = 180.0 / math.pi
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def sample_stream(reports, skip_first: bool = True):
    """Flatten reports into samples, dropping the backlogged first one.

    Same reason as in dk1_orient.py: the first report after opening carries the
    firmware's accumulated backlog and its leading sample would jolt the filter.
    """
    for i, report in enumerate(reports):
        if i == 0 and skip_first:
            continue
        for sample in report.samples:
            yield sample


class HeadTracking:
    """Runs the filter on its own thread so the render loop never blocks on USB.

    The render loop reads :attr:`matrix` once per frame. That is safe without a
    lock because the filter's state is a tuple it replaces wholesale, so a reader
    sees either the old orientation or the new one, never half of each.
    """

    CALIBRATION_ATTEMPTS = 60  # roughly a minute of "please hold still"

    def __init__(self, source: str, path: str = "", pid: int = 0x0001, kp: float = 0.5) -> None:
        self.source = source  # "live", "replay" or "mouse"
        self.path = path
        self.pid = pid
        self.kp = kp

        self.filter = OrientationFilter(kp=kp)
        self.status = "starting"
        self.error = ""
        self.ready = threading.Event()

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Mouse look state, used only when there is no tracker.
        self.yaw = 0.0
        self.pitch = 0.0

    @property
    def matrix(self):
        if self.source == "mouse":
            return _yaw_pitch_matrix(self.yaw, self.pitch)
        if not self.ready.is_set():
            return IDENTITY
        return self.filter.matrix

    def recentre(self) -> None:
        if self.source == "mouse":
            self.yaw = 0.0
            self.pitch = 0.0
        else:
            self.filter.recentre()

    def start(self) -> "HeadTracking":
        if self.source == "mouse":
            self.status = "mouse look"
            self.ready.set()
            return self
        self._thread = threading.Thread(target=self._run, name="dk1-tracking", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            if self.source == "live":
                from dk1.device import Tracker

                with Tracker(product_id=self.pid) as tracker:
                    self._consume(sample_stream(tracker.reports(timeout_ms=200)))
            else:
                from dk1.device import replay_raw

                while not self._stop.is_set():
                    self._consume(sample_stream(replay_raw(self.path, realtime=True)))
                    if not self.ready.is_set():
                        return  # calibration failed; do not spin on the file
        except Exception as exc:  # noqa: BLE001 - surfaced in the status line
            self.error = str(exc)
            self.status = "failed"

    def _consume(self, stream) -> None:
        if not self.ready.is_set():
            # Keep trying. Calibration needs a still second, and a player that
            # gives up for good because the headset happened to be in someone's
            # hand as it started is useless -- the next still second will do.
            # Bounded, because an exhausted replay file also fails every attempt,
            # instantly, and must not spin.
            cal = None
            for attempt in range(1, self.CALIBRATION_ATTEMPTS + 1):
                if self._stop.is_set():
                    return
                self.status = ("calibrating - set the headset down and let go"
                               if attempt == 1 else
                               f"still moving - waiting for a still second ({attempt})")
                try:
                    cal = calibrate(stream, seconds=1.0)
                    break
                except NotStationary as exc:
                    self.error = str(exc)
            if cal is None:
                self.status = "calibration failed"
                return

            self.error = ""
            self.filter = OrientationFilter(kp=self.kp, calibration=cal)
            self.status = f"tracking (bias {_norm(cal.gyro_bias) * DEG:.2f} deg/s)"
            self.ready.set()

        for sample in stream:
            if self._stop.is_set():
                return
            self.filter.update(sample)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def _yaw_pitch_matrix(yaw: float, pitch: float):
    """Body-to-world for mouse look: Ry(yaw) then Rx(pitch), row-major."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    return (cy, sy * sp, sy * cp,
            0.0, cp, -sp,
            -sy, cy * sp, cy * cp)


def _norm(v) -> float:
    return math.sqrt(sum(c * c for c in v))


class Player:
    def __init__(self, args) -> None:
        self.args = args
        self.paused = False
        self.fullscreen = bool(args.fullscreen)
        self.stereo = bool(args.stereo)
        self.distort = not bool(args.no_distortion)
        self._windowed_rect = None

        self.frames_drawn = 0
        self._fps = 0.0

    def run(self) -> int:
        import glfw
        import moderngl

        if not glfw.init():
            print("Could not initialise GLFW.")
            return 1
        try:
            return self._run(glfw, moderngl)
        finally:
            glfw.terminate()

    def _open_window(self, glfw):
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)

        monitors = glfw.get_monitors()
        index = self.args.monitor
        if index >= len(monitors):
            print(f"Monitor {index} does not exist; {len(monitors)} present. Using 0.")
            index = 0
        monitor = monitors[index]

        if self.fullscreen:
            mode = glfw.get_video_mode(monitor)
            window = glfw.create_window(mode.size.width, mode.size.height,
                                        "DK1 360 player", monitor, None)
        else:
            window = glfw.create_window(self.args.width, self.args.height,
                                        "DK1 360 player", None, None)
        if not window:
            raise RuntimeError("Could not create a window with an OpenGL 3.3 core context.")

        glfw.make_context_current(window)
        glfw.swap_interval(1 if self.args.vsync else 0)
        return window, monitors, index

    def _run(self, glfw, moderngl) -> int:
        window, monitors, monitor_index = self._open_window(glfw)
        ctx = moderngl.create_context()
        print(f"GL {ctx.info['GL_VERSION']} on {ctx.info['GL_RENDERER']}")

        renderer = PanoramaRenderer(ctx, fov_deg=self.args.fov)

        video = None
        still = None
        if self.args.video:
            video = VideoSource(self.args.video, loop=not self.args.no_loop).open()
            print(f"video {video.width}x{video.height} @ {video.fps:.2f} fps, "
                  f"{video.frame_count} frames")
            if video.width != 2 * video.height:
                print(f"  note: {video.width}x{video.height} is not 2:1, so this may not be "
                      f"equirectangular; it will be stretched to the full sphere")
        else:
            still = synthetic_panorama()
            renderer.upload(still)
            print("no --video given, showing the built-in test pattern")
            print("  green ahead, red right, blue behind, yellow left, white above")

        source = "live" if self.args.live else ("replay" if self.args.replay else "mouse")
        tracking = HeadTracking(source, self.args.replay or "", self.args.pid, self.args.kp).start()

        self._install_input(glfw, window, tracking, monitors, monitor_index)
        print("\nr recentre   s stereo   d distortion   f fullscreen   space pause   q quit\n")
        if self.stereo:
            print("stereo on: left half = left eye, right half = right eye"
                  + ("  (barrel warp on)" if self.distort else "  (no warp)"))

        last_status = 0.0
        frame_times = []
        deadline = time.time() + self.args.seconds if self.args.seconds > 0 else None
        try:
            while not glfw.window_should_close(window):
                if deadline and time.time() >= deadline:
                    break
                glfw.poll_events()

                if video is not None and not self.paused:
                    frame = video.frames.get()
                    if frame is not None:
                        renderer.upload(frame)

                width, height = glfw.get_framebuffer_size(window)
                if width == 0 or height == 0:  # minimised
                    time.sleep(0.05)
                    continue

                ctx.viewport = (0, 0, width, height)
                ctx.clear(0.0, 0.0, 0.0)
                if self.stereo:
                    renderer.render_stereo(tracking.matrix, width, height,
                                           distort=self.distort)
                else:
                    renderer.render(tracking.matrix, aspect=width / height)
                glfw.swap_buffers(window)

                self.frames_drawn += 1
                frame_times.append(time.perf_counter())
                if len(frame_times) > 60:
                    frame_times.pop(0)

                now = time.time()
                if now - last_status > 0.5:
                    last_status = now
                    if len(frame_times) > 1:
                        span = frame_times[-1] - frame_times[0]
                        self._fps = (len(frame_times) - 1) / span if span > 0 else 0.0
                    self._print_status(tracking, video)
        except KeyboardInterrupt:
            pass
        finally:
            tracking.close()
            if video is not None:
                video.close()
            renderer.release()

        print(f"\n\nDrawn {self.frames_drawn} frames.")
        if tracking.error:
            print(f"Tracking error: {tracking.error}")
        return 0

    def _install_input(self, glfw, window, tracking, monitors, monitor_index) -> None:
        state = {"dragging": False, "last": (0.0, 0.0)}

        def on_key(win, key, scancode, action, mods):
            if action != glfw.PRESS:
                return
            if key in (glfw.KEY_Q, glfw.KEY_ESCAPE):
                glfw.set_window_should_close(win, True)
            elif key == glfw.KEY_R:
                tracking.recentre()
            elif key == glfw.KEY_SPACE:
                self.paused = not self.paused
            elif key == glfw.KEY_S:
                self.stereo = not self.stereo
            elif key == glfw.KEY_D:
                self.distort = not self.distort
            elif key == glfw.KEY_F:
                self._toggle_fullscreen(glfw, win, monitors[monitor_index])

        def on_mouse_button(win, button, action, mods):
            if button == glfw.MOUSE_BUTTON_LEFT:
                state["dragging"] = action == glfw.PRESS
                state["last"] = glfw.get_cursor_pos(win)

        def on_cursor(win, x, y):
            if not state["dragging"] or tracking.source != "mouse":
                return
            px, py = state["last"]
            state["last"] = (x, y)
            # Drag right to look right, so yaw decreases: +yaw is a left turn.
            tracking.yaw -= (x - px) * 0.005
            tracking.pitch = max(-math.pi / 2, min(math.pi / 2,
                                                   tracking.pitch - (y - py) * 0.005))

        glfw.set_key_callback(window, on_key)
        glfw.set_mouse_button_callback(window, on_mouse_button)
        glfw.set_cursor_pos_callback(window, on_cursor)

    def _toggle_fullscreen(self, glfw, window, monitor) -> None:
        if self.fullscreen:
            x, y, w, h = self._windowed_rect or (100, 100, self.args.width, self.args.height)
            glfw.set_window_monitor(window, None, x, y, w, h, 0)
            self.fullscreen = False
        else:
            x, y = glfw.get_window_pos(window)
            w, h = glfw.get_window_size(window)
            self._windowed_rect = (x, y, w, h)
            mode = glfw.get_video_mode(monitor)
            glfw.set_window_monitor(window, monitor, 0, 0,
                                    mode.size.width, mode.size.height, mode.refresh_rate)
            self.fullscreen = True
        glfw.swap_interval(1 if self.args.vsync else 0)

    def _print_status(self, tracking, video) -> None:
        yaw, pitch, roll = (a * DEG for a in tracking.filter.euler) \
            if tracking.source != "mouse" else (tracking.yaw * DEG, tracking.pitch * DEG, 0.0)

        parts = [f"{self._fps:5.1f} fps",
                 f"yaw {yaw:+6.1f} pitch {pitch:+6.1f} roll {roll:+6.1f}"]
        if video is not None:
            parts.append(f"decoded {video.frames_decoded} dropped {video.frames.dropped}")
        if not tracking.ready.is_set() or tracking.error:
            parts.append(tracking.status)
        if self.stereo:
            parts.append("STEREO" + ("+warp" if self.distort else ""))
        if self.paused:
            parts.append("PAUSED")

        sys.stdout.write("\r" + "   ".join(parts) + "    ")
        sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="DK1 360 degree video player")
    ap.add_argument("--video", metavar="FILE",
                    help="equirectangular video file (default: built-in test pattern)")
    ap.add_argument("--live", action="store_true", help="aim the view with the headset")
    ap.add_argument("--replay", metavar="FILE",
                    help="aim the view with a recorded capture, for testing without hardware")
    ap.add_argument("--fov", type=float, default=90.0, help="vertical field of view (default 90)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--stereo", action="store_true",
                    help="split the window into left/right DK1 eye viewports")
    ap.add_argument("--no-distortion", action="store_true",
                    help="in --stereo, skip the barrel pre-warp (for judging the split on a monitor)")
    ap.add_argument("--fullscreen", action="store_true", help="start fullscreen")
    ap.add_argument("--monitor", type=int, default=0,
                    help="monitor index for fullscreen (0 = primary)")
    ap.add_argument("--no-loop", action="store_true", help="stop at the end instead of looping")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="quit after this long, for unattended checks (default: run until 'q')")
    ap.add_argument("--no-vsync", dest="vsync", action="store_false", default=True)
    ap.add_argument("--kp", type=float, default=0.5, help="filter gain (default 0.5)")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x0001, help="USB product ID")
    args = ap.parse_args()

    if args.live and args.replay:
        print("Pick one of --live or --replay, not both.")
        return 1

    try:
        return Player(args).run()
    except ImportError as exc:
        print(f"{exc}\n\nPhase 3 needs: pip install moderngl glfw opencv-python")
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        print(f"\nError: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
