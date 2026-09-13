import asyncio
import datetime
import secrets
import struct
import time

import db
import auth
import config
import plan
import protocol as p


def _iso(ts):
    return datetime.datetime.utcfromtimestamp(ts).isoformat()

HELLO_TIMEOUT = 15
# Seconds without any frame (including heartbeats) before a connection is considered
# dead. Desktop clients send a heartbeat every 8s (see viewer_agent.py), so this leaves
# a comfortable margin for normal network jitter without masking a genuinely dead peer.
IDLE_TIMEOUT = 45
# How long a device's viewer slot stays reserved after the viewer's connection drops,
# before the session is actually torn down. The relay can't tell a genuine "viewer
# closed the app" apart from "network blipped" -- both just look like the read failing
# -- so every drop gets this same grace window. Within it, the same viewer reconnecting
# resumes the session transparently (host is never told the viewer left); outside it,
# the session ends and the host is notified as before. Kept well under the desktop
# client's own reconnect attempts (viewer_agent.py) so a real retry lands before this
# expires.
GRACE_SECONDS = 15

# device_id -> HostConn, the single source of truth for "who is live right now"
HOSTS = {}


class HostConn:
    def __init__(self, writer, device_id, owner_id, name, machine_id=None, session_type="normal"):
        self.writer = writer
        self.device_id = device_id
        self.owner_id = owner_id
        self.name = name
        self.machine_id = machine_id  # this host's own machine id, to refuse a same-machine viewer
        self.session_type = session_type  # chosen by the HOST at Start Hosting -- the viewer has no say
        self.viewer = None  # ViewerConn or None -- the single "normal"-role support session, unchanged
        self.grace = None  # dict describing a just-dropped viewer's still-resumable session, or None
        # RBAC: any number of non-"normal"-role (currently just "admin") observers can
        # watch this host's live screen at once, entirely independent of self.viewer --
        # see _handle_observer. Never gated by billing/session limits, never occupies
        # the viewer slot, never affects it either way.
        self.observers = []  # list of ViewerConn
        # Wall-clock time the CURRENT self.viewer connected (kept in sync with it --
        # set together, cleared together), so an admin can be told "this session's
        # been live since <time>" without needing to ask the viewer itself.
        self.viewer_connected_at = None


class ViewerConn:
    def __init__(self, writer, owner_id, device_id):
        self.writer = writer
        self.owner_id = owner_id
        self.device_id = device_id


async def _read_frame(reader):
    header = await reader.readexactly(p.HEADER_SIZE)
    msg_type, length = struct.unpack(p.HEADER_FMT, header)
    payload = await reader.readexactly(length) if length else b""
    return msg_type, payload


async def _send(writer, msg_type, payload=b""):
    writer.write(p.encode_frame(msg_type, payload))
    await writer.drain()


async def _send_error(writer, code, message):
    await _send(writer, p.TYPE_ERROR, {"code": code, "message": message})


async def handle_connection(reader, writer):
    try:
        msg_type, payload = await asyncio.wait_for(_read_frame(reader), timeout=HELLO_TIMEOUT)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError, OSError):
        writer.close()
        return

    data = p.decode_json(payload)
    # resolve_session hits MongoDB -- run off the event loop so a slow/unreachable
    # database stalls only this one connection's setup, not every other active session's
    # frame-forwarding (which shares this same single-threaded event loop).
    user = await asyncio.to_thread(auth.resolve_session, data.get("session_token", ""))
    if not user:
        await _send_error(writer, "AUTH_FAILED", "Invalid or expired session. Please log in again.")
        writer.close()
        return

    if msg_type == p.TYPE_HOST_HELLO:
        await _handle_host(reader, writer, user, data)
    elif msg_type == p.TYPE_VIEWER_HELLO:
        await _handle_viewer(reader, writer, user, data)
    else:
        await _send_error(writer, "PROTOCOL_ERROR", "Expected a host or viewer hello.")
        writer.close()


