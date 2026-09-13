"""MongoDB-backed persistence (Atlas). Every function here keeps the exact same name,
signature, and return shape (plain dicts) as the original SQLite version -- relay.py,
app.py, auth.py, and manage.py call these without knowing or caring that the storage
underneath changed.

Design: rather than a separate auto-increment integer id, `org_id` IS the user's id --
it's already the unique, natural key used everywhere (login, ownership checks). It's
stored as MongoDB's `_id` for `users`, and referenced as plain `owner_id`/`user_id`
strings in `devices`/`sessions`. So `user["id"] == user["org_id"]` always.
"""
import datetime
import math
import secrets

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

import config

_client = None
_db = None


def _get_db():
    global _client, _db
    if _db is None:
        # Tight, explicit timeouts so a network hiccup or an Atlas outage fails fast
        # (raising an exception the caller can handle) instead of hanging for pymongo's
        # much longer defaults -- every one of these calls runs inside asyncio.to_thread
        # from relay.py, but a slow failure still ties up a thread and delays the caller.
        _client = MongoClient(
            config.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=8000,
        )
        _db = _client[config.MONGODB_DB_NAME]
    return _db


def init_db():
    db = _get_db()
    db.users.create_index([("email", ASCENDING)], unique=True)
    db.users.create_index([("phone", ASCENDING)], unique=True)
    # RBAC: additional per-org logins (see the "Org members" section below) --
    # usernames are unique system-wide, not just per-org, so a member logs in with
    # just username+password (see /api/login/member), no separate org id needed.
    db.org_members.create_index([("username", ASCENDING)], unique=True)
    # sessions/devices use their natural key (token / device_id) as _id, already unique.
    # Supports count_sessions_today's per-org, per-day range query below.
    db.session_logs.create_index([("org_id", ASCENDING), ("started_at", ASCENDING)])
    # One-time migration: earlier versions stored the prepaid balance as
    # "balance_minutes" (a flat minute count). Billing is now rate-based (rupees per
    # minute beyond a free window -- see plan.py), so it's renamed to "balance_rupees".
    # $rename is a no-op for any document that doesn't have the old field (i.e. every
    # account created after this change), so this is safe to leave running on every
    # startup rather than as a separate one-off script. Note this only renames the
    # field -- an old balance's numeric value meant minutes under the old model, so it
    # isn't converted to an equivalent rupee amount, just carried over as-is.
    db.users.update_many({"balance_minutes": {"$exists": True}},
                          {"$rename": {"balance_minutes": "balance_rupees"}})


def now_iso():
    return datetime.datetime.utcnow().isoformat()


def _user_view(doc):
    if not doc:
        return None
    doc = dict(doc)
    doc["org_id"] = doc["_id"]
    doc["id"] = doc["_id"]
    return doc


def _device_view(doc):
    if not doc:
        return None
    doc = dict(doc)
    doc["device_id"] = doc["_id"]
    return doc


def _session_view(doc):
    if not doc:
        return None
    doc = dict(doc)
    doc["token"] = doc["_id"]
    return doc


# ---- Users ----

def create_user(org_id, org_name, email, phone, password_hash, account_type="trial", balance_rupees=0):
    _get_db().users.insert_one({
        "_id": org_id,
        "org_name": org_name,
        "email": email,
        "phone": phone,
        "password_hash": password_hash,
        "account_type": account_type,
        "balance_rupees": balance_rupees,
        "session_count": 0,
        "total_minutes_used": 0.0,
        "blocked": 0,
        "interview_session_count": 0,
        "interview_total_minutes_used": 0.0,
        "created_at": now_iso(),
    })
    return org_id


def get_user_by_org_id(org_id):
    return _user_view(_get_db().users.find_one({"_id": org_id}))


def get_user_by_email(email):
    return _user_view(_get_db().users.find_one({"email": email}))


def get_user_by_phone(phone):
    return _user_view(_get_db().users.find_one({"phone": phone}))


def get_user_by_id(user_id):
    return _user_view(_get_db().users.find_one({"_id": user_id}))


def set_account_type(org_id, account_type):
    _get_db().users.update_one({"_id": org_id}, {"$set": {"account_type": account_type}})


def set_blocked(org_id, blocked):
    _get_db().users.update_one({"_id": org_id}, {"$set": {"blocked": 1 if blocked else 0}})


