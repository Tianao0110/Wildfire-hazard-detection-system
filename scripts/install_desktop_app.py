"""Install the console as a desktop app: app icon + a launcher you double-click.

    python -m scripts.install_desktop_app

Windows: writes assets/wildfire.ico and Desktop / Start Menu shortcuts that run
`pythonw.exe -m src.wildfire.console --desktop` - a native window, no terminal.

macOS: writes assets/wildfire.icns and `~/Applications/Wildfire Hazard
Detection.app`, a minimal bundle whose launcher runs the same command with
`.venv/bin/python`. An .app never shows a terminal, so there is no pythonw
equivalent to hunt for, and the bundle is what puts the app in Launchpad,
Spotlight and the Dock.

Run again any time to refresh; delete the shortcut / bundle to "uninstall".
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT_ROOT / "assets"
APP_NAME = "Wildfire Hazard Detection"
BUNDLE_ID = "org.wildfire.hazarddetection.console"
APP_DESCRIPTION = "Offline drone wildfire hazard detection console"


ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
# macOS wants each size twice, once per pixel density, under exactly these names.
ICNS_SIZES = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
              (128, "128x128"), (256, "128x128@2x"), (256, "256x256"), (512, "256x256@2x"),
              (512, "512x512"), (1024, "512x512@2x")]


def venv_python(gui: bool = False) -> Path:
    """The interpreter inside the project venv, per platform.

    `gui=True` asks for the one that opens no console window - pythonw.exe on
    Windows. macOS has no such split: a bundled process is already windowless.
    """
    if sys.platform == "win32":
        return PROJECT_ROOT / ".venv" / "Scripts" / ("pythonw.exe" if gui else "python.exe")
    return PROJECT_ROOT / ".venv" / "bin" / "python"


def _mark_from_logo(logo: Path):
    """The square mark out of branding/logo.png, ready to sit in an icon.

    Most logos are wordmarks - ours is 739x202 - and squeezing one into 16x16
    produces a smudge. So: find the ink, and if the logo is wide, cut at the
    first clear vertical gap, which in a wordmark is the space between the
    symbol and the lettering. A logo that is already roughly square is used
    whole. Returns None if Pillow cannot read the file, so the caller can fall
    back rather than fail the install.
    """
    from PIL import Image

    try:
        im = Image.open(logo).convert("RGBA")
    except Exception:
        return None

    # Ink = visible and not near-white, so a white-on-transparent logo still works.
    px = im.load()
    w, h = im.size
    cols = [False] * w
    for x in range(w):
        for y in range(0, h, 2):  # every other row is plenty to find a gap
            r, g, b, a = px[x, y]
            if a > 24 and r + g + b < 720:
                cols[x] = True
                break
    xs = [x for x, on in enumerate(cols) if on]
    if not xs:
        return None
    left, right = xs[0], xs[-1]

    if (right - left + 1) / max(1, h) > 1.4:  # wordmark: keep the leading mark
        gap_start = None
        for x in range(left, right + 1):
            if not cols[x]:
                gap_start = x if gap_start is None else gap_start
            else:
                if gap_start is not None and x - gap_start >= 8:
                    right = gap_start
                    break
                gap_start = None
    return im.crop((left, 0, right + 1, h))


def render_icon(size: int = 256):
    """Draw the app icon at `size` px from the branding logo.

    The icon used to be drawn here in Pillow - a green square with a flame - so
    the shortcut and the window carried something the rest of the app no longer
    looked like. It now comes from branding/logo.png, which means rebranding is
    still one file drop. Every measurement is a fraction of `size` because
    macOS needs the same artwork up at 1024 px.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # White plate with a brand-coloured rim. A brand-filled plate was tried and
    # the logo's own blues sank into it - illegible by 16px. Most marks are drawn
    # for white, and the rim keeps the icon from vanishing on a light taskbar.
    brand = _brand_colors()
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=round(size * 52 / 256),
                        fill=(255, 255, 255, 255), outline=brand,
                        width=max(1, round(size * 8 / 256)))

    mark = _mark_from_logo(PROJECT_ROOT / "branding" / "logo.png")
    if mark is not None:
        inner = int(size * 0.70)  # padding so it reads as an icon, not a sticker
        # Scale to fit `inner`, UP as well as down. This was Image.thumbnail(),
        # which only ever shrinks: the mark out of logo.png is ~200 px, so the
        # 256 px .ico looked right while the 1024 px .icns kept it at 200 px -
        # 20% of the tile, and macOS 26 rendered the app as a blank white
        # square. The source is only 200 px so a 1024 px icon is a genuine
        # upscale; drop a larger branding/logo.png in and it sharpens for free.
        ratio = min(inner / mark.width, inner / mark.height)
        mark = mark.resize((max(1, round(mark.width * ratio)),
                            max(1, round(mark.height * ratio))), Image.LANCZOS)
        img.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
    else:
        d.text((size // 2, size // 2), "W", anchor="mm", fill=brand)
    return img


def make_icon(dest: Path) -> Path:
    """Build the multi-size Windows .ico from the branding logo."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    render_icon(256).save(dest, sizes=ICON_SIZES)
    return dest


def make_icns(dest: Path) -> Path:
    """Build the macOS .icns from the branding logo, via the system `iconutil`.

    Pillow can write .icns directly but only through the same tool, and with
    less control over which sizes land in the file; building the .iconset by
    hand keeps every density sharp instead of letting one 256 px source get
    scaled up to fill the Retina slots.
    """
    from PIL import Image

    base = render_icon(1024)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "wildfire.iconset"
        iconset.mkdir()
        for px, name in ICNS_SIZES:
            base.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{name}.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)],
                       check=True, capture_output=True, text=True)
    return dest


def app_icon_path() -> Path:
    """Where this platform's icon lives (built on demand by `ensure_icon`)."""
    return ASSETS / ("wildfire.icns" if sys.platform == "darwin" else "wildfire.ico")


def ensure_icon() -> Path | None:
    """The platform icon, generated if it is not on disk yet. None if that fails."""
    icon = app_icon_path()
    if icon.exists():
        return icon
    try:
        return make_icns(icon) if sys.platform == "darwin" else make_icon(icon)
    except Exception:
        return None


def _brand_colors() -> tuple:
    """Icon plate colour from branding/brand.json, so it tracks the brand."""
    import json

    try:
        cfg = json.loads((PROJECT_ROOT / "branding" / "brand.json").read_text(encoding="utf-8"))
        h = (cfg.get("colors", {}).get("primary") or "").lstrip("#")
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    except Exception:
        pass
    return (45, 90, 45, 255)


# --------------------------------------------------------------------- Windows
def make_shortcut(lnk_path: Path, icon: Path) -> None:
    pythonw = venv_python(gui=True)
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{lnk_path}')
$s.TargetPath = '{pythonw}'
$s.Arguments = '-m src.wildfire.console --desktop'
$s.WorkingDirectory = '{PROJECT_ROOT}'
$s.IconLocation = '{icon}'
$s.Description = '{APP_DESCRIPTION}'
$s.Save()
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True, text=True)


def install_windows() -> int:
    import os

    pythonw = venv_python(gui=True)
    if not pythonw.exists():
        print(f"venv pythonw not found at {pythonw} - create the venv first.")
        return 2

    icon = make_icon(ASSETS / "wildfire.ico")
    print(f"icon    : {icon}")

    # Ask Windows for the real Desktop (handles OneDrive-redirected profiles).
    probe = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True)
    desktop = Path(probe.stdout.strip() or (Path.home() / "Desktop"))
    start_menu = Path(os.path.expandvars(
        r"%APPDATA%")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    for where in (desktop, start_menu):
        if where.exists():
            lnk = where / f"{APP_NAME}.lnk"
            make_shortcut(lnk, icon)
            print(f"shortcut: {lnk}")
    print(f"\nDouble-click \"{APP_NAME}\" - the app opens in its own window, no terminal.")
    return 0


# ----------------------------------------------------------------------- macOS
def make_app_bundle(app_path: Path, icon: Path | None) -> Path:
    """Write a minimal .app bundle that launches the console in desktop mode.

    Deliberately hand-rolled rather than py2app/PyInstaller: freezing torch +
    DeepForest is the 8 GB problem INSTALL.md already rejected on Windows. This
    bundle is three small files pointing at the venv that is already installed,
    so it stays in sync with the code instead of snapshotting it.
    """
    if app_path.exists():
        shutil.rmtree(app_path)  # refresh cleanly; a stale plist is hard to debug
    macos_dir = app_path / "Contents" / "MacOS"
    resources = app_path / "Contents" / "Resources"
    macos_dir.mkdir(parents=True)
    resources.mkdir(parents=True)

    info: dict = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "wildfire",
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleVersion": "1.0",
        "CFBundleShortVersionString": "1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    }
    if icon is not None:
        shutil.copyfile(icon, resources / icon.name)
        info["CFBundleIconFile"] = icon.name
    (app_path / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))

    # The launcher stays a shell script so the bundle keeps working after a
    # `git pull` - it re-reads the code every launch, like the .lnk on Windows.
    #
    # Both paths below are absolute and baked in at install time, so moving or
    # renaming the project folder breaks them. `cd ... || exit 1` did that
    # SILENTLY: double-clicking the app did nothing at all, no window, no error,
    # nothing in Console - which reads as "the app is broken" rather than "the
    # folder moved". A .lnk on Windows at least raises its own dialog; a shell
    # launcher raises nothing, so it has to say so itself.
    launcher = macos_dir / "wildfire"
    alert = (
        'osascript -e \'display alert "{title}" message "{msg}" as critical\' '
        ">/dev/null 2>&1"
    )
    moved = alert.format(
        title=APP_NAME,
        msg=(f"This app points at:\n\n{PROJECT_ROOT}\n\n"
             "That folder was moved, renamed or deleted. Open the project in its "
             "new location and run install-macos.command again to rebuild the app."),
    )
    no_venv = alert.format(
        title=APP_NAME,
        msg=(f"The Python environment is missing from:\n\n{PROJECT_ROOT}/.venv\n\n"
             "Run install-macos.command in the project folder to recreate it."),
    )
    launcher.write_text(
        "#!/bin/sh\n"
        f'PROJECT="{PROJECT_ROOT}"\n'
        f'PY="{venv_python()}"\n'
        'if ! cd "$PROJECT" 2>/dev/null; then\n'
        f"  {moved}\n"
        "  exit 1\n"
        "fi\n"
        'if [ ! -x "$PY" ]; then\n'
        f"  {no_venv}\n"
        "  exit 1\n"
        "fi\n"
        'exec "$PY" -m src.wildfire.console --desktop\n',
        encoding="utf-8")
    launcher.chmod(0o755)
    return app_path


