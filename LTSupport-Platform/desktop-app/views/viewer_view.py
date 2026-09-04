import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from PIL import ImageTk

import theme
from viewer_agent import ViewerAgent
from recorder import SessionRecorder


class ViewerView:
    def __init__(self, toplevel, app, device_id, local_target=None):
        self.top = toplevel
        self.app = app
        self.device_id = device_id
        self.local_target = local_target
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

        self.top.configure(fg_color=theme.BG)

        control_bar = ctk.CTkFrame(self.top, fg_color=theme.CARD, height=64, corner_radius=0)
        control_bar.pack(side="top", fill="x")

        self.status_label = ctk.CTkLabel(control_bar, text="Connecting…", font=theme.body(),
                                          text_color=theme.TEXT_MUTED)
        self.status_label.pack(side="left", padx=16)

        self.rec_label = ctk.CTkLabel(control_bar, text="● REC", font=theme.small(), text_color=theme.DANGER)

        self.mute_btn = ctk.CTkButton(control_bar, text="🎤 Mute", width=100, height=36, corner_radius=8,
                                       fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                       command=self.toggle_mute)
        # Shown once "connected" reveals whether the host set this up as an Interview
        # session -- not known until then, so it stays hidden until that point.

        self.control_btn = ctk.CTkButton(control_bar, text="🖱 Take Control", width=140, height=36, corner_radius=8,
                                          fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                          command=self.toggle_control_mode)
        self.control_btn.pack(side="left", padx=6)

        self.toggle_overlay_btn = ctk.CTkButton(control_bar, text="Toggle Overlay", width=130, height=36, corner_radius=8,
                                                 fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                                 command=self.toggle_overlay)
        self.toggle_overlay_btn.pack(side="left", padx=6)

        self.position_btn = ctk.CTkButton(control_bar, text="📍 Position Text", width=140, height=36, corner_radius=8,
                                           fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                           command=self.toggle_position_text)
        self.position_btn.pack(side="left", padx=6)

        self.text_entry = ctk.CTkEntry(control_bar, placeholder_text="Send an instruction to the overlay…",
                                        height=36, corner_radius=8)
        self.text_entry.pack(side="left", fill="x", expand=True, padx=10)
        self.text_entry.bind("<KeyRelease>", lambda e: self.send_text())

        ctk.CTkButton(control_bar, text="Clear", width=80, height=36, corner_radius=8,
                      fg_color=theme.DANGER, hover_color="#ef4444", text_color="white",
                      command=self.clear_text).pack(side="left", padx=6)

        ctk.CTkButton(control_bar, text="⚙ Style", width=90, height=36, corner_radius=8,
                      fg_color=theme.BG, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                      command=self.toggle_settings).pack(side="left", padx=(6, 16))

        self.settings_panel = ctk.CTkFrame(self.top, fg_color=theme.CARD, corner_radius=0)
        self._build_settings_panel()

        # By default (Pointer Mode) moving the mouse over this canvas only drives the
        # independent marker overlay on the host's screen (PointerOverlay, see
        # overlay.py), and clicking only flashes a "click here" pulse on it -- nothing
        # reaches the host's real input. Control Mode (control_btn, opt-in) is the only
        # time real clicks/keystrokes are sent; see the control_mode checks below.
        self.canvas = tk.Canvas(self.top, bg="black", highlightthickness=0, takefocus=True)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Motion>", self.on_mouse_move)
        self.canvas.bind("<Button>", self.on_mouse_click)
        self.canvas.bind("<ButtonRelease>", self.on_mouse_release)
        # Bound to the canvas specifically, not the whole window -- otherwise every
        # keystroke typed anywhere in this window, including into text_entry above
        # (meant only to compose the local overlay message), would also reach the host.
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
        for color in ["white", "#00ff00", "#ffff00", "#0000AA", "#ff0000", "#000000"]:
            ctk.CTkButton(pad, text="", width=26, height=26, corner_radius=6, fg_color=color,
                          hover_color=color, border_width=1, border_color=theme.BORDER,
                          command=lambda c=color: self.send_style(fg=c)).pack(side="left", padx=3)

        ctk.CTkLabel(pad, text="   SIZE", font=theme.small(), text_color=theme.TEXT_MUTED).pack(side="left", padx=(16, 0))
        self.font_size_entry = ctk.CTkEntry(pad, width=50, height=26)
        self.font_size_entry.insert(0, "12")
        self.font_size_entry.pack(side="left", padx=6)
        ctk.CTkButton(pad, text="Apply", width=60, height=26, corner_radius=6, fg_color=theme.ACCENT,
                      text_color="#0f172a",
                      command=lambda: self.send_style(size=self.font_size_entry.get())).pack(side="left")

        pad2 = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        pad2.pack(padx=20, pady=(0, 16), fill="x")

        ctk.CTkLabel(pad2, text="POINTER", font=theme.small(), text_color=theme.TEXT_MUTED).pack(side="left")
        # Soft white/gray first (the default, subtle marker) then a few colors that stay
        # legible against a busy screen for when you want the pointer to stand out more.
        for color in ["#f2f2f2", "#9ca3af", "#38bdf8", "#4ade80", "#fbbf24", "#f87171"]:
            ctk.CTkButton(pad2, text="", width=26, height=26, corner_radius=13, fg_color=color,
                          hover_color=color, border_width=1, border_color=theme.BORDER,
                          command=lambda c=color: self.send_pointer_color(c)).pack(side="left", padx=3)

    def toggle_settings(self):
        if self.settings_panel.winfo_ismapped():
            self.settings_panel.pack_forget()
        else:
            self.settings_panel.pack(side="top", fill="x", before=self.canvas)

    def on_status(self, status, info):
        self.top.after(0, lambda: self._apply_status(status, info))

    def _apply_status(self, status, info):
        if status == "connected":
            self.session_type = self.agent.session_type
            if self.session_type == "interview":
                self.mute_btn.pack(side="left", padx=6, before=self.toggle_overlay_btn)
            via = " (Local Network)" if self.local_target else ""
            mode = " — Interview Mode" if self.session_type == "interview" else ""
            self.status_label.configure(text=f"Connected — {self.device_id}{via}{mode}", text_color=theme.SUCCESS)
            self.recorder = SessionRecorder(self.device_id)
            self.rec_label.pack(side="left", padx=(0, 10), before=self.text_entry)
        elif status == "reconnecting":
            self.status_label.configure(
                text=f"Connection dropped -- reconnecting (attempt {info['attempt']}/{info['max_attempts']})…",
                text_color=theme.WARNING)
        elif status == "resumed":
            via = " (Local Network)" if self.local_target else ""
            mode = " — Interview Mode" if self.session_type == "interview" else ""
            self.status_label.configure(text=f"Connected — {self.device_id}{via}{mode}", text_color=theme.SUCCESS)
        elif status == "ended":
            self.status_label.configure(text="Session ended by host.", text_color=theme.WARNING)
            if not self._closed:
                messagebox.showwarning("Connection Ended", "The host ended the session.")
            self.close()
        elif status == "trial_limit":
            self.status_label.configure(text="Trial time limit reached.", text_color=theme.WARNING)
            messagebox.showwarning("Trial Limit Reached", info.get("message", "Your trial session has ended."))
            self.close()
        elif status == "error":
            self.status_label.configure(text=info.get("message", "Connection error."), text_color=theme.DANGER)
        elif status == "disconnected":
            # Fires at the end of every session regardless of cause. If something more
            # specific (ended/trial_limit) already handled it, self._closed is already
            # True and the window may already be destroyed -- do nothing in that case.
            # Only an unexpected drop reaches here first, and that's the one case that
            # still needs its own clear notice.
            if self._closed:
                return
            self.status_label.configure(text="Disconnected.", text_color=theme.TEXT_MUTED)
            messagebox.showwarning("Connection Dropped", "The connection to the host was lost.")
            self.close()

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
        self.control_mode = not self.control_mode
        if self.control_mode:
            self.control_btn.configure(text="👆 Release Control", fg_color=theme.ACCENT,
                                        hover_color=theme.ACCENT_HOVER, text_color="#0f172a")
            self.agent.send_overlay({"type": "control_start"})
            self.canvas.focus_set()
        else:
            self.control_btn.configure(text="🖱 Take Control", fg_color=theme.BG,
                                        hover_color=theme.CARD_HOVER, text_color=theme.TEXT)
            self.agent.send_overlay({"type": "control_end"})

    def toggle_position_text(self):
        self.positioning_text = not self.positioning_text
        if self.positioning_text:
            self.position_btn.configure(text="📌 Fix Position", fg_color=theme.ACCENT,
                                         hover_color=theme.ACCENT_HOVER, text_color="#0f172a")
        else:
            self.position_btn.configure(text="📍 Position Text", fg_color=theme.BG,
                                         hover_color=theme.CARD_HOVER, text_color=theme.TEXT)

    def _render_frame(self, pil_img):
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

    def _relative_pos(self, event):
        dw, dh = self.display_size
        x_off, y_off = self.last_offset
        rel_x = max(0.0, min(1.0, (event.x - x_off) / dw))
        rel_y = max(0.0, min(1.0, (event.y - y_off) / dh))
        return rel_x, rel_y

    def on_mouse_move(self, event):
        rel_x, rel_y = self._relative_pos(event)
        if self.positioning_text:
            # Drags the instructor text live -- you watch it land in the shared screen
            # feed itself, since that overlay is part of the host's captured desktop.
            self.agent.send_overlay({"type": "overlay_move", "x": rel_x, "y": rel_y})
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

    def send_text(self):
        self.agent.send_overlay({"type": "overlay_text", "text": self.text_entry.get()})

    def clear_text(self):
        self.text_entry.delete(0, "end")
        self.send_text()

    def send_style(self, fg=None, size=None):
        data = {"type": "overlay_style"}
        if fg:
            data["fg"] = fg
        if size:
            data["size"] = size
        self.agent.send_overlay(data)

    def send_pointer_color(self, color):
        self.agent.send_overlay({"type": "overlay_pointer_style", "color": color})

    def close(self):
        if self._closed:
            return
        self._closed = True
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
