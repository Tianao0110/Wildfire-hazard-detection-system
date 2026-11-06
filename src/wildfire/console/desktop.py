"""Native desktop window for the console (pywebview: WebView2 on Windows, WebKit on macOS).

`python -m src.wildfire.console --desktop` starts the FastAPI server on a
background thread and opens the UI in a real application window — no browser
chrome, no terminal. Launched via pythonw.exe on Windows or from the .app bundle
on macOS (see scripts/install_desktop_app.py) there is no console window at all:
it looks and behaves like installed software.

Closing the window shuts the server down.
"""

from __future__ import annotations

import socket
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


def run_desktop(port: int = 7861) -> None:
    import uvicorn
    import webview

    from .server import create_app

    if sys.platform == "darwin":
        _claim_macos_app_identity()

    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="console-server", daemon=True).start()
    if not _wait_for_port(port):
        raise RuntimeError(f"console server did not start on port {port}")

    width, height, min_size = _window_geometry()
    webview.create_window(
        APP_TITLE, f"http://127.0.0.1:{port}",
        width=width, height=height, min_size=min_size,
    )
    icon = _app_icon()
    # icon= needs pywebview 5+; older builds raise TypeError, and a missing icon
    # is not a reason to refuse to open the window.
    try:
        webview.start(icon=icon) if icon else webview.start()
    except TypeError:
        webview.start()
    server.should_exit = True
