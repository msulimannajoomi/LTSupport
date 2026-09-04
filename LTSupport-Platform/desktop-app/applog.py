"""The packaged exe is built with --windowed (no console), which means sys.stdout and
sys.stderr are None -- every print() in the app (used throughout for diagnostics: relay
handshakes, local-network discovery, disconnect causes) has nowhere to go and is silently
lost. This redirects both to a real log file instead, so those same print() calls become
inspectable after the fact without changing a single one of them. Must be imported and
have init() called before anything else prints."""
import os
import sys
import datetime


def _log_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "LTSupport", "logs")
    os.makedirs(path, exist_ok=True)
    return path


def log_path():
    return os.path.join(_log_dir(), "ltsupport.log")


class _LineFlushingWriter:
    """A minimal, crash-proof stand-in for a real stream -- if the log file itself can't
    be opened or written to for some reason, writes just disappear instead of taking the
    whole app down (the same failure mode print() already silently has today)."""

    def __init__(self, path):
        self._path = path
        self._fh = None
        try:
            self._fh = open(path, "a", encoding="utf-8", buffering=1)
        except Exception:
            self._fh = None

    def write(self, text):
        if self._fh is None:
            return
        try:
            self._fh.write(text)
        except Exception:
            pass

    def flush(self):
        if self._fh is None:
            return
        try:
            self._fh.flush()
        except Exception:
            pass


def init():
    path = log_path()
    writer = _LineFlushingWriter(path)
    sys.stdout = writer
    sys.stderr = writer
    print(f"\n===== LTSupport starting {datetime.datetime.now().isoformat()} =====")
    return path
