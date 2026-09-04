"""Direct host<->viewer connection over the local network (WiFi/LAN), bypassing the
internet relay entirely for screen/audio/input traffic. Two pieces:

- LocalHostServer (host side): answers UDP discovery broadcasts from viewers on the same
  LAN, then accepts one direct TCP connection, verifies it belongs to the same account,
  and hands the raw socket back to the caller -- which from that point on runs the exact
  same length-prefixed frame protocol (protocol.py) used with the relay. Nothing about
  screen/audio/input handling needs to know or care which transport it's running over.
- discover_local_hosts / connect_local (viewer side): the other end of that handshake.

Billing is NOT skipped just because the relay is bypassed: the caller (HostAgent) still
calls the backend's /api/session/check and /api/session/report once per session over the
internet, exactly as required -- this module only replaces the internet path for the
actual live data.
"""

import json
import socket
import threading

DISCOVERY_PORT = 41234
LOCAL_TCP_PORT = 41235
MAGIC = "LTSUPPORT"
DISCOVERY_TIMEOUT = 3.5


def _send_json_line(sock, obj):
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _recv_json_line(sock, max_bytes=4096):
    buf = b""
    while b"\n" not in buf and len(buf) < max_bytes:
        chunk = sock.recv(256)
        if not chunk:
            break
        buf += chunk
    line = buf.split(b"\n", 1)[0]
    try:
        return json.loads(line.decode("utf-8"))
    except Exception:
        return None


class LocalHostServer:
    def __init__(self, device_id, verify_peer, check_session, on_viewer_connected, machine_id=None,
                 session_type="normal"):
        self.device_id = device_id
        self.verify_peer = verify_peer               # callable(peer_token) -> bool (same account as this host?)
        self.check_session = check_session          # callable(session_type) -> (allowed, limit_seconds, message)
        self.on_viewer_connected = on_viewer_connected  # callback(sock, limit_seconds, session_type)
        self.machine_id = machine_id  # this host's own machine id, to refuse a same-machine viewer
        # Chosen by the HOST at Start Hosting -- a connecting viewer has no say in this.
        self.session_type = session_type if session_type in ("normal", "interview") else "normal"
        self.running = False
        self._tcp_sock = None
        self._udp_sock = None

    def start(self):
        self.running = True
        threading.Thread(target=self._tcp_loop, daemon=True).start()
        threading.Thread(target=self._udp_loop, daemon=True).start()

    def stop(self):
        self.running = False
        for s in (self._tcp_sock, self._udp_sock):
            try:
                if s:
                    s.close()
            except Exception:
                pass

    def _udp_loop(self):
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp_sock.bind(("0.0.0.0", DISCOVERY_PORT))
        except Exception as e:
            print(f"[LocalLink] Could not bind discovery port {DISCOVERY_PORT}: {e}")
            return
        print(f"[LocalLink] Listening for discovery broadcasts on UDP {DISCOVERY_PORT} "
              f"(device_id={self.device_id})")
        while self.running:
            try:
                data, addr = self._udp_sock.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("magic") != MAGIC or msg.get("type") != "discover":
                    continue
                reply = json.dumps({
                    "magic": MAGIC, "type": "host",
                    "device_id": self.device_id, "port": LOCAL_TCP_PORT,
                    "session_type": self.session_type,
                }).encode("utf-8")
                self._udp_sock.sendto(reply, addr)
                print(f"[LocalLink] Discovery request from {addr[0]} -- replied with device_id={self.device_id}")
            except OSError:
                break
            except Exception:
                continue

    def _tcp_loop(self):
        try:
            self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._tcp_sock.bind(("0.0.0.0", LOCAL_TCP_PORT))
            self._tcp_sock.listen(1)
        except Exception as e:
            print(f"[LocalLink] Could not bind local TCP port {LOCAL_TCP_PORT}: {e}")
            return

        while self.running:
            try:
                conn, _addr = self._tcp_sock.accept()
                try:
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except Exception:
                    pass
            except OSError:
                break
            try:
                conn.settimeout(8)
                hello = _recv_json_line(conn)
                valid = (hello and hello.get("magic") == MAGIC
                         and hello.get("device_id") == self.device_id)
                if not valid:
                    conn.close()
                    continue

                if not self.verify_peer(hello.get("session_token", "")):
                    _send_json_line(conn, {"magic": MAGIC, "ok": False,
                                            "message": "This device belongs to a different account."})
                    conn.close()
                    continue

                if self.machine_id and hello.get("machine_id") == self.machine_id:
                    _send_json_line(conn, {"magic": MAGIC, "ok": False,
                                            "message": "You're already hosting this device from this "
                                                       "same computer -- connect from a different "
                                                       "machine to view it."})
                    conn.close()
                    continue

                # The session type is whatever this host was configured with at Start
                # Hosting -- a connecting viewer never gets to request or override it.
                allowed, limit_seconds, message = self.check_session(self.session_type)
                if not allowed:
                    _send_json_line(conn, {"magic": MAGIC, "ok": False, "message": message})
                    conn.close()
                    continue

                conn.settimeout(None)
                _send_json_line(conn, {"magic": MAGIC, "ok": True, "limit_seconds": limit_seconds,
                                        "session_type": self.session_type})
            except Exception as e:
                print(f"[LocalLink] Handshake failed: {type(e).__name__}: {e}")
                try:
                    conn.close()
                except Exception:
                    pass
                continue

            # One viewer at a time -- stop accepting/advertising once paired.
            self.running = False
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self.on_viewer_connected(conn, limit_seconds, self.session_type)
            return


