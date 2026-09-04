import asyncio
import re

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo.errors import PyMongoError

import db
import auth
import config
import plan
import relay

app = FastAPI(title="LTSupport Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PyMongoError)
async def mongo_error_handler(request: Request, exc: PyMongoError):
    """Without this, a MongoDB hiccup surfaces as a raw 500 with pymongo's own exception
    text. This gives the desktop app (and anything else calling this API) a clean,
    specific error it can actually show someone, instead of a stack trace."""
    return JSONResponse(
        status_code=503,
        content={"detail": "The server can't reach its database right now. Please try again shortly."},
    )

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,}$")


class SignupPayload(BaseModel):
    org_name: str
    email: str
    phone: str
    password: str


class LoginPayload(BaseModel):
    org_id: str
    password: str


class SessionCheckPayload(BaseModel):
    session_type: str = "normal"


class SessionReportPayload(BaseModel):
    minutes: float
    session_type: str = "normal"


class VerifyPeerPayload(BaseModel):
    peer_token: str


def _account_view(user, token=None):
    view = {
        "org_id": user["org_id"],
        "org_name": user["org_name"],
        "account_type": user["account_type"],
        "balance_minutes": user["balance_minutes"],
        "session_count": user["session_count"],
        "total_minutes_used": user["total_minutes_used"],
        "blocked": bool(user["blocked"]),
        "interview_session_count": user["interview_session_count"],
        "interview_total_minutes_used": user["interview_total_minutes_used"],
        "upgrade_contact_number": config.UPGRADE_CONTACT_NUMBER,
    }
    if token:
        view["token"] = token
    return view


