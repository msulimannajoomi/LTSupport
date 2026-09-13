import os
import threading
import customtkinter as ctk

import applog
import theme
from api_client import ApiError

STATUS_COLORS = {
    "online": theme.SUCCESS,
    "busy": theme.WARNING,
    "offline": theme.TEXT_MUTED,
    "local_only": theme.SUCCESS,
}
STATUS_LABELS = {
    "online": "ONLINE",
    "busy": "BUSY",
    "offline": "OFFLINE",
    "local_only": "ON YOUR NETWORK",
}


class DashboardView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app

        scroll = ctk.CTkScrollableFrame(self, fg_color=theme.BG)
        scroll.pack(fill="both", expand=True, padx=40, pady=30)

        header = ctk.CTkFrame(scroll, fg_color="transparent")
        header.pack(fill="x", pady=(0, 6))
        title_col = ctk.CTkFrame(header, fg_color="transparent")
        title_col.pack(side="left")
        ctk.CTkLabel(title_col, text=f"Welcome, {app.api.org_name}", font=theme.h1(),
                     text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(title_col, text=f"Org ID: {app.api.org_id}", font=theme.small(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w")
        # RBAC: "admin" is the org's own original login -- always oversight-only now
        # (see relay.py's _handle_observer): it can see billing, manage team members,
        # and watch any host's screen, but never host or take full control itself.
        # "normal" (a team-member login) is the only role that can actually host or
        # take full control -- it can't touch billing or user management. Any role
        # that isn't "normal" gets the observer/view-only treatment when joining.
        is_admin = self.app.api.role in (None, "admin")
        is_observer = self.app.api.role != "normal"

        ctk.CTkButton(header, text="Log Out", width=100, height=36, corner_radius=8,
                      fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                      command=app.logout).pack(side="right")
        if is_admin:
            ctk.CTkButton(header, text="💳 Billing", width=100, height=36, corner_radius=8,
                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                          command=app.show_billing).pack(side="right", padx=(0, 8))
            ctk.CTkButton(header, text="👥 Users", width=100, height=36, corner_radius=8,
                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                          command=app.show_users).pack(side="right", padx=(0, 8))
            ctk.CTkButton(header, text="📋 Activity", width=100, height=36, corner_radius=8,
                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                          command=app.show_activity_logs).pack(side="right", padx=(0, 8))
        ctk.CTkButton(header, text="🪵 View Logs", width=100, height=36, corner_radius=8,
                      fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                      command=self._open_logs).pack(side="right", padx=(0, 8))

        self.plan_container = ctk.CTkFrame(scroll, fg_color="transparent")
        self.plan_container.pack(fill="x")
        self._render_plan_banner()
        threading.Thread(target=self._refresh_account, daemon=True).start()

        ctk.CTkLabel(scroll, text="Which one do you want? If someone else needs to see or control "
                                   "THIS computer, choose Host. If you need to see or control ANOTHER "
                                   "computer, choose Join.",
                     font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=900,
                     justify="left").pack(anchor="w", pady=(4, 12))

        cards = ctk.CTkFrame(scroll, fg_color="transparent")
        cards.pack(fill="x", pady=(0, 30))
        cards.grid_columnconfigure((0, 1), weight=1)

        blocked = bool(app.api.blocked)
        if is_observer:
            # An observer (admin, or any other non-"normal" role) can never host --
            # no card for it at all, Join takes the full width instead of sharing it
            # with a card that would just be disabled.
            join_card = self._join_card(cards, disabled=blocked)
            join_card.grid(row=0, column=0, columnspan=2, sticky="nsew")
        else:
            host_card = self._action_card(cards, "🖥️ Host This Computer",
                                           "Pick this if YOU are the one being helped. This computer "
                                           "becomes visible and controllable by whoever you share the "
                                           "Device ID with.",
                                           theme.SUCCESS, app.show_host, disabled=blocked, hover_color=theme.SUCCESS_HOVER)
            host_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

            join_card = self._join_card(cards, disabled=blocked)
            join_card.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

        devices_header = ctk.CTkFrame(scroll, fg_color="transparent")
        devices_header.pack(fill="x", pady=(10, 4))
        ctk.CTkLabel(devices_header, text="AVAILABLE DEVICES", font=theme.h3(),
                     text_color=theme.TEXT_MUTED).pack(side="left")
        self.refresh_btn = ctk.CTkButton(devices_header, text="Refresh", width=90, height=30, corner_radius=8,
                                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                          command=self.refresh_devices)
        self.refresh_btn.pack(side="right")
        ctk.CTkLabel(scroll, text="Every device you've hosted from, on this account -- showing whether "
                                   "it's reachable over your Internet connection and/or your local "
                                   "network right now, and whether it's set up for Interview sessions.",
                     font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=900,
                     justify="left").pack(anchor="w", pady=(0, 12))

        self.devices_container = ctk.CTkFrame(scroll, fg_color="transparent")
        self.devices_container.pack(fill="x")

        ctk.CTkLabel(self.devices_container, text="Loading devices…",
                     font=theme.body(), text_color=theme.TEXT_MUTED).pack(pady=20)

        self.refresh_devices()

    def _open_logs(self):
        try:
            os.startfile(os.path.dirname(applog.log_path()))
        except Exception:
            pass

    def _refresh_account(self):
        try:
            self.app.api.refresh_account()
            self.after(0, self._render_plan_banner)
        except ApiError:
            pass

    def _render_plan_banner(self):
        # Scheduled via self.after() from a background thread (_refresh_account) --
        # the user may have already navigated away (main.py's _swap destroys this whole
        # view when switching) by the time it actually runs, which crashed trying to
        # configure widgets that no longer exist.
        if not self.winfo_exists():
            return
        for child in self.plan_container.winfo_children():
            child.destroy()

        api = self.app.api
        color = theme.DANGER if api.blocked else theme.WARNING

        plan_row = ctk.CTkFrame(self.plan_container, fg_color=color, corner_radius=8)
        plan_row.pack(fill="x", pady=(16, 8))
        inner = ctk.CTkFrame(plan_row, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=10)

        if api.blocked:
            text = "ACCOUNT BLOCKED — hosting and joining are disabled. Contact support."
        elif api.account_type == "trial":
            text = f"TRIAL PLAN — 10-min sessions, 3/day · {api.session_count} session(s) so far."
        elif api.account_type == "prepaid":
            text = f"PREPAID PLAN — PKR {api.balance_rupees:,.0f} remaining."
        else:
            text = f"POSTPAID PLAN — {api.session_count} session(s) so far."

        text_color = "white" if api.blocked else "#0f172a"
        ctk.CTkLabel(inner, text=text, font=theme.small(), text_color=text_color,
                     wraplength=600, justify="left").pack(side="left")
        if api.role in (None, "admin"):
            ctk.CTkButton(inner, text="View Billing →", width=140, height=28, corner_radius=6,
                          fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                          font=theme.small(), command=self.app.show_billing).pack(side="right")

    def _action_card(self, parent, title, desc, color, command, disabled=False, hover_color=None):
        frame = ctk.CTkFrame(parent, fg_color=theme.CARD, corner_radius=14)
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=28, pady=28)
        text_color = theme.TEXT_MUTED if disabled else color
        ctk.CTkLabel(inner, text=title, font=theme.h2(), text_color=text_color).pack(anchor="w")
        ctk.CTkLabel(inner, text="Account blocked" if disabled else desc, font=theme.body(),
                     text_color=theme.TEXT_MUTED, wraplength=280, justify="left").pack(anchor="w", pady=(8, 16))
        if not disabled:
            ctk.CTkButton(inner, text=title, height=44, corner_radius=8, fg_color=color,
                          hover_color=hover_color or color, text_color="#0f172a", font=theme.h3(),
                          command=command).pack(fill="x")
        return frame

    def _join_card(self, parent, disabled=False):
        frame = ctk.CTkFrame(parent, fg_color=theme.CARD, corner_radius=14)
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=28, pady=28)
        ctk.CTkLabel(inner, text="💻 Join a Device", font=theme.h2(), text_color=theme.ACCENT).pack(anchor="w")

        if disabled:
            ctk.CTkLabel(inner, text="Account blocked", font=theme.body(),
                         text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(8, 16))
            return frame

        if self.app.api.role != "normal":
            ctk.CTkLabel(inner, text="👁 Oversight access — you can watch any device's screen, "
                                      "including one already in a live session, but never control "
                                      "it or send anything. Watching never disturbs the session "
                                      "actually running between the client and that host.",
                         font=theme.small(), text_color=theme.WARNING, wraplength=280,
                         justify="left").pack(anchor="w", pady=(4, 12))
        ctk.CTkLabel(inner, text="Pick this if YOU need to see or control someone else's "
                                  "computer. Ask them to click \"Host This Computer\" on their "
                                  "end first — they'll get a Device ID to give you. Once they're "
                                  "hosting, their device also shows up below in Available Devices.",
                     font=theme.body(), text_color=theme.TEXT_MUTED, wraplength=280,
                     justify="left").pack(anchor="w", pady=(8, 12))

        ctk.CTkLabel(inner, text="Or enter a Device ID directly:", font=theme.small(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.join_entry = ctk.CTkEntry(inner, placeholder_text="device-id", height=38, corner_radius=8)
        self.join_entry.pack(fill="x", pady=(4, 12))
        self.join_entry.bind("<Return>", lambda e: self._join_manual())
        ctk.CTkButton(inner, text="Connect", height=38, corner_radius=8, fg_color=theme.ACCENT,
                      hover_color=theme.ACCENT_HOVER, text_color="#0f172a", font=theme.h3(),
                      command=self._join_manual).pack(fill="x")
        return frame

    def _join_manual(self):
        device_id = self.join_entry.get().strip()
        if device_id:
            self.app.open_viewer(device_id)

    def refresh_devices(self):
        self.refresh_btn.configure(state="disabled", text="Refreshing…")
        threading.Thread(target=self._load_devices, daemon=True).start()

    def _load_devices(self):
        import local_link
        try:
            devices = self.app.api.list_devices()
        except ApiError:
            devices = []

        # Merge in a live local-network scan: devices already known to this account get
        # tagged as also-reachable-locally right now; a device found on the LAN that this
        # account has never registered (e.g. only ever hosted in Local Network mode, which
        # never touches the backend) shows up as a new "on your network" entry. Ownership
        # is still verified for real at the moment of connecting (verify_peer) -- this scan
        # only decides what to show, never what to allow.
        merged = {d["device_id"]: dict(d, local=False) for d in devices}
        try:
            for device_id, ip, port, session_type in local_link.discover_local_hosts():
                if device_id in merged:
                    merged[device_id]["local"] = True
                    merged[device_id]["local_target"] = (ip, port)
                    merged[device_id]["session_type"] = session_type
                else:
                    merged[device_id] = {
                        "device_id": device_id, "name": device_id, "status": "local_only",
                        "session_type": session_type, "local": True, "local_target": (ip, port),
                    }
        except Exception:
            pass

        self.after(0, lambda: self._render_devices(list(merged.values())))

    def _render_devices(self, devices):
        # Same reasoning as _render_plan_banner -- scheduled via self.after() from a
        # background thread (_load_devices), which can easily still be in flight after
        # the user has already navigated to a different view, destroying this one.
        if not self.winfo_exists():
            return
        self.refresh_btn.configure(state="normal", text="Refresh")
        for child in self.devices_container.winfo_children():
            child.destroy()

        if not devices:
            ctk.CTkLabel(self.devices_container,
                         text="No devices found — start hosting on a PC to see it here.",
                         font=theme.body(), text_color=theme.TEXT_MUTED).pack(pady=20)
            return

        for d in devices:
            row = ctk.CTkFrame(self.devices_container, fg_color=theme.CARD, corner_radius=10)
            row.pack(fill="x", pady=6)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=20, pady=14)

            ctk.CTkLabel(inner, text="●", text_color=STATUS_COLORS.get(d["status"], theme.TEXT_MUTED),
                         font=theme.body()).pack(side="left", padx=(0, 12))

            text_col = ctk.CTkFrame(inner, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(text_col, text=d["name"], font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")

            badges = [d["device_id"]]
            if d["status"] != "local_only":
                badges.append(f"🌐 Internet: {STATUS_LABELS.get(d['status'], d['status'].upper())}")
            if d.get("local"):
                badges.append("📶 Local Network")
            if d.get("session_type") == "interview":
                badges.append("🎙️ Interview available")
            # Lets an observer (admin) see, without connecting first, which devices
            # have a real support session running right now and since when -- see
            # relay.py's HostConn.viewer_connected_at, surfaced via /api/devices.
            if d.get("live_since"):
                since_time = d["live_since"][11:16]  # ISO "...THH:MM:SS..." -> "HH:MM"
                badges.append(f"🔴 Live since {since_time}")
            ctk.CTkLabel(text_col, text="  ·  ".join(badges), font=theme.small(),
                         text_color=theme.TEXT_MUTED).pack(anchor="w")

            local_target = d.get("local_target")
            is_observer = self.app.api.role != "normal"
            reachable = d.get("local") or d["status"] == "online"
            # An observer can join a "busy" device too (that's the whole point --
            # watching an already-live session without disturbing it); a "normal"
            # role still can't, since two "normal" viewers can't share the one
            # primary viewer slot (see relay.py's DEVICE_BUSY).
            if is_observer:
                reachable = reachable or d["status"] == "busy"
            can_connect = reachable and not self.app.api.blocked

            def _connect(did=d["device_id"], target=local_target):
                self.app.open_viewer(did, local_target=target)

            connect_btn = ctk.CTkButton(inner, text="Connect", width=100, height=32, corner_radius=8,
                                         fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                         text_color="#0f172a", font=theme.small(), command=_connect)
            connect_btn.configure(state="normal" if can_connect else "disabled")
            connect_btn.pack(side="right")

            # "local_only" entries exist only because a live discovery reply matched
            # nothing in this account's registered devices -- there's no backend record
            # to remove, so no button for those.
            if d["status"] != "local_only":
                ctk.CTkButton(inner, text="✕", width=32, height=32, corner_radius=8,
                              fg_color=theme.BG, hover_color=theme.DANGER, text_color=theme.TEXT_MUTED,
                              font=theme.small(),
                              command=lambda did=d["device_id"]: self._remove_device(did)
                              ).pack(side="right", padx=(0, 8))

    def _remove_device(self, device_id):
        threading.Thread(target=self._do_remove_device, args=(device_id,), daemon=True).start()

    def _do_remove_device(self, device_id):
        try:
            self.app.api.remove_device(device_id)
        except ApiError:
            pass
        self._load_devices()