def discover_local_hosts(timeout=DISCOVERY_TIMEOUT):
    """Broadcasts a UDP discovery request on the LAN and collects replies. Returns a list
    of (device_id, ip, port, session_type) tuples for hosts currently in Local Network
    mode -- session_type is whatever that host was configured with at Start Hosting.

    A single UDP broadcast is easy to lose (weak WiFi signal, a busy AP) with nothing to
    retry it -- resending every 500ms for the whole window costs nothing extra (hosts just
    reply again) and makes one-off packet loss far less likely to hide a real host."""
    import time

    results = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.2)
    request = json.dumps({"magic": MAGIC, "type": "discover"}).encode("utf-8")
    deadline = time.time() + timeout
    next_send = 0.0
    try:
        while time.time() < deadline:
            if time.time() >= next_send:
                try:
                    sock.sendto(request, ("255.255.255.255", DISCOVERY_PORT))
                except OSError as e:
                    print(f"[LocalLink] Could not send discovery broadcast: {e}")
                next_send = time.time() + 0.5
            try:
                data, addr = sock.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("magic") == MAGIC and msg.get("type") == "host":
                    session_type = msg.get("session_type")
                    if session_type not in ("normal", "interview"):
                        session_type = "normal"
                    if msg["device_id"] not in results:
                        print(f"[LocalLink] Discovery reply from {addr[0]}: device_id={msg['device_id']}")
                    results[msg["device_id"]] = (msg["device_id"], addr[0], msg["port"], session_type)
            except socket.timeout:
                continue
            except Exception:
                continue
    finally:
        sock.close()
    if not results:
        print("[LocalLink] Discovery: no hosts replied. If a device is actively hosting in "
              "Local Network mode and still doesn't show up, check: both devices are on the "
              "same network (not just the same internet connection), Windows Firewall allows "
              f"inbound UDP/TCP for this app, and the network/router/hotspot doesn't isolate "
              f"connected devices from each other (common on phone hotspots and public WiFi).")
    return list(results.values())


def connect_local(ip, port, session_token, device_id, machine_id=None,
                   connect_timeout=5.0, reply_timeout=30.0):
    """Connects directly to a host on the LAN. Returns (sock, limit_seconds, session_type,
    error_message). sock is None on failure. session_type is whatever the host is
    configured with -- the viewer has no say in it, only learns it from the reply.

    Two different timeouts on purpose: the TCP connect itself is on the same LAN and
    should be near-instant, but before the host can reply it makes two sequential REST
    calls of its own (verify_peer, then check_session) -- each hitting the backend's
    database. We've directly observed MongoDB Atlas taking several seconds even when
    perfectly healthy, so a short timeout here would fail a connection that's actually
    working fine, just waiting on the host's end."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(connect_timeout)
    try:
        sock.connect((ip, port))
        _send_json_line(sock, {"magic": MAGIC, "session_token": session_token, "device_id": device_id,
                                "machine_id": machine_id})
        sock.settimeout(reply_timeout)
        reply = _recv_json_line(sock)
        if not reply or not reply.get("ok"):
            message = reply.get("message") if reply else "No response from that device."
            sock.close()
            return None, None, None, message or "Connection rejected."
        sock.settimeout(None)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except Exception:
            pass
        session_type = reply.get("session_type")
        if session_type not in ("normal", "interview"):
            session_type = "normal"
        return sock, reply.get("limit_seconds"), session_type, None
    except Exception as e:
        try:
            sock.close()
        except Exception:
            pass
        return None, None, None, str(e)
