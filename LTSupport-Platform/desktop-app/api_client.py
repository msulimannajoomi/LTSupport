import requests

import config

REQUEST_TIMEOUT = 8  # seconds -- matches the connect timeout used for relay/local sockets elsewhere


class ApiError(Exception):
    pass


def _request_error(e):
    if isinstance(e, requests.exceptions.Timeout):
        return ApiError("The server didn't respond in time. Check your connection and try again.")
    return ApiError("Could not reach the server. It may be offline, or the address in config.py is wrong.")


def _error_detail(resp, fallback="Request failed."):
    """Pulls a human-readable message out of an error response, always falling back
    to `fallback` -- never None, never an empty string, and never blows up on a
    response that isn't valid JSON at all (a proxy/timeout error page, an empty body
    during a network hiccup, etc.). Plain `.get("detail", fallback)` only falls back
    when the key is entirely MISSING -- a response shaped like {"detail": null} (or a
    body that fails to parse as JSON in the first place) slipped through as a literal
    None, which is what showed up as the text "None" instead of a real message."""
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    return detail or fallback


class ApiClient:
    def __init__(self):
        self.token = None
        self.org_id = None
        self.org_name = None
        self.account_type = None
        # USD cents -- the whole prepaid balance is USD-denominated now (see
        # backend/config.py's PREPAID_RATE_PER_HOUR_CENTS). hours_remaining/
        # hours_consumed come pre-computed from the backend rather than derived
        # here, so this client never needs its own copy of the per-hour rate.
        self.balance_cents = 0
        self.hours_remaining = 0
        self.hours_consumed = 0
        self.session_count = 0
        self.total_minutes_used = 0
        self.blocked = False
        self.interview_session_count = 0
        self.interview_total_minutes_used = 0
        self.upgrade_contact_number = ""
        # "admin" (the org's own original login), "normal", or "view_only" -- see
        # backend/db.py's org_members section. None until a real login response
        # populates it, same as the other account fields above.
        self.role = None
        # AI Assistant (see views/ai_assist_view.py). ai_assistant_available already
        # folds in every gate (role, trial, org subscription, per-member permission)
        # -- that's the only one views should actually gate showing the feature on.
        # ai_assistant_enabled is the raw org-level switch alone, only used by the
        # Users screen to decide whether per-member toggles are even relevant yet.
        self.ai_assistant_available = False
        self.ai_assistant_enabled = False
        self.ai_questions_asked = 0
        self.ai_cost_cents = 0
        self.ai_cost_per_question_cents = 0
        self.ai_image_cost_per_question_cents = 0

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _post(self, path, json_body):
        try:
            resp = requests.post(f"{config.API_BASE_URL}{path}", json=json_body, headers=self._headers(),
                                  timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise _request_error(e)
        if resp.status_code >= 400:
            raise ApiError(_error_detail(resp))
        return resp.json()

    def _get(self, path):
        try:
            resp = requests.get(f"{config.API_BASE_URL}{path}", headers=self._headers(), timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise _request_error(e)
        if resp.status_code >= 400:
            raise ApiError(_error_detail(resp))
        return resp.json()

    def _delete(self, path):
        try:
            resp = requests.delete(f"{config.API_BASE_URL}{path}", headers=self._headers(), timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise _request_error(e)
        if resp.status_code >= 400:
            raise ApiError(_error_detail(resp))
        return resp.json()

    def check_connection(self):
        """A lightweight, unauthenticated reachability check -- used by the login
        screen's Retry button so someone can tell "the server is down" apart from
        "my password is wrong" before even submitting credentials. Its own short
        timeout, not REQUEST_TIMEOUT -- this is meant to give a quick yes/no, not wait
        as long as a real login attempt would."""
        try:
            resp = requests.get(f"{config.API_BASE_URL}/api/health", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def signup(self, org_name, email, phone, password):
        """Every new account starts on trial -- there's no account_type choice here by
        design. There's no separate "upgrade to prepaid" step either -- buying hours
        (see create_checkout) upgrades a trial account to prepaid automatically the
        moment Stripe confirms payment; postpaid is never self-service either way."""
        data = self._post("/api/signup", {
            "org_name": org_name, "email": email, "phone": phone, "password": password,
        })
        self._apply(data)
        return data["org_id"]

    def login(self, org_id, password):
        data = self._post("/api/login", {"org_id": org_id, "password": password})
        self._apply(data)

    def login_member(self, username, password):
        """A team-member login (RBAC) -- entirely separate from the org's own
        Organization ID + password above. Resolves to the same org's account/devices/
        billing either way, just with self.role reflecting whatever this member was
        assigned (see backend/db.py's org_members section)."""
        data = self._post("/api/login/member", {"username": username, "password": password})
        self._apply(data)

    def refresh_account(self):
        """Re-fetches plan/balance/count -- call after a session ends so the dashboard
        reflects the latest usage."""
        data = self._get("/api/me")
        self._apply(data, keep_token=True)

    def _apply(self, data, keep_token=False):
        if not keep_token:
            self.token = data["token"]
        self.org_id = data["org_id"]
        self.org_name = data["org_name"]
        self.account_type = data["account_type"]
        self.balance_cents = data.get("balance_cents", 0)
        self.hours_remaining = data.get("hours_remaining", 0)
        self.hours_consumed = data.get("hours_consumed", 0)
        self.session_count = data.get("session_count", 0)
        self.total_minutes_used = data.get("total_minutes_used", 0)
        self.blocked = data.get("blocked", False)
        self.interview_session_count = data.get("interview_session_count", 0)
        self.interview_total_minutes_used = data.get("interview_total_minutes_used", 0)
        self.upgrade_contact_number = data.get("upgrade_contact_number", "")
        self.role = data.get("role", "admin")
        self.ai_assistant_available = data.get("ai_assistant_available", False)
        self.ai_assistant_enabled = data.get("ai_assistant_enabled", False)
        self.ai_questions_asked = data.get("ai_questions_asked", 0)
        self.ai_cost_cents = data.get("ai_cost_cents", 0)
        self.ai_cost_per_question_cents = data.get("ai_cost_per_question_cents", 0)
        self.ai_image_cost_per_question_cents = data.get("ai_image_cost_per_question_cents", 0)

    def logout(self):
        try:
            requests.post(f"{config.API_BASE_URL}/api/logout", headers=self._headers(), timeout=5)
        except Exception:
            pass
        self.token = None
        self.org_id = None
        self.org_name = None
        self.account_type = None
        self.balance_cents = 0
        self.hours_remaining = 0
        self.hours_consumed = 0
        self.session_count = 0
        self.total_minutes_used = 0
        self.blocked = False
        self.interview_session_count = 0
        self.interview_total_minutes_used = 0
        self.upgrade_contact_number = ""
        self.role = None
        self.ai_assistant_available = False
        self.ai_assistant_enabled = False
        self.ai_questions_asked = 0
        self.ai_cost_cents = 0
        self.ai_cost_per_question_cents = 0
        self.ai_image_cost_per_question_cents = 0

    def list_devices(self):
        return self._get("/api/devices")["devices"]

    def remove_device(self, device_id):
        return self._delete(f"/api/devices/{device_id}")

    def session_check(self, session_type="normal"):
        """For a local/WiFi-direct session: confirms the account currently allows a
        session of this type and for how long, using the same rules the relay enforces
        for internet sessions. An "interview" session skips the plan/balance check --
        only the account's blocked status is checked."""
        return self._post("/api/session/check", {"session_type": session_type})

    def session_report(self, minutes, session_type="normal", device_id="", device_name=""):
        """For a local/WiFi-direct session: applies the same bookkeeping the relay
        applies automatically for internet sessions -- balance+counters for "normal",
        or the separate interview_* counters for "interview" -- and logs the session
        (device_id ties the log entry to which device was hosted; device_name is sent
        directly since a Local Network run has no backend device record to look it up
        from)."""
        return self._post("/api/session/report",
                           {"minutes": minutes, "session_type": session_type, "device_id": device_id,
                            "device_name": device_name})

    def verify_peer(self, peer_token):
        """For a local/WiFi-direct session: confirms a connecting viewer's own token
        belongs to the same account as this (the host's) logged-in session."""
        return self._post("/api/session/verify-peer", {"peer_token": peer_token})["same_account"]

    def create_checkout(self):
        """Starts a Stripe purchase -- returns a hosted checkout URL to open in the
        system browser, where Stripe's own UI is what actually lets the customer pick
        how many hours and see the price, not anything on this end. Balance isn't
        credited by this call, and a trial account isn't upgraded to prepaid by it
        either; both only happen once Stripe confirms payment via the backend's
        webhook, which is why refresh_account() won't show either updated until
        sometime after the browser checkout actually completes."""
        return self._post("/api/billing/checkout", {})["checkout_url"]

    def list_payments(self):
        return self._get("/api/billing/payments")["payments"]

    def list_users(self):
        """RBAC, admin-only -- this org's team-member logins (not the org's own
        primary login, which isn't a "member")."""
        return self._get("/api/users")["users"]

    def create_user(self, username, password):
        """Always creates a "normal"-role team member -- the only kind there is now
        (the org's own admin login handles all oversight/viewing itself, see
        relay.py's _handle_observer, so there's no separate role to assign here)."""
        return self._post("/api/users", {"username": username, "password": password})

    def remove_user(self, member_id):
        return self._delete(f"/api/users/{member_id}")

    def set_user_ai_allowed(self, member_id, allowed):
        """RBAC, admin-only -- grants or revokes ONE team member's own permission to
        use the AI Assistant (see views/ai_assist_view.py). Only actually usable by
        that member once the org's own ai_assistant_enabled subscription is also on
        (see refresh_account's ai_assistant_enabled) -- toggleable here either way,
        so an admin can pre-approve members before the org subscription starts."""
        return self._post(f"/api/users/{member_id}/ai-assist", {"allowed": allowed})

    def list_logs(self):
        """RBAC, admin-only -- full session history for this org, most recent first,
        each entry naming which team member (member_username) did the work."""
        return self._get("/api/logs")["logs"]

    def list_ai_chat_logs(self):
        """RBAC, admin-only -- every AI Assistant question/answer pair this org's team
        has actually been charged for, most recent first (see db.log_ai_chat)."""
        return self._get("/api/ai/chat-logs")["logs"]

    def transcribe_audio(self, wav_bytes):
        """Sends a short recorded voice clip (WAV bytes) to the backend's speech-to-text
        proxy and returns the transcribed English text. Proxied through our own server
        rather than calling Groq directly from here -- see GROQ_API_KEY in the backend's
        config.py for why a key like that can never live in this distributed client.
        Uses its own longer timeout (not the generic _post helper's, sized for tiny JSON
        bodies) since this uploads real audio and waits on a transcription, not just a
        quick round trip."""
        try:
            resp = requests.post(
                f"{config.API_BASE_URL}/api/speech/transcribe",
                headers=self._headers(),
                files={"audio": ("speech.wav", wav_bytes, "audio/wav")},
                timeout=20,
            )
        except requests.RequestException as e:
            raise _request_error(e)
        if resp.status_code >= 400:
            raise ApiError(_error_detail(resp, "Transcription failed."))
        return resp.json().get("text", "")

    def ai_transcribe(self, wav_bytes):
        """Same shape as transcribe_audio above, but for the AI Assistant dialog's
        own mic button (see views/ai_assist_view.py) -- a separate, gated endpoint
        (OpenAI's Whisper, not Groq's) that does NOT charge on its own; only
        ai_chat below (the actual answered question) does."""
        try:
            resp = requests.post(
                f"{config.API_BASE_URL}/api/ai/transcribe",
                headers=self._headers(),
                files={"audio": ("speech.wav", wav_bytes, "audio/wav")},
                timeout=20,
            )
        except requests.RequestException as e:
            raise _request_error(e)
        if resp.status_code >= 400:
            raise ApiError(_error_detail(resp, "Transcription failed."))
        return resp.json().get("text", "")

    def ai_chat(self, messages):
        """Sends the AI Assistant dialog's whole conversation so far (see
        views/ai_assist_view.py) and returns the AI's reply. `messages` is a list of
        {"role": "user"|"assistant", "content": str} dicts, oldest first -- the
        backend charges AI_ASSISTANT_COST_CENTS_PER_QUESTION from the org's prepaid
        balance for this call (refunded automatically if OpenAI itself fails) --
        raises ApiError with a specific message if the balance can't cover it."""
        return self._post("/api/ai/chat", {"messages": messages})["reply"]
