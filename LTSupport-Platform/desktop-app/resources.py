import os
import sys


def resource_path(filename):
    """Locate a bundled resource (e.g. an icon) whether running from source
    or from a PyInstaller-built .exe, where files live under sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)
