import os

API_HOST = "0.0.0.0"
API_PORT = int(os.environ.get("LTSUPPORT_API_PORT", 8000))

RELAY_HOST = "0.0.0.0"
RELAY_PORT = int(os.environ.get("LTSUPPORT_RELAY_PORT", 7000))

# MongoDB Atlas connection. Override via env var in any real deployment rather than
# editing this file -- the default below is a placeholder pointing at a specific
# developer's cluster and its credentials should be rotated before distributing this
# codebase or committing it anywhere shared.
MONGODB_URI = os.environ.get(
    "LTSUPPORT_MONGODB_URI",
    "mongodb+srv://sulimanmuhammad68_db_user:Os8fbFBBdClolVEV@tlsupport.9qfpqde.mongodb.net/",
)
MONGODB_DB_NAME = os.environ.get("LTSUPPORT_MONGODB_DB", "ltsupport")

SESSION_TTL_HOURS = 24 * 7

# Trial accounts: how long a single remote-control session may run before the relay
# cuts it off and shows the upgrade message below. Replace with your real support
# number before distributing this.
TRIAL_SESSION_LIMIT_SECONDS = 60 * 60
UPGRADE_CONTACT_NUMBER = "+92-XXX-XXXXXXX"
