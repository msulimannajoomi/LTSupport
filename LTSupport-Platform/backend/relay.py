import asyncio
import secrets
import struct
import time

import db
import auth
import config
import plan
import protocol as p

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
        self.viewer = None  # ViewerConn or None
        self.grace = None  # dict describing a just-dropped viewer's still-resumable session, or None


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
        try:
            writer.close()
        except Exception:
            pass


async def _viewer_forward_loop(reader, host_conn):
    """Runs until the viewer's connection ends, for any reason -- including exceptions,
    which are caught and logged here (rather than left to surface as an untracked task
    exception) so a session ending unexpectedly is diagnosable from the server log
    instead of just looking like an ordinary disconnect."""
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


async def _expire_grace(host_conn, org_id, session_type, session_started):
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
    elapsed_minutes = (time.time() - session_started) / 60
    if session_type == "interview":
        await asyncio.to_thread(db.record_interview_usage, org_id, elapsed_minutes)
    else:
        await asyncio.to_thread(db.record_session_usage, org_id, elapsed_minutes)
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
    else:
        # The session type is whatever the HOST chose when it started hosting -- a viewer
        # never gets to request or override it.
        session_type = host_conn.session_type

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

        if not plan.session_allowed(user, session_type):
            await _send_error(writer, "INSUFFICIENT_BALANCE", plan.insufficient_balance_message(config.UPGRADE_CONTACT_NUMBER))
            writer.close()
            return

        session_started = time.time()
        limit_seconds = plan.session_limit_seconds(user, config.TRIAL_SESSION_LIMIT_SECONDS, session_type)

    viewer = ViewerConn(writer, user["id"], device_id)
    host_conn.viewer = viewer

    await _send(writer, p.TYPE_HELLO_OK, {"device_id": device_id, "device_name": host_conn.name, "session_type": session_type})
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
        elapsed_minutes = (time.time() - session_started) / 60
        if session_type == "interview":
            await asyncio.to_thread(db.record_interview_usage, user["org_id"], elapsed_minutes)
        else:
            await asyncio.to_thread(db.record_session_usage, user["org_id"], elapsed_minutes)
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
        }
        host_conn.grace["expire_task"] = asyncio.create_task(
            _expire_grace(host_conn, user["org_id"], session_type, session_started))

    try:
        writer.close()
    except Exception:
        pass


async def start_server(host, port):
    server = await asyncio.start_server(handle_connection, host, port)
    print(f"[Relay] Listening on {host}:{port}")
    async with server:
        await server.serve_forever()
