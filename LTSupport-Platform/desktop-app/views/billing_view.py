import threading
import webbrowser
from urllib.parse import quote
import customtkinter as ctk

import config
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
        ctk.CTkButton(header, text="←  Back to Dashboard", fg_color="transparent", hover_color=theme.CARD_HOVER,
                      text_color=theme.TEXT_MUTED, anchor="w",
                      command=self.app.show_dashboard).pack(side="left")

        title_row = ctk.CTkFrame(scroll, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(title_row, text="Billing", font=theme.h1(), text_color=theme.TEXT).pack(side="left")
        # Paid via Stripe in a separate browser window -- balance only updates once
        # Stripe's webhook confirms payment, sometime after checkout completes, not
        # the instant this window regains focus. This is the explicit way to check
        # again without leaving and reopening the whole Billing screen.
        self.refresh_btn = ctk.CTkButton(title_row, text="🔄 Refresh", width=100, height=32, corner_radius=8,
                                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                          border_width=1, border_color=theme.BORDER,
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
            self._banner(
                theme.DANGER,
                "⛔  ACCOUNT BLOCKED",
                "Hosting and joining are disabled for this organization, including "
                "Interview Mode sessions, until this is lifted. Contact support.",
            )
            return

        if api.account_type == "trial":
            self._plan_header("TRIAL PLAN", theme.WARNING)
            self._stat_grid([
                ("Sessions run so far", f"{api.session_count}"),
                ("Session length limit", "10 min"),
                ("Sessions per day", "3"),
            ])
            self._buy_hours_card()
        elif api.account_type == "prepaid":
            self._plan_header("PREPAID PLAN", theme.ACCENT)
            self._hero_balance(api)
            self._stat_grid([
                ("Sessions run so far", f"{api.session_count}"),
                ("Total minutes used", f"{api.total_minutes_used:.1f}"),
                ("Free per session", "First 10 min, then $5.00/hr"),
            ])
            self._buy_hours_card()
        else:
            self._plan_header("POSTPAID PLAN", theme.SUCCESS)
            self._stat_grid([
                ("Sessions run so far", f"{api.session_count}"),
                ("Total minutes used", f"{api.total_minutes_used:.1f}"),
                ("Billing", "Handled separately by your account manager"),
            ])

        if api.interview_session_count:
            self._section_card("INTERVIEW MODE USAGE (separate from the above)", [
                ("Interview sessions", f"{api.interview_session_count}"),
                ("Interview minutes", f"{api.interview_total_minutes_used:.1f}"),
            ])

        self._render_payment_history()

    # ---- small building blocks -------------------------------------------------

    def _card_frame(self, pady=(0, 16)):
        card = ctk.CTkFrame(self.content, fg_color=theme.CARD, corner_radius=14,
                             border_width=1, border_color=theme.BORDER)
        card.pack(fill="x", pady=pady)
        return card

    def _pill(self, parent, text, color):
        pill = ctk.CTkFrame(parent, fg_color=color, corner_radius=999)
        ctk.CTkLabel(pill, text=text, font=theme.small(), text_color="white").pack(padx=14, pady=5)
        return pill

    def _banner(self, color, title, body_text):
        card = ctk.CTkFrame(self.content, fg_color=color, corner_radius=14)
        card.pack(fill="x", pady=(0, 16))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)
        ctk.CTkLabel(inner, text=title, font=theme.h3(), text_color="white").pack(anchor="w")
        ctk.CTkLabel(inner, text=body_text, font=theme.small(), text_color="white",
                     wraplength=700, justify="left").pack(anchor="w", pady=(4, 0))

    def _plan_header(self, title, color):
        row = ctk.CTkFrame(self.content, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12))
        self._pill(row, title, color).pack(side="left")

    def _hero_balance(self, api):
        """The one number that matters most on this whole screen -- shown big and bold
        at the top of its own card, with hours remaining right underneath it, instead
        of buried as just another row alongside session counts and free-minute rules."""
        card = self._card_frame()
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=28, pady=24)
        ctk.CTkLabel(inner, text="BALANCE REMAINING", font=theme.small(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(inner, text=f"${api.balance_cents / 100:,.2f}",
                     font=(theme.FONT, 40, "bold"), text_color=theme.TEXT).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(inner, text=f"≈ {api.hours_remaining:.2f} hours remaining at $5.00/hour",
                     font=theme.body(), text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 0))
        divider = ctk.CTkFrame(inner, fg_color=theme.BORDER, height=1)
        divider.pack(fill="x", pady=(18, 14))
        ctk.CTkLabel(inner, text=f"{api.hours_consumed:.2f}h consumed so far",
                     font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")

    def _stat_grid(self, rows):
        card = self._card_frame()
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=18)
        for i, (label, value) in enumerate(rows):
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(0 if i == 0 else 10, 0))
            ctk.CTkLabel(row, text=label, font=theme.body(), text_color=theme.TEXT_MUTED).pack(side="left")
            ctk.CTkLabel(row, text=value, font=theme.h3(), text_color=theme.TEXT,
                         wraplength=380, justify="right").pack(side="right")

    def _section_card(self, title, rows):
        card = self._card_frame()
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)
        ctk.CTkLabel(inner, text=title, font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")
        for label, value in rows:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(10, 0))
            ctk.CTkLabel(row, text=label, font=theme.body(), text_color=theme.TEXT_MUTED).pack(side="left")
            ctk.CTkLabel(row, text=value, font=theme.h3(), text_color=theme.TEXT).pack(side="right")

    def _buy_hours_card(self):
        card = self._card_frame()
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)

        # Still just one click through to Stripe's own hosted checkout either way --
        # no separate free "upgrade" step and no quantity field of our own (Stripe's
        # checkout handles quantity/amount: $5/hour, minimum 1 hour). A trial
        # account is upgraded to prepaid automatically the moment Stripe confirms
        # payment (see the backend's webhook) -- never before, and never for free.
        # Only the copy/heading differs so a trial account sees what upgrading
        # actually gets them before clicking.
        is_trial = self.app.api.account_type == "trial"
        heading = "💳  Upgrade to Prepaid" if is_trial else "💳  Buy More Hours"
        self._buy_label = "Upgrade to Prepaid" if is_trial else "Buy Hours"
        ctk.CTkLabel(inner, text=heading, font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")

        if is_trial:
            ctk.CTkLabel(inner, text="Prepaid gets you:", font=theme.body(),
                         text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(10, 6))
            for line in [
                "No daily session limit -- Trial is capped at 3 sessions/day",
                "First 10 minutes of every session still free",
                "Only $5.00/hour beyond that, billed by the hour -- pay only for what you use",
            ]:
                row = ctk.CTkFrame(inner, fg_color="transparent")
                row.pack(fill="x", pady=(2, 0))
                ctk.CTkLabel(row, text="✓", font=theme.body(), text_color=theme.SUCCESS, width=20).pack(side="left")
                ctk.CTkLabel(row, text=line, font=theme.body(), text_color=theme.TEXT, wraplength=650,
                             justify="left").pack(side="left")
            # Matches the interim _do_buy swap (see its own comment) -- update this
            # copy back to "Stripe's own checkout page handles the amount" once that
            # swap is reverted.
            ctk.CTkLabel(inner, text="Click below and enter your email -- our team will reach out to "
                                      "get you upgraded.",
                         font=theme.small(), text_color=theme.TEXT_MUTED, wraplength=700,
                         justify="left").pack(anchor="w", pady=(12, 16))
        else:
            ctk.CTkLabel(inner, text="$5 per hour, one hour minimum. Click below and enter your email "
                                      "-- our team will reach out to get you set up.",
                         font=theme.body(), text_color=theme.TEXT_MUTED, wraplength=700,
                         justify="left").pack(anchor="w", pady=(6, 16))

        self.buy_btn = ctk.CTkButton(inner, text=self._buy_label, height=42, corner_radius=8,
                                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                      text_color="white", font=theme.h3(), command=self._do_buy)
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
        # INTERIM SWAP (2026-09-16): opens the request-upgrade.html page (a simple
        # "email us to upgrade" form) instead of starting a real Stripe checkout --
        # the live Stripe account is deployed and wired correctly, but still hasn't
        # completed its own account activation (business/banking/identity
        # verification, a Stripe-side process outside this codebase). Once that's
        # done, revert this method to what _do_buy_bg/_checkout_opened/_buy_failed
        # below still do (they're left in place, just unused, for exactly that).
        api = self.app.api
        url = (f"{config.PUBLIC_WEB_BASE_URL}/request-upgrade.html"
               f"?org_id={quote(api.org_id or '')}&org_name={quote(api.org_name or '')}")
        webbrowser.open(url)

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
        card = self._card_frame(pady=(0, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)
        ctk.CTkLabel(inner, text="Payment History", font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")

        if not self._payments:
            ctk.CTkLabel(inner, text="No payments yet.", font=theme.body(),
                         text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(10, 0))
            return

        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(14, 6))
        for text, side in (("Date", "left"), ("Hours", "left"), ("Amount", "right"), ("Status", "right")):
            ctk.CTkLabel(header, text=text, font=theme.small(), text_color=theme.TEXT_MUTED).pack(
                side=side, padx=(0, 20) if side == "left" else (20, 0))

        for i, payment in enumerate(self._payments):
            # Faint alternating row shading -- makes a longer history easy to scan
            # across without needing gridlines.
            row_bg = theme.CARD_HOVER if i % 2 == 0 else "transparent"
            row = ctk.CTkFrame(inner, fg_color=row_bg, corner_radius=6)
            row.pack(fill="x", pady=(2, 0))
            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=8, pady=6)
            date = (payment.get("created_at") or "")[:16].replace("T", " ")
            hours = payment.get("quantity", 0)
            amount_text = "—"
            try:
                amount_text = f"{float(payment.get('amount')) / 100:.2f} {payment.get('currency', '')}".strip()
            except (TypeError, ValueError):
                pass
            status = (payment.get("status") or "").capitalize()
            ctk.CTkLabel(row_inner, text=date, font=theme.small(), text_color=theme.TEXT).pack(side="left")
            ctk.CTkLabel(row_inner, text=f"{hours}h", font=theme.small(), text_color=theme.TEXT).pack(
                side="left", padx=(20, 0))
            ctk.CTkLabel(row_inner, text=status, font=theme.small(), text_color=theme.SUCCESS).pack(side="right")
            ctk.CTkLabel(row_inner, text=amount_text, font=theme.small(), text_color=theme.TEXT).pack(
                side="right", padx=(0, 20))
