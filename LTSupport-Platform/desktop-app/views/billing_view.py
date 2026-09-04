import threading
import customtkinter as ctk
from tkinter import messagebox

import theme
from api_client import ApiError


class BillingView(ctk.CTkFrame):
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

        ctk.CTkLabel(scroll, text="Billing", font=theme.h1(), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(scroll, text="Your plan, balance, and usage.", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 24))

        self.content = ctk.CTkFrame(scroll, fg_color="transparent")
        self.content.pack(fill="x")

        ctk.CTkLabel(self.content, text="Loading…", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(pady=20)

        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            self.app.api.refresh_account()
        except ApiError:
            pass
        self.after(0, self._render)

    def _render(self):
        for child in self.content.winfo_children():
            child.destroy()

        api = self.app.api

        if api.blocked:
            self._card(
                theme.DANGER,
                "ACCOUNT BLOCKED",
                "Hosting and joining are disabled for this organization, including "
                "Interview Mode sessions, until this is lifted. Contact support.",
            )
            return

        if api.account_type == "trial":
            self._plan_card("TRIAL PLAN", theme.WARNING, [
                ("Sessions run so far", f"{api.session_count}"),
                ("Session length limit", "2 minutes (Normal Mode)"),
            ])
            self._upgrade_card()
        elif api.account_type == "prepaid":
            self._plan_card("PREPAID PLAN", theme.ACCENT, [
                ("Balance remaining", f"{api.balance_minutes:.1f} minute(s)"),
                ("Sessions run so far", f"{api.session_count}"),
                ("Total minutes used", f"{api.total_minutes_used:.1f}"),
            ])
            self._topup_card()
        else:
            self._plan_card("POSTPAID PLAN", theme.SUCCESS, [
                ("Sessions run so far", f"{api.session_count}"),
                ("Total minutes used", f"{api.total_minutes_used:.1f}"),
                ("Billing", "Handled separately by your account manager"),
            ])

        if api.interview_session_count:
            self._plan_card("INTERVIEW MODE USAGE (separate from the above)", theme.TEXT_MUTED, [
                ("Interview sessions", f"{api.interview_session_count}"),
                ("Interview minutes", f"{api.interview_total_minutes_used:.1f}"),
            ], header_color=theme.TEXT)

    def _card(self, color, title, body_text, text_color="#0f172a"):
        card = ctk.CTkFrame(self.content, fg_color=color, corner_radius=12)
        card.pack(fill="x", pady=(0, 16))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)
        ctk.CTkLabel(inner, text=title, font=theme.h3(), text_color=text_color).pack(anchor="w")
        ctk.CTkLabel(inner, text=body_text, font=theme.small(), text_color=text_color,
                     wraplength=700, justify="left").pack(anchor="w", pady=(4, 0))

    def _plan_card(self, title, accent_color, rows, header_color=None):
        card = ctk.CTkFrame(self.content, fg_color=theme.CARD, corner_radius=12)
        card.pack(fill="x", pady=(0, 16))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)
        ctk.CTkLabel(inner, text=title, font=theme.h3(), text_color=header_color or accent_color).pack(anchor="w")
        for label, value in rows:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(10, 0))
            ctk.CTkLabel(row, text=label, font=theme.body(), text_color=theme.TEXT_MUTED).pack(side="left")
            ctk.CTkLabel(row, text=value, font=theme.h3(), text_color=theme.TEXT).pack(side="right")

    def _upgrade_card(self):
        card = ctk.CTkFrame(self.content, fg_color=theme.CARD, corner_radius=12)
        card.pack(fill="x", pady=(0, 16))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)

        ctk.CTkLabel(inner, text="Want longer sessions?", font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(inner, text="Trial sessions are capped at 2 minutes. Upgrade to Prepaid to pay "
                                  "as you go with a minutes balance instead — no more session cutoffs "
                                  "at 2 minutes.",
                     font=theme.body(), text_color=theme.TEXT_MUTED, wraplength=700,
                     justify="left").pack(anchor="w", pady=(6, 16))

        self.upgrade_btn = ctk.CTkButton(inner, text="Upgrade to Prepaid", height=40, corner_radius=8,
                                          fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                          text_color="#0f172a", font=theme.h3(),
                                          command=self._do_upgrade)
        self.upgrade_btn.pack(anchor="w")

        contact = self.app.api.upgrade_contact_number or "support"
        ctk.CTkLabel(inner, text=f"Want Postpaid instead (unlimited, billed separately)? "
                                  f"That's set up for you manually — call {contact}.",
                     font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=700,
                     justify="left").pack(anchor="w", pady=(16, 0))

    def _topup_card(self):
        contact = self.app.api.upgrade_contact_number or "support"
        self._card(
            theme.CARD_HOVER,
            "NEED MORE BALANCE?",
            f"Topping up isn't self-service yet — call {contact} to add minutes to this "
            f"account.",
            text_color=theme.TEXT,
        )

    def _do_upgrade(self):
        self.upgrade_btn.configure(state="disabled", text="Upgrading...")
        threading.Thread(target=self._do_upgrade_bg, daemon=True).start()

    def _do_upgrade_bg(self):
        try:
            self.app.api.upgrade_to_prepaid()
            self.after(0, self._upgrade_done)
        except ApiError as e:
            self.after(0, lambda: self._upgrade_failed(str(e)))

    def _upgrade_done(self):
        messagebox.showinfo("Upgraded", "This account is now on the Prepaid plan. "
                                         "Balance starts at 0 minutes -- call support to top up.")
        self._render()

    def _upgrade_failed(self, message):
        messagebox.showerror("Upgrade Failed", message)
        self.upgrade_btn.configure(state="normal", text="Upgrade to Prepaid")
