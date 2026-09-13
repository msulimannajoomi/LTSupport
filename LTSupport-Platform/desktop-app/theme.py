import customtkinter as ctk

BG = "#FAF6EC"
CARD = "#FFFFFF"
CARD_HOVER = "#FBF3DC"
ACCENT = "#EAB308"
ACCENT_HOVER = "#CA8A04"
SUCCESS = "#22C55E"
SUCCESS_HOVER = "#16A34A"
WARNING = "#FB923C"
WARNING_HOVER = "#EA7C1C"
DANGER = "#DC2626"
DANGER_HOVER = "#B91C1C"
TEXT = "#1F2937"
TEXT_MUTED = "#78716C"
BORDER = "#EDE4CC"

FONT = "Segoe UI"


def apply():
    ctk.set_appearance_mode("light")
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