async def _handle_host(reader, writer, user, data):
    # RBAC: only a "normal"-role login can host -- the org's own original ("admin")
    # login is deliberately restricted to oversight (see _handle_observer) and can
    # never host or take control itself, same as any other non-"normal" role.
    if user.get("role") != "normal":
        await _send_error(writer, "ROLE_FORBIDDEN", "Only a normal-role account can host.")
        writer.close()
        return

    device_id = (data.get("device_id") or "").strip()
    device_name = (data.get("device_name") or "Unnamed PC").strip()
    session_type = data.get("session_type")
    if session_type not in plan.SESSION_TYPES:
        session_type = "normal"

    if not device_id:
        # A 10-hex-char random suffix, not time-based -- unlike the old epoch-derived
        # suffix, this can't collide between two devices registering close together and
        # can't be guessed from the registration time.
        device_id = f"{user['org_id']}-{secrets.token_hex(5).upper()}"
        while await asyncio.to_thread(db.get_device, device_id):
            device_id = f"{user['org_id']}-{secrets.token_hex(5).upper()}"

    existing_record = await asyncio.to_thread(db.get_device, device_id)
    if existing_record and existing_record["owner_id"] != user["id"]:
        await _send_error(writer, "DEVICE_ID_TAKEN", "This device id belongs to another account.")
        writer.close()
        return

    stale = HOSTS.get(device_id)
    if stale is not None:
        try:
            stale.writer.close()
        except Exception:
            pass

    await asyncio.to_thread(db.upsert_device, device_id, user["id"], device_name, "online", session_type)
    conn = HostConn(writer, device_id, user["id"], device_name, machine_id=data.get("machine_id"),
                     session_type=session_type)
    HOSTS[device_id] = conn

    await _send(writer, p.TYPE_HELLO_OK, {"device_id": device_id, "device_name": device_name})

    try:
        while True:
            msg_type, payload = await asyncio.wait_for(_read_frame(reader), timeout=IDLE_TIMEOUT)
            if msg_type == p.TYPE_HEARTBEAT:
                continue
            if conn.viewer is not None:
                await _send(conn.viewer.writer, msg_type, payload)
            # Fan out to every observer independent of conn.viewer -- an admin can be
            # watching whether or not a "normal" support session is currently live.
            # Fire-and-forget: observers never ack, so this can never affect the
            # host's own flow-control window (see host_agent.py), only ever add a
            # send per observer per frame. A send failure just drops that one
            # observer, same as _handle_observer's own loop ending would.
            if conn.observers and msg_type in (p.TYPE_SCREEN_FRAME, p.TYPE_AUDIO_FRAME):
                for obs in list(conn.observers):
                    try:
                        await _send(obs.writer, msg_type, payload)
                    except Exception:
                        try:
                            conn.observers.remove(obs)
                        except ValueError:
                            pass
    except asyncio.TimeoutError:
        print(f"[Relay] device={device_id}: host connection idle-timed-out after {IDLE_TIMEOUT}s")
    except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionAbortedError, OSError) as e:
        print(f"[Relay] device={device_id}: host connection ended: {type(e).__name__}: {e}")
    finally:
        if HOSTS.get(device_id) is conn:
            del HOSTS[device_id]
        await asyncio.to_thread(db.set_device_status, device_id, "offline")
        if conn.grace is not None:
            conn.grace["expire_task"].cancel()
        if conn.viewer is not None:
            try:
                await _send(conn.viewer.writer, p.TYPE_SESSION_END, {"reason": "host_disconnected"})
            except Exception:
                pass
            try:
                conn.viewer.writer.close()
            except Exception:
                pass
        for obs in list(conn.observers):
            try:
                await _send(obs.writer, p.TYPE_SESSION_END, {"reason": "host_disconnected"})
            except Exception:
                pass
            try:
                obs.writer.close()
            except Exception:
                pass
        try:
            writer.close()
        except Exception:
            pass


