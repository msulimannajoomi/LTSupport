import struct
import json

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

HEADER_FMT = ">BI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


def encode_frame(msg_type, payload=b""):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload).encode("utf-8")
    return struct.pack(HEADER_FMT, msg_type, len(payload)) + payload


def decode_json(payload: bytes):
    return json.loads(payload.decode("utf-8")) if payload else {}
