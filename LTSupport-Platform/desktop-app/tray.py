import threading

import pystray
from PIL import Image

from resources import resource_path


class TrayIcon:
    def __init__(self, on_show, on_stop):
        self.on_show = on_show
        self.on_stop = on_stop
        self.icon = None

    def _load_image(self):
        try:
            return Image.open(resource_path("app_icon.png"))
        except Exception:
            return Image.new("RGB", (64, 64), "#38bdf8")

    def start(self, title="LTSupport — Hosting"):
        menu = pystray.Menu(
            pystray.MenuItem("Show Window", lambda icon, item: self.on_show()),
            pystray.MenuItem("Stop Hosting", lambda icon, item: self.on_stop()),
        )
        self.icon = pystray.Icon("ltsupport", self._load_image(), title, menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def stop(self):
        if self.icon:
            self.icon.stop()
            self.icon = None
