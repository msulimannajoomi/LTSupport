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
# current balance -- a PKR 0 balance still gets exactly this window, never zero. Time
# beyond it is billed by the HOUR, not the minute: any partial hour of overage rounds
# UP to a full hour (15 minutes over costs the same as 59 minutes over -- both round up
# to 1 full hour; 1h15m rounds up to 2 hours), deducted from balance_rupees (never
# below 0). Both figures apply per session, not once total.
PREPAID_FREE_MINUTES_PER_SESSION = 10
PREPAID_RATE_PER_HOUR = 5000.0

# Paddle: top-ups for a prepaid balance. PADDLE_PRICE_ID is Paddle's own unit -- one
# unit there means one hour here; how much Paddle actually charges per unit and in
# what currency is whatever that price is configured for on Paddle's end (it doesn't
# have to be PKR -- Paddle doesn't settle in PKR at all, so it's priced there in USD;
# the balance credited on a successful payment is quantity * PREPAID_RATE_PER_HOUR
# regardless, since that's OUR unit's definition, not a currency conversion of what
# Paddle charged). PADDLE_API_BASE picks sandbox vs live -- never assume which one a
# given key belongs to; verify against the API directly (see the paddle_integration
# memory from 2026-09-12, where a key labeled "sandbox" by the user turned out to be
# live).
PADDLE_API_BASE = os.environ.get("PADDLE_API_BASE", "https://sandbox-api.paddle.com")
PADDLE_API_KEY = os.environ["PADDLE_API_KEY"]
PADDLE_PRICE_ID = os.environ["PADDLE_PRICE_ID"]
# The quantity /api/billing/checkout opens a transaction at -- just a starting point,
# since PADDLE_PRICE_ID's own quantity range lets the customer adjust it upward
# inside Paddle's own hosted checkout UI before paying (see create_checkout in
# app.py). Must be >= that price's configured minimum quantity in the Paddle
# dashboard, or transaction creation is rejected.
PADDLE_STARTING_QUANTITY = 4
# Verifies that a webhook claiming to be "payment completed" actually came from
# Paddle (see the signature check in app.py's /api/webhooks/paddle) -- without this,
# anyone who finds that URL could fake a completed payment and credit their own
# account for free. From the Paddle dashboard: Developer Tools -> Notifications ->
# your destination's Secret Key.
PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
