"""Native desktop window for the console (pywebview: WebView2 on Windows, WebKit on macOS).

`python -m src.wildfire.console --desktop` starts the FastAPI server on a
background thread and opens the UI in a real application window — no browser
chrome, no terminal. Launched via pythonw.exe on Windows or from the .app bundle
on macOS (see scripts/install_desktop_app.py) there is no console window at all:
it looks and behaves like installed software.

Closing the window shuts the server down. The window reopens where it was
left, and a running detection shows on the Dock / title bar and ends with a
system notification.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))  # so scripts/ is importable for the icon

APP_TITLE = "Wildfire Hazard Detection System"


def _wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _app_icon() -> str | None:
    """Path to the window icon, built from the branding logo if it is missing.

    Without this the title bar and the taskbar (Windows) or the Dock (macOS)
    show the Python interpreter's icon, because that is the process pywebview is
    hosted in - it looks like a script someone left running rather than an
    installed application. `ensure_icon` picks the right format per platform:
    .ico for Windows, .icns for macOS.
    """
    try:
        from scripts.install_desktop_app import ensure_icon

        icon = ensure_icon()
    except Exception:
        return None
    return str(icon) if icon is not None else None


def _claim_macos_app_identity() -> None:
    """Give the menu bar the app's name instead of "Python".

    macOS reads the running executable's bundle, and a framework build re-execs
    into its own Python.app - so the process is seen as plain Python no matter
    what our bundle's Info.plist says. Overwriting CFBundleName on the in-memory
    bundle before AppKit builds the menu is the standard fix. The SHORT name is
    used on purpose: the window server clips an owner name at 30 characters, and
    APP_TITLE is 32. Best-effort - a wrong menu title is not a reason to refuse
    to open the window.
    """
    try:
        from AppKit import NSBundle

        from scripts.install_desktop_app import APP_NAME

        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = APP_NAME
    except Exception:
        pass


# Preferred size, and the floor below which the layout stops working. Measured:
# every page holds up at 1024x640 (Settings drops to one column).
PREFERRED_SIZE = (1440, 920)
MINIMUM_SIZE = (1024, 640)


def _window_geometry() -> tuple[int, int, tuple[int, int]]:
    """Window size that fits the screen it opens on, and a minimum that does too.

    It used to be a fixed 1440x920 with a 1100x700 floor - larger than the
    1366x768 panel of a typical field laptop, so the window opened partly off
    screen and could not be shrunk to fit. Uses 92% x 90% of the primary
    screen, capped at the preferred size; falls back to the old fixed size if
    pywebview cannot report a screen.
    """
    pref_w, pref_h = PREFERRED_SIZE
    min_w, min_h = MINIMUM_SIZE
    try:
        import webview

        screen = webview.screens[0]
        sw, sh = int(screen.width), int(screen.height)
    except Exception:
        return pref_w, pref_h, (min_w, min_h)
    width = max(min(pref_w, int(sw * 0.92)), min(min_w, sw))
    height = max(min(pref_h, int(sh * 0.90)), min(min_h, sh))
    return width, height, (min(min_w, sw), min(min_h, sh))


# ------------------------------------------------------------- window state
# Where the operator left the window, restored on the next launch. Its own file
# rather than a setting: it changes every time the window moves, holds nothing
# worth editing, and must never travel with a copied settings.json to a
# machine with different screens. Gitignored.
WINDOW_STATE_FILE = PROJECT_ROOT / "config" / "window_state.json"


def _load_window_state(path: Path = WINDOW_STATE_FILE) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else None
    except Exception:
        return None


def _save_window_state(state: dict, path: Path = WINDOW_STATE_FILE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass  # a window that forgets its place is not worth an error


def _restored_geometry(state: dict | None, screens: list[tuple[int, int, int, int]],
                       min_size: tuple[int, int]) -> dict | None:
    """The saved window, if it still fits on a screen that is attached now.

    A laptop undocked from the monitor it was last used on would otherwise open
    its window on a screen that no longer exists. Accepted when the title-bar
    end of the window - its top 40 px, across at least 200 px - lies on some
    screen, so it can always be grabbed and moved; the size is clamped to that
    screen and to the minimum. `screens` is (x, y, width, height) per screen.
    """
    if not state:
        return None
    try:
        x, y = int(state["x"]), int(state["y"])
        w, h = int(state["width"]), int(state["height"])
    except (KeyError, TypeError, ValueError):
        return None
    for sx, sy, sw, sh in screens:
        visible_w = min(x + w, sx + sw) - max(x, sx)
        if visible_w >= min(200, w) and sy <= y <= sy + sh - 40:
            return {"x": x, "y": y,
                    "width": max(min_size[0], min(w, sw)),
                    "height": max(min_size[1], min(h, sh)),
                    "maximized": bool(state.get("maximized"))}
    return None


def _screens() -> list[tuple[int, int, int, int]]:
    try:
        import webview

        return [(int(getattr(sc, "x", 0)), int(getattr(sc, "y", 0)), int(sc.width), int(sc.height))
                for sc in webview.screens]
    except Exception:
        return []


def _track_window_state(window) -> None:
    """Keep the window's last normal (un-maximised) rectangle and save it on close."""
    state = dict(_load_window_state() or {})

    def moved(x, y):
        if not state.get("maximized"):
            state.update(x=int(x), y=int(y))

    def resized(width, height):
        if not state.get("maximized"):
            state.update(width=int(width), height=int(height))

    def maximized():
        state["maximized"] = True

    def restored():
        state["maximized"] = False

    def closing():
        # The events above are the source of truth; reading the live window too
        # covers a platform that does not fire them for the initial placement.
        if not state.get("maximized"):
            try:
                state.update(x=int(window.x), y=int(window.y),
                             width=int(window.width), height=int(window.height))
            except Exception:
                pass
        if {"x", "y", "width", "height"} <= state.keys():
            _save_window_state(state)

    window.events.moved += moved
    window.events.resized += resized
    window.events.maximized += maximized
    window.events.restored += restored
    window.events.closing += closing