def _auth_user(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    user = auth.resolve_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return user


@app.on_event("startup")
async def on_startup():
    # If MongoDB is temporarily unreachable at the exact moment the process starts,
    # that must not take the whole server down -- index creation can simply be retried
    # implicitly by future calls (MongoClient connects lazily); what matters is that the
    # API and relay come up and start accepting connections regardless, so everything
    # recovers on its own the moment the database becomes reachable again.
    try:
        await asyncio.to_thread(db.init_db)
    except Exception as e:
        print(f"[Startup] Could not reach the database to set up indexes: {e}")
        print("[Startup] Starting anyway -- requests needing the database will fail "
              "until it's reachable, but the server itself is up.")
    asyncio.create_task(relay.start_server(config.RELAY_HOST, config.RELAY_PORT))


@app.post("/api/signup")
def signup(payload: SignupPayload):
    org_name = payload.org_name.strip()
    email = payload.email.strip().lower()
    phone = payload.phone.strip()

    if len(org_name) < 2:
        raise HTTPException(400, "Please enter your organization name.")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address.")
    if not PHONE_RE.match(phone):
        raise HTTPException(400, "Please enter a valid phone number.")
    if len(payload.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")

    if db.get_user_by_email(email):
        raise HTTPException(400, "An account already exists for that email address.")
    if db.get_user_by_phone(phone):
        raise HTTPException(400, "An account already exists for that phone number.")

    org_id = auth.generate_org_id(org_name)
    while db.get_user_by_org_id(org_id):
        org_id = auth.generate_org_id(org_name)

    # Every account starts on trial -- no self-service choice at signup. Prepaid is a
    # one-way self-service upgrade from inside the app (see /api/account/upgrade-to-
    # prepaid below); postpaid can only be set by an operator (backend/manage.py).
    user_id = db.create_user(org_id, org_name, email, phone, auth.hash_password(payload.password),
                              "trial", 0)
    token = auth.issue_session(user_id)
    return _account_view(db.get_user_by_id(user_id), token)


@app.post("/api/login")
def login(payload: LoginPayload):
    user = db.get_user_by_org_id(payload.org_id.strip().upper())
    if not user or not auth.verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect org ID or password.")
    token = auth.issue_session(user["id"])
    return _account_view(user, token)


@app.post("/api/logout")
def logout(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    if token:
        db.delete_session(token)
    return {"ok": True}


@app.get("/api/me")
def me(authorization: str = Header(default="")):
    return _account_view(_auth_user(authorization))


@app.post("/api/account/upgrade-to-prepaid")
def upgrade_to_prepaid(authorization: str = Header(default="")):
    """Self-service, in-app upgrade -- the only account-type change a user can make
    themselves. Only valid from trial; postpaid is never self-service (set only via
    backend/manage.py, per the 'call to arrange' model), and there's nothing to do if
    already prepaid or postpaid. Starts at a 0 balance -- topping it up is still a manual
    step (backend/manage.py topup), since there's no payment processor wired up here."""
    user = _auth_user(authorization)
    if user["account_type"] != "trial":
        raise HTTPException(400, "Only a trial account can be upgraded to prepaid this way.")
    db.set_account_type(user["org_id"], "prepaid")
    return _account_view(db.get_user_by_id(user["id"]))


@app.post("/api/session/verify-peer")
def verify_peer(payload: VerifyPeerPayload, authorization: str = Header(default="")):
    """Used by a local/WiFi host to confirm a connecting viewer's own session token
    belongs to the same account as the host itself. Local mode has no relay in the
    middle to do the usual account-ownership check automatically -- each login issues
    its own unique token, so the host can't just compare tokens for equality (that would
    require both sides to somehow share one login, which isn't how logging in works).
    This does the same ownership check the relay does, just as an explicit call."""
    user = _auth_user(authorization)
    peer = auth.resolve_session(payload.peer_token)
    return {"same_account": bool(peer and peer["org_id"] == user["org_id"])}


@app.get("/api/devices")
def list_devices(authorization: str = Header(default="")):
    user = _auth_user(authorization)
    devices = db.list_devices_for_user(user["id"])
    result = []
    for d in devices:
        live = relay.HOSTS.get(d["device_id"])
        if live is not None:
            status = "busy" if live.viewer is not None else "online"
            session_type = live.session_type
        else:
            status = "offline"
            session_type = d.get("session_type", "normal")
        result.append({
            "device_id": d["device_id"],
            "name": d["name"],
            "status": status,
            "last_seen": d["last_seen"],
            "session_type": session_type,
        })
    return {"devices": result}


@app.delete("/api/devices/{device_id}")
def remove_device(device_id: str, authorization: str = Header(default="")):
    """Removes a device from this account's list -- for clearing out stale/test entries
    that no longer reflect anything real. Does not affect an active session on that device
    if one happens to be running right now; it'll simply re-register itself the next time
    it starts hosting."""
    user = _auth_user(authorization)
    if not db.delete_device(device_id, user["id"]):
        raise HTTPException(404, "No such device on your account.")
    return {"ok": True}


@app.post("/api/session/check")
def session_check(payload: SessionCheckPayload, authorization: str = Header(default="")):
    """Called once, at the start of a session, by a desktop app about to connect directly
    (WiFi/local-network mode, bypassing the relay). Confirms the account currently allows
    a session of this type and how long it may run, using the exact same rules the relay
    enforces for internet sessions -- see plan.py. An "interview" session skips the plan/
    balance check entirely; the only gate for it is the account's blocked status."""
    user = _auth_user(authorization)
    session_type = payload.session_type if payload.session_type in plan.SESSION_TYPES else "normal"
    if user["blocked"]:
        return {"allowed": False, "limit_seconds": None, "message": plan.blocked_message()}
    allowed = plan.session_allowed(user, session_type)
    limit_seconds = plan.session_limit_seconds(user, config.TRIAL_SESSION_LIMIT_SECONDS, session_type)
    message = None if allowed else plan.insufficient_balance_message(config.UPGRADE_CONTACT_NUMBER)
    return {"allowed": allowed, "limit_seconds": limit_seconds, "message": message}


@app.post("/api/session/report")
def session_report(payload: SessionReportPayload, authorization: str = Header(default="")):
    """Called once, at the end of a local/WiFi session, to apply the same bookkeeping the
    relay applies automatically for internet sessions -- balance deduction + counters for
    a "normal" session, or the separate interview_* counters (no balance touched) for an
    "interview" session."""
    user = _auth_user(authorization)
    session_type = payload.session_type if payload.session_type in plan.SESSION_TYPES else "normal"
    minutes = max(0.0, payload.minutes)
    if session_type == "interview":
        db.record_interview_usage(user["org_id"], minutes)
    else:
        db.record_session_usage(user["org_id"], minutes)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