async def _viewer_forward_loop(reader, host_conn):
    """Runs until the viewer's connection ends, for any reason -- including exceptions,
    which are caught and logged here (rather than left to surface as an untracked task
    exception) so a session ending unexpectedly is diagnosable from the server log
    instead of just looking like an ordinary disconnect. Only ever used for the
    single "normal"-role viewer slot (host_conn.viewer) -- any other role is routed to
    _handle_observer instead (see _handle_viewer), which never forwards anything
    upstream at all, so there's no role-based filtering needed here any more."""
    try:
        while True:
            msg_type, payload = await asyncio.wait_for(_read_frame(reader), timeout=IDLE_TIMEOUT)
            if msg_type == p.TYPE_HEARTBEAT:
                continue
            await _send(host_conn.writer, msg_type, payload)
    except asyncio.TimeoutError:
        print(f"[Relay] device={host_conn.device_id}: viewer connection idle-timed-out after {IDLE_TIMEOUT}s "
              f"with no frame (including heartbeats)")
    except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionAbortedError, OSError) as e:
        print(f"[Relay] device={host_conn.device_id}: viewer connection ended: {type(e).__name__}: {e}")


async def _handle_observer(reader, writer, user, host_conn):
    """A non-"normal" role (currently just "admin") watching a host's live screen --
    entirely separate from the primary "normal"-role viewer slot (host_conn.viewer).
    No billing, session-limit, or grace-period logic applies here: this isn't a
    billable support session, just oversight, and it works identically whether or not
    a real "normal" viewer is currently connected (see the fan-out in _handle_host,
    which sends every frame to host_conn.observers independent of host_conn.viewer).
    Disconnecting -- for any reason -- has zero effect on anything else: it's simply
    removed from the list, exactly like it never affected host_conn.viewer by joining
    in the first place."""
    observer = ViewerConn(writer, user["id"], host_conn.device_id)
    host_conn.observers.append(observer)
    live_since = _iso(host_conn.viewer_connected_at) if host_conn.viewer_connected_at else None
    try:
        await _send(writer, p.TYPE_HELLO_OK, {
            "device_id": host_conn.device_id, "device_name": host_conn.name,
            "session_type": host_conn.session_type, "account_type": user["account_type"],
            "limit_seconds": None, "observer": True, "live_since": live_since,
        })
        while True:
            msg_type, payload = await asyncio.wait_for(_read_frame(reader), timeout=IDLE_TIMEOUT)
            # Nothing an observer sends is ever forwarded to the host, not even
            # FRAME_ACK -- observers were never part of the host's flow-control
            # window to begin with (see the fan-out in _handle_host), so there's
            # nothing here that needs a reply either way. This loop only exists to
            # detect the connection ending (or an explicit heartbeat keeping it
            # alive past IDLE_TIMEOUT).
    except asyncio.TimeoutError:
        pass
    except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionAbortedError, OSError):
        pass
    finally:
        try:
            host_conn.observers.remove(observer)
        except ValueError:
            pass
        try:
            writer.close()
        except Exception:
            pass


async def _expire_grace(host_conn, org_id, session_type, session_started, account_type, member_username):
    """Runs for GRACE_SECONDS after a viewer's connection drops. If nothing cancels it
    first (a reconnect claiming the slot -- see _handle_viewer), the session is really
    over: record usage and tell the host the viewer left, same as an immediate drop used
    to. Identifying the grace by `session_started` guards against a rare race where this
    task's cancellation is still in flight right as a *new* grace begins."""
    try:
        await asyncio.sleep(GRACE_SECONDS)
    except asyncio.CancelledError:
        return
    if host_conn.grace is None or host_conn.grace["session_started"] != session_started:
        return
    host_conn.grace = None
    # Only clear this once the session is genuinely over -- not the moment the grace
    # window merely starts (see _handle_viewer), since a resuming viewer within the
    # window reuses this exact session_started and would just look like nothing ever
    # happened to anything watching it, correctly.
    if host_conn.viewer_connected_at == session_started:
        host_conn.viewer_connected_at = None
    ended_at = time.time()
    elapsed_minutes = (ended_at - session_started) / 60
    billed_minutes, amount_charged = 0.0, 0.0
    if session_type == "interview":
        await asyncio.to_thread(db.record_interview_usage, org_id, elapsed_minutes)
    else:
        billed_minutes, amount_charged = await asyncio.to_thread(
            db.record_session_usage, org_id, elapsed_minutes,
            config.PREPAID_FREE_MINUTES_PER_SESSION, config.PREPAID_RATE_PER_HOUR)
    await asyncio.to_thread(
        db.log_session, org_id, host_conn.device_id, session_type, account_type,
        _iso(session_started), _iso(ended_at), elapsed_minutes, billed_minutes, amount_charged,
        "viewer_disconnected", member_username=member_username)
    try:
        await _send(host_conn.writer, p.TYPE_SESSION_END, {"reason": "viewer_disconnected"})
    except Exception:
        pass


