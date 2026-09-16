import customtkinter as ctk

# "Slate & Indigo" -- VantagePoint's brand palette (2026-09-15 rebrand from the old
# warm gold/amber "LTSupport" theme). Every color below is used app-wide via this one
# module, so changing it here re-themes every screen consistently.
BG = "#F8FAFC"
CARD = "#FFFFFF"
CARD_HOVER = "#F1F5F9"
ACCENT = "#6366F1"
ACCENT_HOVER = "#4F46E5"
SUCCESS = "#10B981"
SUCCESS_HOVER = "#059669"
WARNING = "#F59E0B"
WARNING_HOVER = "#D97706"
DANGER = "#EF4444"
DANGER_HOVER = "#DC2626"
TEXT = "#0F172A"
TEXT_MUTED = "#64748B"
BORDER = "#E2E8F0"

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
