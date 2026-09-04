import threading
import customtkinter as ctk

import theme
from api_client import ApiError


class LoginView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app

        card = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=16, width=420)
        card.place(relx=0.5, rely=0.5, anchor="center")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=48, pady=48)

        ctk.CTkLabel(inner, text="LTSupport", font=theme.h1(), text_color=theme.ACCENT).pack(anchor="w")
        ctk.CTkLabel(inner, text="Sign in to manage and access your devices", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 28))

        ctk.CTkLabel(inner, text="ORGANIZATION ID", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.org_id_entry = ctk.CTkEntry(inner, width=320, height=42, corner_radius=8)
        self.org_id_entry.pack(pady=(4, 16))

        ctk.CTkLabel(inner, text="PASSWORD", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.password_entry = ctk.CTkEntry(inner, width=320, height=42, corner_radius=8, show="•")
        self.password_entry.pack(pady=(4, 8))
        self.password_entry.bind("<Return>", lambda e: self.submit())

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

    def submit(self):
        org_id = self.org_id_entry.get().strip()
        password = self.password_entry.get()
        if not org_id or not password:
            self.error_label.configure(text="Please enter both your Organization ID and password.")
            return
        self.submit_btn.configure(state="disabled", text="Signing in...")
        self.error_label.configure(text="")
        threading.Thread(target=self._do_login, args=(org_id, password), daemon=True).start()

    def _do_login(self, org_id, password):
        try:
            self.app.api.login(org_id, password)
            self.after(0, self.app.show_dashboard)
        except ApiError as e:
            self.after(0, lambda: self._fail(str(e)))

    def _fail(self, message):
        self.error_label.configure(text=message)
        self.submit_btn.configure(state="normal", text="Log In")
