import customtkinter as ctk

BG = "#0f172a"
CARD = "#1e293b"
CARD_HOVER = "#243244"
ACCENT = "#38bdf8"
ACCENT_HOVER = "#0ea5e9"
SUCCESS = "#4ade80"
WARNING = "#fbbf24"
DANGER = "#f87171"
TEXT = "#f8fafc"
TEXT_MUTED = "#94a3b8"
BORDER = "#334155"

FONT = "Segoe UI"


def apply():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def h1():
    return (FONT, 28, "bold")


def h2():
    return (FONT, 20, "bold")


def h3():
    return (FONT, 14, "bold")


def body():
    return (FONT, 12)


def small():
    return (FONT, 10)


def mono(size=18):
    return ("Consolas", size, "bold")