def record_interview_usage(org_id, minutes_used):
    """Interview-mode sessions are never balance-restricted and never touch
    balance_rupees -- tracked in entirely separate counters from normal/billable
    sessions, per the requirement that interview usage counts separately."""
    _get_db().users.update_one(
        {"_id": org_id},
        {"$inc": {"interview_session_count": 1, "interview_total_minutes_used": minutes_used}},
    )


def add_balance(org_id, rupees):
    """Manual top-up (stand-in for a real payment step, per the 'call to upgrade' model --
    Paddle will replace this as the actual funding source later)."""
    _get_db().users.update_one({"_id": org_id}, {"$inc": {"balance_rupees": rupees}})


def record_session_usage(org_id, minutes_used, free_minutes=0, rate_per_hour=1):
    """Called once a "normal" session ends, regardless of plan: always increments
    session_count and total_minutes_used. A prepaid account's first free_minutes of
    THIS session are never billed (see plan.py's session_limit_seconds, which already
    guarantees the session couldn't run past what free_minutes + balance covers) --
    time beyond that is billed by the HOUR, not the minute: any partial hour of overage
    rounds UP to a full hour (a session 61 minutes past the free window is billed as 2
    full hours, not 1 hour and 1 minute), deducted from balance_rupees (never below 0).
    Trial has no balance concept and postpaid is billed later, outside this system, so
    neither has anything to deduct. Returns (billed_minutes, amount_charged) for the
    caller to log alongside the session record (see log_session) -- billed_minutes here
    is the raw, un-rounded overage (for audit purposes); amount_charged already reflects
    the rounded-up hourly billing actually applied."""
    users = _get_db().users
    user = users.find_one({"_id": org_id}, {"account_type": 1, "balance_rupees": 1})
    if not user:
        return 0.0, 0.0
    update = {"$inc": {"session_count": 1, "total_minutes_used": minutes_used}}
    billed_minutes = 0.0
    amount_charged = 0.0
    if user["account_type"] == "prepaid":
        billed_minutes = max(0.0, minutes_used - free_minutes)
        billed_hours = math.ceil(billed_minutes / 60) if billed_minutes > 0 else 0
        amount_charged = billed_hours * rate_per_hour
        new_balance = max(0.0, user["balance_rupees"] - amount_charged)
        update["$set"] = {"balance_rupees": new_balance}
    users.update_one({"_id": org_id}, update)
    return billed_minutes, amount_charged


# ---- Session log (a full audit trail -- every session, any type/plan, one document
# each, independent of the billing counters above) ----

def log_session(org_id, device_id, session_type, account_type, started_at, ended_at,
                 duration_minutes, billed_minutes=0.0, amount_charged=0.0, end_reason="",
                 member_username=None):
    """member_username identifies WHICH team-member login actually did the joining/
    support work (see relay.py/app.py's call sites) -- None for a session run under
    the org's own original login directly (there's no such thing any more for a real
    "normal" support session, since only a team member can host or join now, but kept
    optional rather than required in case anything ever calls this without one)."""
    _get_db().session_logs.insert_one({
        "org_id": org_id,
        "device_id": device_id,
        "session_type": session_type,
        "account_type": account_type,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_minutes": duration_minutes,
        "billed_minutes": billed_minutes,
        "amount_charged": amount_charged,
        "end_reason": end_reason,
        "member_username": member_username,
    })


def list_session_logs(org_id, limit=200):
    """Full session history for this org, most recent first -- each entry already
    carries member_username (see log_session), so this alone is what an admin's
    activity-log screen needs to show "who did what, when" per team member."""
    docs = _get_db().session_logs.find({"org_id": org_id}).sort("started_at", DESCENDING).limit(limit)
    return list(docs)


def count_sessions_today(org_id, session_type="normal"):
    """How many sessions of this type this org has already logged today (UTC calendar
    day) -- used to enforce TRIAL_MAX_SESSIONS_PER_DAY. Only "normal" sessions ever
    call this; interview sessions have no daily cap to check."""
    start_of_day = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return _get_db().session_logs.count_documents({
        "org_id": org_id,
        "session_type": session_type,
        "started_at": {"$gte": start_of_day},
    })


# ---- Payments (Paddle top-ups) ----

