import asyncio
import datetime
import hashlib
import hmac
import json
import re

import requests
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
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
    device_id: str = ""


class VerifyPeerPayload(BaseModel):
    peer_token: str


class MemberLoginPayload(BaseModel):
    username: str
    password: str


class CreateMemberPayload(BaseModel):
    username: str
    password: str
    # Always "normal" in practice -- there's only one creatable member role now (see
    # db.py's org_members docstring). "admin" is never self-service; it's the org's
    # own original login, not a member at all. A separately-assignable "view_only"
    # role used to exist here but was removed -- oversight/viewing is now what the
    # admin role itself always does (see relay.py's _handle_observer), not something
    # you assign to a specific member.


def _account_view(user, token=None):
    view = {
        "org_id": user["org_id"],
        "org_name": user["org_name"],
        "account_type": user["account_type"],
        "balance_rupees": user["balance_rupees"],
        "session_count": user["session_count"],
        "total_minutes_used": user["total_minutes_used"],
        "blocked": bool(user["blocked"]),
        "interview_session_count": user["interview_session_count"],
        "interview_total_minutes_used": user["interview_total_minutes_used"],
        "upgrade_contact_number": config.UPGRADE_CONTACT_NUMBER,
        # "admin" for the org's own original login, or whatever role a member session
        # carries (see auth.resolve_session) -- every pre-existing field above is
        # completely unaffected by which one this is.
        "role": user.get("role", "admin"),
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


def _require_admin(user):
    if user.get("role", "admin") != "admin":
        raise HTTPException(403, "Only an account admin can do this.")


def _verify_paddle_signature(raw_body: bytes, signature_header: str) -> bool:
    """Paddle signs each webhook as "Paddle-Signature: ts=<unix-ts>;h1=<hex-hmac>",
    the hash being HMAC-SHA256 over "<ts>:<raw-body>" using the destination's own
    secret (Paddle dashboard -> Developer Tools -> Notifications). Verifying this is
    the only thing standing between a real payment and anyone who finds this URL and
    POSTs a fake "payment completed" body to credit their own account for free -- so a
    missing/misconfigured secret must fail closed (reject), never fall through as
    trusted."""
    if not config.PADDLE_WEBHOOK_SECRET or not signature_header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(";"))
        ts, h1 = parts["ts"], parts["h1"]
    except Exception:
        return False
    signed_payload = f"{ts}:".encode() + raw_body
    computed = hmac.new(config.PADDLE_WEBHOOK_SECRET.encode(), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, h1)


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


@app.get("/api/health")
def health():
    """Unauthenticated reachability check -- the desktop login screen's Retry button
    hits this to tell "server unreachable" apart from "wrong credentials" before the
    user even submits the form. Deliberately doesn't touch MongoDB -- this only
    confirms the API process itself is up, not the database (a DB hiccup already
    surfaces its own clear error via the actual signup/login endpoints)."""
    return {"ok": True}


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


@app.post("/api/login/member")
def login_member(payload: MemberLoginPayload):
    """A team-member login (RBAC) -- entirely separate from the org's own Organization
    ID + password above, which this never touches. Username alone identifies the
    member (and, through it, which org they belong to) since usernames are unique
    system-wide -- no org id needed here."""
    member = db.get_org_member_by_username(payload.username.strip())
    if not member or not member.get("active", True) or not auth.verify_password(
            payload.password, member["password_hash"]):
        raise HTTPException(401, "Incorrect username or password.")
    org = db.get_user_by_id(member["org_id"])
    if not org:
        raise HTTPException(401, "Incorrect username or password.")
    token = auth.issue_session(org["id"], member_id=member["member_id"])
    user = dict(org)
    user["role"] = member["role"]
    return _account_view(user, token)


