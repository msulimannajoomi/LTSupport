import threading
import customtkinter as ctk

import theme
from api_client import ApiError


class ActivityLogsView(ctk.CTkFrame):
    """RBAC, admin-only -- detailed per-team-member session history (see dashboard_view.py,
    which only shows the "Activity" button for role == "admin"; the backend enforces this
    too, on GET /api/logs, so reaching this view without being an admin still couldn't
    fetch anything). Every entry names which team member (member_username) actually did
    the hosting/joining -- the org's own admin login never appears as an actor here since
    it can only ever observe (see relay.py's _handle_observer), never host or take a
    normal-role slot itself."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app

        scroll = ctk.CTkScrollableFrame(self, fg_color=theme.BG)
        scroll.pack(fill="both", expand=True, padx=40, pady=30)

        header = ctk.CTkFrame(scroll, fg_color="transparent")
        header.pack(fill="x", pady=(0, 20))
        ctk.CTkButton(header, text="←  Back to Dashboard", fg_color="transparent", hover_color=theme.CARD,
                      text_color=theme.TEXT_MUTED, anchor="w",
                      command=self.app.show_dashboard).pack(side="left")

        title_row = ctk.CTkFrame(scroll, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(title_row, text="Activity Logs", font=theme.h1(), text_color=theme.TEXT).pack(side="left")
        self.refresh_btn = ctk.CTkButton(title_row, text="🔄 Refresh", width=100, height=32, corner_radius=8,
                                          fg_color=theme.CARD, hover_color=theme.CARD_HOVER, text_color=theme.TEXT,
                                          font=theme.small(), command=self.reload)
        self.refresh_btn.pack(side="right")

        ctk.CTkLabel(scroll, text="Every session run, most recent first.",
                     font=theme.body(), text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 24))

        self.list_container = ctk.CTkFrame(scroll, fg_color="transparent")
        self.list_container.pack(fill="x")
        ctk.CTkLabel(self.list_container, text="Loading…", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(pady=20)

        # A second, separate section on the same scrollable page rather than its own
        # tab -- same "admin sees what the team actually did" purpose as the session
        # list above, just for AI Assistant questions instead of remote-support work.
        ctk.CTkLabel(scroll, text="🤖 AI Assistant Conversations", font=theme.h2(),
                     text_color=theme.TEXT).pack(anchor="w", pady=(32, 4))
        ctk.CTkLabel(scroll, text="Every question a team member has asked the AI Assistant, "
                                   "most recent first.", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(anchor="w", pady=(0, 16))
        self.ai_list_container = ctk.CTkFrame(scroll, fg_color="transparent")
        self.ai_list_container.pack(fill="x")
        ctk.CTkLabel(self.ai_list_container, text="Loading…", font=theme.body(),
                     text_color=theme.TEXT_MUTED).pack(pady=20)

        self.reload()

    def reload(self):
        self.refresh_btn.configure(state="disabled", text="Refreshing…")
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            logs = self.app.api.list_logs()
        except ApiError:
            logs = []
        try:
            ai_chats = self.app.api.list_ai_chat_logs()
        except ApiError:
            ai_chats = []
        self.after(0, lambda: self._render(logs))
        self.after(0, lambda: self._render_ai_chats(ai_chats))

    def _render(self, logs):
        if not self.winfo_exists():
            return
        self.refresh_btn.configure(state="normal", text="🔄 Refresh")
        for child in self.list_container.winfo_children():
            child.destroy()

        if not logs:
            ctk.CTkLabel(self.list_container, text="No sessions logged yet.",
                         font=theme.body(), text_color=theme.TEXT_MUTED).pack(pady=20)
            return

        header = ctk.CTkFrame(self.list_container, fg_color="transparent")
        header.pack(fill="x", pady=(0, 4))
        for text in ("Team Member", "Device", "Started", "Duration", "Ended By"):
            ctk.CTkLabel(header, text=text, font=theme.small(), text_color=theme.TEXT_MUTED,
                         width=150, anchor="w").pack(side="left", padx=(0, 10))

        for log in logs:
            row = ctk.CTkFrame(self.list_container, fg_color=theme.CARD, corner_radius=8)
            row.pack(fill="x", pady=4)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=16, pady=10)

            member = log.get("member_username") or "—"
            device = log.get("device_name") or log.get("device_id") or "—"
            started = (log.get("started_at") or "")[:16].replace("T", " ") or "—"
            duration = f"{log.get('duration_minutes', 0):.1f} min"
            end_reason = {
                "viewer_disconnected": "Disconnected",
                "plan_limit_reached": "Plan limit reached",
                "local_session_ended": "Local session ended",
                "host_disconnected": "Host disconnected",
            }.get(log.get("end_reason", ""), log.get("end_reason", "—"))

            for text in (member, device, started, duration, end_reason):
                ctk.CTkLabel(inner, text=text, font=theme.small(), text_color=theme.TEXT,
                             width=150, anchor="w").pack(side="left", padx=(0, 10))

    def _render_ai_chats(self, chats):
        if not self.winfo_exists():
            return
        for child in self.ai_list_container.winfo_children():
            child.destroy()

        if not chats:
            ctk.CTkLabel(self.ai_list_container, text="No AI Assistant questions asked yet.",
                         font=theme.body(), text_color=theme.TEXT_MUTED).pack(pady=20)
            return

        for chat in chats:
            row = ctk.CTkFrame(self.ai_list_container, fg_color=theme.CARD, corner_radius=8)
            row.pack(fill="x", pady=4)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=16, pady=12)

            member = chat.get("member_username") or "—"
            when = (chat.get("created_at") or "")[:16].replace("T", " ") or "—"
            top_row = ctk.CTkFrame(inner, fg_color="transparent")
            top_row.pack(fill="x")
            ctk.CTkLabel(top_row, text=member, font=theme.small(), text_color=theme.TEXT_MUTED
                         ).pack(side="left")
            ctk.CTkLabel(top_row, text=when, font=theme.small(), text_color=theme.TEXT_MUTED
                         ).pack(side="right")

            question_prefix = "Q: 📷 " if chat.get("has_image") else "Q: "
            ctk.CTkLabel(inner, text=f"{question_prefix}{chat.get('question', '')}", font=theme.body(),
                         text_color=theme.TEXT, wraplength=900, justify="left"
                         ).pack(anchor="w", pady=(8, 2))
            ctk.CTkLabel(inner, text=f"A: {chat.get('answer', '')}", font=theme.body(),
                         text_color=theme.TEXT_MUTED, wraplength=900, justify="left"
                         ).pack(anchor="w")
