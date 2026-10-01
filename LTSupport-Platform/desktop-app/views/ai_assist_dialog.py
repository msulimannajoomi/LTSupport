import base64
import io
import threading
import wave
from tkinter import messagebox
from PIL import ImageGrab
import customtkinter as ctk

import config
import theme

# Shown to the AI when a screenshot is pasted with no typed question alongside it --
# Gemini still needs some text alongside the image to answer anything at all.
_DEFAULT_IMAGE_PROMPT = "What's in this screenshot?"
# A pasted screenshot is downscaled to this before sending -- a full-resolution
# capture is both slower to upload and costs more in vision tokens than the AI
# needs to actually read it.
_MAX_IMAGE_DIMENSION = 1280
_THUMBNAIL_DIMENSION = 200

# A message bubble's wraplength -- plain word-wrap width for the CTkLabel that
# displays each message (see _add_selectable_text).
_BUBBLE_TEXT_WIDTH_PX = 236

# How close to an edge (in pixels) the cursor has to be before it's treated as a
# resize handle -- this is a Windows-only cursor name set (Tk maps these to the
# native Windows resize cursors directly), consistent with the rest of this
# Windows-only desktop app.
_RESIZE_MARGIN = 6
_RESIZE_CURSORS = {
    "n": "size_ns", "s": "size_ns",
    "e": "size_we", "w": "size_we",
    "ne": "size_nesw", "sw": "size_nesw",
    "nw": "size_nwse", "se": "size_nwse",
}


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
        self._pending_image = None  # a PIL Image once a screenshot is pasted, else None

        self.top = ctk.CTkToplevel(parent)
        self.top.title("AI Assistant")
        # Anchored to the right edge of the PARENT window (the Viewer), not the
        # virtual screen -- winfo_screenwidth()/screenheight() report the combined
        # bounding box of ALL monitors on a multi-monitor setup, not the one the
        # Viewer is actually on. Positioning off that could land the dialog spanning
        # the boundary between two monitors (half on, half off) whenever the
        # Viewer wasn't on the primary/leftmost one. The parent's own on-screen
        # bounds are always within a single real monitor.
        width, height = 380, 540
        self.top.update_idletasks()
        # CTkToplevel.geometry() scales the WIDTH/HEIGHT it's given by the display's
        # DPI scaling factor before handing them to real Tk (so "380x540" here
        # actually renders as 475x675 physical pixels at 125% scaling) -- but
        # winfo_rootx/rooty/width/height on the parent are plain Tkinter, always in
        # physical pixels, and the position half of the geometry string is passed
        # through unscaled. Sizing the offset off the un-scaled 380/540 instead of
        # the real physical footprint put the window's right edge further right
        # than its actual edge by however much that scaling factor added -- enough,
        # at some scaling factors, to push part of it off the right side of the
        # screen entirely.
        scaling = ctk.ScalingTracker.get_window_scaling(self.top)
        physical_w = round(width * scaling)
        physical_h = round(height * scaling)
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + parent_w - physical_w - 24
        y = parent_y + max(24, (parent_h - physical_h) // 2)
        self.top.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.top.minsize(320, 400)
        # A genuinely see-through overlay -- low enough alpha that the screen behind
        # it (the host/viewer feed, whatever else is open) stays visible through the
        # whole window, not just a near-opaque tint. Not a real backdrop blur (that
        # needs undocumented per-OS compositor APIs); this is a plain whole-window
        # alpha instead. Also always-on-top: it's meant to float above everything
        # else on screen, not just above its own parent window.
        try:
            self.top.attributes("-alpha", 0.93)
        except Exception:
            pass
        try:
            self.top.attributes("-topmost", True)
        except Exception:
            pass
        self.top.configure(fg_color=theme.BG)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        # Without an explicit lift/focus here, a freshly-created Toplevel can spawn
        # BEHIND an already-maximized parent window on Windows and never visibly
        # appear on the first click -- only becoming visible once something else
        # (like a second click hitting the "reuse existing dialog" branch, which
        # already did call .lift()) brings it forward. -topmost above already forces
        # this, but doing it explicitly too is what actually fixed it in testing.
        self.top.lift()
        self.top.focus_force()
        self._init_edge_resize()

        header = ctk.CTkFrame(self.top, fg_color=theme.CARD, corner_radius=0)
        header.pack(fill="x")
        header_inner = ctk.CTkFrame(header, fg_color="transparent")
        header_inner.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(header_inner, text="🤖 AI Assistant", font=theme.h3(), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(header_inner, text="Draft a reply, then copy it into your message.",
                     font=theme.small(), text_color=theme.TEXT_MUTED).pack(anchor="w")
        # A hairline under the header -- otherwise the header and the chat area
        # behind it are both near-white and run together with no visible edge.
        ctk.CTkFrame(self.top, fg_color=theme.BORDER, height=1, corner_radius=0).pack(fill="x")

        self.chat_container = ctk.CTkScrollableFrame(self.top, fg_color=theme.BG)
        self.chat_container.pack(fill="both", expand=True, padx=12, pady=8)
        # Packed first and the only expand=True child -- pack gives an expanding
        # widget any leftover space in its OWN parcel, growing it downward since
        # it's listed before every message row, which pushes those rows toward the
        # bottom instead of leaving them stuck at the top with a trailing gap
        # underneath whenever the conversation is shorter than the window.
        self._chat_spacer = ctk.CTkFrame(self.chat_container, fg_color="transparent")
        self._chat_spacer.pack(fill="both", expand=True)

        self.status_label = ctk.CTkLabel(self.top, text="", font=theme.small(), text_color=theme.TEXT_MUTED)
        self.status_label.pack(anchor="w", padx=16)

        # Not packed here -- only shown once a screenshot is actually pasted (see
        # _set_pending_image/_clear_pending_image), right above the input row.
        self.attachment_row = ctk.CTkFrame(self.top, fg_color=theme.CARD, corner_radius=8,
                                            border_width=1, border_color=theme.BORDER)
        attachment_inner = ctk.CTkFrame(self.attachment_row, fg_color="transparent")
        attachment_inner.pack(fill="x", padx=8, pady=6)
        self.attachment_thumb_label = ctk.CTkLabel(attachment_inner, text="")
        self.attachment_thumb_label.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(attachment_inner, text="Screenshot attached", font=theme.small(),
                     text_color=theme.TEXT_MUTED).pack(side="left")
        ctk.CTkButton(attachment_inner, text="✕", width=24, height=24, corner_radius=6,
                      fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                      command=self._clear_pending_image).pack(side="right")

        self.input_row = ctk.CTkFrame(self.top, fg_color="transparent")
        self.input_row.pack(fill="x", padx=12, pady=(4, 12))
        # theme.CARD (near-white) on this dialog's own near-white, translucent
        # background was indistinguishable from empty space -- same issue as the
        # message bubbles, just for the input box. theme.BORDER is a visibly
        # distinct gray against both, so the input field actually reads as a
        # field instead of blank space.
        self.entry = ctk.CTkTextbox(self.input_row, height=56, corner_radius=8, wrap="word",
                                     font=theme.body(), fg_color=theme.BORDER, text_color=theme.TEXT,
                                     activate_scrollbars=False)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entry.bind("<Return>", self._on_enter)
        # A pasted screenshot (e.g. from the Windows Snipping Tool, or Win+Shift+S)
        # is image data on the clipboard, not text -- grabclipboard() returns a PIL
        # Image for that, a list of file paths for copied files, or None for plain
        # text. Only the image case is ours to handle; anything else falls through
        # to the textbox's own normal paste.
        self.entry.bind("<<Paste>>", self._on_entry_paste)

        btn_col = ctk.CTkFrame(self.input_row, fg_color="transparent")
        btn_col.pack(side="left")
        self.mic_btn = ctk.CTkButton(btn_col, text="🎙", width=40, height=26, corner_radius=6,
                                      fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                      command=self.toggle_mic)
        self.mic_btn.pack(pady=(0, 4))
        self.send_btn = ctk.CTkButton(btn_col, text="Send", width=40, height=26, corner_radius=6,
                                       fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color="white",
                                       font=theme.small(), command=self._on_send_clicked)
        self.send_btn.pack(pady=(0, 4))
        self.refresh_btn = ctk.CTkButton(btn_col, text="🔄", width=40, height=26, corner_radius=6,
                                          fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                          command=self.clear_chat)
        self.refresh_btn.pack()

        self._add_message("assistant", "Ask me anything, or click 🎙 to speak your question.")

    def _on_enter(self, event):
        self._on_send_clicked()
        return "break"  # don't also insert the newline Return normally would

    def _on_send_clicked(self):
        # Pressing Send mid-recording stops the recording AND sends it in one
        # step -- requiring a separate explicit Stop click first was pointless
        # friction when Send already means "I'm done, answer this now". Any
        # typed text sitting in the box is ignored in that case since the
        # recording is what's actually being sent (see _stop_recording, which
        # transcribes and sends on its own once it has the audio).
        if self.speech_recording:
            self._stop_recording()
            return
        self.send_typed()

    def send_typed(self):
        text = self.entry.get("1.0", "end").strip()
        if not text and not self._pending_image:
            return
        self.entry.delete("1.0", "end")
        self._send(text)

    def _on_entry_paste(self, event):
        try:
            clip = ImageGrab.grabclipboard()
        except Exception:
            clip = None
        if clip is None or isinstance(clip, list):
            return None  # plain text, or copied file paths -- let the normal paste happen
        self._set_pending_image(clip)
        return "break"  # an image paste has no text of its own to insert here

    def _set_pending_image(self, pil_image):
        self._pending_image = pil_image.convert("RGB")
        self._pending_image.thumbnail((_MAX_IMAGE_DIMENSION, _MAX_IMAGE_DIMENSION))
        thumb = self._pending_image.copy()
        thumb.thumbnail((48, 48))
        # Kept as an attribute, not just a local -- CTkImage (like Tk's own
        # PhotoImage) is only displayed for as long as something still holds a
        # reference to it; letting it fall out of scope here would blank the
        # label the next time Tk garbage-collects it.
        self._attachment_thumb_image = ctk.CTkImage(light_image=thumb, size=thumb.size)
        self.attachment_thumb_label.configure(image=self._attachment_thumb_image)
        self.attachment_row.pack(fill="x", padx=12, pady=(0, 4), before=self.input_row)

    def _clear_pending_image(self):
        self._pending_image = None
        self.attachment_row.pack_forget()

    def _send(self, text):
        image, image_b64 = None, None
        if self._pending_image is not None:
            image = self._pending_image
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            image_b64 = base64.b64encode(buf.getvalue()).decode()
            self._clear_pending_image()
            text = text or _DEFAULT_IMAGE_PROMPT
        self._add_message("user", text, image=image, image_base64=image_b64)
        self.send_btn.configure(state="disabled")
        self.status_label.configure(text="Thinking…")
        threading.Thread(target=self._ask_ai, args=(list(self.messages),), daemon=True).start()

    def _ask_ai(self, messages_snapshot):
        try:
            reply = self.api.ai_chat(messages_snapshot)
            self.top.after(0, lambda: self._on_reply(reply, None))
        except Exception as e:
            # Not `lambda: ...str(e)` -- Python deletes the except-clause variable
            # the moment this block exits, and .after() only runs the lambda much
            # later, once Tk gets around to it -- by then `e` is already gone and
            # the lambda dies with "cannot access free variable 'e'" instead of ever
            # showing the real error. Capturing the string into a plain local first
            # (unaffected by that deletion) is what actually fixes it.
            message = str(e)
            self.top.after(0, lambda: self._on_reply(None, message))

    def _on_reply(self, reply, error):
        if self._closed or not self.top.winfo_exists():
            return
        self.send_btn.configure(state="normal")
        self.status_label.configure(text="")
        if error:
            self._add_message("assistant", f"⚠ {error}")
            return
        self._add_message("assistant", reply)

    def _add_message(self, role, text, image=None, image_base64=None):
        # The system's own opening prompt isn't part of the real conversation history
        # sent back to the AI -- only real user/assistant turns are.
        if not (role == "assistant" and not self.messages):
            entry = {"role": role, "content": text}
            if image_base64:
                entry["image_base64"] = image_base64
            self.messages.append(entry)
        row = ctk.CTkFrame(self.chat_container, fg_color="transparent")
        row.pack(fill="x", pady=4)
        bg_color = theme.ACCENT if role == "user" else theme.CARD
        # Assistant bubbles get a visible border -- without one, a white bubble on
        # this dialog's own near-white, translucent background had no visible edge
        # at all, reading as empty space even once the text-height bug below was
        # fixed. The user bubble doesn't need one; ACCENT already contrasts fine.
        bubble = ctk.CTkFrame(row, fg_color=bg_color, corner_radius=10,
                               border_width=0 if role == "user" else 1,
                               border_color=theme.BORDER)
        bubble.pack(anchor="e" if role == "user" else "w")
        inner = ctk.CTkFrame(bubble, fg_color="transparent")
        inner.pack(padx=10, pady=8)
        text_color = "white" if role == "user" else theme.TEXT
        if image is not None:
            self._add_image_thumbnail(inner, image)
        self._add_selectable_text(inner, text, bg_color, text_color)
        if role == "assistant":
            ctk.CTkButton(inner, text="📋 Copy", width=70, height=24, corner_radius=6,
                          fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                          font=theme.small(), command=lambda t=text: self._copy(t)).pack(anchor="w", pady=(6, 0))
        self.top.update_idletasks()
        try:
            self.chat_container._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _add_selectable_text(self, parent, text, bg_color, text_color):
        # This used to be a per-message CTkTextbox, sized by hand to fit its own
        # text -- selectable (a plain CTkLabel only ever lets you copy the WHOLE
        # message, via the 📋 button below, not drag-select part of it), but
        # getting that sizing right fought CustomTkinter's DPI scaling at every
        # turn: first it came out with ~0 height (invisible), then with the wrong
        # width assumption (visible empty space below the text), then a wrapping
        # estimate that didn't match Tk's own real word-wrap closely enough
        # (empty space again, this time because CTkTextbox's `height=` -- like its
        # `width=` -- is itself in CTk's scaled units, not raw pixels, which the
        # pre-wrap estimate had no way to account for). Three different failure
        # modes on the same approach is a sign to stop fighting it: a CTkLabel
        # auto-sizes correctly on its own, same as everywhere else in this app, so
        # there's nothing left here to get wrong. In-dialog partial selection is
        # the one capability that trades away -- copy the whole reply with 📋,
        # paste it into the actual message box, and select/edit part of it there,
        # which already supports that natively.
        ctk.CTkLabel(parent, text=text, font=theme.body(), text_color=text_color,
                     wraplength=_BUBBLE_TEXT_WIDTH_PX, justify="left").pack(anchor="w")

    def _add_image_thumbnail(self, parent, pil_image):
        thumb = pil_image.copy()
        thumb.thumbnail((_THUMBNAIL_DIMENSION, _THUMBNAIL_DIMENSION))
        ctk_image = ctk.CTkImage(light_image=thumb, size=thumb.size)
        label = ctk.CTkLabel(parent, image=ctk_image, text="")
        label.image = ctk_image  # see _set_pending_image's note on why this is needed
        label.pack(anchor="w", pady=(0, 6))

    def _copy(self, text):
        self.top.clipboard_clear()
        self.top.clipboard_append(text)

    def clear_chat(self):
        self._clear_pending_image()
        for child in self.chat_container.winfo_children():
            child.destroy()
        # The spacer (see __init__) gets destroyed along with everything else above --
        # recreated here so later messages still have something ahead of them in
        # packing order to push them toward the bottom.
        self._chat_spacer = ctk.CTkFrame(self.chat_container, fg_color="transparent")
        self._chat_spacer.pack(fill="both", expand=True)
        self.messages = []
        self._add_message("assistant", "Ask me anything, or click 🎙 to speak your question.")

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
            message = str(e)  # see the same note in _ask_ai above
            self.top.after(0, lambda: self._on_transcribe_error(message))
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

    def _init_edge_resize(self):
        # Native Windows resize cursors never showed up at this window's borders --
        # between -alpha/-topmost and this process's per-monitor DPI awareness (see
        # main.py), Tk's own border hit-testing on Windows doesn't reliably line up
        # with where the cursor actually is on a scaled display. This reimplements
        # just the cursor-and-drag part directly instead of relying on it.
        #
        # bind_all (not self.top.bind) because every child frame already covers the
        # window edge-to-edge -- a plain bind on the toplevel only ever fires for
        # events on its own bare background, which here is never visible. Each
        # handler checks the event's own toplevel before doing anything, so this
        # is a no-op everywhere else in the app; still unbound in close() so it
        # doesn't keep piling up across repeated opens of this dialog.
        self._resize_edge = None
        self._resize_start = None
        self._resize_cursor = ""
        # minsize(320, 400) elsewhere is in the same CTk-scaled logical units as the
        # 380x540 passed to geometry() -- the drag handler below works entirely in
        # real physical pixels (see its own note on why), so the floor it clamps
        # against needs converting to match, same as the initial position did.
        scaling = ctk.ScalingTracker.get_window_scaling(self.top)
        self._resize_min_w = round(320 * scaling)
        self._resize_min_h = round(400 * scaling)
        self.top.bind_all("<Motion>", self._on_resize_motion, add=True)
        self.top.bind_all("<ButtonPress-1>", self._on_resize_press, add=True)
        self.top.bind_all("<B1-Motion>", self._on_resize_drag, add=True)
        self.top.bind_all("<ButtonRelease-1>", self._on_resize_release, add=True)

    def _event_is_for_this_dialog(self, event):
        try:
            return event.widget.winfo_toplevel() is self.top
        except Exception:
            return False

    def _edge_at(self, x, y):
        w = self.top.winfo_width()
        h = self.top.winfo_height()
        left = x <= _RESIZE_MARGIN
        right = x >= w - _RESIZE_MARGIN
        top = y <= _RESIZE_MARGIN
        bottom = y >= h - _RESIZE_MARGIN
        if left and top:
            return "nw"
        if right and top:
            return "ne"
        if left and bottom:
            return "sw"
        if right and bottom:
            return "se"
        if left:
            return "w"
        if right:
            return "e"
        if top:
            return "n"
        if bottom:
            return "s"
        return None

    def _on_resize_motion(self, event):
        if self._resize_edge or not self._event_is_for_this_dialog(event):
            return
        local_x = event.x_root - self.top.winfo_rootx()
        local_y = event.y_root - self.top.winfo_rooty()
        edge = self._edge_at(local_x, local_y)
        cursor = _RESIZE_CURSORS.get(edge, "")
        # <Motion> fires on every pixel the mouse crosses -- reconfiguring the
        # cursor that often (almost always back to the exact cursor it already
        # is) was itself a source of visible stutter. Only touch it on an actual
        # change.
        if cursor != self._resize_cursor:
            self._resize_cursor = cursor
            self.top.configure(cursor=cursor)

    def _on_resize_press(self, event):
        if not self._event_is_for_this_dialog(event):
            return
        local_x = event.x_root - self.top.winfo_rootx()
        local_y = event.y_root - self.top.winfo_rooty()
        edge = self._edge_at(local_x, local_y)
        if not edge:
            return
        self._resize_edge = edge
        self._resize_start = (event.x_root, event.y_root, self.top.winfo_width(),
                               self.top.winfo_height(), self.top.winfo_x(), self.top.winfo_y())

    def _on_resize_drag(self, event):
        if not self._resize_edge:
            return
        x0, y0, w0, h0, left0, top0 = self._resize_start
        dx = event.x_root - x0
        dy = event.y_root - y0
        edge = self._resize_edge
        new_w, new_h, new_x, new_y = w0, h0, left0, top0
        if "e" in edge:
            new_w = max(self._resize_min_w, w0 + dx)
        if "w" in edge:
            new_w = max(self._resize_min_w, w0 - dx)
            new_x = left0 + (w0 - new_w)
        if "s" in edge:
            new_h = max(self._resize_min_h, h0 + dy)
        if "n" in edge:
            new_h = max(self._resize_min_h, h0 - dy)
            new_y = top0 + (h0 - new_h)
        # wm_geometry, not geometry() -- w0/h0 (from winfo_width/height) are already
        # real physical pixels, and so is every value derived from them above. CTk's
        # own geometry() always multiplies whatever it's given by the DPI scaling
        # factor again (see __init__'s positioning math for the same issue), so
        # calling it here fed it already-physical numbers as if they were logical
        # ones -- at this machine's 125% scaling, every 4px the mouse moved dragged
        # the edge 5px, overshooting the cursor more and more as the drag continued
        # and reading as sluggish/disconnected rather than smooth. wm_geometry is
        # the same underlying Tk call CTk's own wrapper eventually makes, just
        # without that extra multiplication.
        self.top.wm_geometry(f"{int(new_w)}x{int(new_h)}+{int(new_x)}+{int(new_y)}")

    def _on_resize_release(self, event):
        self._resize_edge = None
        self._resize_start = None
        self.top.configure(cursor="")

    def close(self):
        if self._closed:
            return
        self._closed = True
        # unbind_all(sequence) drops every bind_all registration for that sequence,
        # not just this dialog's -- safe only because nothing else in this app uses
        # bind_all for these four sequences (see _init_edge_resize).
        for sequence in ("<Motion>", "<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
            try:
                self.top.unbind_all(sequence)
            except Exception:
                pass
        if self._speech_stream:
            try:
                self._speech_stream.stop()
                self._speech_stream.close()
            except Exception:
                pass
        self.top.destroy()
