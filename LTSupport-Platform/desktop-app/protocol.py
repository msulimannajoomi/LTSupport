import struct
import json
import socket

TYPE_HOST_HELLO = 1
TYPE_VIEWER_HELLO = 2
TYPE_HELLO_OK = 3
TYPE_ERROR = 4
TYPE_SCREEN_FRAME = 5
TYPE_INPUT_EVENT = 6
TYPE_OVERLAY_CMD = 7
TYPE_HEARTBEAT = 8
TYPE_SESSION_END = 9
TYPE_VIEWER_JOINED = 10
TYPE_TRIAL_LIMIT = 11
TYPE_AUDIO_FRAME = 12
TYPE_FRAME_ACK = 13
# Sent right before deliberately closing the socket (Stop Hosting / viewer Disconnect)
# so the relay can tell that apart from an ordinary drop and skip its reconnect-grace
# window entirely instead of waiting it out for nothing -- see HOST_GRACE_SECONDS/
# GRACE_SECONDS in relay.py.
TYPE_HOST_STOPPING = 14
TYPE_VIEWER_CLOSING = 15

HEADER_FMT = ">BI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


def encode_frame(msg_type, payload=b""):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload).encode("utf-8")
    return struct.pack(HEADER_FMT, msg_type, len(payload)) + payload


def decode_json(payload: bytes):
    return json.loads(payload.decode("utf-8")) if payload else {}


def _recv_exact(sock: socket.socket, n: int):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_frame(sock: socket.socket):
    header = _recv_exact(sock, HEADER_SIZE)
    if header is None:
        return None, None
    msg_type, length = struct.unpack(HEADER_FMT, header)
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        return None, None
    return msg_type, payload


def send_frame(sock: socket.socket, msg_type, payload=b""):
    sock.sendall(encode_frame(msg_type, payload))