# ------------------------------------------------------------- job progress
# A 100-image flight takes ~20 minutes, and the operator walks away. Progress
# goes where they will see it from another app: the Dock badge on macOS, the
# window title everywhere (it is what the Windows taskbar shows); and the end of
# a job raises a system notification.


def _progress_label(jobs) -> str | None:
    active = [j for j in jobs.active() if j.total]
    if not active:
        return None
    done = sum(j.done for j in active)
    total = sum(j.total for j in active)
    return f"{int(100 * done / total)}%"


def _set_dock_badge(label: str | None) -> None:
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApp
        from PyObjCTools import AppHelper

        AppHelper.callAfter(lambda: NSApp.dockTile().setBadgeLabel_(label or ""))
    except Exception:
        pass


def _request_attention(window) -> None:
    """Bounce the Dock icon / flash the taskbar button, once."""
    try:
        if sys.platform == "darwin":
            from AppKit import NSApp, NSInformationalRequest
            from PyObjCTools import AppHelper

            AppHelper.callAfter(lambda: NSApp.requestUserAttention_(NSInformationalRequest))
        elif sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                            ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                            ("dwTimeout", wintypes.DWORD)]

            hwnd = int(window.native.Handle.ToInt64())
            # FLASHW_ALL | FLASHW_TIMERNOFG: flash until the window comes forward
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 0, 0)
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


_WINDOWS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $x.GetElementsByTagName('text')
$t.Item(0).AppendChild($x.CreateTextNode($env:WF_NOTE_TITLE)) > $null
$t.Item(1).AppendChild($x.CreateTextNode($env:WF_NOTE_BODY)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def _notify(title: str, body: str) -> None:
    """A system notification, best-effort. The text goes in as arguments or
    environment, never pasted into a script, so a run name cannot break out."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["osascript", "-e", "on run argv", "-e",
                 "display notification (item 2 of argv) with title (item 1 of argv)",
                 "-e", "end run", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_TOAST],
                env={**os.environ, "WF_NOTE_TITLE": title, "WF_NOTE_BODY": body},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _finish_message(job, settings) -> tuple[str, str]:
    if job.state != "done":
        return "Detection failed", f"{job.id}: {job.error or 'the worker stopped'}"
    from .data import scan_summary

    summary = scan_summary(settings.output_path / job.id, settings) or {}
    counts = summary.get("detections_by_type") or {}
    found = ", ".join(f"{v} {k}" for k, v in counts.items()) or "no hazards found"
    images = summary.get("images") or job.total
    return ("Detection finished",
            f"{summary.get('title') or job.id} - {images} image{'s' if images != 1 else ''}, {found}")


def _watch_jobs(window, app) -> None:
    """Mirror job progress onto the Dock / title, and announce each job's end."""
    jobs = app.state.jobs

    def finished(job):
        title, body = _finish_message(job, app.state.settings)
        _notify(title, body)
        _request_attention(window)

    jobs.on_finish.append(finished)

    def loop():
        shown = None
        while True:
            label = _progress_label(jobs)
            if label != shown:
                shown = label
                _set_dock_badge(label)
                try:
                    window.set_title(f"{label} - Detecting - {APP_TITLE}" if label else APP_TITLE)
                except Exception:
                    pass
            time.sleep(1.0)

    threading.Thread(target=loop, name="desktop-progress", daemon=True).start()


def run_desktop(port: int = 7861) -> None:
    import uvicorn
    import webview

    from .server import create_app

    if sys.platform == "darwin":
        _claim_macos_app_identity()

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="console-server", daemon=True).start()
    if not _wait_for_port(port):
        raise RuntimeError(f"console server did not start on port {port}")

    width, height, min_size = _window_geometry()
    placement = {"width": width, "height": height}
    restored = _restored_geometry(_load_window_state(), _screens(), min_size)
    if restored:
        placement = restored
    window = webview.create_window(
        APP_TITLE, f"http://127.0.0.1:{port}", min_size=min_size, **placement,
    )
    _track_window_state(window)
    _watch_jobs(window, app)
    icon = _app_icon()
    # icon= needs pywebview 5+; older builds raise TypeError, and a missing icon
    # is not a reason to refuse to open the window.
    try:
        webview.start(icon=icon) if icon else webview.start()
    except TypeError:
        webview.start()
    server.should_exit = True
