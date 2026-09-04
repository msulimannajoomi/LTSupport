import tkinter as tk
import tkinter.font as tkfont
import ctypes


def _make_click_through(hwnd):
    """Makes a layered window pass mouse clicks through to whatever's underneath it --
    shared by MovableOverlay and PointerOverlay, neither of which the host is meant to
    be able to interact with directly any more (position/size/color are all set
    remotely, by the viewer)."""
    try:
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
    except Exception as e:
        print(f"[Overlay] Could not make window click-through: {e}")


class MovableOverlay:
    """The instructor-message overlay. Text only -- no visible box, drag handle, close
    button, or resize grip on the host's own screen. Position, size, and color are all
    set remotely by the viewer (update_position / update_style), and dragging the text
    into place live is done from the viewer's side (see ViewerView's Position Text
    toggle) by watching it move in the shared screen feed itself, since this window is
    part of the host's captured desktop like everything else here."""

    def __init__(self, root):
        self.root = root
        self.root.title("Remote Message Overlay")

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        initial_width = int(screen_width * 0.5)
        initial_height = int(screen_height * 0.15)
        x = int((screen_width - initial_width) / 2)
        y = int(screen_height * 0.1)

        self.root.geometry(f"{initial_width}x{initial_height}+{x}+{y}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        # Chroma-key transparency (same technique as PointerOverlay) instead of a
        # uniform alpha fade -- only the text glyphs (never drawn in pure black) stay
        # visible; the window's own background disappears completely rather than
        # showing as a faint box.
        self.root.attributes("-transparentcolor", "black")
        self.root.configure(bg="black")
        self.bg_color = "black"
        # A soft, slightly-gray white by default -- small and unobtrusive -- still fully
        # changeable from the viewer's Style panel (send_style / update_style below).
        self.fg_color = "#e5e5e5"
        self.is_hidden_from_capture = True

        self.apply_affinity()

        self.font_size = 12
        self.custom_font = tkfont.Font(family="Arial", size=self.font_size, weight="bold")

        self.entry = tk.Text(self.root, bg="black", fg=self.fg_color, font=self.custom_font,
                              wrap=tk.WORD, bd=0, highlightthickness=0)
        self.entry.pack(fill="both", expand=True, padx=5, pady=0)

        self.root.update_idletasks()
        _make_click_through(ctypes.windll.user32.GetParent(self.root.winfo_id()))

    def apply_affinity(self):
        try:
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception as e:
            print(f"Error applying window affinity: {e}")

    def update_text(self, text):
        self.entry.delete("1.0", tk.END)
        self.entry.insert("1.0", text)

    def update_style(self, fg=None, size=None):
        if fg:
            self.fg_color = fg
            self.entry.configure(fg=fg, insertbackground=fg)
        if size:
            try:
                self.font_size = int(size)
                self.custom_font.configure(size=self.font_size)
            except Exception:
                pass

    def update_position(self, x, y):
        self.root.geometry(f"+{int(x)}+{int(y)}")

    def set_capture_visibility(self, visible_on_capture):
        self.is_hidden_from_capture = not visible_on_capture
        affinity = 0x00000011 if not visible_on_capture else 0
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity)
        except Exception:
            pass

    def close_overlay(self, event=None):
        self.root.withdraw()

    def show_overlay(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)


class PointerOverlay:
    """A separate, click-through, always-on-top marker that lets a remote viewer point at
    something on the host's screen without taking control of the actual mouse cursor --
    distinct from real cursor control, which still moves the real pointer normally.

    Excluded from capture by third-party screen-share tools (Zoom, Teams, WhatsApp, etc)
    the same way MovableOverlay is -- those typically capture via the Windows Graphics
    Capture / DXGI Desktop Duplication APIs, which respect WDA_EXCLUDEFROMCAPTURE. Our
    own screen-sharing capture (mss, classic GDI BitBlt) does not respect that flag, so
    the pointer marker still shows up for the remote viewer it's meant for."""

    # Small and quiet by default -- a thin ring with a soft center dot, not a bold
    # crosshair, so it reads as "someone is pointing here" without dominating the
    # screen. The dot's stipple pattern fakes translucency (plain Tk canvas fills have
    # no real alpha channel). Both are changed live via update_color().
    SIZE = 16
    COLOR = "#f2f2f2"
    OUTLINE = "#9ca3af"
    PULSE_SIZE = 44  # briefly grows to this size for the "click here" cue -- see pulse()

    def __init__(self, root, color=None):
        self.root = root
        self.color = color or self.COLOR
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "black")
        self.root.configure(bg="black")
        self.root.geometry(f"{self.SIZE}x{self.SIZE}+0+0")

        self.canvas = tk.Canvas(self.root, width=self.SIZE, height=self.SIZE, bg="black", highlightthickness=0)
        self.canvas.pack()
        self._draw()

        self.root.update_idletasks()
        _make_click_through(ctypes.windll.user32.GetParent(self.root.winfo_id()))
        self._apply_capture_exclusion()
        self.root.withdraw()

    def _draw(self):
        self.canvas.delete("all")
        pad = 2
        self.ring_id = self.canvas.create_oval(pad, pad, self.SIZE - pad, self.SIZE - pad,
                                                outline=self.OUTLINE, width=1)
        dot_r = max(2, self.SIZE // 6)
        cx = cy = self.SIZE / 2
        self.dot_id = self.canvas.create_oval(cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r,
                                               fill=self.color, outline="", stipple="gray50")

    def update_color(self, color):
        if not color:
            return
        self.color = color
        self.canvas.itemconfig(self.dot_id, fill=color)
        self.canvas.itemconfig(self.ring_id, outline=color)

    def _apply_capture_exclusion(self):
        try:
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception as e:
            print(f"[Pointer] Could not exclude overlay from capture: {e}")

    def move_to(self, x, y):
        if self.root.state() == "withdrawn":
            self.root.deiconify()
        half = self.SIZE // 2
        self.root.geometry(f"+{int(x - half)}+{int(y - half)}")

    def pulse(self):
        """Briefly grows into a bigger ring centered on the current position -- the
        "click here" cue shown instead of ever performing a real click on the host."""
        if self.root.state() == "withdrawn":
            return
        cx = self.root.winfo_x() + self.SIZE // 2
        cy = self.root.winfo_y() + self.SIZE // 2
        half = self.PULSE_SIZE // 2
        self.root.geometry(f"{self.PULSE_SIZE}x{self.PULSE_SIZE}+{cx - half}+{cy - half}")
        self.canvas.configure(width=self.PULSE_SIZE, height=self.PULSE_SIZE)
        self.canvas.delete("all")
        pad = 3
        self.canvas.create_oval(pad, pad, self.PULSE_SIZE - pad, self.PULSE_SIZE - pad,
                                 outline=self.color, width=3)
        self.root.after(280, lambda: self._end_pulse(cx, cy))

    def _end_pulse(self, cx, cy):
        half = self.SIZE // 2
        self.root.geometry(f"{self.SIZE}x{self.SIZE}+{cx - half}+{cy - half}")
        self.canvas.configure(width=self.SIZE, height=self.SIZE)
        self._draw()

    def hide(self):
        self.root.withdraw()
