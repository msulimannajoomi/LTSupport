import os
import time
import wave
import queue
import threading

import cv2
import numpy as np

import config


def _recordings_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "LTSupport", config.RECORDINGS_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _mix_pcm16(a, b):
    """Sample-wise sum of two equal-format PCM16 mono chunks, clipped to int16 range."""
    n = min(len(a), len(b)) // 2 * 2
    arr_a = np.frombuffer(a[:n], dtype=np.int16).astype(np.int32)
    arr_b = np.frombuffer(b[:n], dtype=np.int16).astype(np.int32)
    return np.clip(arr_a + arr_b, -32768, 32767).astype(np.int16).tobytes()


class SessionRecorder:
    """Records a viewer session to disk: an .avi (video, MJPG) and a .wav (audio -- both
    call directions mixed into one track), as two separate files rather than one muxed
    file -- avoids bundling ffmpeg. Video is written at a fixed nominal frame rate, not
    timestamped per-frame, so playback speed approximates but won't exactly match real
    elapsed session time."""

    NOMINAL_FPS = 12.0
    _BLOCK_BYTES = config.AUDIO_BLOCK_SIZE * 2  # int16 = 2 bytes/sample
    _BLOCK_SECONDS = config.AUDIO_BLOCK_SIZE / config.AUDIO_SAMPLE_RATE

    def __init__(self, device_id):
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        base_name = f"{device_id}_{timestamp}".replace(":", "-")
        self.video_path = os.path.join(_recordings_dir(), base_name + ".avi")
        self.audio_path = os.path.join(_recordings_dir(), base_name + ".wav")

        self._writer = None
        self._frame_size = None

        self._wav = wave.open(self.audio_path, "wb")
        self._wav.setnchannels(config.AUDIO_CHANNELS)
        self._wav.setsampwidth(2)  # int16
        self._wav.setframerate(config.AUDIO_SAMPLE_RATE)
        self._wav_lock = threading.Lock()

        self._host_q = queue.Queue()
        self._viewer_q = queue.Queue()
        self._mix_stop = threading.Event()
        self._mix_thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._mix_thread.start()

    def write_frame(self, pil_image):
        frame = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        h, w = frame.shape[:2]
        if self._writer is None:
            self._frame_size = (w, h)
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            self._writer = cv2.VideoWriter(self.video_path, fourcc, self.NOMINAL_FPS, self._frame_size)
        if (w, h) != self._frame_size:
            frame = cv2.resize(frame, self._frame_size)
        self._writer.write(frame)

    def write_host_audio(self, pcm_bytes):
        self._host_q.put(pcm_bytes)

    def write_viewer_audio(self, pcm_bytes):
        self._viewer_q.put(pcm_bytes)

    def _mix_loop(self):
        silence = b"\x00" * self._BLOCK_BYTES
        while not self._mix_stop.is_set():
            start = time.monotonic()
            try:
                a = self._host_q.get_nowait()
            except queue.Empty:
                a = silence
            try:
                b = self._viewer_q.get_nowait()
            except queue.Empty:
                b = silence
            mixed = _mix_pcm16(a, b)
            with self._wav_lock:
                try:
                    self._wav.writeframes(mixed)
                except Exception:
                    pass
            elapsed = time.monotonic() - start
            remaining = self._BLOCK_SECONDS - elapsed
            if remaining > 0:
                time.sleep(remaining)

    def close(self):
        self._mix_stop.set()
        self._mix_thread.join(timeout=1)
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        with self._wav_lock:
            try:
                self._wav.close()
            except Exception:
                pass
