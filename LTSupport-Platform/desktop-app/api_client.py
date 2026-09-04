import requests

import config

REQUEST_TIMEOUT = 8  # seconds -- matches the connect timeout used for relay/local sockets elsewhere


class ApiError(Exception):
    pass


def _request_error(e):
    if isinstance(e, requests.exceptions.Timeout):
        return ApiError("The server didn't respond in time. Check your connection and try again.")
    return ApiError("Could not reach the server. It may be offline, or the address in config.py is wrong.")


class ApiClient:
    def __init__(self):
        self.token = None
        self.org_id = None
        self.org_name = None
        self.account_type = None
        self.balance_minutes = 0
        self.session_count = 0
        self.total_minutes_used = 0
        self.blocked = False
        self.interview_session_count = 0
        self.interview_total_minutes_used = 0
        self.upgrade_contact_number = ""

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _post(self, path, json_body):
        try:
            resp = requests.post(f"{config.API_BASE_URL}{path}", json=json_body, headers=self._headers(),
                                  timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise _request_error(e)
        if resp.status_code >= 400:
            raise ApiError(resp.json().get("detail", "Request failed."))
        return resp.json()

    def _get(self, path):
        try:
            resp = requests.get(f"{config.API_BASE_URL}{path}", headers=self._headers(), timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise _request_error(e)
        if resp.status_code >= 400:
            raise ApiError(resp.json().get("detail", "Request failed."))
        return resp.json()

    def _delete(self, path):
        try:
            resp = requests.delete(f"{config.API_BASE_URL}{path}", headers=self._headers(), timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise _request_error(e)
        if resp.status_code >= 400:
            raise ApiError(resp.json().get("detail", "Request failed."))
        return resp.json()

    def signup(self, org_name, email, phone, password):
        """Every new account starts on trial -- there's no account_type choice here by
        design. Upgrading to prepaid is a separate, in-app step (upgrade_to_prepaid);
        postpaid is never self-service."""
        data = self._post("/api/signup", {
            "org_name": org_name, "email": email, "phone": phone, "password": password,
        })
        self._apply(data)
        return data["org_id"]

    def upgrade_to_prepaid(self):
        data = self._post("/api/account/upgrade-to-prepaid", {})
        self._apply(data, keep_token=True)

    def login(self, org_id, password):
        data = self._post("/api/login", {"org_id": org_id, "password": password})
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
        self.balance_minutes = data.get("balance_minutes", 0)
        self.session_count = data.get("session_count", 0)
        self.total_minutes_used = data.get("total_minutes_used", 0)
        self.blocked = data.get("blocked", False)
        self.interview_session_count = data.get("interview_session_count", 0)
        self.interview_total_minutes_used = data.get("interview_total_minutes_used", 0)
        self.upgrade_contact_number = data.get("upgrade_contact_number", "")

    def logout(self):
        try:
            requests.post(f"{config.API_BASE_URL}/api/logout", headers=self._headers(), timeout=5)
        except Exception:
            pass
        self.token = None
        self.org_id = None
        self.org_name = None
        self.account_type = None
        self.balance_minutes = 0
        self.session_count = 0
        self.total_minutes_used = 0
        self.blocked = False
        self.interview_session_count = 0
        self.interview_total_minutes_used = 0
        self.upgrade_contact_number = ""

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

    def session_report(self, minutes, session_type="normal"):
        """For a local/WiFi-direct session: applies the same bookkeeping the relay
        applies automatically for internet sessions -- balance+counters for "normal",
        or the separate interview_* counters for "interview"."""
        return self._post("/api/session/report", {"minutes": minutes, "session_type": session_type})

    def verify_peer(self, peer_token):
        """For a local/WiFi-direct session: confirms a connecting viewer's own token
        belongs to the same account as this (the host's) logged-in session."""
        return self._post("/api/session/verify-peer", {"peer_token": peer_token})["same_account"]