@app.get("/api/users")
def list_users(authorization: str = Header(default="")):
    """Lists this org's team-member logins (never the org's own primary login --
    that's not a "member", it's the account itself). Admin-only."""
    user = _auth_user(authorization)
    _require_admin(user)
    members = db.list_org_members(user["org_id"])
    return {"users": [{"member_id": m["member_id"], "username": m["username"], "role": m["role"]}
                       for m in members]}


@app.post("/api/users")
def create_user(payload: CreateMemberPayload, authorization: str = Header(default="")):
    """Always creates a "normal"-role member -- the only kind of member there is now.
    Admin-only (naturally: this account itself is what does all the oversight/viewing
    now, there's nothing to separately configure for that)."""
    user = _auth_user(authorization)
    _require_admin(user)
    username = payload.username.strip()
    if len(username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters.")
    if len(payload.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if db.get_org_member_by_username(username):
        raise HTTPException(400, "That username is already taken.")
    member_id = db.create_org_member(user["org_id"], username, auth.hash_password(payload.password), "normal")
    return {"member_id": member_id, "username": username, "role": "normal"}


@app.delete("/api/users/{member_id}")
def remove_user(member_id: str, authorization: str = Header(default="")):
    user = _auth_user(authorization)
    _require_admin(user)
    if not db.delete_org_member(member_id, user["org_id"]):
        raise HTTPException(404, "No such user on your account.")
    return {"ok": True}


@app.post("/api/logout")
def logout(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    if token:
        db.delete_session(token)
    return {"ok": True}


@app.get("/api/me")
def me(authorization: str = Header(default="")):
    return _account_view(_auth_user(authorization))


@app.post("/api/billing/checkout")
def create_checkout(authorization: str = Header(default="")):
    """Starts a Paddle purchase for prepaid hours -- one button, no separate "upgrade
    to prepaid" step and no quantity field of our own first: Paddle's own hosted
    checkout UI is what actually lets the customer pick how many hours (and see the
    resulting price) before paying, since PADDLE_PRICE_ID's own quantity range allows
    it. This just opens a transaction at the price's minimum quantity as a starting
    point -- Paddle's checkout can adjust it upward from there. Returns a hosted
    checkout URL for the desktop app to open in the system browser; balance is only
    actually credited later, once Paddle confirms payment via /api/webhooks/paddle
    (never here -- this endpoint only starts the purchase, it doesn't know yet
    whether it'll succeed). Available from trial or prepaid alike -- a trial account
    is upgraded to prepaid automatically by the webhook the moment payment succeeds,
    so there's nothing to do here first. Admin-only (RBAC) -- billing is explicitly
    one of the two things a "normal" team-member role can't touch."""
    user = _auth_user(authorization)
    _require_admin(user)
    try:
        resp = requests.post(
            f"{config.PADDLE_API_BASE}/transactions",
            headers={"Authorization": f"Bearer {config.PADDLE_API_KEY}", "Content-Type": "application/json"},
            json={
                "items": [{"price_id": config.PADDLE_PRICE_ID, "quantity": config.PADDLE_STARTING_QUANTITY}],
                # Echoed back on every webhook for this transaction -- this is how the
                # webhook handler knows which account to credit, since Paddle has no
                # other concept of "this org's account" of its own.
                "custom_data": {"org_id": user["org_id"]},
            },
            timeout=15,
        )
    except requests.RequestException:
        raise HTTPException(503, "Could not reach the payment provider. Please try again.")
    if resp.status_code >= 400:
        detail = resp.json().get("error", {}).get("detail", "Could not start checkout.")
        raise HTTPException(502, detail)
    checkout_url = resp.json().get("data", {}).get("checkout", {}).get("url")
    if not checkout_url:
        raise HTTPException(502, "Payment provider did not return a checkout link.")
    return {"checkout_url": checkout_url}


@app.post("/api/webhooks/paddle")
async def paddle_webhook(request: Request):
    """Paddle calls this once a transaction's status changes -- this credits balance
    ONLY on transaction.completed, and ONLY once per transaction id (db.log_payment is
    the idempotency guard: Paddle delivers webhooks at-least-once, so the same event
    can legitimately arrive more than once, and this must not credit twice for it).
    Must read the raw body for signature verification before any JSON parsing --
    re-serializing a parsed-then-rebuilt body would not byte-for-byte match what
    Paddle actually signed."""
    raw_body = await request.body()
    if not _verify_paddle_signature(raw_body, request.headers.get("paddle-signature", "")):
        raise HTTPException(401, "Invalid webhook signature.")
    event = json.loads(raw_body)
    if event.get("event_type") == "transaction.completed":
        data = event.get("data", {})
        transaction_id = data.get("id")
        org_id = (data.get("custom_data") or {}).get("org_id")
        quantity = sum(item.get("quantity", 0) for item in (data.get("items") or []))
        if transaction_id and org_id and quantity > 0:
            totals = (data.get("details") or {}).get("totals") or {}
            if db.log_payment(transaction_id, org_id, quantity, totals.get("grand_total"),
                               data.get("currency_code", ""), "completed", db.now_iso()):
                # quantity * PREPAID_RATE_PER_HOUR, not whatever Paddle actually
                # charged -- that's the USD-denominated card charge; PKR balance
                # credited is purely OUR unit definition (1 unit = 1 hour = this many
                # rupees), unrelated to currency conversion.
                db.add_balance(org_id, quantity * config.PREPAID_RATE_PER_HOUR)
                # A successful payment IS the upgrade now -- no separate "Upgrade to
                # Prepaid" step for the user to click first (see create_checkout).
                # Never touches an account that's already prepaid/postpaid.
                buyer = db.get_user_by_org_id(org_id)
                if buyer and buyer["account_type"] == "trial":
                    db.set_account_type(org_id, "prepaid")
        else:
            print(f"[Paddle webhook] transaction.completed missing expected fields: {data}")
    return {"ok": True}


@app.get("/api/billing/payments")
def list_payments(authorization: str = Header(default="")):
    user = _auth_user(authorization)
    _require_admin(user)
    return {"payments": db.list_payments_for_org(user["org_id"])}


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
        live_since = None
        if live is not None:
            status = "busy" if live.viewer is not None else "online"
            session_type = live.session_type
            # Lets an admin see, without connecting first, which devices currently
            # have a real "normal"-role support session in progress and since when --
            # see relay.py's HostConn.viewer_connected_at. None whenever there's no
            # live session right now, regardless of whether the device is online.
            if live.viewer_connected_at:
                live_since = datetime.datetime.utcfromtimestamp(live.viewer_connected_at).isoformat()
        else:
            status = "offline"
            session_type = d.get("session_type", "normal")
        result.append({
            "device_id": d["device_id"],
            "name": d["name"],
            "status": status,
            "last_seen": d["last_seen"],
            "session_type": session_type,
            "live_since": live_since,
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
    # RBAC: only a "normal"-role login can host at all -- the org's own original
    # ("admin") login is restricted to oversight/viewing (see relay.py's
    # _handle_observer) and can never host, same as relay.py's own HOST_HELLO gate
    # for the internet-relay path.
    if user.get("role") != "normal":
        return {"allowed": False, "limit_seconds": None, "message": "Only a normal-role account can host."}
    sessions_today = 0
    if session_type == "normal" and user["account_type"] == "trial":
        sessions_today = db.count_sessions_today(user["org_id"])
    allowed = plan.session_allowed(user, session_type, sessions_today=sessions_today,
                                    trial_max_sessions_per_day=config.TRIAL_MAX_SESSIONS_PER_DAY)
    limit_seconds = plan.session_limit_seconds(
        user, config.TRIAL_SESSION_LIMIT_SECONDS, session_type,
        prepaid_free_minutes=config.PREPAID_FREE_MINUTES_PER_SESSION,
        prepaid_rate_per_hour=config.PREPAID_RATE_PER_HOUR)
    # user["blocked"] was already handled above, so the only way this can still be
    # False here is a trial account out of sessions for today.
    message = None if allowed else plan.trial_sessions_exhausted_message(config.TRIAL_MAX_SESSIONS_PER_DAY)
    return {"allowed": allowed, "limit_seconds": limit_seconds, "message": message,
            "account_type": user["account_type"]}


@app.post("/api/speech/transcribe")
def speech_transcribe(audio: UploadFile = File(...), authorization: str = Header(default="")):
    """Transcribes a short voice recording into English text, for the overlay text
    box's mic button in the viewer app. Proxied through here rather than the desktop
    client calling Groq directly -- see GROQ_API_KEY in config.py for why. Auth-gated
    like every other endpoint here (just to confirm the caller is a real logged-in
    account, not to attribute usage to a specific device/session)."""
    _auth_user(authorization)
    audio_bytes = audio.file.read()
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            files={"file": (audio.filename or "speech.wav", audio_bytes, audio.content_type or "audio/wav")},
            data={"model": config.GROQ_WHISPER_MODEL, "language": "en", "response_format": "json"},
            timeout=20,
        )
    except requests.RequestException:
        raise HTTPException(503, "Could not reach the speech-to-text service. Please try again.")
    if resp.status_code >= 400:
        raise HTTPException(502, "Speech-to-text request failed. Please try again.")
    return {"text": resp.json().get("text", "").strip()}


@app.post("/api/session/report")
def session_report(payload: SessionReportPayload, authorization: str = Header(default="")):
    """Called once, at the end of a local/WiFi session, to apply the same bookkeeping the
    relay applies automatically for internet sessions -- balance deduction + counters for
    a "normal" session, or the separate interview_* counters (no balance touched) for an
    "interview" session. Also logs the session (see db.log_session) -- this endpoint only
    ever receives the elapsed minutes, not real wall-clock timestamps, so the start time
    is derived by working backwards from now."""
    user = _auth_user(authorization)
    session_type = payload.session_type if payload.session_type in plan.SESSION_TYPES else "normal"
    minutes = max(0.0, payload.minutes)
    ended_at = datetime.datetime.utcnow()
    started_at = ended_at - datetime.timedelta(minutes=minutes)
    billed_minutes, amount_charged = 0.0, 0.0
    if session_type == "interview":
        db.record_interview_usage(user["org_id"], minutes)
    else:
        billed_minutes, amount_charged = db.record_session_usage(
            user["org_id"], minutes, config.PREPAID_FREE_MINUTES_PER_SESSION, config.PREPAID_RATE_PER_HOUR)
    db.log_session(user["org_id"], payload.device_id, session_type, user["account_type"],
                    started_at.isoformat(), ended_at.isoformat(), minutes, billed_minutes, amount_charged,
                    "local_session_ended", member_username=user.get("member_username"))
    return {"ok": True}


@app.get("/api/logs")
def list_logs(authorization: str = Header(default="")):
    """Admin-only activity log -- who (member_username) did what (device_id/session_type),
    when and for how long, across every session this org has ever run (relay-driven
    internet sessions and local/WiFi ones alike -- both call db.log_session). A plain
    "admin" login sessions itself never appear here as an actor since it can only ever
    observe (see relay.py's _handle_observer), never host or take a normal-role slot."""
    user = _auth_user(authorization)
    _require_admin(user)
    logs = db.list_session_logs(user["org_id"])
    return {"logs": [{
        "device_id": l.get("device_id", ""),
        "session_type": l.get("session_type", ""),
        "account_type": l.get("account_type", ""),
        "started_at": l.get("started_at", ""),
        "ended_at": l.get("ended_at", ""),
        "duration_minutes": l.get("duration_minutes", 0),
        "billed_minutes": l.get("billed_minutes", 0),
        "amount_charged": l.get("amount_charged", 0),
        "end_reason": l.get("end_reason", ""),
        "member_username": l.get("member_username"),
    } for l in logs]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