async def _handle_viewer(reader, writer, user, data):
    device_id = (data.get("device_id") or "").strip()
    device = await asyncio.to_thread(db.get_device, device_id)

    if not device or device["owner_id"] != user["id"]:
        await _send_error(writer, "NOT_FOUND", "No such device on your account.")
        writer.close()
        return

    if user.get("blocked"):
        await _send_error(writer, "ACCOUNT_BLOCKED", plan.blocked_message())
        writer.close()
        return

    host_conn = HOSTS.get(device_id)
    if host_conn is None:
        await _send_error(writer, "DEVICE_OFFLINE", "That device is not currently online.")
        writer.close()
        return

    # RBAC: any role other than "normal" (currently just "admin") never touches the
    # single viewer slot below at all -- it's an independent observer instead (see
    # _handle_observer), which can watch whether or not a "normal" session is
    # currently live, and never disturbs one either way.
    if user.get("role") != "normal":
        await _handle_observer(reader, writer, user, host_conn)
        return

    # A reconnect from the same owner, arriving inside the grace window left by that
    # owner's own previous viewer connection dropping, resumes the same session instead
    # of being treated as a brand new one -- the host is never told anything happened.
    grace = host_conn.grace
    resuming = grace is not None and host_conn.viewer is None and grace["owner_id"] == user["id"]

    if resuming:
        host_conn.grace = None
        grace["expire_task"].cancel()
        session_type = grace["session_type"]
        session_started = grace["session_started"]
        limit_seconds = grace["limit_seconds"]
        account_type = grace["account_type"]
        member_username = grace["member_username"]
    else:
        # The session type is whatever the HOST chose when it started hosting -- a viewer
        # never gets to request or override it.
        session_type = host_conn.session_type
        account_type = user["account_type"]
        # Only a "normal"-role login ever reaches here (see the role check above), and
        # a "normal" role only ever comes from a team-member login, so this is always
        # set -- identifies WHICH team member actually did this session's support work,
        # for the admin's activity log (see db.log_session/list_session_logs).
        member_username = user.get("member_username")

        if host_conn.viewer is not None:
            await _send_error(writer, "DEVICE_BUSY", "Someone is already connected to that device.")
            writer.close()
            return

        machine_id = data.get("machine_id")
        if machine_id and host_conn.machine_id and machine_id == host_conn.machine_id:
            await _send_error(writer, "SELF_CONNECT",
                               "You're already hosting this device from this same computer -- "
                               "connect from a different machine to view it.")
            writer.close()
            return

        # Only a trial account's daily session count actually gates anything here (see
        # plan.session_allowed) -- skip the lookup otherwise to avoid a needless query
        # on every prepaid/postpaid/interview connection.
        sessions_today = 0
        if session_type == "normal" and account_type == "trial":
            sessions_today = await asyncio.to_thread(db.count_sessions_today, user["org_id"])

        if not plan.session_allowed(user, session_type, sessions_today=sessions_today,
                                     trial_max_sessions_per_day=config.TRIAL_MAX_SESSIONS_PER_DAY):
            # user.get("blocked") was already handled above, so the only way this can
            # still be False here is a trial account out of sessions for today.
            await _send_error(writer, "TRIAL_SESSIONS_EXHAUSTED",
                               plan.trial_sessions_exhausted_message(config.TRIAL_MAX_SESSIONS_PER_DAY))
            writer.close()
            return

        session_started = time.time()
        limit_seconds = plan.session_limit_seconds(
            user, config.TRIAL_SESSION_LIMIT_SECONDS, session_type,
            prepaid_free_minutes=config.PREPAID_FREE_MINUTES_PER_SESSION,
            prepaid_rate_per_hour=config.PREPAID_RATE_PER_HOUR)

    viewer = ViewerConn(writer, user["id"], device_id)
    host_conn.viewer = viewer
    # Kept in sync with host_conn.viewer itself (set together, cleared together) --
    # session_started is already correct either way: the original connect time when
    # resuming, or a fresh one for a genuinely new session. Lets an observer be told
    # "this session has been live since <time>" without asking the viewer at all.
    host_conn.viewer_connected_at = session_started

    await _send(writer, p.TYPE_HELLO_OK, {
        "device_id": device_id, "device_name": host_conn.name, "session_type": session_type,
        "account_type": account_type, "limit_seconds": limit_seconds,
    })
    if not resuming:
        await _send(host_conn.writer, p.TYPE_VIEWER_JOINED, {"session_type": session_type})

    remaining = None
    if limit_seconds is not None:
        remaining = max(0.0, limit_seconds - (time.time() - session_started))

    forward_task = asyncio.create_task(_viewer_forward_loop(reader, host_conn))
    tasks = [forward_task]
    if remaining is not None:
        tasks.append(asyncio.create_task(asyncio.sleep(remaining)))

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

    plan_limit_hit = len(tasks) > 1 and forward_task not in done

    if plan_limit_hit:
        if host_conn.viewer is viewer:
            host_conn.viewer = None
            host_conn.viewer_connected_at = None
        ended_at = time.time()
        elapsed_minutes = (ended_at - session_started) / 60
        billed_minutes, amount_charged = 0.0, 0.0
        if session_type == "interview":
            await asyncio.to_thread(db.record_interview_usage, user["org_id"], elapsed_minutes)
        else:
            billed_minutes, amount_charged = await asyncio.to_thread(
                db.record_session_usage, user["org_id"], elapsed_minutes,
                config.PREPAID_FREE_MINUTES_PER_SESSION, config.PREPAID_RATE_PER_HOUR)
        await asyncio.to_thread(
            db.log_session, user["org_id"], device_id, session_type, account_type,
            _iso(session_started), _iso(ended_at), elapsed_minutes, billed_minutes, amount_charged,
            "plan_limit_reached", member_username=member_username)
        message = {
            "code": "PLAN_LIMIT_REACHED",
            "message": plan.limit_reached_message(user, config.TRIAL_SESSION_LIMIT_SECONDS, config.UPGRADE_CONTACT_NUMBER),
        }
        try:
            await _send(writer, p.TYPE_TRIAL_LIMIT, message)
        except Exception:
            pass
        try:
            await _send(host_conn.writer, p.TYPE_TRIAL_LIMIT, message)
        except Exception:
            pass
    elif host_conn.viewer is viewer:
        # The forward loop ended on its own (the relay can't tell a clean close apart
        # from a network blip from here). Hold the slot open for GRACE_SECONDS rather
        # than tearing the session down immediately -- see _expire_grace.
        host_conn.viewer = None
        host_conn.grace = {
            "owner_id": user["id"],
            "session_type": session_type,
            "session_started": session_started,
            "limit_seconds": limit_seconds,
            "account_type": account_type,
            "member_username": member_username,
        }
        host_conn.grace["expire_task"] = asyncio.create_task(
            _expire_grace(host_conn, user["org_id"], session_type, session_started, account_type, member_username))

    try:
        writer.close()
    except Exception:
        pass


async def start_server(host, port):
    server = await asyncio.start_server(handle_connection, host, port)
    print(f"[Relay] Listening on {host}:{port}")
    async with server:
        await server.serve_forever()
