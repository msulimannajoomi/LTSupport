import bcrypt
import re
import secrets
import datetime
import time

import db
import config

# Relay connects/reconnects call resolve_session on every hello -- without this cache,
# each one costs two sequential Mongo Atlas round-trips (get_session + get_user_by_id)
# before the connection can even proceed, which is a big share of "why is connecting
# slow". Kept short so a revoked session isn't honored for long after logout.
_session_cache = {}  # token -> (user_dict, cached_at)
_SESSION_CACHE_TTL = 15  # seconds


def generate_org_id(org_name: str) -> str:
    slug = re.sub(r"[^A-Z0-9]", "", org_name.upper())[:10] or "ORG"
    return f"{slug}-{secrets.token_hex(3).upper()}"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def issue_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    expires_at = (
        datetime.datetime.utcnow() + datetime.timedelta(hours=config.SESSION_TTL_HOURS)
    ).isoformat()
    db.create_session(token, user_id, expires_at)
    return token


def resolve_session(token: str):
    """Returns the owning user dict for a valid, non-expired session token, else None."""
    if not token:
        return None

    cached = _session_cache.get(token)
    if cached is not None:
        user, cached_at = cached
        if time.time() - cached_at < _SESSION_CACHE_TTL:
            return user
        del _session_cache[token]

    session = db.get_session(token)
    if not session:
        return None
    if datetime.datetime.utcnow() > datetime.datetime.fromisoformat(session["expires_at"]):
        db.delete_session(token)
        return None

    user = db.get_user_by_id(session["user_id"])
    if user:
        _session_cache[token] = (user, time.time())
    return user
