import ctypes

try:
    # PROCESS_PER_MONITOR_DPI_AWARE -- must happen before any window is created, this
    # early. Without it, Windows silently hands this whole process "virtualized"
    # (scaled) screen and cursor coordinates on any display running above 100%
    # scaling -- which desyncs screen capture (mss), cursor control (pynput/
    # pyautogui), and Tk's own screen-size queries from each other by just enough
    # that the instructor text overlay lands in a visibly wrong spot and size on the
    # host's real screen relative to where the viewer placed it. This is the actual
    # fix for that (see host_agent.py/overlay.py for the matching other half), not a
    # cosmetic setting.
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # older Windows fallback
    except Exception:
        pass

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import applog
LOG_PATH = applog.init()  # must happen before anything else prints -- see applog.py

import customtkinter as ctk

import theme
from resources import resource_path
from api_client import ApiClient
from views.login_view import LoginView
from views.signup_view import SignupView
from views.dashboard_view import DashboardView
from views.host_view import HostView
from views.viewer_view import ViewerView
from views.billing_view import BillingView
from views.users_view import UsersView
from views.activity_logs_view import ActivityLogsView


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        theme.apply()
        self.title("VantagePoint")
        self.geometry("1100x780")
        self.minsize(900, 640)
        self.configure(fg_color=theme.BG)

        try:
            icon_path = resource_path("app_icon.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

        self.api = ApiClient()
        self.current_view = None
        self.show_login()

    def _swap(self, view_cls, *args, **kwargs):
        if self.current_view is not None:
            self.current_view.destroy()
        self.current_view = view_cls(self, self, *args, **kwargs)
        self.current_view.pack(fill="both", expand=True)

    def show_login(self):
        self._swap(LoginView)

    def show_signup(self):
        self._swap(SignupView)

    def show_dashboard(self):
        self._swap(DashboardView)

    def show_host(self):
        self._swap(HostView)

    def show_billing(self):
        self._swap(BillingView)

    def show_users(self):
        self._swap(UsersView)

    def show_activity_logs(self):
        self._swap(ActivityLogsView)

    def open_viewer(self, device_id, local_target=None):
        top = ctk.CTkToplevel(self)
        # RBAC: only "normal" ever gets full control -- the org's own admin login is
        # always an observer now (see relay.py's _handle_observer), same as any other
        # non-"normal" role.
        is_observer = self.api.role != "normal"
        title_suffix = " (View Only)" if is_observer else ""
        top.title(f"VantagePoint Viewer — {device_id}{title_suffix}")
        top.geometry("1200x800")
        # Calling state("zoomed") this early -- before the toplevel has actually been
        # drawn -- is unreliable on Windows and often gets silently ignored, leaving the
        # window at the 1200x800 fallback above instead of filling the screen. Forcing a
        # draw first, then deferring the actual maximize by one more event-loop tick,
        # makes it take effect every time.
        top.update_idletasks()
        top.after(10, lambda: top.state("zoomed"))
        # An observer role gets the exact same live feed, just with every interactive
        # control (control mode, overlay editing, speech-to-text) stripped out
        # client-side -- see ViewerView's view_only param. The relay also drops any
        # input/overlay command from this role server-side regardless, so this is
        # about UI, not the only thing standing between them and control.
        ViewerView(top, self, device_id, local_target=local_target, view_only=is_observer)

    def logout(self):
        self.api.logout()
        self.show_login()


if __name__ == "__main__":
    app = App()
    app.mainloop()