def make_desktop_alias(app_path: Path) -> Path | None:
    """Symlink the bundle onto the Desktop, matching what Windows install does.

    The bundle lives in ~/Applications, which is NOT the "Applications" in the
    Finder sidebar (that is /Applications) - and macOS 26 removed Launchpad, so
    "it is in Launchpad" stopped being an answer. A Desktop icon is the one
    place a field operator will actually find it, and it is what the Windows
    installer has always created. Returns None if the Desktop is unavailable or
    already holds something real by that name, which we will not overwrite.
    """
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        return None
    link = desktop / app_path.name
    if link.is_symlink():
        link.unlink()  # ours from a previous run; refresh it
    elif link.exists():
        print(f"note    : {link} exists and is not a shortcut - left alone")
        return None
    link.symlink_to(app_path)
    return link


def install_macos() -> int:
    python = venv_python()
    if not python.exists():
        print(f"venv python not found at {python} - run ./install-macos.command first.")
        return 2

    icon = make_icns(ASSETS / "wildfire.icns")
    print(f"icon    : {icon}")

    apps = Path.home() / "Applications"  # user-level: no admin password needed
    apps.mkdir(parents=True, exist_ok=True)
    app_path = make_app_bundle(apps / f"{APP_NAME}.app", icon)
    print(f"app     : {app_path}")

    alias = make_desktop_alias(app_path)
    if alias is not None:
        print(f"shortcut: {alias}")

    # Finder and the Dock cache icons per bundle, so a rebuilt icon can keep
    # showing the old one until the bundle is re-registered and Dock restarts.
    subprocess.run(["touch", str(app_path)], check=False)
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/"
                    "LaunchServices.framework/Support/lsregister", "-f", str(app_path)],
                   check=False, capture_output=True)
    subprocess.run(["killall", "Dock"], check=False, capture_output=True)

    print(f"\nDouble-click \"{APP_NAME}\" on the Desktop - it runs in its own window,")
    print("no terminal. Spotlight (Cmd-Space) finds it by name too, and you can drag")
    print("it to the Dock. (Launchpad is gone as of macOS 26; use Spotlight instead.)")
    return 0


def main() -> int:
    if sys.platform == "win32":
        return install_windows()
    if sys.platform == "darwin":
        return install_macos()
    print(f"No desktop-shortcut installer for {sys.platform}; the app itself runs fine.")
    print("Start it with:  .venv/bin/python -m src.wildfire.console --desktop")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
