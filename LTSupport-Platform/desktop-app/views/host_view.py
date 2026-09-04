import socket
import customtkinter as ctk

import theme
from host_agent import HostAgent
from tray import TrayIcon


class HostView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self.agent = None
        self.tray = None
        self._viewer_was_connected = False
        self._shutdown = False

        ctk.CTkButton(self, text="←  Back to Dashboard", fg_color="transparent", hover_color=theme.CARD,
                      text_color=theme.TEXT_MUTED, anchor="w", command=self._back).place(x=30, y=24)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.place(relx=0.5, rely=0.5, anchor="center")

        card = ctk.CTkFrame(container, fg_color=theme.CARD, corner_radius=16, width=460)
        card.pack()
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=48, pady=48)

        ctk.CTkLabel(inner, text="Host This Computer", font=theme.h1(), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(inner, text="Share this PC so you can access or support it remotely.",
                     font=theme.body(), text_color=theme.TEXT_MUTED, wraplength=360,
                     justify="left").pack(anchor="w", pady=(6, 28))

        status_row = ctk.CTkFrame(inner, fg_color=theme.BG, corner_radius=10)
        status_row.pack(fill="x", pady=(0, 20))
        status_inner = ctk.CTkFrame(status_row, fg_color="transparent")
        status_inner.pack(fill="x", padx=20, pady=16)
        self.status_dot = ctk.CTkLabel(status_inner, text="●", text_color=theme.TEXT_MUTED, font=theme.body())
        self.status_dot.pack(side="left", padx=(0, 10))
        self.status_label = ctk.CTkLabel(status_inner, text="Not hosting", font=theme.h3(), text_color=theme.TEXT)
        self.status_label.pack(side="left")

        ctk.CTkLabel(inner, text="DEVICE ID", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        id_row = ctk.CTkFrame(inner, fg_color="transparent")
        id_row.pack(fill="x", pady=(4, 4))
        self.id_value = ctk.CTkEntry(id_row, height=42, corner_radius=8, font=theme.mono(16), justify="center")
        self.id_value.insert(0, "—")
        self.id_value.configure(state="readonly")
        self.id_value.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.copy_id_btn = ctk.CTkButton(id_row, text="📋 Copy", width=90, height=42, corner_radius=8,
                                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                          command=self._copy_device_id, state="disabled")
        self.copy_id_btn.pack(side="left")

        self.instructions_label = ctk.CTkLabel(
            inner, text="", font=theme.small(), text_color=theme.ACCENT, wraplength=360,
            justify="left",
        )
        self.instructions_label.pack(anchor="w", pady=(0, 16))

        ctk.CTkLabel(inner, text="SESSION TYPE", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.session_type_selector = ctk.CTkSegmentedButton(inner, values=["Normal", "Interview"])
        self.session_type_selector.set("Normal")
        self.session_type_selector.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(inner, text="Interview Mode shares two-way voice and isn't limited by your "
                                  "plan/balance -- only whether this account is blocked. Whoever "
                                  "connects to this device gets the mode you pick here; they have no "
                                  "way to change it.",
                     font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=360,
                     justify="left").pack(anchor="w", pady=(0, 16))

        self.local_mode_var = ctk.BooleanVar(value=False)
        self.local_mode_check = ctk.CTkCheckBox(
            inner, text="Connect over Local Network (WiFi) instead of the Internet",
            variable=self.local_mode_var, font=theme.small(), text_color=theme.TEXT_MUTED,
        )
        self.local_mode_check.pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(inner, text="Only reachable by a viewer on the same WiFi/network. Faster and "
                                  "uses no internet bandwidth for the session itself -- your plan is "
                                  "still checked and updated online at the start and end of the session.",
                     font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=360,
                     justify="left").pack(anchor="w", pady=(0, 16))

        self.toggle_btn = ctk.CTkButton(inner, text="Start Hosting", height=44, corner_radius=8,
                                         fg_color=theme.SUCCESS, hover_color="#22c55e", text_color="#0f172a",
                                         font=theme.h3(), command=self.toggle)
        self.toggle_btn.pack(fill="x")

        ctk.CTkLabel(inner, text="Once a viewer connects, this window hides to the system tray, "
                                  "your microphone becomes live for the call, and the whole session "
                                  "is recorded. The app closes automatically when they disconnect.",
                     font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=360,
                     justify="left").pack(anchor="w", pady=(16, 0))

    def _copy_device_id(self):
        device_id = self.id_value.get()
        self.clipboard_clear()
        self.clipboard_append(device_id)
        self.copy_id_btn.configure(text="Copied!")
        self.after(1200, lambda: self.copy_id_btn.configure(text="📋 Copy"))

    def _back(self):
        if self.agent:
            self.agent.stop()
        self.app.show_dashboard()

    def toggle(self):
        if self.agent is None:
            self._start()
        else:
            self._stop()

    def _start(self):
        self.toggle_btn.configure(state="disabled", text="Starting...")
        self.local_mode_check.configure(state="disabled")
        self.session_type_selector.configure(state="disabled")
        local_mode = self.local_mode_var.get()
        session_type = self.session_type_selector.get().lower()
        self.agent = HostAgent(self.app.api.token, socket.gethostname(), self._on_status,
                                api=self.app.api, local_mode=local_mode, session_type=session_type)
        self.agent.start()

    def _stop(self):
        if self.agent:
            self.agent.stop()
        self.agent = None
        if self.tray:
            self.tray.stop()
            self.tray = None
        if self.app.state() == "withdrawn":
            self.app.deiconify()
            self.app.lift()
        self.status_dot.configure(text_color=theme.TEXT_MUTED)
        self.status_label.configure(text="Not hosting")
        self.toggle_btn.configure(state="normal", text="Start Hosting", fg_color=theme.SUCCESS, hover_color="#22c55e")
        self.local_mode_check.configure(state="normal")
        self.session_type_selector.configure(state="normal")
        self.copy_id_btn.configure(state="disabled")
        self.instructions_label.configure(text="")

    def _on_status(self, status, info):
        self.after(0, lambda: self._apply_status(status, info))

    def _apply_status(self, status, info):
        if self._shutdown:
            return  # a shutdown is already underway (or done) -- show nothing else, ever
        if status == "online":
            device_id = info["device_id"]
            self.id_value.configure(state="normal")
            self.id_value.delete(0, "end")
            self.id_value.insert(0, device_id)
            self.id_value.configure(state="readonly")
            self.copy_id_btn.configure(state="normal")
            self.status_dot.configure(text_color=theme.SUCCESS)
            mode_label = " — Interview Mode" if self.agent._session_type == "interview" else ""
            if self.agent.local_mode:
                self.status_label.configure(text=f"Hosting — waiting for a viewer (Local Network){mode_label}")
                self.instructions_label.configure(
                    text=f"To connect: on the other computer, open LTSupport (same WiFi network), "
                         f"log in to the same account, and go to the Dashboard — this PC will show up "
                         f"as {device_id} in the device list, reachable over the local network. If it "
                         f"isn't found, type the ID above into the device-id box instead.")
            else:
                self.status_label.configure(text=f"Hosting — waiting for a viewer{mode_label}")
                self.instructions_label.configure(
                    text=f"To connect: on the other computer, open LTSupport, log in, go to the "
                         f"Dashboard, and under \"Join a Device\" paste this ID: {device_id}")
            self.toggle_btn.configure(state="normal", text="Stop Hosting", fg_color=theme.DANGER, hover_color="#ef4444")
            self.agent.launch_overlay(self.app)
        elif status == "reconnecting":
            self.status_dot.configure(text_color=theme.WARNING)
            self.status_label.configure(
                text=f"Connection dropped -- reconnecting (attempt {info['attempt']}/{info['max_attempts']})…")
        elif status == "resumed":
            self.status_dot.configure(text_color=theme.SUCCESS)
            mode_label = " — Interview Mode" if self.agent._session_type == "interview" else ""
            self.status_label.configure(text=f"Hosting — waiting for a viewer{mode_label}")
        elif status == "viewer_joined":
            self._viewer_was_connected = True
            self.status_dot.configure(text_color=theme.ACCENT)
            self.status_label.configure(text="Viewer connected — mic live, recording")
            self._hide_to_tray()
        elif status == "viewer_left":
            self._full_shutdown()
        elif status == "trial_limit":
            self._full_shutdown()
        elif status == "error":
            self.status_dot.configure(text_color=theme.DANGER)
            self.status_label.configure(text=info.get("message", "Error"))
            self.toggle_btn.configure(state="normal", text="Start Hosting")
            self.local_mode_check.configure(state="normal")
            self.session_type_selector.configure(state="normal")
            self.agent = None
        elif status == "offline":
            # Fires at the end of every hosting attempt, for any reason -- including
            # after viewer_left/trial_limit already handled it (both set _shutdown=True
            # via _full_shutdown, so the guard above already returned before reaching
            # here in that case). Only route here for real: manually stopping before any
            # viewer ever connected (window stays open, ready to host again) vs. a
            # connection that was live and then unexpectedly dropped (full silent exit,
            # matching viewer_left/trial_limit -- never re-show the window for this).
            if self._viewer_was_connected:
                self._full_shutdown()
            else:
                self._stop()

    def _hide_to_tray(self):
        if self.tray is None:
            self.tray = TrayIcon(
                on_show=lambda: self.app.after(0, self._restore_from_tray),
                on_stop=lambda: self.app.after(0, self._tray_stop_clicked),
            )
            device_id = self.agent.device_id if self.agent else ""
            self.tray.start(f"LTSupport — Live: screen, mic & recording ({device_id})")
        self.app.withdraw()

    def _restore_from_tray(self):
        self.app.deiconify()
        self.app.lift()

    def _tray_stop_clicked(self):
        self._restore_from_tray()
        self._stop()

    def _full_shutdown(self):
        """A viewer disconnected while this host was running hidden in the background.
        Per the one-shot-session model, the whole app exits rather than waiting idle --
        silently, with no dialog, tray notice, or any other visible sign on this side.
        _shutdown latches immediately so nothing later (a stray status update racing in
        from the network thread) can un-hide the window or show anything, even briefly."""
        if self._shutdown:
            return
        self._shutdown = True
        if self.agent:
            self.agent.stop()
        if self.tray:
            self.tray.stop()
            self.tray = None
        self.app.destroy()
