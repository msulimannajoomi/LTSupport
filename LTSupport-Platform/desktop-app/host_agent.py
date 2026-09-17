import socket
import threading
import time
import io
import queue
import collections
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
# How many frames can be in flight (sent, not yet acked) at once. A real deployment
# means real geographic round-trip time -- 150-300ms is normal for a distant server,
# not a sign of a bad link. Bounding to exactly 1 in-flight frame (the previous design)
# hard-caps the frame rate to roughly 1000/RTT regardless of available bandwidth, which
# is what "the stream is so slow" actually was. A small window decouples throughput
# from RTT while still bounding backlog far short of unbounded bufferbloat.
FRAME_WINDOW = 3
# Upper bound on how long the capture loop waits for a free slot in that window before
# skipping this capture cycle and trying again -- without this cap, a lost ack (dropped
# packet, viewer on an older build) would stall the stream forever instead of degrading
# gracefully.
FRAME_ACK_TIMEOUT = 2.0
# Round-trip thresholds for the adaptive quality step in _on_frame_ack. Set well above
# typical cross-region latency (150-300ms) so ordinary geography doesn't get mistaken
# for network trouble and needlessly throttled -- propagation delay from distance isn't
# something a smaller/lower-quality frame can fix anyway; these only kick in for RTTs
# that actually indicate queuing/congestion on the link itself.
RTT_DEGRADE_THRESHOLD = 0.8
RTT_IMPROVE_THRESHOLD = 0.3
# Delays between retries when the relay connection drops, whether that's before any
# viewer has ever joined (e.g. a flaky tunnel dropping an idle "waiting for a viewer"
# connection) or after one has already come and gone -- a dropped leg no longer ends the
# whole hosting run (see _recv_loop's TYPE_SESSION_END/TYPE_TRIAL_LIMIT handling), so
# there's always something worth reconnecting to resume. The first few land inside the
# relay's own GRACE_SECONDS window; attempts after that just register fresh, which the
# relay accepts the same way. Retried indefinitely, same as the viewer side's own
# indefinite reconnect (see RECONNECT_RETRY_INTERVAL in viewer_agent.py) -- only this
# host's own explicit Stop Hosting (self.running = False) ever gives up.
RELAY_RECONNECT_DELAYS = [1, 2, 4, 8, 8]
RELAY_RECONNECT_RETRY_INTERVAL = 20

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
        # Host's own system/speaker output (e.g. a YouTube video playing on the host)
        # is what gets sent to the viewer -- see _audio_loop/_system_audio_loop; the
        # host's own mic is not captured/sent anywhere at all. Queue is (re)created
        # fresh each time _audio_loop starts; None means "not currently capturing"
        # (either not started yet, or WASAPI loopback failed to open on this
        # machine), in which case _audio_loop just has nothing to send.
        self._system_audio_queue = None
        self._system_audio_leftover = None
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
        # Screen capture and system audio each run on their own thread but write to the same
        # socket -- without a lock, concurrent sendall() calls can interleave and corrupt
        # the frame stream, which looks exactly like a random, unexplained disconnect on
        # the receiving end. Most visible in Interview Mode, where both are active at once.
        self._sock_lock = threading.Lock()
        # Bounds outstanding frames to FRAME_WINDOW: the capture loop won't grab+send
        # another frame once that many are already in flight, unacked. Without this, a
        # slow/congested link lets JPEGs pile up in the OS send buffer -- each one still
        # gets sent eventually, so the viewer just watches an ever-growing backlog of
        # stale frames ("bufferbloat"). A window > 1 (rather than a strict one-at-a-time
        # Event) is what lets throughput scale beyond 1000ms/RTT on a real, geographically
        # distant connection -- see FRAME_WINDOW.
        self._frame_ack_semaphore = threading.Semaphore(FRAME_WINDOW)
        # FIFO of send timestamps, one per frame currently in flight -- paired with acks
        # in the same order on the assumption the viewer acks frames in the order it
        # receives them (true: TCP delivers in order, and the viewer acks immediately on
        # receipt), so popleft() in _on_frame_ack always matches the oldest still-unacked
        # frame.
        self._frame_sent_times = collections.deque()
        self._send_quality = 65
        self._send_width = MAX_SEND_WIDTH
        # Replaced with the host's real screen width (capped) as soon as _capture_loop
        # starts -- see there. Starting crisp and only backing off when the measured
        # round-trip actually calls for it looks far better than always starting
        # downscaled "to be safe."
        self._max_send_width = MAX_SEND_WIDTH
        # The actual pixel dimensions of whatever mss captures (sct.monitors[0] -- the
        # full virtual desktop across every monitor), set for real once _capture_loop
        # starts. Every relative (0..1) position/pointer coordinate from the viewer
        # gets multiplied back into absolute host pixels against THESE, not a fresh
        # pyautogui.size() call -- pyautogui.size() only ever reports the PRIMARY
        # monitor, which silently disagrees with this on any multi-monitor host and is
        # exactly what let a positioned text/pointer land in the wrong spot there. The
        # pyautogui fallback here only covers the brief window before the capture loop
        # has actually measured the real thing.
        try:
            self._monitor_width, self._monitor_height = pyautogui.size()
        except Exception:
            self._monitor_width, self._monitor_height = 1920, 1080

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
        # Host system audio -> viewer runs in both Normal and Interview Mode -- see the
        # matching comment in _recv_loop's TYPE_VIEWER_JOINED branch (the relay-mode path).
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
            self.api.session_report(minutes, self._session_type, device_id=self.device_id or "")
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
        # Frames in flight on the old (now-dead) connection are irrelevant -- reset the
        # window to fully available rather than leaving it partially checked-out with no
        # acks ever coming for those frames.
        self._frame_ack_semaphore = threading.Semaphore(FRAME_WINDOW)
        self._frame_sent_times.clear()
        return True

    def _relay_reconnect(self):
        attempt = 0
        while self.running:
            attempt += 1
            delay = (RELAY_RECONNECT_DELAYS[attempt - 1] if attempt <= len(RELAY_RECONNECT_DELAYS)
                      else RELAY_RECONNECT_RETRY_INTERVAL)
            time.sleep(delay)
            if not self.running:
                return False
            self.on_status("reconnecting", {"attempt": attempt})
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
            # A viewer's own session ending (TYPE_SESSION_END/TYPE_TRIAL_LIMIT) no longer
            # counts as "done" -- _recv_loop keeps looping through those on its own,
            # waiting for the next viewer, so getting here at all means either a real
            # protocol error (clean=True) or the connection itself actually dropped
            # (clean=False, worth reconnecting through) or Stop Hosting was clicked.
            if clean or not self.running:
                break
            if not self._relay_reconnect():
                break
            threading.Thread(target=self._capture_loop, daemon=True).start()

        self._finish_session()

    def _capture_loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            # The real, authoritative pixel dimensions of what's actually being
            # captured and sent -- see the matching comment in __init__ for why
            # position/pointer math uses these instead of pyautogui.size(), and below
            # for why the overlay's own font-size scaling now uses this same number
            # too (it used to independently query its own window's screen width,
            # which -- like pyautogui.size() -- only ever reflects the PRIMARY
            # monitor, silently disagreeing with this on a multi-monitor host).
            self._monitor_width, self._monitor_height = monitor["width"], monitor["height"]
            if self.overlay_app:
                self.overlay_app.root.after(0, self.overlay_app.sync_screen_dimensions,
                                             monitor["width"], monitor["height"])
            # Cap at 1920 even on a larger/multi-monitor host -- a full-quality frame
            # much wider than that gets expensive to encode/send every ~30ms for a gain
            # the viewer's own display usually can't even show.
            self._max_send_width = min(monitor["width"], 1920)
            self._send_width = self._max_send_width
            # How many consecutive full-window timeouts (see FRAME_ACK_TIMEOUT) to
            # tolerate before assuming the missing acks are never coming and
            # self-healing -- see the comment below.
            consecutive_timeouts = 0
            while self.running:
                try:
                    # Wait for a free slot in the in-flight window (see FRAME_WINDOW)
                    # rather than a strict one-at-a-time ack.
                    if self._frame_ack_semaphore.acquire(timeout=FRAME_ACK_TIMEOUT):
                        consecutive_timeouts = 0
                    else:
                        consecutive_timeouts += 1
                        if consecutive_timeouts < 2:
                            continue
                        # The whole window has sat checked out with zero acks for
                        # ~2*FRAME_ACK_TIMEOUT seconds straight. That's not "the link is
                        # slow" (a slow link still delivers *some* acks, just later) --
                        # it means the in-flight frames' acks are never coming at all.
                        # One real way that happens: the relay briefly holds a dropped
                        # viewer's slot open to let it reconnect (GRACE_SECONDS in
                        # relay.py) and silently discards any frames sent during that
                        # window since there's no viewer attached to forward them to --
                        # their acks can then never arrive, permanently leaking permits
                        # off this window one grace period at a time until it's stuck at
                        # zero forever, which looked like the video just stopping dead
                        # while everything else in the session kept working. Self-heal
                        # by resetting to a fresh, fully-available window instead of
                        # staying wedged.
                        self._frame_ack_semaphore = threading.Semaphore(FRAME_WINDOW)
                        self._frame_sent_times.clear()
                        self._frame_ack_semaphore.acquire()  # always succeeds, just reset
                        consecutive_timeouts = 0

                    img = sct.grab(monitor)
                    pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                    self._queue_frame_for_recording(pil_img)

                    send_img = pil_img
                    width = self._send_width
                    if send_img.width > width:
                        scale = width / send_img.width
                        send_img = send_img.resize((width, max(1, int(send_img.height * scale))))

                    buf = io.BytesIO()
                    # subsampling=0 forces full-resolution color (4:4:4) instead of
                    # JPEG's default 4:2:0, which halves color detail in both directions.
                    # Barely matters for photo-like content, but it's exactly what was
                    # blurring sharp text edges and thin strokes -- the instructor
                    # overlay, cursors, UI text -- since those are almost pure color
                    # transitions, not gradients. Costs some bandwidth for a real gain in
                    # legibility on exactly the content this was complained about.
                    send_img.save(buf, format="JPEG", quality=self._send_quality, subsampling=0)
                    self._frame_sent_times.append(time.time())
                    self._send(proto.TYPE_SCREEN_FRAME, buf.getvalue())
                    time.sleep(0.03)
                except Exception as e:
                    print(f"[Capture] loop ended: {type(e).__name__}: {e}")
                    break

    def _on_frame_ack(self):
        """Called from _recv_loop when the viewer acks a frame. Adjusts outgoing quality/
        resolution from the measured round-trip time -- eases up under a genuinely
        congested link, creeps back up once it recovers -- then frees a slot in the
        in-flight window for the capture loop to send another frame."""
        if self._frame_sent_times:
            # FIFO: pairs with the oldest still-unacked frame, on the assumption acks
            # arrive in the same order frames were sent (true given TCP's in-order
            # delivery and the viewer acking immediately on receipt).
            rtt = time.time() - self._frame_sent_times.popleft()
            if rtt > RTT_DEGRADE_THRESHOLD:
                self._send_quality = max(MIN_JPEG_QUALITY, self._send_quality - 10)
                self._send_width = max(MIN_SEND_WIDTH, self._send_width - 128)
            elif rtt < RTT_IMPROVE_THRESHOLD:
                self._send_quality = min(MAX_JPEG_QUALITY, self._send_quality + 5)
                self._send_width = min(self._max_send_width, self._send_width + 64)
        self._frame_ack_semaphore.release()

    def _audio_loop(self):
        """Sends the host's own system/speaker output (a YouTube video, music, any
        other app's audio) to the viewer -- NOT the host's mic. Explicitly not
        mixed with the mic (a mixed version existed briefly and was deliberately
        reverted): this channel is system audio only now. The host's own mic isn't
        captured/sent anywhere in this codebase at all any more."""
        try:
            import numpy as np
        except Exception as e:
            print(f"[Audio] numpy unavailable: {e}")
            return

        # Fresh queue/leftover-buffer for this run -- _system_audio_loop (its own
        # thread, started below) pushes captured chunks in; _pull_system_audio drains
        # and converts them below.
        self._system_audio_queue = queue.Queue()
        self._system_audio_leftover = np.array([], dtype="int16")
        threading.Thread(target=self._system_audio_loop, daemon=True).start()

        try:
            while self.running and self._audio_active:
                chunk = self._pull_system_audio(config.AUDIO_BLOCK_SIZE)
                if chunk is None:
                    # System audio capture hasn't started yet (or failed outright) --
                    # nothing to send this round. Keep pacing at roughly real-time
                    # rather than spinning, in case it recovers (e.g. still opening).
                    time.sleep(config.AUDIO_BLOCK_SIZE / config.AUDIO_SAMPLE_RATE)
                    continue
                pcm_bytes = chunk.tobytes()
                self._send(proto.TYPE_AUDIO_FRAME, pcm_bytes)
                if self.recorder:
                    self.recorder.write_host_audio(pcm_bytes)
        except Exception:
            pass
        finally:
            self._system_audio_queue = None

    def _system_audio_loop(self):
        """Captures whatever's playing through the host's own speakers (a YouTube
        video, music, any other app's audio) via WASAPI loopback, so the viewer can
        hear it. sounddevice's WASAPI bindings don't expose PortAudio's loopback flag
        (verified against the installed version, not assumed), so this uses
        PyAudioWPatch instead, a PortAudio fork built specifically to add it. Pushes
        raw (bytes, native_rate, native_channels) chunks into
        self._system_audio_queue for _pull_system_audio to downmix/resample down to
        config.AUDIO_SAMPLE_RATE mono -- WASAPI loopback has to be opened at the
        device's own native format, never an arbitrary one. Any failure here (no
        PyAudioWPatch, no WASAPI device, anything) just leaves the queue without a
        live producer -- _audio_loop's own None-check already handles that (nothing
        gets sent to the viewer at all until/unless this recovers)."""
        try:
            import pyaudiowpatch as pyaudio
        except Exception as e:
            print(f"[Audio] System audio capture unavailable: {e}")
            return

        q = self._system_audio_queue
        p = pyaudio.PyAudio()
        stream = None
        try:
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            device = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            if not device["isLoopbackDevice"]:
                for loopback in p.get_loopback_device_info_generator():
                    if device["name"] in loopback["name"]:
                        device = loopback
                        break
            native_rate = int(device["defaultSampleRate"])
            native_channels = int(device["maxInputChannels"])
            # Roughly one config.AUDIO_BLOCK_SIZE-sized chunk's worth of time, but at
            # the loopback device's own native rate (WASAPI loopback must be opened
            # at the device's real rate -- see _pull_system_audio for the conversion
            # back down to config.AUDIO_SAMPLE_RATE).
            native_blocksize = max(1, round(config.AUDIO_BLOCK_SIZE * native_rate / config.AUDIO_SAMPLE_RATE))

            def callback(in_data, frame_count, time_info, status):
                if self.running and self._audio_active and self._system_audio_queue is q:
                    q.put((in_data, native_rate, native_channels))
                    return (in_data, pyaudio.paContinue)
                return (in_data, pyaudio.paComplete)

            stream = p.open(format=pyaudio.paInt16, channels=native_channels, rate=native_rate,
                             frames_per_buffer=native_blocksize, input=True,
                             input_device_index=device["index"], stream_callback=callback)
            stream.start_stream()
            while self.running and self._audio_active and self._system_audio_queue is q and stream.is_active():
                time.sleep(0.2)
        except Exception as e:
            print(f"[Audio] Could not open system audio loopback: {e}")
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            try:
                p.terminate()
            except Exception:
                pass

    def _pull_system_audio(self, target_len):
        """Returns exactly `target_len` int16 mono samples (at config.AUDIO_SAMPLE_RATE)
        of the host's own system audio, downmixing/resampling queued WASAPI loopback
        chunks (see _system_audio_loop) as needed -- or None if that capture never
        started at all (self._system_audio_queue is None), telling _audio_loop there's
        nothing to send yet at all (see its own fallback there)."""
        import numpy as np
        q = self._system_audio_queue
        if q is None:
            return None
        buf = self._system_audio_leftover
        while len(buf) < target_len:
            try:
                raw, native_rate, native_channels = q.get(timeout=0.1)
            except queue.Empty:
                # Nothing new arrived in time -- the host may simply be silent right
                # now (WASAPI's shared-mode loopback goes quiet, not zero-filled,
                # when nothing is actually playing). Pad with real silence instead
                # of blocking indefinitely for audio that might not come this round.
                buf = np.concatenate([buf, np.zeros(target_len - len(buf), dtype="int16")])
                break
            arr = np.frombuffer(raw, dtype="int16")
            if native_channels > 1:
                arr = arr.reshape(-1, native_channels).mean(axis=1).astype("int16")
            if native_rate != config.AUDIO_SAMPLE_RATE:
                src_n = len(arr)
                dst_n = max(1, round(src_n * config.AUDIO_SAMPLE_RATE / native_rate))
                arr = np.interp(np.linspace(0, src_n - 1, dst_n), np.arange(src_n),
                                 arr.astype("float32")).astype("int16")
            buf = np.concatenate([buf, arr])
        self._system_audio_leftover = buf[target_len:]
        return buf[:target_len]

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
        """Runs until this leg of the connection ends. TYPE_SESSION_END and
        TYPE_TRIAL_LIMIT don't end this at all -- see below, they just reset per-session
        state and let the loop keep running, waiting for the next viewer. Returns True
        only for a genuine protocol error, which should NOT be retried -- False for
        anything that looks like an ordinary connection drop, which _run_relay retries
        through."""
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
                    # in Normal Mode only the host's system audio goes out (see
                    # _audio_loop below), so this branch simply never fires for Normal
                    # Mode sessions.
                    if self._session_type == "interview":
                        self._play_audio(payload)
                        if self.recorder:
                            self.recorder.write_viewer_audio(payload)
                elif msg_type == proto.TYPE_VIEWER_JOINED:
                    # self._session_type was already fixed at Start Hosting -- the
                    # relay's payload here just confirms it, it can't change it.
                    self._start_recording()
                    # Host system audio -> viewer now runs in both Normal and Interview
                    # Mode -- only the viewer's own mic (see viewer_agent.py's
                    # _mic_loop) stays Interview-only, which is what makes Interview
                    # "two-way" and Normal "just the host's system audio and the screen".
                    self._audio_active = True
                    threading.Thread(target=self._audio_loop, daemon=True).start()
                    self.on_status("viewer_joined", {"session_type": self._session_type})
                elif msg_type == proto.TYPE_SESSION_END:
                    # That viewer's session is over -- not this hosting run. Reset back
                    # to the same idle state as right after Start Hosting and keep
                    # waiting on this same connection; the relay keeps this device
                    # registered and ready for the next viewer regardless. Only this
                    # host's own explicit Stop Hosting (self.running = False, checked by
                    # the while loop above) ever ends things from here now.
                    self._audio_active = False
                    self._stop_recording()
                    self.on_status("viewer_left", {})
                elif msg_type == proto.TYPE_TRIAL_LIMIT:
                    # Same as TYPE_SESSION_END above -- this viewer's plan/balance ran
                    # out, not this hosting run. A later viewer is re-checked against the
                    # account's plan fresh when they connect (see relay.py), so there's
                    # no reason to end the whole session over it here.
                    self._audio_active = False
                    self._stop_recording()
                    self.on_status("trial_limit", proto.decode_json(payload))
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
            self.mouse.position = (int(data["x"] * self._monitor_width), int(data["y"] * self._monitor_height))
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
            # Decided from the overlay's own tracked .visible flag, not root.state() --
            # state() is unreliable for this overrideredirect window and was what let
            # rapid toggling eventually get stuck permanently hidden.
            root.after(0, lambda: self.overlay_app.close_overlay() if self.overlay_app.visible else self.overlay_app.show_overlay())
        elif cmd == "overlay_style":
            root.after(0, self.overlay_app.update_style, data.get("fg"), data.get("size"),
                       data.get("family"), data.get("bold"), data.get("opacity"))
        elif cmd == "overlay_move":
            root.after(0, self.overlay_app.update_position,
                       data.get("x", 0) * self._monitor_width, data.get("y", 0) * self._monitor_height)
        elif cmd == "overlay_visibility":
            root.after(0, self.overlay_app.set_capture_visibility, data.get("visible", True))
        elif cmd == "overlay_pointer":
            if not self.pointer_overlay:
                return
            if data.get("visible", True) and "x" in data:
                x, y = data["x"] * self._monitor_width, data["y"] * self._monitor_height
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
            root.after(0, self.pointer_overlay.update_style, data.get("color"), data.get("size"))

    def launch_overlay(self, tk_root):
        try:
            top = tk.Toplevel(tk_root)
            self.overlay_app = MovableOverlay(top)
            pointer_top = tk.Toplevel(tk_root)
            self.pointer_overlay = PointerOverlay(pointer_top)
        except Exception as e:
            print(f"[Overlay] Failed to launch: {e}")
