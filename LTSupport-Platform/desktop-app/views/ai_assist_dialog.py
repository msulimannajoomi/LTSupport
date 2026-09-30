import io
import threading
import wave
from tkinter import messagebox
import customtkinter as ctk

import config
import theme


class AiAssistDialog:
    """A separate, floating chat panel for the viewer to draft a message with an AI
    assistant's help -- entirely client-side; the host never sees this window or
    knows it exists (see viewer_view.py, the only caller). Deliberately NOT modal:
    it stays open alongside the main Viewer window so a reply can be copied from
    here and pasted into the overlay text box there, which needs that window
    focused to type into."""

    def __init__(self, parent, api):
        self.api = api
        self.messages = []  # [{"role": "user"/"assistant", "content": str}, ...], oldest first
        self.speech_recording = False
        self._speech_stream = None
        self._speech_frames = []
        self._speech_stop_timer = None
        self._closed = False

        self.top = ctk.CTkToplevel(parent)
        self.top.title("AI Assistant")
        self.top.geometry("380x540")
        self.top.minsize(320, 400)
        # A frosted/translucent look for this one window -- not a real backdrop
        # blur (that needs undocumented per-OS compositor APIs), but reads as
        # "floating over everything else" at a glance, which is the part that
        # actually matters here.
        try:
            self.top.attributes("-alpha", 0.97)
        except Exception:
            pass
        self.top.configure(fg_color=theme.BG)
        self.top.protocol("WM_DELETE_WINDOW", self.close)

        header = ctk.CTkFrame(self.top, fg_color=theme.CARD, corner_radius=0)
        header.pack(fill="x")
        header_inner = ctk.CTkFrame(header, fg_color="transparent")
        header_inner.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(header_inner, text="🤖 AI Assistant", font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(header_inner, text="Draft a reply, then copy it into your message.",
                     font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")

        self.chat_container = ctk.CTkScrollableFrame(self.top, fg_color=theme.BG)
        self.chat_container.pack(fill="both", expand=True, padx=12, pady=8)

        self.status_label = ctk.CTkLabel(self.top, text="", font=theme.small(), text_color=theme.TEXT_MUTED)
        self.status_label.pack(anchor="w", padx=16)

        input_row = ctk.CTkFrame(self.top, fg_color="transparent")
        input_row.pack(fill="x", padx=12, pady=(4, 12))
        self.entry = ctk.CTkTextbox(input_row, height=56, corner_radius=8, wrap="word",
                                     font=theme.body(), fg_color=theme.CARD, text_color=theme.TEXT)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entry.bind("<Return>", self._on_enter)

        btn_col = ctk.CTkFrame(input_row, fg_color="transparent")
        btn_col.pack(side="left")
        self.mic_btn = ctk.CTkButton(btn_col, text="🎙", width=40, height=26, corner_radius=6,
                                      fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                      command=self.toggle_mic)
        self.mic_btn.pack(pady=(0, 4))
        self.send_btn = ctk.CTkButton(btn_col, text="Send", width=40, height=26, corner_radius=6,
                                       fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color="white",
                                       font=theme.small(), command=self.send_typed)
        self.send_btn.pack()

        self._add_message("assistant", "Ask me anything, or click 🎙 to speak your question.")

    def _on_enter(self, event):
        self.send_typed()
        return "break"  # don't also insert the newline Return normally would

    def send_typed(self):
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return
        self.entry.delete("1.0", "end")
        self._send(text)

    def _send(self, text):
        self._add_message("user", text)
        self.send_btn.configure(state="disabled")
        self.status_label.configure(text="Thinking…")
        threading.Thread(target=self._ask_ai, args=(list(self.messages),), daemon=True).start()

    def _ask_ai(self, messages_snapshot):
        try:
            reply = self.api.ai_chat(messages_snapshot)
            self.top.after(0, lambda: self._on_reply(reply, None))
        except Exception as e:
            self.top.after(0, lambda: self._on_reply(None, str(e)))

    def _on_reply(self, reply, error):
        if self._closed or not self.top.winfo_exists():
            return
        self.send_btn.configure(state="normal")
        self.status_label.configure(text="")
        if error:
            self._add_message("assistant", f"⚠ {error}")
            return
        self._add_message("assistant", reply)

    def _add_message(self, role, text):
        # The system's own opening prompt isn't part of the real conversation history
        # sent back to the AI -- only real user/assistant turns are.
        if not (role == "assistant" and not self.messages):
            self.messages.append({"role": role, "content": text})
        row = ctk.CTkFrame(self.chat_container, fg_color="transparent")
        row.pack(fill="x", pady=4)
        bubble = ctk.CTkFrame(row, fg_color=theme.ACCENT if role == "user" else theme.CARD, corner_radius=10)
        bubble.pack(anchor="e" if role == "user" else "w")
        inner = ctk.CTkFrame(bubble, fg_color="transparent")
        inner.pack(padx=10, pady=8)
        text_color = "white" if role == "user" else theme.TEXT
        ctk.CTkLabel(inner, text=text, font=theme.body(), text_color=text_color, wraplength=260,
                     justify="left").pack(anchor="w")
        if role == "assistant":
            ctk.CTkButton(inner, text="📋 Copy", width=70, height=24, corner_radius=6,
                          fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                          font=theme.small(), command=lambda t=text: self._copy(t)).pack(anchor="w", pady=(6, 0))
        self.top.update_idletasks()
        try:
            self.chat_container._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _copy(self, text):
        self.top.clipboard_clear()
        self.top.clipboard_append(text)

    def toggle_mic(self):
        if self.speech_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        try:
            import sounddevice as sd
        except Exception as e:
            messagebox.showerror("Microphone Unavailable", f"Could not access the microphone: {e}")
            return
        self._speech_frames = []
        try:
            self._speech_stream = sd.InputStream(
                samplerate=config.AUDIO_SAMPLE_RATE, channels=config.AUDIO_CHANNELS, dtype="int16",
                blocksize=config.AUDIO_BLOCK_SIZE, callback=self._on_chunk,
            )
            self._speech_stream.start()
        except Exception as e:
            messagebox.showerror("Microphone Unavailable", f"Could not open the microphone: {e}")
            self._speech_stream = None
            return
        self.speech_recording = True
        self.mic_btn.configure(text="⏺", fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, text_color="white")
        # Same safety cap as the overlay's own speech-to-text button -- forgetting to
        # click Stop shouldn't quietly turn into a multi-minute recording.
        self._speech_stop_timer = self.top.after(60000, self._stop_recording)

    def _on_chunk(self, indata, frames, time_info, status):
        # Runs on sounddevice's own audio thread -- just appends, no Tk calls here.
        self._speech_frames.append(indata.tobytes())

    def _stop_recording(self):
        if self._speech_stop_timer:
            self.top.after_cancel(self._speech_stop_timer)
            self._speech_stop_timer = None
        if self._speech_stream:
            try:
                self._speech_stream.stop()
                self._speech_stream.close()
            except Exception:
                pass
            self._speech_stream = None
        self.speech_recording = False
        self.mic_btn.configure(text="🎙", fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT)
        frames = self._speech_frames
        self._speech_frames = []
        if not frames:
            return
        self.status_label.configure(text="Transcribing…")
        wav_bytes = self._frames_to_wav(frames)
        threading.Thread(target=self._transcribe_and_ask, args=(wav_bytes,), daemon=True).start()

    def _frames_to_wav(self, frames):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(config.AUDIO_CHANNELS)
            w.setsampwidth(2)  # int16
            w.setframerate(config.AUDIO_SAMPLE_RATE)
            w.writeframes(b"".join(frames))
        return buf.getvalue()

    def _transcribe_and_ask(self, wav_bytes):
        try:
            text = self.api.ai_transcribe(wav_bytes)
        except Exception as e:
            self.top.after(0, lambda: self._on_transcribe_error(str(e)))
            return
        text = (text or "").strip()
        if not text:
            self.top.after(0, lambda: self.status_label.configure(text=""))
            return
        # Speaking a question answers it right away -- unlike the overlay's own mic
        # button (which fills the text box for review before sending), there's
        # nothing to send onward here until the AI actually replies, so there's
        # nothing gained by pausing for a manual Send click in between.
        self.top.after(0, lambda: self._send(text))

    def _on_transcribe_error(self, error):
        if self._closed or not self.top.winfo_exists():
            return
        self.status_label.configure(text="")
        messagebox.showerror("Transcription Failed", error)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._speech_stream:
            try:
                self._speech_stream.stop()
                self._speech_stream.close()
            except Exception:
                pass
        self.top.destroy()
