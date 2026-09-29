import os
import threading
import customtkinter as ctk

import applog
import theme
from api_client import ApiError


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
        ctk.CTkButton(header, text="📃 Devices", width=100, height=36, corner_radius=8,
                      fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                      command=app.show_devices).pack(side="right", padx=(0, 8))
        ctk.CTkButton(header, text="🪵 View Logs", width=100, height=36, corner_radius=8,
                      fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                      command=self._open_logs).pack(side="right", padx=(0, 8))

        self.plan_container = ctk.CTkFrame(scroll, fg_color="transparent")
        self.plan_container.pack(fill="x")
        self._render_plan_banner()
        threading.Thread(target=self._refresh_account, daemon=True).start()

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
                                           "Let someone else see and control this PC.",
                                           theme.SUCCESS, app.show_host, disabled=blocked, hover_color=theme.SUCCESS_HOVER)
            host_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

            join_card = self._join_card(cards, disabled=blocked)
            join_card.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

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
            text = f"PREPAID PLAN — ${api.balance_cents / 100:,.2f} ({api.hours_remaining:.2f}h) remaining."
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
                          hover_color=hover_color or color, text_color="white", font=theme.h3(),
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
            ctk.CTkLabel(inner, text="👁 Oversight access — watch any device's screen without "
                                      "disturbing a session already in progress there.",
                         font=theme.small(), text_color=theme.WARNING, wraplength=280,
                         justify="left").pack(anchor="w", pady=(4, 12))
        ctk.CTkLabel(inner, text="Ask them to click \"Host This Computer\" first, then enter the "
                                  "Device ID they're given below — or pick it from Devices.",
                     font=theme.body(), text_color=theme.TEXT_MUTED, wraplength=280,
                     justify="left").pack(anchor="w", pady=(8, 12))

        self.join_entry = ctk.CTkEntry(inner, placeholder_text="Device ID", height=38, corner_radius=8)
        self.join_entry.pack(fill="x", pady=(4, 12))
        self.join_entry.bind("<Return>", lambda e: self._join_manual())
        ctk.CTkButton(inner, text="Connect", height=38, corner_radius=8, fg_color=theme.ACCENT,
                      hover_color=theme.ACCENT_HOVER, text_color="white", font=theme.h3(),
                      command=self._join_manual).pack(fill="x")
        return frame

    def _join_manual(self):
        device_id = self.join_entry.get().strip()
        if device_id:
            self.app.open_viewer(device_id)