def log_payment(transaction_id, org_id, quantity, amount, currency, status, created_at):
    """One document per Paddle transaction, keyed by transaction_id as _id -- Paddle
    delivers webhooks at-least-once, so the SAME transaction.completed event can
    arrive more than once. Returns True the first time (caller should credit balance),
    False if this transaction_id was already logged (caller must NOT credit again --
    that's what actually prevents a retried webhook from double-crediting an
    account)."""
    try:
        _get_db().payments.insert_one({
            "_id": transaction_id,
            "org_id": org_id,
            "quantity": quantity,
            "amount": amount,
            "currency": currency,
            "status": status,
            "created_at": created_at,
        })
        return True
    except DuplicateKeyError:
        return False


def list_payments_for_org(org_id, limit=50):
    docs = _get_db().payments.find({"org_id": org_id}).sort("created_at", DESCENDING).limit(limit)
    result = []
    for d in docs:
        d = dict(d)
        d["transaction_id"] = d.pop("_id")
        result.append(d)
    return result


# ---- Org members (RBAC) ----
#
# The org's own original login (users._id / password_hash, completely untouched by any
# of this) is always the implicit "admin" of itself -- see auth.resolve_session, which
# attaches role="admin" to every session that isn't tied to one of these. This
# collection only holds ADDITIONAL logins an admin creates for their org, each with
# their own username/password and a restricted role ("normal" or "view_only" -- never
# "admin", there is only ever one admin per org: the original account). Billing,
# devices, and every other org-level record still belong to the org itself, never to
# an individual member -- a member session just carries a different role alongside
# the exact same org_id/account_type/balance_rupees/etc. every existing endpoint
# already reads.

def _member_view(doc):
    if not doc:
        return None
    doc = dict(doc)
    doc["member_id"] = doc["_id"]
    return doc


def create_org_member(org_id, username, password_hash, role):
    member_id = secrets.token_hex(8)
    _get_db().org_members.insert_one({
        "_id": member_id,
        "org_id": org_id,
        "username": username,
        "password_hash": password_hash,
        "role": role,
        "active": True,
        "created_at": now_iso(),
    })
    return member_id


def get_org_member(member_id):
    return _member_view(_get_db().org_members.find_one({"_id": member_id}))


def get_org_member_by_username(username):
    return _member_view(_get_db().org_members.find_one({"username": username}))


def list_org_members(org_id):
    docs = _get_db().org_members.find({"org_id": org_id}).sort("created_at", ASCENDING)
    return [_member_view(d) for d in docs]


def delete_org_member(member_id, org_id):
    """Scoped to org_id too -- an admin can only ever remove a member of their OWN
    org, never guess another org's member_id and remove it."""
    result = _get_db().org_members.delete_one({"_id": member_id, "org_id": org_id})
    return result.deleted_count > 0


# ---- Sessions ----

def create_session(token, user_id, expires_at, member_id=None):
    doc = {
        "_id": token,
        "user_id": user_id,
        "created_at": now_iso(),
        "expires_at": expires_at,
    }
    if member_id:
        doc["member_id"] = member_id
    _get_db().sessions.insert_one(doc)


def get_session(token):
    return _session_view(_get_db().sessions.find_one({"_id": token}))


def delete_session(token):
    _get_db().sessions.delete_one({"_id": token})


# ---- Devices ----

def upsert_device(device_id, owner_id, name, status, session_type="normal"):
    _get_db().devices.update_one(
        {"_id": device_id},
        {"$set": {"owner_id": owner_id, "name": name, "status": status, "last_seen": now_iso(),
                   "session_type": session_type}},
        upsert=True,
    )


def set_device_status(device_id, status):
    _get_db().devices.update_one({"_id": device_id}, {"$set": {"status": status, "last_seen": now_iso()}})


def get_device(device_id):
    return _device_view(_get_db().devices.find_one({"_id": device_id}))


def list_devices_for_user(owner_id):
    docs = _get_db().devices.find({"owner_id": owner_id}).sort("name", ASCENDING)
    return [_device_view(d) for d in docs]


def delete_device(device_id, owner_id):
    """Removes a device record from an account's list. Only ever removes the DB record --
    if that device happens to be actively hosting right now, the live in-memory connection
    (relay.HOSTS) is untouched, so an active session isn't interrupted by this."""
    result = _get_db().devices.delete_one({"_id": device_id, "owner_id": owner_id})
    return result.deleted_count > 0
