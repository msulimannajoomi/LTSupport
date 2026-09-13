import threading
import customtkinter as ctk

import theme
from api_client import ApiError


class LoginView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        # "admin" (the org's own Organization ID + password, exactly as always) or
        # "member" (RBAC team-member login, username + password -- see
        # api_client.py's login_member). "member" is the default now -- day-to-day
        # hosting/joining is done by team members; the org's own admin login is only
        # for oversight/billing/user management, so it's the one someone opts into
        # rather than lands on by default.
        self.login_mode = "member"

        card = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=16, width=420)
        card.place(relx=0.5, rely=0.5, anchor="center")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=48, pady=48)

        ctk.CTkLabel(inner, text="LTSupport", font=theme.h1(), text_color=theme.ACCENT).pack(anchor="w")
        ctk.CTkLabel(inner, text="Sign in to manage and access your devices", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 12))

        # A lightweight, unauthenticated reachability check -- lets someone tell "the
        # server is down" apart from "my password is wrong" before even submitting the
        # form, and gives an explicit way to retry it without restarting the app.
        status_row = ctk.CTkFrame(inner, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 16))
        self.conn_dot = ctk.CTkLabel(status_row, text="●", text_color=theme.TEXT_MUTED, font=theme.body())
        self.conn_dot.pack(side="left", padx=(0, 6))
        self.conn_label = ctk.CTkLabel(status_row, text="Checking server…", font=theme.small(),
                                        text_color=theme.TEXT_MUTED)
        self.conn_label.pack(side="left")
        self.retry_btn = ctk.CTkButton(status_row, text="🔄 Retry", width=80, height=24, corner_radius=6,
                                        fg_color=theme.CARD_HOVER, hover_color=theme.BORDER, text_color=theme.TEXT,
                                        font=theme.small(), command=self.check_connection)
        self.retry_btn.pack(side="right")

        # Purely additive tab -- "Organization Admin" is the default, pre-existing
        # login (Organization ID + password); "Team Member" is the new RBAC login
        # (username + password, see api_client.py's login_member). Both share the one
        # entry field below, just relabeled, to avoid duplicating the whole form.
        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.pack(fill="x", pady=(0, 12))
        self.admin_mode_btn = ctk.CTkButton(mode_row, text="Organization Admin", width=155, height=28,
                                             corner_radius=6, font=theme.small(),
                                             command=lambda: self._set_login_mode("admin"))
        self.admin_mode_btn.pack(side="left", padx=(0, 6))
        self.member_mode_btn = ctk.CTkButton(mode_row, text="Team Member", width=155, height=28,
                                              corner_radius=6, font=theme.small(),
                                              command=lambda: self._set_login_mode("member"))
        self.member_mode_btn.pack(side="left")

        self.id_label = ctk.CTkLabel(inner, text="ORGANIZATION ID", font=theme.small(), text_color=theme.TEXT_MUTED)
        self.id_label.pack(anchor="w")
        self.org_id_entry = ctk.CTkEntry(inner, width=320, height=42, corner_radius=8)
        self.org_id_entry.pack(pady=(4, 16))

        ctk.CTkLabel(inner, text="PASSWORD", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        password_row = ctk.CTkFrame(inner, fg_color="transparent")
        password_row.pack(pady=(4, 8))
        self.password_entry = ctk.CTkEntry(password_row, width=280, height=42, corner_radius=8, show="•")
        self.password_entry.pack(side="left")
        self.password_entry.bind("<Return>", lambda e: self.submit())
        self.show_password_btn = ctk.CTkButton(password_row, text="👁", width=36, height=42, corner_radius=8,
                                                fg_color=theme.CARD_HOVER, hover_color=theme.BORDER,
                                                text_color=theme.TEXT, command=self.toggle_password_visibility)
        self.show_password_btn.pack(side="left", padx=(6, 0))

        self.error_label = ctk.CTkLabel(inner, text="", text_color=theme.DANGER, font=theme.small())
        self.error_label.pack(anchor="w", pady=(0, 8))

        self.submit_btn = ctk.CTkButton(inner, text="Log In", width=320, height=42, corner_radius=8,
                                         fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                         text_color="#0f172a", font=theme.h3(), command=self.submit)
        self.submit_btn.pack(pady=(12, 20))

        bottom = ctk.CTkFrame(inner, fg_color="transparent")
        bottom.pack()
        ctk.CTkLabel(bottom, text="Don't have an account?", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(side="left", padx=(0, 6))
        link = ctk.CTkLabel(bottom, text="Create one", font=theme.body(), text_color=theme.ACCENT, cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: self.app.show_signup())

        self._set_login_mode("member")
        self.check_connection()

    def _set_login_mode(self, mode):
        self.login_mode = mode
        self.error_label.configure(text="")
        if mode == "admin":
            self.id_label.configure(text="ORGANIZATION ID")
            self.admin_mode_btn.configure(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                           text_color="#0f172a")
            self.member_mode_btn.configure(fg_color=theme.CARD_HOVER, hover_color=theme.BORDER,
                                            text_color=theme.TEXT)
        else:
            self.id_label.configure(text="USERNAME")
            self.member_mode_btn.configure(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                            text_color="#0f172a")
            self.admin_mode_btn.configure(fg_color=theme.CARD_HOVER, hover_color=theme.BORDER,
                                           text_color=theme.TEXT)

    def toggle_password_visibility(self):
        if self.password_entry.cget("show") == "":
            self.password_entry.configure(show="•")
            self.show_password_btn.configure(text="👁")
        else:
            self.password_entry.configure(show="")
            self.show_password_btn.configure(text="🙈")

    def check_connection(self):
        self.conn_dot.configure(text_color=theme.TEXT_MUTED)
        self.conn_label.configure(text="Checking server…")
        self.retry_btn.configure(state="disabled")
        threading.Thread(target=self._do_check_connection, daemon=True).start()

    def _do_check_connection(self):
        ok = self.app.api.check_connection()
        self.after(0, lambda: self._apply_connection_status(ok))

    def _apply_connection_status(self, ok):
        # Reached via self.after() from a background thread -- the user can navigate to
        # Signup (destroying this view) while a check is still in flight.
        if not self.winfo_exists():
            return
        self.retry_btn.configure(state="normal")
        if ok:
            self.conn_dot.configure(text_color=theme.SUCCESS)
            self.conn_label.configure(text="Server reachable")
        else:
            self.conn_dot.configure(text_color=theme.DANGER)
            self.conn_label.configure(text="Server unreachable")

    def submit(self):
        org_id = self.org_id_entry.get().strip()
        password = self.password_entry.get()
        if not org_id or not password:
            field = "Organization ID" if self.login_mode == "admin" else "Username"
            self.error_label.configure(text=f"Please enter both your {field} and password.")
            return
        self.submit_btn.configure(state="disabled", text="Signing in...")
        self.error_label.configure(text="")
        threading.Thread(target=self._do_login, args=(org_id, password), daemon=True).start()

    def _do_login(self, org_id, password):
        try:
            if self.login_mode == "member":
                self.app.api.login_member(org_id, password)
            else:
                self.app.api.login(org_id, password)
            self.after(0, self.app.show_dashboard)
        except ApiError as e:
            self.after(0, lambda: self._fail(str(e)))

    def _fail(self, message):
        # Reached via self.after() from a background thread (_do_login) -- the user can
        # click "Create one" (switching to Signup, which destroys this view) while a
        # login attempt is still in flight.
        if not self.winfo_exists():
            return
        self.error_label.configure(text=message)
        self.submit_btn.configure(state="normal", text="Log In")
