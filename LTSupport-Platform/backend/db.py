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

from pymongo import MongoClient, ASCENDING

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
    # sessions/devices use their natural key (token / device_id) as _id, already unique.


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

def create_user(org_id, org_name, email, phone, password_hash, account_type="trial", balance_minutes=0):
    _get_db().users.insert_one({
        "_id": org_id,
        "org_name": org_name,
        "email": email,
        "phone": phone,
        "password_hash": password_hash,
        "account_type": account_type,
        "balance_minutes": balance_minutes,
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
    balance_minutes -- tracked in entirely separate counters from normal/billable
    sessions, per the requirement that interview usage counts separately."""
    _get_db().users.update_one(
        {"_id": org_id},
        {"$inc": {"interview_session_count": 1, "interview_total_minutes_used": minutes_used}},
    )


def add_balance(org_id, minutes):
    """Manual top-up (stand-in for a real payment step, per the 'call to upgrade' model)."""
    _get_db().users.update_one({"_id": org_id}, {"$inc": {"balance_minutes": minutes}})


def record_session_usage(org_id, minutes_used):
    """Called once a session ends, regardless of plan: always increments session_count and
    total_minutes_used. Only prepaid accounts also have the balance deducted (never below 0) --
    trial has no balance concept and postpaid is billed later, outside this system."""
    users = _get_db().users
    user = users.find_one({"_id": org_id}, {"account_type": 1, "balance_minutes": 1})
    if not user:
        return
    update = {"$inc": {"session_count": 1, "total_minutes_used": minutes_used}}
    if user["account_type"] == "prepaid":
        new_balance = max(0.0, user["balance_minutes"] - minutes_used)
        update["$set"] = {"balance_minutes": new_balance}
    users.update_one({"_id": org_id}, update)


# ---- Sessions ----

def create_session(token, user_id, expires_at):
    _get_db().sessions.insert_one({
        "_id": token,
        "user_id": user_id,
        "created_at": now_iso(),
        "expires_at": expires_at,
    })


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
