import os
import sys


def resource_path(filename):
    """Locate a bundled resource (e.g. an icon) whether running from source,
    from a PyInstaller-built .exe (files under sys._MEIPASS), or a
    Nuitka-compiled one. PyInstaller sets sys.frozen; Nuitka doesn't, but
    injects a __compiled__ global into every module it compiles (verified
    empirically against real Nuitka onefile/standalone builds, not just
    Nuitka's docs) -- when present, a --include-data-files bundle is
    extracted next to sys.executable at runtime, which is the correct base
    for this same lookup either way (onefile or standalone)."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        try:
            __compiled__  # noqa: F821 -- only defined when Nuitka-compiled
            base = os.path.dirname(sys.executable)
        except NameError:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)
