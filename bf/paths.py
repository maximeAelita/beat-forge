"""Where BeatForge reads its assets from and where it writes your work.

Running from a checkout these are the same directory. Bundled into a one-file
executable they must not be: PyInstaller unpacks the app into a temporary
folder that is deleted on exit, so `web/` comes from there while `projects/`
and `exports/` have to live somewhere that survives closing the app.
"""

import os
import sys

APP_NAME = "BeatForge"


def is_frozen():
    """True when running from a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_root():
    """Read-only assets -- the `web/` directory."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_root():
    """Writable state -- `projects/` and `exports/`.

    From a checkout this is the project directory, so nothing moves for anyone
    running it the old way. Frozen, it is a per-user folder, which also keeps
    the app working when it is installed somewhere unwritable.
    """
    if not is_frozen():
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    override = os.environ.get("BEATFORGE_HOME")
    if override:
        return os.path.abspath(override)
    # Prefer sitting next to the executable: your projects and renders stay
    # with the app, on whichever drive you put it. Falls back to a per-user
    # folder when that location is read-only, e.g. under Program Files.
    beside = os.path.dirname(os.path.abspath(sys.executable))
    if _writable(beside):
        return beside
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_NAME)


def _writable(path):
    probe = os.path.join(path, ".beatforge-write-test")
    try:
        with open(probe, "w") as fh:
            fh.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def ensure_dirs(root):
    """Make sure the writable subfolders exist. Returns `root`."""
    for sub in ("projects", "exports"):
        path = os.path.join(root, sub)
        if not os.path.isdir(path):
            try:
                os.makedirs(path)
            except OSError:
                pass
    return root
