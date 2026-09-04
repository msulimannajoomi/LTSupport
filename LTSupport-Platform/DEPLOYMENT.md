# LTSupport Platform — Deployment & Operations Guide

This is the complete reference for running LTSupport in the real world: what runs on
your server, what runs on a user's PC, what happens if you move the server, and
exactly how the two sides find each other. If you only need a quick local test, see
`README.md` instead — this document is for actually operating it.

---

## 1. The two things that exist, and who owns each one

| | `backend/` | `desktop-app/` |
|---|---|---|
| **What it is** | The always-on server: REST API + relay + database | The one Windows app, built to `LTSupport.exe` |
| **Who runs it** | **You** (the operator) — once, on infrastructure you control | **Every user** — you and every customer, one copy each |
| **Where it lives** | A VPS / cloud box / your own always-on machine | Any Windows PC, downloaded once and double-clicked |
| **Holds the database?** | Yes — `backend/ltsupport.db` (accounts, devices, sessions) | No — only a tiny local file remembering this PC's Device ID |
| **"Server" / "client" role** | Not applicable — this is infrastructure, not a role | Switches anytime: **Host** (= server role) or **Join** (= client role) |

The most common confusion: **"server" is used two different ways.** `backend/` is the
literal server machine that must be always-on and reachable. Inside the desktop app,
"acting as server" / "acting as client" refers to which *role* that copy of the app is
playing in a given session (being controlled vs. controlling) — that has nothing to do
with where `backend/` runs.

---

## 2. What runs on the server (`backend/`)

This is the piece with the database and all the control logic — accounts, device
registry, session brokering, and the trial-limit enforcement.

### 2.1 Components (one process, one machine)

Starting `backend/app.py` brings up **two services in the same process**:

- **REST API — TCP port 8000.** Handles `/api/signup`, `/api/login`, `/api/logout`,
  `/api/devices`, `/api/me`. This is what the desktop app's Login/Signup/Dashboard
  screens talk to.
- **Relay — TCP port 7000.** A raw async TCP broker. Every "Host This Computer" and
  every "Join a Device" action opens one outbound connection here. It authenticates
  both sides, pairs a viewer to the right host device, forwards screen/input frames
  between them, enforces "one viewer per device at a time," and enforces the trial
  session time limit.

Both are launched by the one command `python app.py` — the relay is started as a
background task inside FastAPI's startup hook (`backend/app.py`, `on_startup`).

### 2.2 Data it owns

- **MongoDB Atlas** (`backend/config.py`'s `MONGODB_URI`/`MONGODB_DB_NAME`, overridable via
  `LTSUPPORT_MONGODB_URI`/`LTSUPPORT_MONGODB_DB` env vars) — three collections:
  - `users` — one document per organization/account, `_id` = `org_id`: `org_name`,
    `email`, `phone`, `password_hash`, `account_type` (`trial` by default), balance/usage
    counters.
  - `devices` — one document per host machine that has ever hosted: `_id` = `device_id`,
    which account owns it (`owner_id`), its friendly name, and live status.
  - `sessions` — login tokens issued by `/api/login` / `/api/signup`, `_id` = the token,
    with expiry.
