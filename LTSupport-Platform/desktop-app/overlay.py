import os
import tkinter as tk
import ctypes
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# The chosen text size is treated as points-on-a-screen-this-wide, then scaled to
# whatever the host's actual screen width is -- see MovableOverlay._render. Picked as a
# common, unremarkable desktop width; what matters is that the viewer's local preview
# (viewer_view.py) scales against the same constant, not the specific number itself.
REFERENCE_SCREEN_WIDTH = 1920

# How often MovableOverlay re-pushes its current content as a defensive safety net --
# see _periodic_refresh.
_REFRESH_INTERVAL_MS = 5000

# (regular, bold) TrueType filenames for the families offered in the viewer's Style
# panel -- all standard, bundled with every normal Windows install.
_FONT_FILES = {
    "Arial": ("arial.ttf", "arialbd.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf"),
    "Courier New": ("cour.ttf", "courbd.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf"),
}
_FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _load_truetype_font(family, size, bold):
    regular, bold_file = _FONT_FILES.get(family, _FONT_FILES["Arial"])
    path = os.path.join(_FONTS_DIR, bold_file if bold else regular)
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.truetype(os.path.join(_FONTS_DIR, "arial.ttf"), size)


def _hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


def _push_layered_image(hwnd, pil_rgba_image, x, y):
    """Renders a PIL RGBA image onto a WS_EX_LAYERED window with real per-pixel alpha
    blending (UpdateLayeredWindow, ULW_ALPHA) instead of Tk's -transparentcolor
    chroma-key. This is the actual fix for the color-fringe/border that chroma-key
    transparency leaves around anti-aliased text: color-keying is all-or-nothing per
    pixel (exactly this RGB is invisible, everything else is fully opaque), so an
    anti-aliased glyph's partially-transparent edge pixels -- which are blended toward
    the "invisible" background color, not equal to it -- stay fully opaque and visible
    as a border. Real alpha blending honors each pixel's actual transparency instead,
    so the edges fade out cleanly with nothing behind them, exactly like the flat local
    preview that never had this problem to begin with."""
    w, h = pil_rgba_image.size
    if w <= 0 or h <= 0:
        return

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)

    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h  # negative => top-down DIB, matching PIL's row order
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0  # BI_RGB

    ppv_bits = ctypes.c_void_p()
    hbitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0, ctypes.byref(ppv_bits), None, 0)
    old_bitmap = gdi32.SelectObject(mem_dc, hbitmap)

    try:
        # ULW_ALPHA requires BGRA with each color channel pre-multiplied by alpha.
        arr = np.frombuffer(pil_rgba_image.tobytes("raw", "RGBA"), dtype=np.uint8) \
                .reshape((h, w, 4)).astype(np.uint16)
        r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
        bgra = np.empty((h, w, 4), dtype=np.uint8)
        bgra[..., 0] = (b * a // 255).astype(np.uint8)
        bgra[..., 1] = (g * a // 255).astype(np.uint8)
        bgra[..., 2] = (r * a // 255).astype(np.uint8)
        bgra[..., 3] = a.astype(np.uint8)
        raw = bgra.tobytes()
        ctypes.memmove(ppv_bits, raw, len(raw))

        pt_dst = _POINT(int(x), int(y))
        sz = _SIZE(w, h)
        pt_src = _POINT(0, 0)
        blend = _BLENDFUNCTION(0x00, 0, 255, 0x01)  # AC_SRC_OVER, -, 255, AC_SRC_ALPHA
        ULW_ALPHA = 0x02
        user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(pt_dst), ctypes.byref(sz),
                                    mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA)
    finally:
        gdi32.SelectObject(mem_dc, old_bitmap)
        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, screen_dc)


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
    """The instructor-message overlay: plain text, nothing else -- no box, no
    wrapping, no fixed size. The window is exactly as big as the current text needs
    (see _resize_to_fit) and nothing more; there's no drag handle, close button, or
    resize grip on the host's own screen either. Position and style are all set
    remotely by the viewer (update_position / update_style), and dragging it into
    place live is done from the viewer's side (see ViewerView's Position Text toggle)
    by watching it move in the shared screen feed itself, since this window is part of
    the host's captured desktop like everything else here."""

    def __init__(self, root):
        self.root = root
        self.root.title("Remote Message Overlay")

        # Tk's own winfo_screenwidth/height() is only ever a fallback, used briefly
        # until host_agent.py's capture loop reports the real thing (see
        # sync_screen_dimensions below) -- like pyautogui.size(), it only ever
        # reflects the PRIMARY monitor, which silently disagrees with what's actually
        # captured (and sent to the viewer) on a multi-monitor host. That mismatch is
        # exactly what let this text land at the wrong position/size on the host's
        # real screen relative to where the viewer placed it.
        self.host_screen_width = self.root.winfo_screenwidth()
        self.host_screen_height = self.root.winfo_screenheight()
        # Just a starting position -- there's no starting *size* to speak of, it's
        # always exactly whatever the current text needs (see _render).
        self._x = int(self.host_screen_width * 0.25)
        self._y = int(self.host_screen_height * 0.1)

        self.root.geometry(f"1x1+{self._x}+{self._y}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        # No -transparentcolor / bg here -- this window's actual visible content is
        # rendered entirely by _render() below via UpdateLayeredWindow (real per-pixel
        # alpha), not by Tk's own widget drawing. See _push_layered_image's docstring
        # for why: chroma-key transparency leaves a visible border/fringe around
        # anti-aliased text that real alpha blending doesn't.
        self.bg_color = None
        # Same soft white/gray as the pointer marker's default (PointerOverlay.COLOR) --
        # small and unobtrusive, consistent between the two -- still fully changeable
        # from the viewer's Style panel (send_style / update_style below).
        self.fg_color = "#f2f2f2"
        self.is_hidden_from_capture = True
        # Tracked ourselves rather than read back via root.state() -- state() is
        # unreliable for an overrideredirect window like this one (it deliberately
        # bypasses the window manager, which is normally what tracks
        # normal/withdrawn state). Relying on it to decide which way to toggle was
        # what let repeated show/hide toggles eventually get out of sync and end up
        # permanently stuck hidden -- see host_agent.py's overlay_toggle handling.
        # Starts hidden -- off by default until the viewer explicitly toggles it on,
        # same as it'll be after any future toggle-off (see close_overlay below).
        self.visible = False

        self.apply_affinity()

        self.font_size = 12
        self.font_family = "Arial"
        self.font_weight = "normal"
        # 0-255. Full opacity by default; lowering this is what makes text "only
        # noticeable if you're really looking" -- real per-pixel alpha blending with
        # whatever's actually behind it, not a fixed pale color that only happens to
        # look faint against one particular background.
        self.opacity = 255
        self.text = ""

        self.root.update_idletasks()
        self._hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        _make_click_through(self._hwnd)
        self._render()
        self._schedule_refresh()
        self.root.withdraw()  # matches self.visible = False above -- see close_overlay

    def sync_screen_dimensions(self, width, height):
        """Called once host_agent.py's capture loop knows the real, authoritative
        pixel dimensions of what's actually being captured (sct.monitors[0] -- the
        full virtual desktop across every monitor). Re-renders immediately so an
        already-visible message picks up the corrected scale right away, instead of
        waiting for the next unrelated text/style change."""
        if width and height:
            self.host_screen_width = width
            self.host_screen_height = height
            self._render()

    def _schedule_refresh(self):
        self.root.after(_REFRESH_INTERVAL_MS, self._periodic_refresh)

    def _periodic_refresh(self):
        # Defensive: re-push the current content even though nothing here changed it.
        # A layered window's compositor surface can in principle be invalidated by
        # something outside this app's control (display sleep/wake, a GPU driver
        # reset, DWM hiccups) with no notification back to us -- if that ever happens,
        # this brings the text back within a few seconds instead of it staying blank
        # until the next real text/style/position change, which could be a long time
        # for a message nobody's actively editing.
        if self.visible:
            self._render()
        self._schedule_refresh()

    def apply_affinity(self):
        try:
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception as e:
            print(f"Error applying window affinity: {e}")

    def _render(self):
        """Re-renders the current text (with the current font/color) to an RGBA image
        with real anti-aliased alpha, sized to exactly fit it, and pushes it onto the
        window. Called on every text/style change and on show/reposition, since
        UpdateLayeredWindow needs re-pushing each time regardless of what changed."""
        if not self.text:
            # An empty layered window would be a 0-sized image, which Windows doesn't
            # like -- shrink to a harmless 1x1 transparent pixel instead.
            img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            _push_layered_image(self._hwnd, img, self._x, self._y)
            return
        # The size the viewer picks is meant as "points on a REFERENCE_SCREEN_WIDTH-wide
        # screen" rather than a literal point count -- scaled here by how this host's
        # actual screen compares to that reference. Without this, the same chosen size
        # rendered literally looked correct on a screen close to the reference width but
        # visibly too small/large on anything meaningfully higher- or lower-resolution,
        # which is what made the host not match the viewer's own local preview (see the
        # matching scale in viewer_view.py's _update_text_preview, using the displayed
        # video width in place of the host's real screen width).
        scale = self.host_screen_width / REFERENCE_SCREEN_WIDTH
        effective_size = max(6, round(self.font_size * scale))
        font = _load_truetype_font(self.font_family, effective_size, self.font_weight == "bold")
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        left, top, right, bottom = draw.multiline_textbbox((0, 0), self.text, font=font)
        w, h = max(1, right - left), max(1, bottom - top)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.multiline_text((-left, -top), self.text, font=font, fill=_hex_to_rgb(self.fg_color) + (self.opacity,))
        _push_layered_image(self._hwnd, img, self._x, self._y)

    def update_text(self, text):
        self.text = text
        self._render()

    def update_style(self, fg=None, size=None, family=None, bold=None, opacity=None):
        if fg:
            self.fg_color = fg
        if size:
            try:
                self.font_size = int(size)
            except Exception:
                pass
        if family:
            self.font_family = family
        if bold is not None:
            self.font_weight = "bold" if bold else "normal"
        if opacity is not None:
            try:
                # opacity arrives as a 0-100 percentage; stored as the 0-255 alpha
                # PIL/UpdateLayeredWindow actually want.
                self.opacity = max(0, min(255, round(float(opacity) * 255 / 100)))
            except Exception:
                pass
        self._render()

    def update_position(self, x, y):
        self._x, self._y = int(x), int(y)
        self._render()

    def set_capture_visibility(self, visible_on_capture):
        self.is_hidden_from_capture = not visible_on_capture
        affinity = 0x00000011 if not visible_on_capture else 0
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity)
        except Exception:
            pass

    def close_overlay(self, event=None):
        self.visible = False
        self.root.withdraw()

    def show_overlay(self):
        self.visible = True
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._render()


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
