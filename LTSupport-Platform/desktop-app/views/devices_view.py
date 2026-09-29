import threading
import customtkinter as ctk

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


class DevicesView(ctk.CTkFrame):
    """The full device list, split out of the Dashboard into its own page -- Join a
    Device there still covers the common case (Device ID already in hand), this is
    for browsing/reconnecting to a device already known to this account."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app

        scroll = ctk.CTkScrollableFrame(self, fg_color=theme.BG)
        scroll.pack(fill="both", expand=True, padx=40, pady=30)

        header = ctk.CTkFrame(scroll, fg_color="transparent")
        header.pack(fill="x", pady=(0, 20))
        ctk.CTkButton(header, text="←  Back to Dashboard", fg_color="transparent", hover_color=theme.CARD,
                      text_color=theme.TEXT_MUTED, anchor="w",
                      command=self.app.show_dashboard).pack(side="left")

        title_row = ctk.CTkFrame(scroll, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(title_row, text="Devices", font=theme.h1(), text_color=theme.TEXT).pack(side="left")
        self.refresh_btn = ctk.CTkButton(title_row, text="🔄 Refresh", width=100, height=32, corner_radius=8,
                                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                          font=theme.small(), command=self.refresh_devices)
        self.refresh_btn.pack(side="right")
        ctk.CTkLabel(scroll, text="Every device you've hosted from on this account.",
                     font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 20))

        self.devices_container = ctk.CTkFrame(scroll, fg_color="transparent")
        self.devices_container.pack(fill="x")

        ctk.CTkLabel(self.devices_container, text="Loading devices…",
                     font=theme.body(), text_color=theme.TEXT_MUTED).pack(pady=20)

        self.refresh_devices()

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
            for device_id, ip, port, session_type, device_name in local_link.discover_local_hosts():
                if device_id in merged:
                    merged[device_id]["local"] = True
                    merged[device_id]["local_target"] = (ip, port)
                    merged[device_id]["session_type"] = session_type
                else:
                    merged[device_id] = {
                        "device_id": device_id, "name": device_name, "status": "local_only",
                        "session_type": session_type, "local": True, "local_target": (ip, port),
                    }
        except Exception:
            pass

        self.after(0, lambda: self._render_devices(list(merged.values())))

    def _render_devices(self, devices):
        # Scheduled via self.after() from a background thread (_load_devices), which
        # can easily still be in flight after the user has already navigated to a
        # different view, destroying this one.
        if not self.winfo_exists():
            return
        self.refresh_btn.configure(state="normal", text="🔄 Refresh")
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
                                         text_color="white", font=theme.small(), command=_connect)
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
