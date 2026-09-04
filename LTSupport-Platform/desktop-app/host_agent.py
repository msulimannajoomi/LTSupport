import socket
import threading
import time
import io
import queue
import ctypes
import tkinter as tk

import mss
from PIL import Image
import pyautogui
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController

import config
import protocol as proto
import device_store
import local_link
from overlay import MovableOverlay, PointerOverlay
from recorder import SessionRecorder

MAX_SEND_WIDTH = 1280  # fallback ceiling before the capture loop learns the host's actual
                        # screen width (see _capture_loop) -- the local recording always
                        # gets the full-res frame regardless of this
# The viewer window always fills the whole screen (see main.py), so anything much below
# the host's actual resolution gets visibly upscaled and soft. These floors keep the
# adaptive step in _on_frame_ack from ever throttling down past "still legible" even
# under a sustained slow link -- previously (640px @ quality 20) a bad connection could
# ratchet all the way down to illegible and then never recover, because on a link whose
# latency is dominated by a slow relay/tunnel hop rather than payload size, a smaller
# frame doesn't actually bring the round-trip time back under the "improve" threshold.
MIN_SEND_WIDTH = 960
MAX_JPEG_QUALITY = 85
MIN_JPEG_QUALITY = 40
# Upper bound on how long the capture loop waits for the previous frame's ack before
# sending another one anyway -- without this cap, a lost ack (dropped packet, viewer on
# an older build) would stall the stream forever instead of degrading gracefully.
FRAME_ACK_TIMEOUT = 1.0
# Delays between retries when the relay connection drops before any viewer has ever
# joined (e.g. a flaky tunnel dropping an idle "waiting for a viewer" connection) --
# retried silently instead of ending the hosting session and forcing the user to click
# Start Hosting again. Once a viewer HAS joined, a dropped leg ends that session instead
# (the relay tells the viewer host_disconnected), so this never applies mid-session.
RELAY_RECONNECT_DELAYS = [1, 2, 4, 8, 8]

KEY_MAPPING = {
    'BackSpace': 'backspace', 'Return': 'enter', 'Tab': 'tab', 'Escape': 'esc',
    'Delete': 'delete', 'Up': 'up', 'Down': 'down', 'Left': 'left', 'Right': 'right',
    'Prior': 'page_up', 'Next': 'page_down', 'Home': 'home', 'End': 'end',
    'Caps_Lock': 'caps_lock', 'Num_Lock': 'num_lock', 'Scroll_Lock': 'scroll_lock',
    'Shift_L': 'shift', 'Shift_R': 'shift_r', 'Control_L': 'ctrl', 'Control_R': 'ctrl_r',
    'Alt_L': 'alt', 'Alt_R': 'alt_r', 'space': 'space', 'Win_L': 'cmd', 'Win_R': 'cmd_r',
    'F1': 'f1', 'F2': 'f2', 'F3': 'f3', 'F4': 'f4', 'F5': 'f5', 'F6': 'f6',
    'F7': 'f7', 'F8': 'f8', 'F9': 'f9', 'F10': 'f10', 'F11': 'f11', 'F12': 'f12',
}


def _hide_system_cursor():
    """Hides the host's own visible mouse pointer while the viewer has explicitly taken
    Control Mode, so whoever is physically at this machine doesn't see the arrow darting
    around under remote control -- clicks/moves still land correctly (Windows still
    tracks the real cursor position internally for hit-testing; this only stops drawing
    it). ShowCursor is a display reference count, not a plain on/off switch, so this
    loops until it's actually hidden rather than assuming one call does it."""
    try:
        while ctypes.windll.user32.ShowCursor(False) >= 0:
            pass
    except Exception:
        pass


def _show_system_cursor():
    """Restores the host's own visible mouse pointer when Control Mode is released (or
    the session ends while it was still active). Loops until the display count is back
    to visible, which also makes this safe to call defensively even when nothing was
    hidden in the first place."""
    try:
        while ctypes.windll.user32.ShowCursor(True) < 0:
            pass
    except Exception:
        pass


