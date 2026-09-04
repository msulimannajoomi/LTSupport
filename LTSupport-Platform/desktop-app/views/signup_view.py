import threading
import customtkinter as ctk

import theme
from api_client import ApiError
from dialogs import show_copyable_id


class SignupView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app

        card = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=16, width=440)
        card.place(relx=0.5, rely=0.5, anchor="center")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=48, pady=40)

        ctk.CTkLabel(inner, text="Create Account", font=theme.h1(), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(inner, text="Set up your organization's LTSupport account", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 24))

        ctk.CTkLabel(inner, text="ORGANIZATION NAME", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.org_entry = ctk.CTkEntry(inner, width=340, height=40, corner_radius=8)
        self.org_entry.pack(pady=(4, 12))

        ctk.CTkLabel(inner, text="EMAIL", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.email_entry = ctk.CTkEntry(inner, width=340, height=40, corner_radius=8)
        self.email_entry.pack(pady=(4, 12))

        ctk.CTkLabel(inner, text="PHONE NUMBER", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.phone_entry = ctk.CTkEntry(inner, width=340, height=40, corner_radius=8, placeholder_text="+92 3XX XXXXXXX")
        self.phone_entry.pack(pady=(4, 12))

        ctk.CTkLabel(inner, text="PASSWORD", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.password_entry = ctk.CTkEntry(inner, width=340, height=40, corner_radius=8, show="•")
        self.password_entry.pack(pady=(4, 12))

        ctk.CTkLabel(inner, text="CONFIRM PASSWORD", font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.confirm_entry = ctk.CTkEntry(inner, width=340, height=40, corner_radius=8, show="•")
        self.confirm_entry.pack(pady=(4, 12))
        self.confirm_entry.bind("<Return>", lambda e: self.submit())

        ctk.CTkLabel(inner, text="Every new account starts on a free Trial plan (2-minute "
                                  "sessions). You can upgrade to Prepaid anytime from Billing "
                                  "inside the app.",
                     font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=340,
                     justify="left").pack(anchor="w", pady=(0, 8))

        self.error_label = ctk.CTkLabel(inner, text="", text_color=theme.DANGER, font=theme.small(),
                                         wraplength=340, justify="left")
        self.error_label.pack(anchor="w", pady=(0, 8))

        self.submit_btn = ctk.CTkButton(inner, text="Create Account", width=340, height=42, corner_radius=8,
                                         fg_color=theme.SUCCESS, hover_color="#22c55e",
                                         text_color="#0f172a", font=theme.h3(), command=self.submit)
        self.submit_btn.pack(pady=(12, 20))

        bottom = ctk.CTkFrame(inner, fg_color="transparent")
        bottom.pack()
        ctk.CTkLabel(bottom, text="Already have an account?", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(side="left", padx=(0, 6))
        link = ctk.CTkLabel(bottom, text="Log in", font=theme.body(), text_color=theme.ACCENT, cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: self.app.show_login())

    def submit(self):
        org_name = self.org_entry.get().strip()
        email = self.email_entry.get().strip()
        phone = self.phone_entry.get().strip()
        password = self.password_entry.get()
        confirm = self.confirm_entry.get()

        if len(org_name) < 2:
            self.error_label.configure(text="Please enter your organization name.")
            return
        if "@" not in email or "." not in email:
            self.error_label.configure(text="Please enter a valid email address.")
            return
        if len(phone) < 7:
            self.error_label.configure(text="Please enter a valid phone number.")
            return
        if len(password) < 6:
            self.error_label.configure(text="Password must be at least 6 characters.")
            return
        if password != confirm:
            self.error_label.configure(text="Passwords do not match.")
            return

        self.submit_btn.configure(state="disabled", text="Creating...")
        self.error_label.configure(text="")
        threading.Thread(target=self._do_signup, args=(org_name, email, phone, password), daemon=True).start()

    def _do_signup(self, org_name, email, phone, password):
        try:
            org_id = self.app.api.signup(org_name, email, phone, password)
            self.after(0, lambda: self._success(org_id))
        except ApiError as e:
            self.after(0, lambda: self._fail(str(e)))

    def _success(self, org_id):
        show_copyable_id(
            self.app, "Account Created",
            "Your Organization ID — save it, it's what you'll use to log in from now on:",
            org_id,
            note_text="This is not emailed to you. If you lose it, you'll need to contact "
                       "support to recover access to this account.",
        )
        self.app.show_dashboard()

    def _fail(self, message):
        self.error_label.configure(text=message)
        self.submit_btn.configure(state="normal", text="Create Account")
