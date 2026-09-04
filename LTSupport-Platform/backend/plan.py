"""Shared account_type/balance/blocked rules, used by both the relay (real-time sessions)
and the REST API (the check/report pair a locally-connected desktop app calls when it
bypasses the relay entirely -- see /api/session/check and /api/session/report in app.py).

Two session types:
- "normal": the existing trial/prepaid/postpaid enforcement.
- "interview": no time/balance restriction at all -- the ONLY gate is whether the
  organization's account is blocked. Usage is tracked in separate interview_* counters
  so it never mixes with billable normal-session usage.
"""

SESSION_TYPES = ("normal", "interview")


def session_allowed(user, session_type="normal"):
    """False if the account is blocked (any session type), or -- for a "normal" session
    only -- a prepaid account that has run out of balance. An "interview" session is
    otherwise always allowed, regardless of plan or balance."""
    if user.get("blocked"):
        return False
    if session_type == "interview":
        return True
    if user["account_type"] == "prepaid" and user["balance_minutes"] <= 0:
        return False
    return True


def session_limit_seconds(user, trial_limit_seconds, session_type="normal"):
    """None means unrestricted. An "interview" session is always unrestricted (its only
    gate is session_allowed's blocked check, already applied before this is consulted)."""
    if session_type == "interview":
        return None
    if user["account_type"] == "trial":
        return trial_limit_seconds
    if user["account_type"] == "prepaid":
        return max(0.0, user["balance_minutes"]) * 60
    return None


def limit_reached_message(user, trial_limit_seconds, upgrade_contact_number):
    if user["account_type"] == "trial":
        return (f"Trial sessions are limited to {trial_limit_seconds // 60} minutes. "
                f"Call {upgrade_contact_number} to upgrade your plan.")
    return (f"Your prepaid balance ran out during this session. "
            f"Call {upgrade_contact_number} to top up.")


def insufficient_balance_message(upgrade_contact_number):
    return f"Your prepaid balance is empty. Call {upgrade_contact_number} to top up."


def blocked_message():
    return "This organization's account has been blocked. Contact support."
