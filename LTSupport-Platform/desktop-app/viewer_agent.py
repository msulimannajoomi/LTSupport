import socket
import threading
import time
import io

from PIL import Image

import config
import protocol as proto
import local_link
import device_store

HEARTBEAT_INTERVAL = 8  # seconds -- must stay well under the relay's IDLE_TIMEOUT (30s)
# Delays between reconnect attempts after an unexpected drop, in the relay-hosted case
# only (a LAN host only ever accepts one viewer -- see local_link.py -- so there's no
# point retrying there). Sums to ~11s, comfortably inside the relay's own
# GRACE_SECONDS=15 window (relay.py) so a real retry lands before that slot expires.
RECONNECT_DELAYS = [0, 1, 2, 4, 4]


class ViewerAgent:
    def __init__(self, session_token, device_id, on_frame, on_status, on_audio=None, on_own_audio=None,
                 local_target=None):
        self.session_token = session_token
        self.device_id = device_id
        self.on_frame = on_frame           # callback(pil_image)
        self.on_status = on_status         # callback(status, info)
        self.on_audio = on_audio           # callback(pcm16_mono_bytes) -- host's mic, for recording
        self.on_own_audio = on_own_audio   # callback(pcm16_mono_bytes) -- this viewer's own mic, for recording
        self.local_target = local_target   # (ip, port) to connect directly over WiFi/LAN, or None for the relay
        # Not known until the host responds -- the host alone decides Normal vs Interview
        # at Start Hosting, the viewer never requests or chooses it.
        self.session_type = "normal"
        self.sock = None
        self.running = False
        self._audio_stream = None
        self.muted = False
        # Multiple threads (mic capture, heartbeat, and Tk-thread input/overlay sends)
        # all write to the same socket -- without a lock, concurrent sendall() calls can
        # interleave and corrupt the frame stream, which looks exactly like a random,
        # unexplained disconnect on the receiving end.
        self._sock_lock = threading.Lock()
        self._explicit_close = False

    def _send(self, msg_type, payload=b""):
        with self._sock_lock:
            proto.send_frame(self.sock, msg_type, payload)

    def connect(self):
        if not self._do_connect(silent=False):
            return False
        self.running = True
        threading.Thread(target=self._session_thread, daemon=True).start()
        self.on_status("connected", {})
        return True

    def _do_connect(self, silent):
        """One connection attempt: opens the transport and completes the hello handshake.
        On success, leaves self.sock/self.session_type set and returns True. `silent`
        suppresses the "error" status callback -- used while auto-reconnecting so a
        mid-retry rejection (e.g. the relay hasn't yet noticed the old socket is dead)
        doesn't flash a scary error at the user; the final "disconnected" after all
        retries are exhausted covers that instead."""
        try:
            if self.local_target:
                ip, port = self.local_target
                sock, _limit_seconds, session_type, err = local_link.connect_local(
                    ip, port, self.session_token, self.device_id,
                    machine_id=device_store.load_machine_id())
                if sock is None:
                    if not silent:
                        self.on_status("error", {"message": err or "Could not connect directly on your network."})
                    return False
                self.session_type = session_type
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(15)  # matches the relay's own HELLO_TIMEOUT
                sock.connect((config.RELAY_HOST, config.RELAY_PORT))
                proto.send_frame(sock, proto.TYPE_VIEWER_HELLO, {
                    "session_token": self.session_token,
                    "device_id": self.device_id,
                    "machine_id": device_store.load_machine_id(),
                })
                msg_type, payload = proto.recv_frame(sock)
                if msg_type != proto.TYPE_HELLO_OK:
                    if not silent:
                        info = proto.decode_json(payload) if payload else {}
                        self.on_status("error", {"message": info.get("message", "Connection rejected.")})
                    sock.close()
                    return False
                sock.settimeout(None)
                info = proto.decode_json(payload) if payload else {}
                session_type = info.get("session_type")
                self.session_type = session_type if session_type in ("normal", "interview") else "normal"

            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass
            self.sock = sock
            return True
        except Exception as e:
            if not silent:
                self.on_status("error", {"message": str(e)})
            return False

    def _session_thread(self):
        """Owns the live connection for as long as this agent is running, transparently
        reconnecting through a brief drop (the relay holds this viewer's slot open for a
        short grace window -- see GRACE_SECONDS in relay.py) instead of ending the
        session the instant a network blip closes the socket."""
        while self.running:
            threading.Thread(target=self._mic_loop, daemon=True).start()
            threading.Thread(target=self._heartbeat_loop, daemon=True).start()
            clean = self._recv_loop()
            if not self.running or self._explicit_close or clean:
                break
            if not self._reconnect():
                break
        self.running = False
        if not self._explicit_close:
            self.on_status("disconnected", {})

    def _reconnect(self):
        if self.local_target:
            return False  # the LAN host only ever accepts one viewer -- see local_link.py
        for attempt, delay in enumerate(RECONNECT_DELAYS, start=1):
            if not self.running or self._explicit_close:
                return False
            if delay:
                time.sleep(delay)
            if not self.running or self._explicit_close:
                return False
            self.on_status("reconnecting", {"attempt": attempt, "max_attempts": len(RECONNECT_DELAYS)})
            if self._do_connect(silent=True):
                self.on_status("resumed", {})
                return True
        return False

    def _recv_loop(self):
        """Runs until this leg of the connection ends. Returns True if it ended for a
        reason that should NOT be retried -- the server said the session is genuinely
        over, or rejected it outright -- and False for anything that looks like an
        ordinary network drop, which _session_thread will try to reconnect through."""
        try:
            while self.running:
                msg_type, payload = proto.recv_frame(self.sock)
                if msg_type is None:
                    return False
                if msg_type == proto.TYPE_SCREEN_FRAME:
                    try:
                        img = Image.open(io.BytesIO(payload)).convert("RGB")
                        self.on_frame(img)
                    except Exception:
                        pass
                    try:
                        self._send(proto.TYPE_FRAME_ACK)
                    except Exception:
                        return False
                elif msg_type == proto.TYPE_AUDIO_FRAME:
                    # The host's mic plays for the viewer in both Normal and Interview
                    # Mode now -- only sending the viewer's own mic back (_mic_loop
                    # below) stays Interview-only, which is what makes Interview
                    # "two-way" and Normal "just the host's voice and the screen".
                    self._play_audio(payload)
                    if self.on_audio:
                        self.on_audio(payload)
                elif msg_type == proto.TYPE_SESSION_END:
                    self.on_status("ended", proto.decode_json(payload) if payload else {})
                    return True
                elif msg_type == proto.TYPE_TRIAL_LIMIT:
                    self.on_status("trial_limit", proto.decode_json(payload) if payload else {})
                    return True
                elif msg_type == proto.TYPE_ERROR:
                    self.on_status("error", proto.decode_json(payload))
                    return True
        except Exception as e:
            print(f"[ViewerAgent] recv_loop ended: {type(e).__name__}: {e}")
            return False
        return False

    def _play_audio(self, pcm_bytes):
        try:
            import sounddevice as sd
            import numpy as np
            if self._audio_stream is None:
                self._audio_stream = sd.OutputStream(
                    samplerate=config.AUDIO_SAMPLE_RATE,
                    channels=config.AUDIO_CHANNELS,
                    dtype="int16",
                )
                self._audio_stream.start()
            data = np.frombuffer(pcm_bytes, dtype="int16").reshape(-1, config.AUDIO_CHANNELS)
            self._audio_stream.write(data)
        except Exception:
            pass

    def _mic_loop(self):
        if self.session_type != "interview":
            return
        try:
            import sounddevice as sd
        except Exception as e:
            print(f"[Audio] sounddevice unavailable: {e}")
            return

        try:
            stream = sd.InputStream(
                samplerate=config.AUDIO_SAMPLE_RATE,
                channels=config.AUDIO_CHANNELS,
                dtype="int16",
                blocksize=config.AUDIO_BLOCK_SIZE,
            )
            stream.start()
        except Exception as e:
            print(f"[Audio] Could not open microphone: {e}")
            return

        try:
            while self.running:
                data, _ = stream.read(config.AUDIO_BLOCK_SIZE)
                if self.muted:
                    continue
                pcm_bytes = data.tobytes()
                self._send(proto.TYPE_AUDIO_FRAME, pcm_bytes)
                if self.on_own_audio:
                    self.on_own_audio(pcm_bytes)
        except Exception:
            pass
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def set_muted(self, muted):
        self.muted = muted

    def _heartbeat_loop(self):
        """Keeps the relay's idle-connection detection satisfied during stretches where
        nothing else is being sent -- e.g. a viewer just watching without moving the
        mouse, typing, or (in Normal Mode) sending any audio. Without this, the relay's
        IDLE_TIMEOUT silently drops the connection after 30s of inactivity in this
        direction, even though the session is still perfectly alive."""
        while self.running:
            time.sleep(HEARTBEAT_INTERVAL)
            if not self.running:
                break
            try:
                self._send(proto.TYPE_HEARTBEAT)
            except Exception:
                break

    def send_input(self, data):
        if self.running:
            try:
                self._send(proto.TYPE_INPUT_EVENT, data)
            except Exception:
                pass

    def send_overlay(self, data):
        if self.running:
            try:
                self._send(proto.TYPE_OVERLAY_CMD, data)
            except Exception:
                pass

    def close(self):
        self._explicit_close = True
        self.running = False
        if self._audio_stream:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:
                pass
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
