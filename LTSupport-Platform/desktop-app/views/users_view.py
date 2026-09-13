import threading
import customtkinter as ctk

import theme
from api_client import ApiError


class UsersView(ctk.CTkFrame):
    """RBAC user management -- admin-only (see dashboard_view.py, which only shows the
    "Users" button for role == "admin"; the backend enforces this too, on every
    endpoint this view calls, so reaching this view without being an admin still
    couldn't do anything). Manages team-member logins only -- the org's own original
    Organization ID + password login isn't a "member" and isn't listed or editable
    here at all. Every team member created here is a "normal"-role login: the one
    that can actually host and take full control -- your own admin login already
    handles all oversight/viewing itself (see relay.py's _handle_observer), so
    there's nothing to choose here."""

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

        ctk.CTkLabel(scroll, text="Team Members", font=theme.h1(), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(scroll, text="Logins that can host and take full control -- separate from your own "
                                   "Organization ID login, which never hosts or takes control itself and "
                                   "isn't listed here. Your own login can watch any of these devices at "
                                   "any time, live sessions included, without disturbing them.",
                     font=theme.body(), text_color=theme.TEXT_MUTED, wraplength=900,
                     justify="left").pack(anchor="w", pady=(4, 24))

        add_card = ctk.CTkFrame(scroll, fg_color=theme.CARD, corner_radius=12)
        add_card.pack(fill="x", pady=(0, 16))
        add_inner = ctk.CTkFrame(add_card, fg_color="transparent")
        add_inner.pack(fill="x", padx=24, pady=20)
        ctk.CTkLabel(add_inner, text="Add a Team Member", font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")

        form_row = ctk.CTkFrame(add_inner, fg_color="transparent")
        form_row.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(form_row, text="USERNAME", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.username_entry = ctk.CTkEntry(form_row, width=220, height=38, corner_radius=8)
        self.username_entry.pack(anchor="w", pady=(4, 12))

        ctk.CTkLabel(form_row, text="PASSWORD", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.password_entry = ctk.CTkEntry(form_row, width=220, height=38, corner_radius=8, show="•")
        self.password_entry.pack(anchor="w", pady=(4, 16))

        self.add_status_label = ctk.CTkLabel(add_inner, text="", font=theme.small(), text_color=theme.DANGER,
                                              wraplength=700, justify="left")
        self.add_status_label.pack(anchor="w", pady=(0, 8))

        self.add_btn = ctk.CTkButton(add_inner, text="Add Team Member", height=40, corner_radius=8,
                                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                      text_color="#0f172a", font=theme.h3(), command=self._do_add)
        self.add_btn.pack(anchor="w")

        list_header = ctk.CTkFrame(scroll, fg_color="transparent")
        list_header.pack(fill="x", pady=(10, 4))
        ctk.CTkLabel(list_header, text="EXISTING TEAM MEMBERS", font=theme.h3(),
                     text_color=theme.TEXT_MUTED).pack(side="left")

        self.list_container = ctk.CTkFrame(scroll, fg_color="transparent")
        self.list_container.pack(fill="x")
        ctk.CTkLabel(self.list_container, text="Loading…", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(pady=20)

        self.reload()

    def reload(self):
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            users = self.app.api.list_users()
        except ApiError:
            users = []
        self.after(0, lambda: self._render_list(users))

    def _render_list(self, users):
        if not self.winfo_exists():
            return
        for child in self.list_container.winfo_children():
            child.destroy()

        if not users:
            ctk.CTkLabel(self.list_container, text="No team members yet -- add one above.",
                         font=theme.body(), text_color=theme.TEXT_MUTED).pack(pady=20)
            return

        for u in users:
            row = ctk.CTkFrame(self.list_container, fg_color=theme.CARD, corner_radius=10)
            row.pack(fill="x", pady=6)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=20, pady=14)

            text_col = ctk.CTkFrame(inner, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(text_col, text=u["username"], font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")
            ctk.CTkLabel(text_col, text="Can host and take full control", font=theme.small(),
                         text_color=theme.ACCENT).pack(anchor="w")

            ctk.CTkButton(inner, text="Remove", width=90, height=32, corner_radius=8,
                          fg_color=theme.BG, hover_color=theme.DANGER, text_color=theme.TEXT_MUTED,
                          font=theme.small(),
                          command=lambda mid=u["member_id"]: self._remove(mid)).pack(side="right")

    def _do_add(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        if len(username) < 3:
            self.add_status_label.configure(text="Username must be at least 3 characters.")
            return
        if len(password) < 6:
            self.add_status_label.configure(text="Password must be at least 6 characters.")
            return
        self.add_btn.configure(state="disabled", text="Adding…")
        self.add_status_label.configure(text="")
        threading.Thread(target=self._do_add_bg, args=(username, password), daemon=True).start()

    def _do_add_bg(self, username, password):
        try:
            self.app.api.create_user(username, password)
            self.after(0, self._add_done)
        except ApiError as e:
            self.after(0, lambda: self._add_failed(str(e)))

    def _add_done(self):
        if not self.winfo_exists():
            return
        self.add_btn.configure(state="normal", text="Add Team Member")
        self.username_entry.delete(0, "end")
        self.password_entry.delete(0, "end")
        self.reload()

    def _add_failed(self, message):
        if not self.winfo_exists():
            return
        self.add_btn.configure(state="normal", text="Add Team Member")
        self.add_status_label.configure(text=message)

    def _remove(self, member_id):
        threading.Thread(target=self._do_remove, args=(member_id,), daemon=True).start()

    def _do_remove(self, member_id):
        try:
            self.app.api.remove_user(member_id)
        except ApiError:
            pass
        self.reload()
