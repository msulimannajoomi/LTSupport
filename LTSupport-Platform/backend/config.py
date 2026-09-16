import os

API_HOST = "0.0.0.0"
API_PORT = int(os.environ.get("LTSUPPORT_API_PORT", 8000))

RELAY_HOST = "0.0.0.0"
RELAY_PORT = int(os.environ.get("LTSUPPORT_RELAY_PORT", 7000))

# MongoDB Atlas connection. Must come from the environment -- there is deliberately no
# hardcoded fallback here (there used to be one, a real working credential, which is
# exactly the kind of thing that ends up baked into a Docker image and pushed to a
# public registry otherwise). Fails fast and loudly if it's missing rather than
# silently falling back to some other cluster.
MONGODB_URI = os.environ["LTSUPPORT_MONGODB_URI"]
MONGODB_DB_NAME = os.environ.get("LTSUPPORT_MONGODB_DB", "ltsupport")

# Groq's Whisper API, for the overlay text box's speech-to-text button. Server-side
# only -- the desktop app is distributed to every viewer's machine, so a key baked into
# it there could be pulled straight out of the installed exe and run up usage on this
# account. Routing it through /api/speech/transcribe instead keeps it here, gated
# behind that same account's own session auth.
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"  # fastest Groq STT model still supported

SESSION_TTL_HOURS = 24 * 7

# Trial accounts: how long a single "normal" session may run before the relay cuts it
# off and shows the upgrade message below, and how many such sessions the account gets
# per calendar day (see db.count_sessions_today) -- both reset with a fresh trial
# account each day, never accumulate. Replace the contact number with your real support
# number before distributing this.
TRIAL_SESSION_LIMIT_SECONDS = 10 * 60
TRIAL_MAX_SESSIONS_PER_DAY = 3
UPGRADE_CONTACT_NUMBER = "+92-XXX-XXXXXXX"

# Prepaid accounts: every "normal" session gets this many minutes free regardless of
# current balance -- a $0 balance still gets exactly this window, never zero. Time
# beyond it is billed by the HOUR, not the minute: any partial hour of overage rounds
# UP to a full hour (15 minutes over costs the same as 59 minutes over -- both round up
# to 1 full hour; 1h15m rounds up to 2 hours), deducted from balance_cents (never
# below 0). Both figures apply per session, not once total.
PREPAID_FREE_MINUTES_PER_SESSION = 10
# USD cents, matching STRIPE_UNIT_AMOUNT_CENTS's unit -- this account's whole prepaid
# balance is USD-denominated now (see the 2026-09-15 migration in db.py's init_db,
# which converted every existing account's old PKR-denominated balance_rupees over to
# this, preserving hours remaining exactly). Kept as its own separate constant from
# STRIPE_UNIT_AMOUNT_CENTS rather than reusing it directly -- they happen to be equal
# right now (both $5.00/hour), but one prices buying an hour and the other prices
# consuming one, and there's no guarantee those never diverge later (e.g. a discount
# on purchase that doesn't change the consumption rate).
PREPAID_RATE_PER_HOUR_CENTS = 500

# Stripe: top-ups for a prepaid balance, via a hosted Stripe Checkout Session (see
# create_checkout in app.py) -- no pre-created Product/Price needed in the Stripe
# dashboard; price_data is built dynamically per checkout from STRIPE_UNIT_AMOUNT_CENTS
# below. Unlike Paddle, Stripe Checkout needs no "approved domain" step at all -- it's
# a fully Stripe-hosted page, so there's nothing tied to our own domain to approve.
# sk_test_/pk_test_ vs sk_live_/pk_live_ prefixes are an authoritative, Stripe-enforced
# split (unlike the Paddle key-labeling mixup from 2026-09-12) -- a test key can never
# move real money, so no separate verification step is needed here.
STRIPE_API_KEY = os.environ["STRIPE_API_KEY"]
STRIPE_CURRENCY = "usd"
# Real-world price charged per hour, in the smallest currency unit (cents for USD).
# $5.00/hour, confirmed 2026-09-15 -- a plain constant here (not read from the
# environment) so changing the price is just editing this one line and redeploying,
# nothing to also update in the VM's env vars. Currently equal to
# PREPAID_RATE_PER_HOUR_CENTS below (both $5.00/hour) -- see that constant's own
# comment for why they're still kept separate rather than merged into one.
STRIPE_UNIT_AMOUNT_CENTS = 500
# The quantity /api/billing/checkout opens a Checkout Session at -- just a starting
# point (a single hour is purchasable on its own, confirmed 2026-09-15);
# adjustable_quantity (see create_checkout, minimum=1) lets the customer change it
# upward on Stripe's own hosted page before paying.
STRIPE_STARTING_QUANTITY = 1
# Where Stripe redirects the browser after checkout -- purely informational pages;
# the actual balance credit happens via the webhook below, not this redirect (Stripe
# requires both URLs to be set even though nothing in this app reads them back).
STRIPE_SUCCESS_URL = os.environ.get(
    "STRIPE_SUCCESS_URL", "https://testingpaddletl.najoomi.ai/checkout-success.html")
STRIPE_CANCEL_URL = os.environ.get(
    "STRIPE_CANCEL_URL", "https://testingpaddletl.najoomi.ai/checkout-cancel.html")
# Verifies that a webhook claiming "payment completed" actually came from Stripe (see
# the signature check in app.py's /api/webhooks/stripe) -- without this, anyone who
# finds that URL could fake a completed payment and credit their own account for
# free. From the Stripe dashboard: Developers -> Webhooks -> your endpoint's Signing
# secret. Required -- a missing secret must fail closed (reject), never fall through
# as trusted.
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Interim "request an upgrade by email" flow (see /api/billing/request-upgrade and
# request-upgrade.html), used in place of the real Stripe checkout button while the
# live Stripe account is still completing its own activation (business/banking/
# identity verification -- see the 2026-09-16 stripe_integration memory; this is a
# Stripe-side process, not something this code can speed up). Meant to be temporary:
# once Stripe's account is live, point the desktop app's buy-hours button back at
# create_checkout (see billing_view.py's _do_buy_bg for the interim swap) and this
# whole block becomes unused, though harmless to leave configured.
# Gmail SMTP with an App Password (Google Account -> Security -> App Passwords,
# requires 2FA) -- not the account's normal login password, which Gmail no longer
# accepts for SMTP at all.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
UPGRADE_REQUEST_EMAIL = "sulimanmuhammad68@gmail.com"
