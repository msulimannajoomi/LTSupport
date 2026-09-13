"""Shared account_type/balance/blocked rules, used by both the relay (real-time sessions)
and the REST API (the check/report pair a locally-connected desktop app calls when it
bypasses the relay entirely -- see /api/session/check and /api/session/report in app.py).

Two session types:
- "normal": the existing trial/prepaid/postpaid enforcement.
- "interview": no time/balance restriction at all -- the ONLY gate is whether the
  organization's account is blocked. Usage is tracked in separate interview_* counters
  so it never mixes with billable normal-session usage.

Pricing model for "normal" sessions:
- trial: TRIAL_SESSION_LIMIT_SECONDS per session (config.py), capped at
  TRIAL_MAX_SESSIONS_PER_DAY sessions per calendar day (see db.count_sessions_today).
- prepaid: every session gets PREPAID_FREE_MINUTES_PER_SESSION free, regardless of
  current balance -- a PKR 0 balance still gets exactly that window, never zero. Time
  beyond it is billed by the HOUR at PREPAID_RATE_PER_HOUR, deducted from
  balance_rupees (see db.record_session_usage) -- any partial hour of overage rounds
  UP to a full hour (there is no per-minute proration once billing starts). Both
  figures are per session, not a one-time allowance.
- postpaid: unrestricted, billed separately outside this system.

This module stays deliberately free of any direct config/db import -- every number it
needs is passed in by the caller (relay.py or app.py), which already has config loaded
and, for the session-count check, already looked up today's count from the database.
"""

import math

SESSION_TYPES = ("normal", "interview")
ACCOUNT_TYPES = ("trial", "prepaid", "postpaid")


def _effective_account_type(user):
    """Treats any unrecognized account_type as "trial" -- the most restrictive tier,
    never postpaid's unrestricted one. Getting here with something outside
    ACCOUNT_TYPES means the stored value itself is bad data (e.g. a typo written
    directly to the database, or straight into manage.py's upgrade command --
    "trail" instead of "trial" is exactly the kind of mistake this guards against),
    and silently treating unrecognized input as postpaid would grant free unlimited
    access for what's actually just corrupted data, instead of surfacing it."""
    account_type = user.get("account_type")
    return account_type if account_type in ACCOUNT_TYPES else "trial"


def session_allowed(user, session_type="normal", sessions_today=0, trial_max_sessions_per_day=None):
    """False if the account is blocked (any session type). For a "normal" session on a
    trial account, also False once sessions_today has already reached
    trial_max_sessions_per_day. Prepaid and postpaid can always start a normal session
    -- prepaid's free-minutes window (see session_limit_seconds) applies regardless of
    balance, so there's nothing to gate at start time, only how long it can run. An
    "interview" session is always allowed regardless of plan, balance, or session
    count."""
    if user.get("blocked"):
        return False
    if session_type == "interview":
        return True
    if _effective_account_type(user) == "trial" and trial_max_sessions_per_day is not None:
        return sessions_today < trial_max_sessions_per_day
    return True


def session_limit_seconds(user, trial_limit_seconds, session_type="normal",
                           prepaid_free_minutes=0, prepaid_rate_per_hour=1):
    """None means unrestricted. An "interview" session is always unrestricted (its only
    gate is session_allowed's blocked check, already applied before this is consulted).
    A trial session is capped at trial_limit_seconds flat. A prepaid session always
    gets prepaid_free_minutes for free, plus as many WHOLE hours of overage as its
    current balance can fully cover -- never a partial hour, since billing itself rounds
    any partial hour up to a full one (see db.record_session_usage), and letting the
    session drift even a minute into an hour the balance can't fully pay for would mean
    charging more than the balance actually covers. A PKR 0 balance still gets exactly
    the free window, never zero. Only a genuine postpaid account is unrestricted --
    see _effective_account_type for why anything unrecognized is treated as trial
    instead."""
    if session_type == "interview":
        return None
    account_type = _effective_account_type(user)
    if account_type == "trial":
        return trial_limit_seconds
    if account_type == "prepaid":
        affordable_hours = math.floor(max(0.0, user["balance_rupees"]) / prepaid_rate_per_hour)
        return prepaid_free_minutes * 60 + affordable_hours * 3600
    return None


def limit_reached_message(user, trial_limit_seconds, upgrade_contact_number):
    if _effective_account_type(user) == "trial":
        return (f"Your trial session ended after {trial_limit_seconds // 60} minutes. "
                f"Upgrade to Prepaid or Postpaid for longer sessions -- call {upgrade_contact_number}.")
    return (f"Your prepaid balance ran out during this session. "
            f"Call {upgrade_contact_number} to top up.")


def trial_sessions_exhausted_message(trial_max_sessions_per_day):
    return (f"This trial account has used all {trial_max_sessions_per_day} free sessions "
            f"for today. Upgrade to Prepaid or Postpaid for unlimited sessions, or try "
            f"again tomorrow.")


def blocked_message():
    return "This organization's account has been blocked. Contact support."
