import io
import threading
import wave
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from PIL import ImageTk

import config
import theme
import dialogs
from viewer_agent import ViewerAgent
from recorder import SessionRecorder
from overlay import REFERENCE_SCREEN_WIDTH


class ViewerView:
    def __init__(self, toplevel, app, device_id, local_target=None, view_only=False):
        self.top = toplevel
        self.app = app
        self.device_id = device_id
        self.local_target = local_target
        # RBAC "view_only" role: strips every interactive control out of this window
        # (control mode, overlay text/style editing, speech-to-text, pointer) so
        # there's nothing here to even try sending -- the relay also drops any
        # input/overlay command from this role server-side regardless (see relay.py's
        # _viewer_forward_loop), so this is belt-and-suspenders, not the only gate.
        # Every existing code path below is unchanged when this is False (the default,
        # and the only way any pre-existing caller of ViewerView behaves).
        self.view_only = view_only
        # Not known until the host responds -- the host alone chooses Normal vs Interview
        # at Start Hosting, this viewer has no way to request or override it.
        self.session_type = "normal"
        self.display_size = (1, 1)
        self.last_offset = (0, 0)
        self.photo = None
        self.canvas_image_id = None
        self.recorder = None
        self._closed = False
        # Starts in Pointer Mode (marker-only, nothing reaches the host's real input) --
        # the viewer explicitly opts into Control Mode when they actually need to click/
        # type on the host, and can hand it back at any time.
        self.control_mode = False
        # When on, mouse movement drags the instructor text overlay live (visible in
        # the shared screen feed itself, since that overlay is part of the host's
        # captured desktop) instead of doing anything else with the mouse -- toggle off
        # again to leave it wherever it last landed. See toggle_position_text.
        self.positioning_text = False
        # Local, instant preview of the instructor text -- drawn directly on this
        # viewer's own canvas rather than relied on from the shared screen feed, which
        # is delayed and JPEG-compressed and made it hard to judge position/color/size
        # accurately while adjusting them. Mirrors overlay.py's MovableOverlay defaults.
        self.text_preview_id = None
        self._text_pos = (0.5, 0.15)
        self._preview_fg = "#f2f2f2"
        self._preview_size = 12
        self._preview_family = "Arial"
        self._preview_bold = False
        self._preview_opacity = 100

        # State for the mic button's speech-to-text recording (see toggle_speech_input
        # and friends, below) -- a separate InputStream from the always-on mic capture
        # in viewer_agent.py (that one continuously streams to the host for the live
        # call; this one only runs while explicitly recording a note to transcribe).
        self.speech_recording = False
        self._speech_stream = None
        self._speech_frames = []
        self._speech_stop_timer = None
        # Whisper (via Groq) returns a transcript with no line breaks at all, however
        # long the spoken note runs -- landing it in the overlay as one continuous
        # line. Wrapped to this many words per line instead (see _wrap_transcript),
        # client-configurable in the Style panel since what reads well depends on the
        # chosen font size/overlay width, which varies per session.
        self._words_per_line = 8

        self.top.configure(fg_color=theme.BG)

        control_bar = ctk.CTkFrame(self.top, fg_color=theme.CARD, height=64, corner_radius=0)
        control_bar.pack(side="top", fill="x")

        self.status_label = ctk.CTkLabel(control_bar, text="Connecting…", font=theme.body(),
                                          text_color=theme.TEXT_MUTED)
        self.status_label.pack(side="left", padx=16)

        # Shown only while reconnecting (or in the rare case reconnection gives up
        # entirely) -- a network blip never closes this window on its own any more (see
        # _apply_status's "reconnecting"/"disconnected" handling), so this is the
        # explicit way to actually give up and end the session instead of waiting.
        self.terminate_btn = ctk.CTkButton(control_bar, text="Terminate Connection", width=160, height=36,
                                            corner_radius=8, fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
                                            text_color="white", command=self.close)

        self.rec_label = ctk.CTkLabel(control_bar, text="● REC", font=theme.small(), text_color=theme.DANGER)

        # Always available, in both Normal and Interview Mode -- the host's mic plays
        # for the viewer in both now, so muting means "stop listening" (Normal) or also
        # "stop sending my own mic" (Interview, the only mode with one to send). See
        # toggle_mute and the muted check in viewer_agent.py's _recv_loop/_mic_loop.
        self.mute_btn = ctk.CTkButton(control_bar, text="🎤 Mute", width=100, height=36, corner_radius=8,
                                       fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                       command=self.toggle_mute)
        self.mute_btn.pack(side="left", padx=6)

        # An explicit, always-visible way to end the session -- previously the only
        # way was closing this whole window (the X button / Alt+F4), which reads as
        # "cancel" rather than a deliberate disconnect. Same effect as that always
        # did (close() sends the usual cleanup/overlay-off signals before actually
        # closing), just given its own clearly-labeled button instead of only being
        # reachable via the window chrome. Available in every state (connected,
        # reconnecting, or already disconnected) and for view_only too, not just
        # while reconnecting like terminate_btn below.
        self.disconnect_btn = ctk.CTkButton(control_bar, text="🔌 Disconnect", width=130, height=36, corner_radius=8,
                                             fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
                                             text_color="white", command=self.close)
        self.disconnect_btn.pack(side="right", padx=16)

        if not self.view_only:
            self.control_btn = ctk.CTkButton(control_bar, text="🖱 Take Control", width=140, height=36, corner_radius=8,
                                              fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                              command=self.toggle_control_mode)
            self.control_btn.pack(side="left", padx=6)

            # Starts "off" -- MovableOverlay is created hidden on the host the moment
            # hosting starts (see overlay.py), before any viewer even connects. Nothing
            # else ever changes its visibility, so tracking it locally here from that
            # known starting state stays accurate.
            self.overlay_visible = False
            self.toggle_overlay_btn = ctk.CTkButton(control_bar, text="Toggle Overlay (Off)", width=140, height=36,
                                                     corner_radius=8, fg_color=theme.BG, hover_color=theme.CARD_HOVER,
                                                     text_color=theme.TEXT, command=self.toggle_overlay)
            self.toggle_overlay_btn.pack(side="left", padx=6)

            self.position_btn = ctk.CTkButton(control_bar, text="📍 Position Text", width=140, height=36, corner_radius=8,
                                               fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                               command=self.toggle_position_text)
            self.position_btn.pack(side="left", padx=6)

        # Shown only for a trial account's normal-mode session (see _apply_status's
        # "connected" handling) -- an unobtrusive strip rather than a popup, since it's
        # informational for the whole session rather than a one-off event. Dismissible;
        # re-appears next time this view is (re)connected regardless.
        self.trial_banner = ctk.CTkFrame(self.top, fg_color=theme.WARNING, corner_radius=0)
        trial_inner = ctk.CTkFrame(self.trial_banner, fg_color="transparent")
        trial_inner.pack(fill="x", padx=16, pady=8)
        self.trial_banner_label = ctk.CTkLabel(trial_inner, text="", font=theme.small(),
                                                text_color="#0f172a", wraplength=800, justify="left")
        self.trial_banner_label.pack(side="left")
        ctk.CTkButton(trial_inner, text="✕", width=24, height=24, corner_radius=6,
                      fg_color="transparent", hover_color=theme.WARNING_HOVER, text_color="#0f172a",
                      font=theme.small(), command=self.trial_banner.pack_forget).pack(side="right")

        # Always created, even for view_only (where nothing goes inside it) -- other
        # code (the trial banner) packs itself "before=self.text_bar", which needs
        # this frame to exist regardless of role to keep that same stacking order.
        # Not packed for view_only, though: an admin observer never gets the trial
        # banner either (see _apply_status -- an observer's limit_seconds is always
        # None, so that branch never fires), so there's nothing that would ever go
        # above it, and leaving it packed just reserved an empty strip of space,
        # shrinking the canvas below it for no reason. Full screen for the observer
        # instead -- see the canvas.pack() below, which then gets that space back.
        self.text_bar = ctk.CTkFrame(self.top, fg_color=theme.CARD, corner_radius=0)
        text_bar = self.text_bar
        if not self.view_only:
            text_bar.pack(side="top", fill="x")

        if not self.view_only:
            text_inner = ctk.CTkFrame(text_bar, fg_color="transparent")
            text_inner.pack(fill="x", padx=16, pady=(0, 10))

            # A real multi-line box, not a single-line Entry -- a message typed across
            # several lines used to get squashed onto one, unreadable while composing
            # it. wrap="none" is deliberate: the ONLY line breaks that ever show up
            # here are real newlines (Enter), with nothing auto-wrapped in between --
            # MovableOverlay on the host only ever breaks at an explicit newline too,
            # never re-flowing by width (see overlay.py), so every break typed here
            # lands at exactly the same place in the text the host renders. Auto-wrap
            # would add visual breaks on this end that don't exist in the string and
            # wouldn't match the host at all.
            self.text_entry = ctk.CTkTextbox(text_inner, height=56, corner_radius=8, wrap="none",
                                              font=theme.body(), fg_color=theme.BG, text_color=theme.TEXT)
            self.text_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
            self.text_entry.bind("<KeyRelease>", lambda e: self.send_text())

            self.speech_btn = ctk.CTkButton(text_inner, text="🎙 Speak", width=90, height=36, corner_radius=8,
                                             fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                             command=self.toggle_speech_input)
            self.speech_btn.pack(side="left", padx=(0, 6))

            ctk.CTkButton(text_inner, text="Clear", width=80, height=36, corner_radius=8,
                          fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, text_color="white",
                          command=self.clear_text).pack(side="left", padx=(0, 6))

            ctk.CTkButton(text_inner, text="⚙ Style", width=90, height=36, corner_radius=8,
                          fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                          command=self.toggle_settings).pack(side="left")

            self.settings_panel = ctk.CTkFrame(self.top, fg_color=theme.CARD, corner_radius=0)
            self._build_settings_panel()

        # By default (Pointer Mode) moving the mouse over this canvas only drives the
        # independent marker overlay on the host's screen (PointerOverlay, see
        # overlay.py), and clicking only flashes a "click here" pulse on it -- nothing
        # reaches the host's real input. Control Mode (control_btn, opt-in) is the only
        # time real clicks/keystrokes are sent; see the control_mode checks below.
        # view_only never binds any of this at all -- there is nothing here that's
        # even allowed to reach the host, so there's nothing to wire up.
        self.canvas = tk.Canvas(self.top, bg="black", highlightthickness=0, takefocus=True)
        self.canvas.pack(fill="both", expand=True)

        if not self.view_only:
            self.canvas.bind("<Motion>", self.on_mouse_move)
            self.canvas.bind("<Button>", self.on_mouse_click)
            self.canvas.bind("<ButtonRelease>", self.on_mouse_release)
            # Bound to the canvas specifically, not the whole window -- otherwise
            # every keystroke typed anywhere in this window, including into
            # text_entry above (meant only to compose the local overlay message),
            # would also reach the host.
            self.canvas.bind("<Key>", self.on_key_press)
            self.canvas.bind("<KeyRelease>", self.on_key_release)
        self.top.protocol("WM_DELETE_WINDOW", self.close)

        self.agent = ViewerAgent(app.api.token, device_id, self.on_frame, self.on_status,
                                  on_audio=self._on_host_audio_chunk, on_own_audio=self._on_own_audio_chunk,
                                  local_target=local_target)
        self.agent.connect()

    def _build_settings_panel(self):
        pad = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        pad.pack(padx=20, pady=16, fill="x")

        ctk.CTkLabel(pad, text="TEXT", font=theme.small(), text_color=theme.TEXT_MUTED).pack(side="left")
        # A straight neutral grayscale ramp from white to black -- R, G, and B are equal
        # in every one of these, so there is no hue/tint at all (no blue, no warm/cool
        # cast), just brightness steps you can actually tell apart.
        for color in ["#ffffff", "#e6e6e6", "#cccccc", "#b3b3b3", "#999999", "#808080",
                      "#666666", "#4d4d4d", "#333333", "#1a1a1a", "#000000"]:
            ctk.CTkButton(pad, text="", width=26, height=26, corner_radius=6, fg_color=color,
                          hover_color=color, border_width=1, border_color=theme.BORDER,
                          command=lambda c=color: self._apply_text_color(c)).pack(side="left", padx=3)

        ctk.CTkLabel(pad, text="   SIZE", font=theme.small(), text_color=theme.TEXT_MUTED).pack(side="left", padx=(16, 0))
        self.font_size_entry = ctk.CTkEntry(pad, width=50, height=26)
        self.font_size_entry.insert(0, "12")
        self.font_size_entry.pack(side="left", padx=6)
        ctk.CTkButton(pad, text="Apply", width=60, height=26, corner_radius=6, fg_color=theme.ACCENT,
                      text_color="white",
                      command=self._apply_text_size).pack(side="left")

        pad_font = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        pad_font.pack(padx=20, pady=(0, 16), fill="x")

        ctk.CTkLabel(pad_font, text="FONT", font=theme.small(), text_color=theme.TEXT_MUTED).pack(side="left")
        # Standard, near-universally-installed Windows fonts -- Arial/normal weight is
        # the default (plain, unstyled-looking text, not the old fixed bold-Arial).
        self.font_family_menu = ctk.CTkOptionMenu(
            pad_font, values=["Arial", "Segoe UI", "Calibri", "Times New Roman", "Courier New", "Verdana", "Georgia"],
            width=140, height=26, fg_color=theme.BG, button_color=theme.CARD_HOVER,
            button_hover_color=theme.ACCENT, text_color=theme.TEXT, dropdown_fg_color=theme.CARD,
            command=self._apply_text_family)
        self.font_family_menu.set("Arial")
        self.font_family_menu.pack(side="left", padx=(6, 16))

        self.bold_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(pad_font, text="Bold", variable=self.bold_var, width=20, checkbox_width=20,
                         checkbox_height=20, fg_color=theme.ACCENT, text_color=theme.TEXT,
                         command=self._apply_text_bold).pack(side="left", padx=(0, 16))

        # Real per-pixel transparency on the host (blends with whatever's actually
        # behind it there, not a fixed pale color that only happens to look faint
        # against one particular background) -- for text meant to be noticeable only
        # if someone's really looking closely, not at a glance.
        ctk.CTkLabel(pad_font, text="OPACITY", font=theme.small(), text_color=theme.TEXT_MUTED).pack(side="left")
        for label, pct in [("100%", 100), ("60%", 60), ("30%", 30), ("15%", 15)]:
            ctk.CTkButton(pad_font, text=label, width=50, height=26, corner_radius=6,
                          fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                          font=theme.small(),
                          command=lambda p=pct: self._apply_text_opacity(p)).pack(side="left", padx=3)

        pad_speech = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        pad_speech.pack(padx=20, pady=(0, 16), fill="x")
        ctk.CTkLabel(pad_speech, text="WORDS/LINE (speech-to-text)", font=theme.small(),
                     text_color=theme.TEXT_MUTED).pack(side="left")
        self.words_per_line_entry = ctk.CTkEntry(pad_speech, width=50, height=26)
        self.words_per_line_entry.insert(0, str(self._words_per_line))
        self.words_per_line_entry.pack(side="left", padx=6)
        ctk.CTkButton(pad_speech, text="Apply", width=60, height=26, corner_radius=6, fg_color=theme.ACCENT,
                      text_color="white",
                      command=self._apply_words_per_line).pack(side="left")

        pad2 = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        pad2.pack(padx=20, pady=(0, 16), fill="x")

        ctk.CTkLabel(pad2, text="POINTER", font=theme.small(), text_color=theme.TEXT_MUTED).pack(side="left")
        # Soft white/gray first (the default, subtle marker) then a few colors that stay
        # legible against a busy screen for when you want the pointer to stand out more.
        for color in ["#f2f2f2", "#9ca3af", "#38bdf8", "#4ade80", "#fbbf24", "#f87171"]:
            ctk.CTkButton(pad2, text="", width=26, height=26, corner_radius=13, fg_color=color,
                          hover_color=color, border_width=1, border_color=theme.BORDER,
                          command=lambda c=color: self._apply_pointer_color(c)).pack(side="left", padx=3)

        ctk.CTkLabel(pad2, text="   SIZE", font=theme.small(), text_color=theme.TEXT_MUTED).pack(side="left", padx=(16, 0))
        self.pointer_size_entry = ctk.CTkEntry(pad2, width=50, height=26)
        self.pointer_size_entry.insert(0, "16")
        self.pointer_size_entry.pack(side="left", padx=6)
        ctk.CTkButton(pad2, text="Apply", width=60, height=26, corner_radius=6, fg_color=theme.ACCENT,
                      text_color="white",
                      command=self._apply_pointer_size).pack(side="left")

        # Every control above applies instantly and leaves the panel open -- changing
        # several things (color, then size, then font...) in one sitting used to mean
        # reopening it after each single change. This is the one explicit way to close
        # it now, alongside the ⚙ Style button itself.
        ctk.CTkButton(pad2, text="Done", width=70, height=26, corner_radius=6, fg_color=theme.ACCENT,
                      text_color="white", font=theme.small(),
                      command=self.toggle_settings).pack(side="right")

    def toggle_settings(self):
        if self.settings_panel.winfo_ismapped():
            self.settings_panel.pack_forget()
        else:
            self.settings_panel.pack(side="top", fill="x", before=self.canvas)

    def on_status(self, status, info):
        self.top.after(0, lambda: self._apply_status(status, info))

    def _apply_status(self, status, info):
        # Reached via self.top.after() from a background thread (ViewerAgent's
        # on_status) -- close() can destroy self.top before an already-scheduled
        # status finishes arriving.
        if not self.top.winfo_exists():
            return
        if status == "connected":
            self.session_type = self.agent.session_type
            via = " (Local Network)" if self.local_target else ""
            mode = " — Interview Mode" if self.session_type == "interview" else ""
            self.status_label.configure(text=f"Connected — {self.device_id}{via}{mode}", text_color=theme.SUCCESS)
            self.recorder = SessionRecorder(self.device_id)
            self.rec_label.pack(side="left", padx=(0, 10), before=self.mute_btn)
            # Trial accounts only ever get their free-minutes window on a "normal"
            # session -- Interview Mode has no time/balance restriction at all (see
            # plan.py), so there's nothing to warn about there. Host and viewer are
            # always the same account in this app, so this account's own already-
            # logged-in ApiClient is the right source for account_type here, not
            # anything the host/relay would need to separately report back.
            limit_seconds = info.get("limit_seconds")
            if self.session_type == "normal" and self.app.api.account_type == "trial" and limit_seconds:
                minutes = max(1, round(limit_seconds / 60))
                self.trial_banner_label.configure(
                    text=f"⏳ Trial Account — this session is limited to {minutes} minute"
                         f"{'s' if minutes != 1 else ''}. Upgrade to Prepaid or Postpaid for "
                         f"longer, unlimited sessions.")
                self.trial_banner.pack(side="top", fill="x", before=self.text_bar)
        elif status == "reconnecting":
            # Never closes this window on its own, however long it takes -- see
            # viewer_agent.py's _reconnect, which now keeps retrying indefinitely
            # rather than giving up after a handful of attempts. Terminate Connection
            # is the one explicit way to actually end it instead of waiting this out.
            self.status_label.configure(
                text=f"⚠ Connection lost — reconnecting… (attempt {info['attempt']})",
                text_color=theme.WARNING)
            self.terminate_btn.pack(side="left", padx=(0, 10), before=self.rec_label)
        elif status == "resumed":
            via = " (Local Network)" if self.local_target else ""
            mode = " — Interview Mode" if self.session_type == "interview" else ""
            self.status_label.configure(text=f"Connected — {self.device_id}{via}{mode}", text_color=theme.SUCCESS)
            self.terminate_btn.pack_forget()
        elif status == "ended":
            # A genuine, intentional end -- the host user themselves stopped hosting
            # (see host_view.py: it no longer stops hosting just because a viewer's
            # connection blipped, only on its own explicit Stop Hosting any more) --
            # not a network issue, so still closes normally.
            self.status_label.configure(text="Session ended by host.", text_color=theme.WARNING)
            if not self._closed:
                dialogs.show_notice(self.top, "Connection Ended", "The host ended the session.", kind="warning")
            self.close()
        elif status == "trial_limit":
            self.status_label.configure(text="Trial time limit reached.", text_color=theme.WARNING)
            dialogs.show_notice(
                self.top, "Trial Limit Reached",
                info.get("message", "Your trial session has ended."),
                kind="warning", primary_text="Upgrade")
            self.close()
        elif status == "error":
            self.status_label.configure(text=info.get("message", "Connection error."), text_color=theme.DANGER)
        elif status == "disconnected":
            # Only reachable now if reconnection gave up entirely rather than an
            # ordinary blip (see viewer_agent.py's _reconnect) -- still doesn't close
            # on its own; same persistent alert + Terminate Connection as
            # "reconnecting", just worded for the fact that it's no longer retrying.
            if self._closed:
                return
            self.status_label.configure(text="⚠ Connection lost. Click Terminate Connection to close, or wait for the host.",
                                         text_color=theme.DANGER)
            self.terminate_btn.pack(side="left", padx=(0, 10), before=self.rec_label)

    def on_frame(self, pil_img):
        if self.recorder:
            self.recorder.write_frame(pil_img)
        self.top.after(0, lambda: self._render_frame(pil_img))

    def _on_host_audio_chunk(self, pcm_bytes):
        if self.recorder:
            self.recorder.write_host_audio(pcm_bytes)

    def _on_own_audio_chunk(self, pcm_bytes):
        if self.recorder:
            self.recorder.write_viewer_audio(pcm_bytes)

    def toggle_mute(self):
        muted = not self.agent.muted
        self.agent.set_muted(muted)
        self.mute_btn.configure(text="🔇 Muted" if muted else "🎤 Mute",
                                 fg_color=theme.DANGER if muted else theme.BG)

    def toggle_control_mode(self):
        self._set_control_mode(not self.control_mode)

    def _set_control_mode(self, active):
        # Mutually exclusive with positioning_text -- both take over what mouse
        # movement/clicks mean, and turning one on mid-way through the other used to
        # leave a stray mouse-button-release event queued up for the host (pressed
        # was never sent, since positioning_text intercepted the click, but release
        # fired anyway once positioning turned off) -- see on_mouse_release.
        if active and self.positioning_text:
            self._set_positioning_text(False)
        self.control_mode = active
        if active:
            self.control_btn.configure(text="👆 Release Control", fg_color=theme.ACCENT,
                                        hover_color=theme.ACCENT_HOVER, text_color="white")
            self.agent.send_overlay({"type": "control_start"})
            self.canvas.focus_set()
        else:
            self.control_btn.configure(text="🖱 Take Control", fg_color=theme.BG,
                                        hover_color=theme.CARD_HOVER, text_color=theme.TEXT)
            self.agent.send_overlay({"type": "control_end"})

    def toggle_position_text(self):
        self._set_positioning_text(not self.positioning_text)

    def _set_positioning_text(self, active):
        # Mutually exclusive with control_mode -- see the comment in _set_control_mode.
        if active and self.control_mode:
            self._set_control_mode(False)
        self.positioning_text = active
        if active:
            self.position_btn.configure(text="📌 Fix Position", fg_color=theme.ACCENT,
                                         hover_color=theme.ACCENT_HOVER, text_color="white")
        else:
            self.position_btn.configure(text="📍 Position Text", fg_color=theme.BG,
                                         hover_color=theme.CARD_HOVER, text_color=theme.TEXT)

    def _render_frame(self, pil_img):
        # Same reasoning as _apply_status -- video frames can keep arriving in the
        # brief window between close() being called and the background threads
        # actually noticing self.running went False.
        if not self.top.winfo_exists():
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return
        fw, fh = pil_img.size
        raspect = fw / fh
        caspect = cw / ch
        if caspect > raspect:
            new_h, new_w = ch, int(ch * raspect)
        else:
            new_w, new_h = cw, int(cw / raspect)

        resized = pil_img.resize((max(new_w, 1), max(new_h, 1)))
        self.display_size = (new_w, new_h)
        self.last_offset = ((cw - new_w) // 2, (ch - new_h) // 2)
        self.photo = ImageTk.PhotoImage(resized)
        if self.canvas_image_id is None:
            self.canvas_image_id = self.canvas.create_image(
                self.last_offset[0], self.last_offset[1], image=self.photo, anchor="nw")
        else:
            self.canvas.coords(self.canvas_image_id, self.last_offset[0], self.last_offset[1])
            self.canvas.itemconfig(self.canvas_image_id, image=self.photo)
        # display_size/last_offset just changed -- keep the local text preview aligned
        # with the new frame instead of drifting from a stale position.
        self._update_text_preview()

    def _update_text_preview(self):
        # view_only has no text_entry to preview at all (see __init__) -- it sees the
        # real overlay text the same way it sees everything else on the host's
        # screen, baked directly into the video frame, so there's nothing of its own
        # to draw here.
        if self.view_only:
            return
        text = self._entry_text()
        if not text:
            if self.text_preview_id is not None:
                self.canvas.delete(self.text_preview_id)
                self.text_preview_id = None
            return
        dw, dh = self.display_size
        x_off, y_off = self.last_offset
        rel_x, rel_y = self._text_pos
        x = x_off + rel_x * dw
        y = y_off + rel_y * dh
        # The chosen size is points-on-a-REFERENCE_SCREEN_WIDTH-wide screen, scaled here
        # against the displayed video's own width (dw) the same way overlay.py's
        # MovableOverlay scales it against the host's actual screen width -- without
        # matching scale factors on both ends, the same chosen size looked right in
        # this preview but wrong once actually rendered on a host whose real resolution
        # (or this canvas's current size) differed from the implicit assumption of a
        # 1:1 point-to-pixel screen.
        preview_scale = dw / REFERENCE_SCREEN_WIDTH
        effective_size = max(6, round(self._preview_size * preview_scale))
        # Negative size tells Tk to treat this as literal PIXELS rather than the
        # default of POINTS -- a positive size gets scaled by the screen's DPI
        # (96/72 on a normal Windows display, ~33% bigger than the number itself),
        # which is what made this preview consistently render larger than the same
        # effective_size actually looks once it comes back through the host's real
        # overlay (a PIL ImageFont size, which is already a literal pixel height
        # with no such DPI scaling applied).
        font = (self._preview_family, -effective_size, "bold" if self._preview_bold else "normal")
        # No width -- MovableOverlay on the host is sized to exactly fit the text now,
        # not a box with a fixed wrapping width (see overlay.py), so this only wraps at
        # an explicit newline the viewer typed, same as the host does.
        # Plain canvas text has no real alpha channel -- stipple (a dithered pattern)
        # is the closest local approximation of the host's actual per-pixel opacity.
        # Coarse compared to the real thing, but enough to judge "is this faint enough".
        if self._preview_opacity >= 90:
            stipple = ""
        elif self._preview_opacity >= 45:
            stipple = "gray50"
        elif self._preview_opacity >= 22:
            stipple = "gray25"
        else:
            stipple = "gray12"
        if self.text_preview_id is None:
            self.text_preview_id = self.canvas.create_text(
                x, y, text=text, fill=self._preview_fg, anchor="nw", font=font, stipple=stipple)
        else:
            self.canvas.coords(self.text_preview_id, x, y)
            self.canvas.itemconfig(self.text_preview_id, text=text, fill=self._preview_fg, font=font,
                                    stipple=stipple)
        self.canvas.tag_raise(self.text_preview_id)

    def _relative_pos(self, event):
        dw, dh = self.display_size
        x_off, y_off = self.last_offset
        rel_x = max(0.0, min(1.0, (event.x - x_off) / dw))
        rel_y = max(0.0, min(1.0, (event.y - y_off) / dh))
        return rel_x, rel_y

    def on_mouse_move(self, event):
        rel_x, rel_y = self._relative_pos(event)
        if self.positioning_text:
            # Drags the instructor text on the host (visible there in the shared screen
            # feed, but delayed/compressed) *and* moves the local preview in lockstep,
            # which updates instantly since it never leaves this machine.
            self.agent.send_overlay({"type": "overlay_move", "x": rel_x, "y": rel_y})
            self._text_pos = (rel_x, rel_y)
            self._update_text_preview()
        elif self.control_mode:
            self.agent.send_input({"type": "mouse_move", "x": rel_x, "y": rel_y})
        else:
            self.agent.send_overlay({"type": "overlay_pointer", "x": rel_x, "y": rel_y, "visible": True})

    def on_mouse_click(self, event):
        # Clicking the remote screen returns keyboard focus to it -- needed after typing
        # into text_entry above, which never sends keystrokes to the host regardless of
        # mode (see on_key_press/on_key_release).
        self.canvas.focus_set()
        if self.positioning_text:
            # Click to drop the text right here and exit positioning mode -- previously
            # this was a no-op and the only way out was the toolbar button, which meant
            # moving the mouse off the canvas and onto it, landing the text wherever the
            # cursor happened to be along the way there instead of where you'd aimed.
            rel_x, rel_y = self._relative_pos(event)
            self.agent.send_overlay({"type": "overlay_move", "x": rel_x, "y": rel_y})
            self._text_pos = (rel_x, rel_y)
            self._update_text_preview()
            self._set_positioning_text(False)
            return
        if self.control_mode:
            btn = {1: "left", 2: "middle", 3: "right"}.get(event.num, "left")
            self.agent.send_input({"type": "mouse_click", "button": btn, "pressed": True})
        else:
            # No real click is sent -- this just flashes the marker to say "click here",
            # for the host user to actually act on themselves.
            rel_x, rel_y = self._relative_pos(event)
            self.agent.send_overlay({"type": "overlay_pointer", "x": rel_x, "y": rel_y, "visible": True, "click": True})

    def on_mouse_release(self, event):
        if self.positioning_text:
            return
        if self.control_mode:
            btn = {1: "left", 2: "middle", 3: "right"}.get(event.num, "left")
            self.agent.send_input({"type": "mouse_click", "button": btn, "pressed": False})

    def on_key_press(self, event):
        if self.control_mode:
            self.agent.send_input({"type": "key_press", "key": event.keysym})

    def on_key_release(self, event):
        if self.control_mode:
            self.agent.send_input({"type": "key_release", "key": event.keysym})

    def toggle_overlay(self):
        self.agent.send_overlay({"type": "overlay_toggle"})
        self.overlay_visible = not self.overlay_visible
        if self.overlay_visible:
            self.toggle_overlay_btn.configure(text="Toggle Overlay (On)", fg_color=theme.ACCENT,
                                               hover_color=theme.ACCENT_HOVER, text_color="white")
        else:
            self.toggle_overlay_btn.configure(text="Toggle Overlay (Off)", fg_color=theme.BG,
                                               hover_color=theme.CARD_HOVER, text_color=theme.TEXT)

    def _entry_text(self):
        # CTkTextbox (a real tk.Text under the hood) always reports one trailing
        # newline it adds itself even on an "empty" box -- "end-1c" strips exactly
        # that one, leaving any newlines the viewer actually typed intact.
        return self.text_entry.get("1.0", "end-1c")

    def send_text(self):
        self.agent.send_overlay({"type": "overlay_text", "text": self._entry_text()})
        self._update_text_preview()

    def clear_text(self):
        self.text_entry.delete("1.0", "end")
        self.send_text()

    def toggle_speech_input(self):
        if self.speech_recording:
            self._stop_speech_recording()
        else:
            self._start_speech_recording()

    def _start_speech_recording(self):
        try:
            import sounddevice as sd
        except Exception as e:
            messagebox.showerror("Microphone Unavailable", f"Could not access the microphone: {e}")
            return
        self._speech_frames = []
        try:
            self._speech_stream = sd.InputStream(
                samplerate=config.AUDIO_SAMPLE_RATE, channels=config.AUDIO_CHANNELS, dtype="int16",
                blocksize=config.AUDIO_BLOCK_SIZE, callback=self._on_speech_chunk,
            )
            self._speech_stream.start()
        except Exception as e:
            messagebox.showerror("Microphone Unavailable", f"Could not open the microphone: {e}")
            self._speech_stream = None
            return
        self.speech_recording = True
        self.speech_btn.configure(text="⏺ Stop", fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
                                   text_color="white")
        # A safety cap, not a feature -- forgetting to click Stop shouldn't quietly turn
        # into a multi-minute recording sent as one huge upload. Cancelled below the
        # moment recording actually stops (by hand or by this itself firing).
        self._speech_stop_timer = self.top.after(60000, self._stop_speech_recording)

    def _on_speech_chunk(self, indata, frames, time_info, status):
        # Runs on sounddevice's own audio thread, same as the always-on mic loops in
        # host_agent.py/viewer_agent.py -- just appends, no Tk calls from here.
        self._speech_frames.append(indata.tobytes())

    def _stop_speech_recording(self):
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
        frames = self._speech_frames
        self._speech_frames = []
        if not frames:
            self.speech_btn.configure(text="🎙 Speak", fg_color=theme.BG, hover_color=theme.CARD_HOVER,
                                       text_color=theme.TEXT)
            return
        self.speech_btn.configure(text="⏳ Transcribing…", state="disabled", fg_color=theme.BG,
                                   text_color=theme.TEXT_MUTED)
        wav_bytes = self._frames_to_wav(frames)
        threading.Thread(target=self._transcribe_in_background, args=(wav_bytes,), daemon=True).start()

    def _frames_to_wav(self, frames):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(config.AUDIO_CHANNELS)
            w.setsampwidth(2)  # int16
            w.setframerate(config.AUDIO_SAMPLE_RATE)
            w.writeframes(b"".join(frames))
        return buf.getvalue()

    def _transcribe_in_background(self, wav_bytes):
        try:
            text = self.app.api.transcribe_audio(wav_bytes)
            self.top.after(0, lambda: self._on_transcribed(text, None))
        except Exception as e:
            self.top.after(0, lambda: self._on_transcribed(None, str(e)))

    def _on_transcribed(self, text, error):
        # Reached via self.top.after() from a background thread -- close() can destroy
        # self.top while a transcription request is still in flight.
        if not self.top.winfo_exists():
            return
        self.speech_btn.configure(text="🎙 Speak", state="normal", fg_color=theme.BG,
                                   hover_color=theme.CARD_HOVER, text_color=theme.TEXT)
        if error:
            messagebox.showerror("Transcription Failed", error)
            return
        text = (text or "").strip()
        if not text:
            return
        text = self._wrap_transcript(text)
        # Appends rather than replaces -- lets someone speak a few separate notes in a
        # row and build up one message, same as typing more after what's already there.
        current = self._entry_text()
        if current and not current.endswith("\n"):
            self.text_entry.insert("end", "\n")
        self.text_entry.insert("end", text)
        self.send_text()

    def _wrap_transcript(self, text):
        """Whisper returns a transcript with no line breaks at all, however long the
        spoken note runs -- inserted as-is, that's one continuous line in the overlay
        no matter how much was said. Break it every self._words_per_line words instead
        (client-configurable in the Style panel) -- simple word-count wrapping rather
        than measuring rendered width, since the overlay's actual on-screen width
        varies with font size/zoom and isn't something this side can measure exactly."""
        words = text.split()
        if self._words_per_line <= 0 or len(words) <= self._words_per_line:
            return text
        lines = [" ".join(words[i:i + self._words_per_line])
                 for i in range(0, len(words), self._words_per_line)]
        return "\n".join(lines)

    def send_style(self, fg=None, size=None, family=None, bold=None, opacity=None):
        data = {"type": "overlay_style"}
        if fg:
            data["fg"] = fg
        if size:
            data["size"] = size
        if family:
            data["family"] = family
        if bold is not None:
            data["bold"] = bold
        if opacity is not None:
            data["opacity"] = opacity
        self.agent.send_overlay(data)

    def send_pointer_style(self, color=None, size=None):
        data = {"type": "overlay_pointer_style"}
        if color:
            data["color"] = color
        if size:
            data["size"] = size
        self.agent.send_overlay(data)

    def _apply_text_color(self, color):
        self.send_style(fg=color)
        self._preview_fg = color
        self._update_text_preview()

    def _apply_words_per_line(self):
        try:
            value = int(self.words_per_line_entry.get().strip())
            if value > 0:
                self._words_per_line = value
        except ValueError:
            pass

    def _apply_text_size(self):
        size = self.font_size_entry.get()
        self.send_style(size=size)
        try:
            self._preview_size = int(size)
        except ValueError:
            pass
        self._update_text_preview()

    def _apply_text_family(self, family):
        self.send_style(family=family)
        self._preview_family = family
        self._update_text_preview()

    def _apply_text_bold(self):
        bold = self.bold_var.get()
        self.send_style(bold=bold)
        self._preview_bold = bold
        self._update_text_preview()

    def _apply_text_opacity(self, pct):
        self.send_style(opacity=pct)
        self._preview_opacity = pct
        self._update_text_preview()

    def _apply_pointer_color(self, color):
        self.send_pointer_style(color=color)

    def _apply_pointer_size(self):
        size = self.pointer_size_entry.get().strip()
        try:
            if int(size) > 0:
                self.send_pointer_style(size=size)
        except ValueError:
            pass

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
        if self.control_mode:
            self.agent.send_overlay({"type": "control_end"})
        self.agent.send_overlay({"type": "overlay_pointer", "visible": False})
        self.agent.close()
        if self.recorder:
            self.recorder.close()
            messagebox.showinfo(
                "Recording Saved",
                f"Video: {self.recorder.video_path}\nAudio: {self.recorder.audio_path}",
            )
        self.top.destroy()
