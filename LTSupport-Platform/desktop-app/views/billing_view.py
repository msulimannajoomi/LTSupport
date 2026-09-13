import threading
import webbrowser
import customtkinter as ctk

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

        title_row = ctk.CTkFrame(scroll, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(title_row, text="Billing", font=theme.h1(), text_color=theme.TEXT).pack(side="left")
        # Paid via Paddle in a separate browser window -- balance only updates once
        # Paddle's webhook confirms payment, sometime after checkout completes, not
        # the instant this window regains focus. This is the explicit way to check
        # again without leaving and reopening the whole Billing screen.
        self.refresh_btn = ctk.CTkButton(title_row, text="🔄 Refresh", width=100, height=32, corner_radius=8,
                                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                          font=theme.small(), command=self.reload)
        self.refresh_btn.pack(side="right")
        ctk.CTkLabel(scroll, text="Your plan, balance, usage, and payment history.", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 24))

        self.content = ctk.CTkFrame(scroll, fg_color="transparent")
        self.content.pack(fill="x")

        self._payments = []
        ctk.CTkLabel(self.content, text="Loading…", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(pady=20)

        self.reload()

    def reload(self):
        self.refresh_btn.configure(state="disabled", text="Refreshing…")
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            self.app.api.refresh_account()
        except ApiError:
            pass
        try:
            self._payments = self.app.api.list_payments()
        except ApiError:
            self._payments = []
        self.after(0, self._render)

    def _render(self):
        # Scheduled via self.after() from a background thread (_load) -- the user may
        # have already navigated back to the Dashboard (main.py's _swap destroys this
        # whole view when switching) by the time it actually runs.
        if not self.winfo_exists():
            return
        self.refresh_btn.configure(state="normal", text="🔄 Refresh")
        for child in self.content.winfo_children():
            child.destroy()

        api = self.app.api

        if api.blocked:
            self._card(
                theme.DANGER,
                "ACCOUNT BLOCKED",
                "Hosting and joining are disabled for this organization, including "
                "Interview Mode sessions, until this is lifted. Contact support.",
                text_color="white",
            )
            return

        if api.account_type == "trial":
            self._plan_card("TRIAL PLAN", theme.WARNING, [
                ("Sessions run so far", f"{api.session_count}"),
                ("Session length limit", "10 minutes (Normal Mode)"),
                ("Sessions per day", "3"),
            ])
            self._buy_hours_card()
        elif api.account_type == "prepaid":
            self._plan_card("PREPAID PLAN", theme.ACCENT, [
                ("Balance remaining", f"PKR {api.balance_rupees:,.0f}"),
                ("Sessions run so far", f"{api.session_count}"),
                ("Total minutes used", f"{api.total_minutes_used:.1f}"),
                ("Free per session", "First 10 minutes, then PKR 5,000/hour (any part of an hour"
                                     " rounds up to a full hour)"),
            ])
            self._buy_hours_card()
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

        self._render_payment_history()

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

    def _buy_hours_card(self):
        card = ctk.CTkFrame(self.content, fg_color=theme.CARD, corner_radius=12)
        card.pack(fill="x", pady=(0, 16))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)

        # Still just one click through to Paddle's own hosted checkout either way --
        # no separate free "upgrade" step and no quantity field of our own (Paddle's
        # checkout handles quantity/amount: PKR 5,000/hour, minimum 4 hours). A trial
        # account is upgraded to prepaid automatically the moment Paddle confirms
        # payment (see the backend's webhook) -- never before, and never for free.
        # Only the copy/heading differs so a trial account sees what upgrading
        # actually gets them before clicking.
        is_trial = self.app.api.account_type == "trial"
        heading = "Upgrade to Prepaid" if is_trial else "Buy More Hours"
        self._buy_label = "Upgrade to Prepaid" if is_trial else "Buy Hours"
        ctk.CTkLabel(inner, text=heading, font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")

        if is_trial:
            ctk.CTkLabel(inner, text="Prepaid gets you:", font=theme.body(),
                         text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(10, 6))
            for line in [
                "No daily session limit -- Trial is capped at 3 sessions/day",
                "First 10 minutes of every session still free",
                "Only PKR 5,000/hour beyond that, billed by the hour -- pay only for what you use",
                "Upgrade happens automatically the moment you pay -- no separate step",
            ]:
                row = ctk.CTkFrame(inner, fg_color="transparent")
                row.pack(fill="x", pady=(2, 0))
                ctk.CTkLabel(row, text="✓", font=theme.body(), text_color=theme.SUCCESS, width=20).pack(side="left")
                ctk.CTkLabel(row, text=line, font=theme.body(), text_color=theme.TEXT, wraplength=650,
                             justify="left").pack(side="left")
            ctk.CTkLabel(inner, text="Click below to pick how many hours and pay -- Paddle's own "
                                      "checkout page handles the amount.",
                         font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=700,
                         justify="left").pack(anchor="w", pady=(12, 16))
        else:
            ctk.CTkLabel(inner, text="PKR 5,000 per hour, minimum 4 hours -- pick the exact amount in "
                                      "Paddle's own checkout page after clicking below.",
                         font=theme.body(), text_color=theme.TEXT_MUTED, wraplength=700,
                         justify="left").pack(anchor="w", pady=(6, 16))

        self.buy_btn = ctk.CTkButton(inner, text=self._buy_label, height=40, corner_radius=8,
                                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                      text_color="#0f172a", font=theme.h3(), command=self._do_buy)
        self.buy_btn.pack(anchor="w")

        self.buy_status_label = ctk.CTkLabel(inner, text="", font=theme.small(), text_color=theme.TEXT_MUTED,
                                              wraplength=700, justify="left")
        self.buy_status_label.pack(anchor="w", pady=(10, 0))

        contact = self.app.api.upgrade_contact_number or "support"
        ctk.CTkLabel(inner, text=f"Want Postpaid instead (unlimited, billed separately), or trouble "
                                  f"paying? Call {contact}.",
                     font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=700,
                     justify="left").pack(anchor="w", pady=(12, 0))

    def _do_buy(self):
        self.buy_btn.configure(state="disabled", text="Starting…")
        self.buy_status_label.configure(text="", text_color=theme.TEXT_MUTED)
        threading.Thread(target=self._do_buy_bg, daemon=True).start()

    def _do_buy_bg(self):
        try:
            checkout_url = self.app.api.create_checkout()
            self.after(0, lambda: self._checkout_opened(checkout_url))
        except ApiError as e:
            self.after(0, lambda: self._buy_failed(str(e)))

    def _checkout_opened(self, checkout_url):
        if not self.winfo_exists():
            return
        webbrowser.open(checkout_url)
        self.buy_btn.configure(state="normal", text=self._buy_label)
        self.buy_status_label.configure(
            text="Complete your payment in the browser window that just opened. Your balance "
                 "updates automatically once the payment is confirmed -- click 🔄 Refresh above "
                 "afterward to see it.",
            text_color=theme.TEXT)

    def _buy_failed(self, message):
        if not self.winfo_exists():
            return
        self.buy_btn.configure(state="normal", text=self._buy_label)
        self.buy_status_label.configure(text=message, text_color=theme.DANGER)

    def _render_payment_history(self):
        card = ctk.CTkFrame(self.content, fg_color=theme.CARD, corner_radius=12)
        card.pack(fill="x", pady=(0, 16))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)
        ctk.CTkLabel(inner, text="Payment History", font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")

        if not self._payments:
            ctk.CTkLabel(inner, text="No payments yet.", font=theme.body(),
                         text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(10, 0))
            return

        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(12, 4))
        for text, side in (("Date", "left"), ("Hours", "left"), ("Amount", "right"), ("Status", "right")):
            ctk.CTkLabel(header, text=text, font=theme.small(), text_color=theme.TEXT_MUTED).pack(
                side=side, padx=(0, 20) if side == "left" else (20, 0))

        for payment in self._payments:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(6, 0))
            date = (payment.get("created_at") or "")[:16].replace("T", " ")
            hours = payment.get("quantity", 0)
            amount_text = "—"
            try:
                amount_text = f"{float(payment.get('amount')) / 100:.2f} {payment.get('currency', '')}".strip()
            except (TypeError, ValueError):
                pass
            status = (payment.get("status") or "").capitalize()
            ctk.CTkLabel(row, text=date, font=theme.small(), text_color=theme.TEXT).pack(side="left")
            ctk.CTkLabel(row, text=f"{hours}h", font=theme.small(), text_color=theme.TEXT).pack(
                side="left", padx=(20, 0))
            ctk.CTkLabel(row, text=status, font=theme.small(), text_color=theme.SUCCESS).pack(side="right")
            ctk.CTkLabel(row, text=amount_text, font=theme.small(), text_color=theme.TEXT).pack(
                side="right", padx=(0, 20))