class HostAgent:
    def __init__(self, session_token, device_name, on_status, api=None, local_mode=False, session_type="normal"):
        self.session_token = session_token
        self.device_name = device_name
        self.on_status = on_status  # callback(status: str, info: dict)
        self.api = api              # ApiClient, required when local_mode=True (billing check/report)
        self.local_mode = local_mode
        self.sock = None
        self.running = False
        self.device_id = None
        self.overlay_app = None
        self.pointer_overlay = None
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self._audio_active = False
        self._playback_stream = None
        self.local_server = None
        self._limit_timer = None
        self._session_started_at = None
        self._usage_reported = False
        # Chosen by the user on the Host screen at Start Hosting -- fixed for the whole
        # time this device is hosting; a connecting viewer never gets to request or
        # override it.
        self._session_type = session_type if session_type in ("normal", "interview") else "normal"
        self.recorder = None
        self.last_recording_paths = None
        self._record_queue = None
        self._record_thread = None
        # Screen capture and mic audio each run on their own thread but write to the same
        # socket -- without a lock, concurrent sendall() calls can interleave and corrupt
        # the frame stream, which looks exactly like a random, unexplained disconnect on
        # the receiving end. Most visible in Interview Mode, where both are active at once.
        self._sock_lock = threading.Lock()
        # Bounds outstanding frames to 1: the capture loop won't grab+send the next frame
        # until the viewer has acked the last one (or FRAME_ACK_TIMEOUT elapses). Without
        # this, a slow/congested link lets JPEGs pile up in the OS send buffer -- each one
        # still gets sent eventually, so the viewer just watches an ever-growing backlog of
        # stale frames ("bufferbloat"), which is what "gets laggier over time" actually is.
        # Starts set so the very first frame goes out immediately.
        self._frame_ack_event = threading.Event()
        self._frame_ack_event.set()
        self._frame_sent_at = None
        self._send_quality = 65
        self._send_width = MAX_SEND_WIDTH
        # Replaced with the host's real screen width (capped) as soon as _capture_loop
        # starts -- see there. Starting crisp and only backing off when the measured
        # round-trip actually calls for it looks far better than always starting
        # downscaled "to be safe."
        self._max_send_width = MAX_SEND_WIDTH
        # Relay mode only: whether a viewer has joined yet this hosting run -- gates
        # whether a dropped relay connection is worth silently retrying (see
        # RELAY_RECONNECT_DELAYS) or means the session is genuinely over.
        self._had_viewer = False

    def _send(self, msg_type, payload=b""):
        with self._sock_lock:
            proto.send_frame(self.sock, msg_type, payload)

    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.running = False
        if self.local_server:
            self.local_server.stop()
        if self._limit_timer:
            self._limit_timer.cancel()
        if self._playback_stream:
            try:
                self._playback_stream.stop()
                self._playback_stream.close()
            except Exception:
                pass
        self._stop_recording()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def _start_recording(self):
        try:
            self.recorder = SessionRecorder(self.device_id)
            self._record_queue = queue.Queue(maxsize=4)
            self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
            self._record_thread.start()
        except Exception as e:
            print(f"[Recorder] Could not start host-side recording: {e}")
            self.recorder = None

    def _record_loop(self):
        while True:
            item = self._record_queue.get()
            if item is None:
                break
            try:
                self.recorder.write_frame(item)
            except Exception:
                pass

    def _queue_frame_for_recording(self, pil_img):
        if not self.recorder or not self._record_queue:
            return
        try:
            self._record_queue.put_nowait(pil_img)
        except queue.Full:
            pass  # recording falling behind -- drop the frame rather than slow the live session

    def _stop_recording(self):
        if not self.recorder:
            return
        if self._record_queue:
            self._record_queue.put(None)
        if self._record_thread:
            self._record_thread.join(timeout=2)
        self.recorder.close()
        self.last_recording_paths = (self.recorder.video_path, self.recorder.audio_path)
        self.recorder = None
        self._record_queue = None
        self._record_thread = None

    def _run(self):
        if self.local_mode:
            self._run_local()
        else:
            self._run_relay()

    def _run_local(self):
        org_id = self.api.org_id if self.api else None
        self.device_id = device_store.load_device_id(org_id) or f"LOCAL-{socket.gethostname()}"
        device_store.save_device_id(self.device_id, org_id)
        self.on_status("online", {"device_id": self.device_id, "device_name": self.device_name})

        self.local_server = local_link.LocalHostServer(
            self.device_id, self._verify_peer, self._check_session, self._on_local_viewer_connected,
            machine_id=device_store.load_machine_id(), session_type=self._session_type,
        )
        self.local_server.start()

    def _verify_peer(self, peer_token):
        try:
            return self.api.verify_peer(peer_token)
        except Exception:
            return False

    def _check_session(self, session_type):
        try:
            result = self.api.session_check(session_type)
            return result["allowed"], result["limit_seconds"], result.get("message")
        except Exception:
            return False, None, "Could not verify your account plan. Check your internet connection and try again."

    def _on_local_viewer_connected(self, sock, limit_seconds, session_type):
        # session_type here always matches self._session_type -- it's this host's own
        # configured value, echoed back by local_link after the handshake.
        self.sock = sock
        self._session_started_at = time.time()
        self._usage_reported = False
        self._start_recording()
        # Host mic -> viewer runs in both Normal and Interview Mode -- see the matching
        # comment in _recv_loop's TYPE_VIEWER_JOINED branch (the relay-mode path).
        self._audio_active = True
        threading.Thread(target=self._audio_loop, daemon=True).start()
        threading.Thread(target=self._capture_loop, daemon=True).start()
        if limit_seconds is not None:
            self._limit_timer = threading.Timer(limit_seconds, self._local_limit_reached)
            self._limit_timer.daemon = True
            self._limit_timer.start()
        self.on_status("viewer_joined", {"session_type": self._session_type})
        self._recv_loop()
        self._finish_session()

    def _local_limit_reached(self):
        message = {"code": "PLAN_LIMIT_REACHED", "message": "Your session time limit was reached."}
        try:
            self._send(proto.TYPE_TRIAL_LIMIT, message)
        except Exception:
            pass
        self._report_local_usage()
        try:
            self.sock.close()
        except Exception:
            pass

    def _report_local_usage(self):
        if self._usage_reported or not self.api or self._session_started_at is None:
            return
        self._usage_reported = True
        minutes = (time.time() - self._session_started_at) / 60
        try:
            self.api.session_report(minutes, self._session_type)
        except Exception:
            pass

    def _relay_register(self, silent):
        """One connect+register attempt against the relay. On success, leaves self.sock
        and self.device_id set and returns True. `silent` suppresses the "error" status
        callback -- used while silently retrying (see RELAY_RECONNECT_DELAYS) so a
        mid-retry failure doesn't flash an error the user can't do anything about."""
        org_id = self.api.org_id if self.api else None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Stays in effect through the whole handshake below, not just connect() --
            # cleared only after HELLO_OK actually arrives. Previously this was cleared
            # right after connect(), leaving the handshake with NO timeout at all: if the
            # relay was ever slow to respond, this could hang indefinitely with nothing
            # shown to the user -- and since this runs on a background thread, a timeout
            # exception here (if one had been set) would otherwise silently kill the
            # thread with no error surfaced at all. Wrapping the whole thing in one try
            # fixes both.
            sock.settimeout(15)  # matches the relay's own HELLO_TIMEOUT
            sock.connect((config.RELAY_HOST, config.RELAY_PORT))

            # Once we already have a device_id (either from a prior run, or assigned by
            # the relay earlier this run), reuse it so a reconnect keeps the same id
            # instead of registering as a "new" device each time.
            saved_id = self.device_id or device_store.load_device_id(org_id)
            # A Local Mode fallback id (see _run_local) is only ever meant for that path
            # -- reusing it here would show a confusing "LOCAL-..." id for what's now an
            # internet-hosted device, even though the relay would happily accept it
            # (same account). Let the relay generate its normal id format instead.
            if saved_id and saved_id.startswith("LOCAL-"):
                saved_id = ""
            proto.send_frame(sock, proto.TYPE_HOST_HELLO, {
                "session_token": self.session_token,
                "device_id": saved_id,
                "device_name": self.device_name,
                "machine_id": device_store.load_machine_id(),
                "session_type": self._session_type,
            })

            msg_type, payload = proto.recv_frame(sock)
            if msg_type != proto.TYPE_HELLO_OK:
                if not silent:
                    info = proto.decode_json(payload) if payload else {}
                    self.on_status("error", {"message": info.get("message", "Registration failed.")})
                sock.close()
                return False

            sock.settimeout(None)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass
        except Exception as e:
            if not silent:
                self.on_status("error", {"message": f"Could not reach relay server: {e}"})
            return False

        self.sock = sock
        info = proto.decode_json(payload)
        self.device_id = info["device_id"]
        device_store.save_device_id(self.device_id, org_id)
        self._frame_ack_event.set()
        return True

    def _relay_reconnect(self):
        for attempt, delay in enumerate(RELAY_RECONNECT_DELAYS, start=1):
            if not self.running:
                return False
            time.sleep(delay)
            if not self.running:
                return False
            self.on_status("reconnecting", {"attempt": attempt, "max_attempts": len(RELAY_RECONNECT_DELAYS)})
            if self._relay_register(silent=True):
                # Deliberately not "online" -- that status also (re-)launches the overlay
                # window in host_view.py, which only needs to happen once per hosting run.
                self.on_status("resumed", {"device_id": self.device_id, "device_name": self.device_name})
                return True
        return False

    def _run_relay(self):
        if not self._relay_register(silent=False):
            return
        self.on_status("online", {"device_id": self.device_id, "device_name": self.device_name})
        threading.Thread(target=self._capture_loop, daemon=True).start()

        while True:
            clean = self._recv_loop()
            # Only worth retrying while still waiting for the very first viewer -- once
            # one has joined, a dropped leg ends that specific session instead (the relay
            # tells the viewer host_disconnected), so there's nothing left to resume here.
            if clean or self._had_viewer or not self.running:
                break
            if not self._relay_reconnect():
                break
            threading.Thread(target=self._capture_loop, daemon=True).start()

        self._finish_session()

    def _capture_loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            # Cap at 1920 even on a larger/multi-monitor host -- a full-quality frame
            # much wider than that gets expensive to encode/send every ~30ms for a gain
            # the viewer's own display usually can't even show.
            self._max_send_width = min(monitor["width"], 1920)
            self._send_width = self._max_send_width
            while self.running:
                try:
                    # Wait for the previous frame's ack before sending the next one -- see
                    # the comment on _frame_ack_event in __init__. The timeout keeps this
                    # from stalling forever if an ack is lost.
                    self._frame_ack_event.wait(timeout=FRAME_ACK_TIMEOUT)
                    self._frame_ack_event.clear()

                    img = sct.grab(monitor)
                    pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                    self._queue_frame_for_recording(pil_img)

                    send_img = pil_img
                    width = self._send_width
                    if send_img.width > width:
                        scale = width / send_img.width
                        send_img = send_img.resize((width, max(1, int(send_img.height * scale))))

                    buf = io.BytesIO()
                    send_img.save(buf, format="JPEG", quality=self._send_quality)
                    self._frame_sent_at = time.time()
                    self._send(proto.TYPE_SCREEN_FRAME, buf.getvalue())
                    time.sleep(0.03)
                except Exception as e:
                    print(f"[Capture] loop ended: {type(e).__name__}: {e}")
                    break

    def _on_frame_ack(self):
        """Called from _recv_loop when the viewer acks a frame. Adjusts outgoing quality/
        resolution from the measured round-trip time -- eases up under a slow link, creeps
        back up once it recovers -- then releases the capture loop to send the next frame."""
        if self._frame_sent_at is not None:
            rtt = time.time() - self._frame_sent_at
            if rtt > 0.35:
                self._send_quality = max(MIN_JPEG_QUALITY, self._send_quality - 10)
                self._send_width = max(MIN_SEND_WIDTH, self._send_width - 128)
            elif rtt < 0.12:
                self._send_quality = min(MAX_JPEG_QUALITY, self._send_quality + 5)
                self._send_width = min(self._max_send_width, self._send_width + 64)
        self._frame_ack_event.set()

    def _audio_loop(self):
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
            while self.running and self._audio_active:
                data, _ = stream.read(config.AUDIO_BLOCK_SIZE)
                pcm_bytes = data.tobytes()
                self._send(proto.TYPE_AUDIO_FRAME, pcm_bytes)
                if self.recorder:
                    self.recorder.write_host_audio(pcm_bytes)
        except Exception:
            pass
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _play_audio(self, pcm_bytes):
        try:
            import sounddevice as sd
            import numpy as np
            if self._playback_stream is None:
                self._playback_stream = sd.OutputStream(
                    samplerate=config.AUDIO_SAMPLE_RATE,
                    channels=config.AUDIO_CHANNELS,
                    dtype="int16",
                )
                self._playback_stream.start()
            data = np.frombuffer(pcm_bytes, dtype="int16").reshape(-1, config.AUDIO_CHANNELS)
            self._playback_stream.write(data)
        except Exception:
            pass

    def _recv_loop(self):
        """Runs until this leg of the connection ends. Returns True if it ended for a
        reason that should NOT be retried (viewer_left/trial_limit/relay error) -- False
        for anything that looks like an ordinary connection drop, which _run_relay may
        retry through (see _had_viewer there)."""
        try:
            while self.running:
                msg_type, payload = proto.recv_frame(self.sock)
                if msg_type is None:
                    return False
                if msg_type == proto.TYPE_INPUT_EVENT:
                    self._process_input(proto.decode_json(payload))
                elif msg_type == proto.TYPE_FRAME_ACK:
                    self._on_frame_ack()
                elif msg_type == proto.TYPE_OVERLAY_CMD:
                    self._process_overlay(proto.decode_json(payload))
                elif msg_type == proto.TYPE_AUDIO_FRAME:
                    # The viewer's own mic is only ever sent in Interview Mode (two-way);
                    # in Normal Mode only the host's mic goes out (see _audio_loop below),
                    # so this branch simply never fires for Normal Mode sessions.
                    if self._session_type == "interview":
                        self._play_audio(payload)
                        if self.recorder:
                            self.recorder.write_viewer_audio(payload)
                elif msg_type == proto.TYPE_VIEWER_JOINED:
                    # self._session_type was already fixed at Start Hosting -- the
                    # relay's payload here just confirms it, it can't change it.
                    self._had_viewer = True
                    self._start_recording()
                    # Host mic -> viewer now runs in both Normal and Interview Mode --
                    # only the viewer's own mic (see viewer_agent.py's _mic_loop) stays
                    # Interview-only, which is what makes Interview "two-way" and Normal
                    # "just the host's voice and the screen".
                    self._audio_active = True
                    threading.Thread(target=self._audio_loop, daemon=True).start()
                    self.on_status("viewer_joined", {"session_type": self._session_type})
                elif msg_type == proto.TYPE_SESSION_END:
                    self._audio_active = False
                    self.on_status("viewer_left", {})
                    return True
                elif msg_type == proto.TYPE_TRIAL_LIMIT:
                    self._audio_active = False
                    self.on_status("trial_limit", proto.decode_json(payload))
                    return True
                elif msg_type == proto.TYPE_ERROR:
                    self.on_status("error", proto.decode_json(payload))
                    return True
        except Exception as e:
            print(f"[HostAgent] recv_loop ended: {type(e).__name__}: {e}")
            return False
        return False

    def _finish_session(self):
        self.running = False
        _show_system_cursor()  # defensive: in case the session ended while still in Control Mode
        if self._limit_timer:
            self._limit_timer.cancel()
        if self.local_mode:
            self._report_local_usage()
        self.on_status("offline", {})

    def _process_input(self, data):
        cmd_type = data.get("type")
        if cmd_type == "mouse_move":
            scr_w, scr_h = pyautogui.size()
            self.mouse.position = (int(data["x"] * scr_w), int(data["y"] * scr_h))
        elif cmd_type == "mouse_click":
            btn = {"left": Button.left, "right": Button.right, "middle": Button.middle}.get(data["button"], Button.left)
            if data["pressed"]:
                self.mouse.press(btn)
            else:
                self.mouse.release(btn)
        elif cmd_type in ("key_press", "key_release"):
            k_name = KEY_MAPPING.get(data["key"], data["key"])
            k = getattr(Key, k_name) if hasattr(Key, k_name) else k_name
            if cmd_type == "key_press":
                self.keyboard.press(k)
            else:
                self.keyboard.release(k)

    def _process_overlay(self, data):
        cmd = data.get("type")
        if cmd == "control_start":
            _hide_system_cursor()
            return
        if cmd == "control_end":
            _show_system_cursor()
            return
        if not self.overlay_app:
            return
        root = self.overlay_app.root
        if cmd == "overlay_text":
            root.after(0, self.overlay_app.update_text, data.get("text", ""))
        elif cmd == "overlay_toggle":
            root.after(0, lambda: self.overlay_app.close_overlay() if root.state() == "normal" else self.overlay_app.show_overlay())
        elif cmd == "overlay_style":
            root.after(0, self.overlay_app.update_style, data.get("fg"), data.get("size"))
        elif cmd == "overlay_move":
            scr_w, scr_h = pyautogui.size()
            root.after(0, self.overlay_app.update_position, data.get("x", 0) * scr_w, data.get("y", 0) * scr_h)
        elif cmd == "overlay_visibility":
            root.after(0, self.overlay_app.set_capture_visibility, data.get("visible", True))
        elif cmd == "overlay_pointer":
            if not self.pointer_overlay:
                return
            if data.get("visible", True) and "x" in data:
                scr_w, scr_h = pyautogui.size()
                x, y = data["x"] * scr_w, data["y"] * scr_h
                root.after(0, self.pointer_overlay.move_to, x, y)
                # There is no real click on this host -- "click" here just means flash
                # the marker to tell whoever's at this machine to click there themselves.
                if data.get("click"):
                    root.after(0, self.pointer_overlay.pulse)
            else:
                root.after(0, self.pointer_overlay.hide)
        elif cmd == "overlay_pointer_style":
            if not self.pointer_overlay:
                return
            root.after(0, self.pointer_overlay.update_color, data.get("color"))

    def launch_overlay(self, tk_root):
        try:
            top = tk.Toplevel(tk_root)
            self.overlay_app = MovableOverlay(top)
            pointer_top = tk.Toplevel(tk_root)
            self.pointer_overlay = PointerOverlay(pointer_top)
        except Exception as e:
            print(f"[Overlay] Failed to launch: {e}")