- This **is your entire user base**, and it lives outside this machine entirely (a plain
  local file it isn't) — back it up via Atlas's own backup/export tools, and rotate the
  connection string's credentials before this codebase is shared or committed anywhere,
  since the checked-in default is a real, working credential.
- Every DB call is a network round-trip to Atlas, unlike the old local SQLite file —
  expect roughly 0.4-1.5s on signup/login/connect (not on the actual screen-sharing
  traffic, which never touches the DB).

### 2.3 Installing and starting it

```powershell
cd backend
pip install -r requirements.txt
python app.py
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
[Relay] Listening on 0.0.0.0:7000
```

`0.0.0.0` means "listen on every network interface" — that's correct and expected; it
does not mean it's already reachable from the internet (see 2.4).

### 2.4 Making it reachable from the internet

If `backend/` runs on your own laptop, only devices on the same LAN can reach it. For
real (cross-region) use, it needs to run somewhere with a stable, internet-reachable
address:

1. **Get a small VPS.** Any $4–6/mo box works (DigitalOcean, Hetzner, Linode, AWS
   Lightsail, etc.) — this app is lightweight (asyncio + a MongoDB Atlas connection, no
   GPU/heavy compute, and no local database to provision on the VPS itself).
2. **Open the two ports** in that VPS's firewall / security group: **TCP 8000** and
   **TCP 7000**, inbound, from anywhere.
3. **(Strongly recommended) Point a domain name at it** — e.g. `relay.yourcompany.com`
   → the VPS's IP, via an A record. Use the *domain*, not the raw IP, everywhere in
   step 3 of Part 3 below. Why this matters is explained in Part 4.
4. Run `python app.py` there. For it to survive reboots/disconnects, run it as a
   background service rather than in a terminal you might close:

   **Linux (systemd) — recommended for a real VPS:**
   ```ini
   # /etc/systemd/system/ltsupport-backend.service
   [Unit]
   Description=LTSupport Backend
   After=network.target

   [Service]
   WorkingDirectory=/opt/LTSupport-Platform/backend
   ExecStart=/usr/bin/python3 app.py
   Restart=always
   User=ltsupport

   [Install]
   WantedBy=multi-user.target
   ```
   Then: `sudo systemctl enable --now ltsupport-backend`.

   **Windows Server:** use [NSSM](https://nssm.cc/) to wrap `python app.py` as a
   Windows Service, or Task Scheduler with "run whether user is logged in or not" +
   restart-on-failure.

5. **TLS is not implemented yet** (see README's "Known limitations"). Traffic to both
   ports is currently plaintext. For anything beyond internal testing, put a reverse
   proxy (nginx/Caddy) with a Let's Encrypt certificate in front of port 8000 at
   minimum, and treat port 7000 accordingly before handling real customer screens.

### 2.5 Day-to-day operations

- **Upgrading a customer off the trial plan** (after they call you, per the trial-limit
  flow): on the server, `cd backend && python manage.py upgrade <ORG_ID> pro`. No
  restart needed — the very next session that account starts is no longer timed.
- **Changing the trial length or your support number**: edit
  `backend/config.py` → `TRIAL_SESSION_LIMIT_SECONDS`, `UPGRADE_CONTACT_NUMBER` →
  restart the backend process. (This does **not** require touching the desktop app.)
- **Backups**: use Atlas's own backup/export tooling (or `mongodump`) against the
  cluster in `MONGODB_URI` — there's no local file to copy anymore.

---

## 3. Running the app on the user's system (`desktop-app/`)

This is the one thing every user — you, your team, your customers — installs. It has
no separate "host build" / "viewer build"; the same install does both roles.

### 3.1 Getting it onto a machine

**As the end user receives it (no Python needed):**
Copy `dist/LTSupport.exe` to the machine and double-click it. That's the entire
install. Nothing else to configure on their end.

**Before distributing it for real, be aware:** the exe is currently unsigned. On a
machine with Windows Smart App Control enabled (default on new Windows 11 installs),
an unsigned exe is blocked outright with no per-app override — confirmed directly
against a freshly-built copy during development ("An Application Control policy has
blocked this file"). The real fix is code-signing `LTSupport.exe` with an Authenticode
certificate before it reaches customers; see `README.md`'s "Known limitations" for the
full explanation. Don't work around this by having customers disable Smart App
Control — it's a one-way switch on their machine (no re-enabling without a Windows
reinstall).

**Building that `.exe` yourself** (you do this, not your customers):
```powershell
cd LTSupport-Platform
pip install -r desktop-app\requirements.txt
python build_exe.py
```
Produces `dist\LTSupport.exe`. **The server address is baked into this file at build
time** — see Part 4.4 before you build the copy you intend to actually distribute.

**Running from source instead** (for your own development/testing only):
```powershell
cd desktop-app
pip install -r requirements.txt
python main.py
```

### 3.2 First run, from the user's point of view

1. **Sign Up** — Organization Name, Email, Phone Number, Password, and a **Plan**
   (Trial / Prepaid / Postpaid — Trial by default; Prepaid also asks for a starting
   balance in minutes, since there's no payment step to top it up automatically). On
   success, a dialog shows their **Organization ID** (e.g. `ACMECORP-DA25BB`) — this is
   their login going forward, not something they pick themselves. They need to save it.
2. **Dashboard** — shows their org name, Org ID, a plan banner (Trial/Prepaid balance/
   Postpaid usage — refreshed from the backend every time the Dashboard loads), their
   list of devices, and two actions:
   - **Host This Computer** — this PC starts sharing/being controllable. Optionally
     check "Connect over Local Network (WiFi)" first to skip the internet relay for this
     session (see README's "Connecting over WiFi/local network"). The window stays
     visible until someone connects, then hides to the system tray; the session starts
     recording; it fully exits the moment that viewer disconnects (see `README.md` for
     the full background behavior).
   - **Join a Device** — pick a **Session Mode** (Normal or Interview), then pick one of
     their own online devices, type a Device ID, or use **"🔍 Find on WiFi"** to connect
     directly to one on the same local network. Interview Mode turns on two-way voice
     (with a Mute button) and skips the plan/balance check entirely (only a blocked
     account can refuse it) — Normal Mode has no voice and enforces the plan table above
     as usual. Either way, a Pointer Mode is available for pointing without taking
     control, and the session is recorded.
3. Logging in again later uses **Org ID + Password** (not email/phone).

### 3.3 What's stored locally on that machine

- `%APPDATA%\LTSupport\device.json` — remembers this PC's Device ID once it has hosted,
  so it keeps the same identity across restarts instead of re-registering as a "new"
  device every time.
- `%APPDATA%\LTSupport\Recordings\` — **on the viewer's machine only** — one `.avi`
  (video) and one `.wav` (audio) per session, named `<device_id>_<timestamp>`. Nothing
  is recorded or saved on the host side.
- Nothing else persists locally — login is not remembered between app launches (no
  "remember me" yet); each run of the app requires signing in again.

---

## 4. Moving the backend to a different host

This is the part that actually changes when you migrate servers — say, from a test VPS
to a production one, or between cloud providers.

### 4.1 What does **not** change

- The database schema, the relay protocol, the desktop app's code — none of it is
  tied to where `backend/` happens to run. You can move it freely.
- Accounts and devices only exist inside `backend/ltsupport.db`. Copy that one file to
  the new host alongside the code, and every existing Org ID/password/device still
  works exactly as before — nothing to re-create.

### 4.2 What changes on the server side

1. Install `backend/` (code + `requirements.txt`) on the new host.
2. Copy over `ltsupport.db` from the old host (or start fresh if this is a clean
   environment — e.g., moving from testing to production for the first time).
3. Start it there (Part 2.4), open ports 8000/7000, and — if you're using one — repoint
   your domain's DNS A record at the new host's IP.
4. Decommission the old host once you've confirmed the new one works.

### 4.3 What changes on every user's system

**This is the one thing to plan for.** Each copy of `desktop-app`/`LTSupport.exe` has
the server's address baked in from `desktop-app/config.py` at the time it was built:

```python
API_BASE_URL = "http://YOUR_BACKEND_IP_OR_DOMAIN:8000"
RELAY_HOST = "YOUR_BACKEND_IP_OR_DOMAIN"
RELAY_PORT = 7000
```

There are two very different outcomes depending on what you put there originally:

- **If you used a raw IP address** (e.g. `http://165.22.4.10:8000`) and that IP
  changes when you move hosts: every already-installed copy of the app is now pointed
  at a dead address. You must edit `config.py` to the new IP, rebuild
  (`python build_exe.py`), and **redistribute the new `.exe` to every user** — old
  copies keep failing to connect until they replace it.

- **If you used a domain name** (e.g. `http://relay.yourcompany.com:8000`) and you only
  update that domain's DNS record to point at the new host: **nothing needs to change
  on any user's machine.** Their already-installed `.exe` resolves the domain fresh on
  every connection attempt, gets the new IP automatically, and keeps working with zero
  redistribution.

**This is why Part 2.4 recommends a domain name over a raw IP** — it turns "migrate the
server" from "rebuild and redistribute to everyone" into "update one DNS record."

*(If you expect to move hosts often, or want to change the server address without even
a DNS change, the next natural improvement is having the desktop app read the server
address from an editable local/remote config instead of a value baked in at build time
— that's not built currently, but is a straightforward addition if you want it.)*

### 4.4 Practical migration checklist

- [ ] Stand up `backend/` on the new host, confirm it's running (`curl` port 8000,
      confirm relay log shows it listening on 7000).
- [ ] Copy `ltsupport.db` over (or confirm a fresh DB is acceptable).
- [ ] Point DNS at the new host (if using a domain) **or** rebuild + redistribute the
      `.exe` with the new address baked in (if using a raw IP).
- [ ] Test one full round-trip: sign in with an existing Org ID, host on one machine,
      join from another, confirm screen/control works.
- [ ] Decommission the old host.

---

## 5. How the two sides actually connect

Both `desktop-app` instances — whichever role they're in — only ever make **outbound**
connections to `backend/`. Neither one needs a public IP, port forwarding, or UPnP;
that's what makes this work across NAT, CGNAT, and different regions/ISPs.

```
                         backend/  (wherever you host it)
                         ┌───────────────────────────────┐
                         │  REST API   :8000               │
                         │  Relay      :7000                │
                         │  ltsupport.db                     │
                         └───────┬───────────────┬─────────┘
                    outbound TCP │               │ outbound TCP
                                 │               │
                    ┌────────────▼───┐   ┌───────▼──────────┐
                    │  desktop-app     │   │  desktop-app      │
                    │  "Host This      │   │  "Join a Device"  │
                    │   Computer"      │   │                    │
                    └─────────────────┘   └───────────────────┘
```

### 5.1 Establishing identity (REST API, port 8000)

1. Sign up or log in → the API returns a **session token** (opaque string, expires
   after 7 days — `SESSION_TTL_HOURS` in `backend/config.py`).
2. Every later request — REST or relay — presents that token. The server looks it up
   against the `sessions` table to resolve which account (org) is making the request.
   There is no other credential exchanged after login.

### 5.2 Pairing host and viewer (relay, port 7000)

1. The **host** copy opens a TCP connection to the relay and sends a `HOST_HELLO`
   (session token, saved Device ID if it has one, and a display name). The relay
   validates the token, assigns/confirms the Device ID, marks it `online` in the
   database, and keeps that TCP connection open indefinitely as its channel.
2. The **viewer** copy opens its own TCP connection and sends a `VIEWER_HELLO`
   (session token + the target Device ID). The relay checks: same account owns that
   device? Is it online? Is it already paired with another viewer? If all clear, it
   marks the pairing and tells both sides `SESSION_START`.
3. From there, the relay is a pure byte-forwarder: screen frames and microphone audio
   flow host → viewer, input events flow viewer → host, all over those same two
   long-lived TCP connections — nothing new is opened per frame, and nothing about the
   relay's forwarding code had to change to add audio: it forwards whatever frame type
   it's handed without inspecting the payload.
4. **Exclusivity**: while a device is paired, any other viewer's connection attempt to
   it is rejected immediately (`DEVICE_BUSY`) without touching the active session.
5. **Trial limit**: if the account is on the `trial` plan, the relay starts a timer the
   moment pairing succeeds. If the session is still running when it fires, the relay
   sends both sides a message naming `UPGRADE_CONTACT_NUMBER` and ends the pairing —
   the viewer sees a dialog and closes; the host (usually hidden in the tray by then)
   shuts down the same way it does on any normal disconnect.
6. Either side disconnecting (viewer closes the window, host's `Stop Hosting`, network
   drop) ends the pairing and frees the device for the next viewer.

No screen, audio, or input data is ever written to disk on the relay itself — it only
holds frames in memory momentarily while forwarding them. Recording (see README) is a
viewer-side-only concern, entirely separate from the relay.
