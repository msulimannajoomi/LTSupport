import customtkinter as ctk

import theme


def show_copyable_id(parent, title, label_text, value, note_text=""):
    """A small modal with the value in a real, selectable entry field plus a one-click
    Copy button -- unlike a plain messagebox, whose text isn't reliably selectable."""
    top = ctk.CTkToplevel(parent)
    top.title(title)
    top.geometry("440x260")
    top.transient(parent)
    top.configure(fg_color=theme.BG)
    top.resizable(False, False)

    inner = ctk.CTkFrame(top, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=24, pady=24)

    ctk.CTkLabel(inner, text=title, font=theme.h2(), text_color=theme.TEXT).pack(anchor="w")
    ctk.CTkLabel(inner, text=label_text, font=theme.body(), text_color=theme.TEXT_MUTED,
                 wraplength=380, justify="left").pack(anchor="w", pady=(6, 16))

    row = ctk.CTkFrame(inner, fg_color="transparent")
    row.pack(fill="x")
    entry = ctk.CTkEntry(row, height=42, corner_radius=8, font=theme.mono(16), justify="center")
    entry.insert(0, value)
    entry.configure(state="readonly")
    entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    try:
        entry.select_range(0, "end")
    except Exception:
        pass

    def do_copy():
        top.clipboard_clear()
        top.clipboard_append(value)
        copy_btn.configure(text="Copied!")
        top.after(1200, lambda: copy_btn.configure(text="Copy"))

    copy_btn = ctk.CTkButton(row, text="Copy", width=80, height=42, corner_radius=8,
                              fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                              text_color="#0f172a", command=do_copy)
    copy_btn.pack(side="left")

    if note_text:
        ctk.CTkLabel(inner, text=note_text, font=theme.small(), text_color=theme.TEXT_MUTED,
                     wraplength=380, justify="left").pack(anchor="w", pady=(16, 12))

    ctk.CTkButton(inner, text="Done", height=36, corner_radius=8, fg_color=theme.CARD,
                  hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                  command=top.destroy).pack(fill="x", pady=(8, 0))

    top.grab_set()
    top.wait_window()


def show_notice(parent, title, message, kind="info", primary_text="OK", on_primary=None):
    """A themed modal notice -- used in place of a plain OS messagebox for things that
    matter more (a trial session ending, an upgrade prompt), so it actually looks like
    part of this app instead of a generic system dialog. `kind` picks the accent color
    down the left edge and on the primary button: "info" (gold accent), "warning"
    (orange), "danger" (red)."""
    accent = {"info": theme.ACCENT, "warning": theme.WARNING, "danger": theme.DANGER}.get(kind, theme.ACCENT)
    hover = {"info": theme.ACCENT_HOVER, "warning": theme.WARNING_HOVER, "danger": theme.DANGER_HOVER}.get(kind, theme.ACCENT_HOVER)
    button_text_color = "white" if kind == "danger" else "#0f172a"

    top = ctk.CTkToplevel(parent)
    top.title(title)
    top.geometry("440x260")
    top.transient(parent)
    top.configure(fg_color=theme.BG)
    top.resizable(False, False)

    ctk.CTkFrame(top, fg_color=accent, width=6, corner_radius=0).place(x=0, y=0, relheight=1)

    inner = ctk.CTkFrame(top, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=(30, 24), pady=24)

    ctk.CTkLabel(inner, text=title, font=theme.h2(), text_color=theme.TEXT).pack(anchor="w")
    ctk.CTkLabel(inner, text=message, font=theme.body(), text_color=theme.TEXT_MUTED,
                 wraplength=370, justify="left").pack(anchor="w", pady=(10, 20))

    def _primary():
        top.destroy()
        if on_primary:
            on_primary()

    ctk.CTkButton(inner, text=primary_text, height=40, corner_radius=8, fg_color=accent,
                  hover_color=hover, text_color=button_text_color, font=theme.h3(),
                  command=_primary).pack(fill="x", side="bottom")

    top.grab_set()
    top.wait_window()
