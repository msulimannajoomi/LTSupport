# LTSupport Platform

A multi-tenant, account-based remote support/desktop tool. Rebuilt from the original
LTSupport prototype (`../server`, `../client`, `../main_gui.py`) with three changes:

1. **Real accounts** — sign up with an organization name, email, phone, and password;
   log in with the generated Org ID instead of typing a raw IP each time.
2. **A relay you control** — both roles connect *outward* to it, so it works over the
   internet regardless of NAT, CGNAT, or region. No port forwarding, no UPnP, no
   third-party tunnel services.
3. **A CustomTkinter UI** — a modern, professional dark theme (rounded cards, live
   status, in-app device list) replacing the plain Tkinter dashboard.
4. **Three account plans** — `trial` (free, 2-minute hard session cap), `prepaid` (a
   minutes balance that's deducted per session and cuts the session off at zero), and
   `postpaid` (unrestricted; usage is only counted for billing you handle separately).
   No payment processor is wired up (out of scope by design) — a plan's balance is set
   manually by whoever runs the backend, same "call to upgrade" model as before.
5. **Two-way voice, only in Interview Mode** — the viewer picks a **Session Mode** before
   connecting: **Normal** (screen + input only, plan/balance enforced as usual) or
   **Interview** (two-way voice is enabled — host's mic to the viewer and back — and the
   plan/trial/balance check is skipped entirely; the *only* gate is whether the
   organization's account is blocked). Interview sessions are counted in their own
   separate `interview_session_count`/`interview_total_minutes_used`, never mixed into
   billable usage. The viewer can mute their own mic at any time during an interview.
   An organization can be blocked outright (`manage.py block <org_id>`), which disallows
   *every* session type, interview included.
6. **Full session recording** — every viewer session is automatically recorded (video +
   audio — both call directions mixed into one track when it's an Interview session,
   silent for a Normal one since there's no voice to record — saved locally on the
   viewer's machine) for later review.
7. **A remote pointer, separate from remote control** — the viewer can toggle "Pointer
   Mode" to show a marker on the host's screen that follows their mouse *without* moving
   the host's actual cursor — for pointing something out without touching anything.
8. **An optional WiFi/local-network connection mode** — when both machines are on the
   same network, a session can bypass the internet relay entirely (lower latency, no
   backend bandwidth used for the actual session) while still checking in with the
   backend once at the start and end of the session to enforce/update the account's plan.

The screen-sharing, remote input, and movable "instructor overlay" features from the
original app are preserved as-is; only the transport and the UI changed.

## The two pieces — and who runs what

This is important, because "server" and "client" mean two different things here
depending on which layer you're talking about:

- **`desktop-app/`** — **one single application**, built to one `.exe`, installed on
  every machine (yours and your customers'). It is *not* split into a separate host
  build and a separate viewer build. From its Dashboard, the same running app can:
  - **Host This Computer** — act as the *server role*: share this PC's screen, be
    controlled remotely.
  - **Join a Device** — act as the *client role*: view and control another device.
  A person switches between these anytime from the same installed app — nothing
  separate to install for each role.

- **`backend/`** — the relay + accounts service. This is infrastructure, not something
  you hand to end users. **You run it once**, yourself, on a machine or small VPS with
  a reachable address, and every copy of `desktop-app` (in either role) connects out to
  it. It's what lets two `desktop-app` instances find each other and exchange
  screen/input data over the internet without either one needing a public IP or open
  router ports.

```
                         ┌─────────────────────────────┐
                         │     backend/ (you run once)   │
                         │  REST API   (port 8000)       │
                         │  Relay      (port 7000)       │
                         │  MongoDB: users/devices/       │
                         │           sessions             │
                         └───────┬───────────────┬───────┘
                    outbound TCP │               │ outbound TCP
                                 │               │
                    ┌────────────▼───┐   ┌───────▼──────────┐
                    │  desktop-app     │   │  desktop-app      │
                    │  acting as HOST  │   │  acting as VIEWER │
                    │  ("server" role) │   │  ("client" role)  │
                    └─────────────────┘   └───────────────────┘
```

This is the internet path, and it's the default. There's also an **optional local-only
path** (see "Connecting over WiFi/local network" below) where the two `desktop-app`
instances connect *directly* to each other over the LAN instead — the backend is still
contacted once at session start/end for billing, but carries none of the actual
screen/audio/input traffic for that session.

- **`backend/app.py`** — FastAPI REST API: `/api/signup`, `/api/login`, `/api/logout`,
  `/api/devices`, `/api/session/check` + `/api/session/report` (used by a local/WiFi
  session in place of the relay's automatic plan enforcement — see "Account plans and
  billing"). On startup it also launches the relay's asyncio TCP server in the same
  process/event loop.
- **`backend/relay.py`** — the broker. Both roles each open one TCP connection to it. It
  authenticates by session token, matches a viewer's requested `device_id` to the right
  host connection (only if it belongs to the same account and isn't already in a
  session), and pipes frames between them until either side disconnects. **One viewer
  per host device at a time** — a second connection attempt to a busy device is
  rejected immediately (`DEVICE_BUSY`) without touching the existing session.
- **`backend/db.py`** — MongoDB (Atlas) storage for `users` (one document per
  organization/account, `_id` = its `org_id`), `sessions` (login tokens, `_id` = the
  token), and `devices` (one document per host machine, `_id` = `device_id`). Connection
  string is `LTSUPPORT_MONGODB_URI` (`backend/config.py`) — **set your own before
  distributing this**; the checked-in default points at a specific developer's cluster.
  Every function in this file keeps the same name/signature regardless of the storage
  engine underneath, so nothing outside `db.py` needed to change for this to be a real
  swap-in, not a rewrite. Trade-off worth knowing: unlike the old local SQLite file,
  every DB call is now a real network round-trip to Atlas — signup/login/connect take
  roughly 0.4-1.5s instead of near-instant. This never touches the actual screen-sharing
  data path (no DB call happens per-frame), only these one-off moments.
- **`backend/manage.py`** — a small ops CLI: `python manage.py upgrade <org_id>
  <account_type>`, for when a customer calls to upgrade off the trial plan.
- **`desktop-app/`** — the one CustomTkinter app. Views: Login, Signup, Dashboard
  (lists your devices + "Host" / "Join" actions), Host, Viewer.

## Accounts

Signup asks for **Organization Name, Email, Phone Number, and Password**. The backend:
- Generates a unique **Org ID** (e.g. `ACMECORP-DA25BB`) from the org name — this is
  what's used to log in from then on, shown once at signup ("save this").
- Enforces **one account per email** and **one account per phone number** — a second
  signup attempt with either is rejected.
- Every new account picks a plan at signup — **Trial**, **Prepaid**, or **Postpaid**
  (defaults to Trial if unset). This is shown on the Dashboard the moment the app
  starts, along with the account's current balance/session count, and it's re-fetched
  from the backend every time the Dashboard loads so it reflects the latest usage after
  a session ends.

### Account plans and billing

All enforcement lives in `backend/plan.py`, used identically by the **relay** (internet
sessions) and by two REST endpoints (WiFi/local sessions — see below) so a session can't
be made unlimited by switching transport. Nothing here processes real payments —
by design, a plan's balance is set manually, the same "call this number" model the
original trial-only version used:

| Plan | Session behavior | How balance changes |
|---|---|---|
| **Trial** | Hard-capped at `TRIAL_SESSION_LIMIT_SECONDS` (`backend/config.py`, default 2 minutes) | Not applicable — no balance |
| **Prepaid** | Capped at however many minutes are left in the balance; a session starting at 0 is rejected immediately, one starting positive is cut off mid-session if it runs out | Deducted by the actual session length when the session ends (never below 0) |
| **Postpaid** | Unrestricted | Not deducted — only `session_count` and `total_minutes_used` accumulate, for you to bill separately |

Whichever plan is active, when a **Normal**-mode session ends the relay (or, for a
local/WiFi session, the host's call to `/api/session/report`) always increments
`session_count` and `total_minutes_used` — useful usage history regardless of plan.

If a session is cut short by a plan limit, both sides get the same kind of message the
original trial-limit dialog showed ("Call `UPGRADE_CONTACT_NUMBER` to top up/upgrade"),
just with wording that matches which limit was actually hit.

#### Interview Mode and blocked accounts

The viewer picks a **Session Mode** — Normal or Interview — before connecting (Dashboard,
next to "Join a Device"). **Interview** sessions:
- Skip the table above entirely — no trial cap, no balance check, unrestricted duration.
- Enable two-way voice (see below) — the only session type that does.
- Are tracked in **separate** counters, `interview_session_count` and
  `interview_total_minutes_used`, never mixed into the billable `session_count`/
  `total_minutes_used` above.

The **only** thing that can stop an Interview session is the account being **blocked** —
a hard kill switch on the whole organization, independent of plan:
```
python backend/manage.py block <org_id>       # disallows every session type, including Interview
python backend/manage.py unblock <org_id>
```
A blocked account can't host or join at all (both actions are greyed out on its own
Dashboard, and the relay/REST endpoints refuse it server-side regardless of what the
client shows — the same "enforced server-side, not just hidden client-side" principle as
the trial timer).

**Setting/topping up a plan** (an operator does this, same manual-step model as before):
```
python backend/manage.py upgrade <org_id> <trial|prepaid|postpaid>
python backend/manage.py topup <org_id> <minutes>       # add to a prepaid balance
```

**Before distributing this**, edit `backend/config.py`:
```python
TRIAL_SESSION_LIMIT_SECONDS = 2 * 60   # change the trial length here
UPGRADE_CONTACT_NUMBER = "+92-XXX-XXXXXXX"   # put your real support number here
```

## Setup

### 1. Run the backend (once — this is infrastructure, not an end-user install)

```
cd backend
pip install -r requirements.txt
python app.py
```

This starts the REST API on `:8000` and the relay on `:7000`, and creates
`backend/ltsupport.db` on first run.

**For real internet use**, deploy this on a small VPS with a public IP or domain (any
$5/mo box works — DigitalOcean, Hetzner, Lightsail, etc.) and open TCP ports 8000 and
7000 in its firewall.

### 2. Point the desktop app at your backend

Edit `desktop-app/config.py`:

```python
API_BASE_URL = "http://YOUR_BACKEND_IP_OR_DOMAIN:8000"
RELAY_HOST = "YOUR_BACKEND_IP_OR_DOMAIN"
RELAY_PORT = 7000
```

(Left as `127.0.0.1` by default for local testing on one machine.) Do this once before
building/distributing the `.exe` — every installed copy points at the same backend.

### 3. Run the desktop app

You have two options:

**Option A — from source (for development/testing):**
```
cd desktop-app
pip install -r requirements.txt
python main.py
```

**Option B — as a standalone `.exe` (what you actually hand to users):**
```
pip install -r desktop-app/requirements.txt
python build_exe.py
```
This produces `dist/LTSupport.exe` — a single self-contained file. It does **not**
require Python (or anything else) installed on the machine it runs on; just copy it
over and double-click it. Rebuild it any time `desktop-app/config.py` or the code
changes — the `.exe` bakes in whatever `API_BASE_URL`/`RELAY_HOST` was set at build
time, so make sure step 2 above points at your real backend *before* running
`build_exe.py` for anything other than local testing.

Either way, sign up once, then, from the same app:
- **Host This Computer** — registers this PC under your account and starts sharing it.
  Its Device ID is generated on first run and remembered (`%APPDATA%/LTSupport`).
- **Join a Device** — from the Dashboard's device list (or by typing a Device ID
  directly) to view and control one of your online, non-busy devices.

There is nothing separate to run for "being a viewer" vs "being a host" — it's the
same install, same login, switchable anytime.

### Background / one-shot session behavior when acting as host

When a copy of the app is in the host ("server") role, it behaves like a background
support agent rather than a normal window you leave open:

- While hosting and **waiting** for a viewer, the window stays visible (so the Device
  ID can be read/shared).
- The **moment a viewer connects**, the window hides to a **system tray icon**
  (right-click it for "Show Window" / "Stop Hosting"). It keeps running in the
  background from there — screen sharing and remote input keep working with no window
  open.
- The **moment that viewer disconnects**, the app fully stops and exits — it does not
  linger waiting for another connection. This is a one-shot support-session model: one
  connect, one session, then the host app is gone.

A visible tray icon (rather than a fully invisible process) is a deliberate choice —
it's what every legitimate remote-support tool does (TeamViewer, AnyDesk, Chrome Remote
Desktop) so the person at that machine always has a way to see it's active and shut it
down themselves.

### Voice, pointer, and recording during a session

Pointer and recording start automatically for every session, no separate toggle. Voice
is different: it's **only on for an Interview-mode session** (see "Interview Mode and
blocked accounts" above) — a Normal session shares screen/input exactly as before, with
no microphone activity on either machine at all.

- **Two-way voice (Interview Mode only)**: the host's microphone streams to the viewer's
  speakers, and the viewer's microphone streams back to the host's speakers — both
  directions ride the same relay/local connection as screen/input data (`TYPE_AUDIO_FRAME`
  both ways, no new ports). Neither side's mic capture even starts for a Normal session —
  it's gated at the source (`ViewerAgent._mic_loop`/`HostAgent._audio_loop`), not just
  muted, so there's nothing to accidentally leak. The viewer has a **Mute** button (shown
  only in Interview Mode) that stops their mic being captured or sent at all — muted
  audio is genuinely not transmitted, and isn't written into the recording either.
- **Pointer Mode**: a button in the viewer's control bar that switches mouse movement
  from *controlling* the host (moving its real cursor) to just *pointing* — a small red
  crosshair marker appears on the host's physical screen at the viewer's mouse position,
  implemented as a separate click-through, always-on-top overlay window
  (`overlay.PointerOverlay`) so it never intercepts the host's own mouse input. Clicks are
  ignored while in this mode. Toggle back to leave the marker and resume real control.
- **Recording**: the viewer records the whole session to two local files — an `.avi`
  (video, MJPG codec) and a `.wav` (audio, both call directions mixed sample-by-sample
  into one track — silent throughout for a Normal session, since there's no voice to
  capture) — under
  `%APPDATA%\LTSupport\Recordings\<device_id>_<timestamp>.{avi,wav}` on the *viewer's*
  machine. A small "● REC" indicator shows in the control bar the whole time. On close, a
  dialog shows exactly where both files were saved.
  - These are two separate files, not one muxed file — avoids bundling ffmpeg. Video is
    written at a fixed nominal rate (12 fps) rather than timestamped per frame, so
    playback speed approximates but won't exactly match real elapsed session time. If
    you need a single properly-synced file, muxing them with ffmpeg after the fact is
    the natural next step.

### Connecting over WiFi/local network instead of the internet relay

Checking **"Connect over Local Network (WiFi)"** on the Host screen switches that
hosting session from the internet relay to a direct connection on the local network —
useful for same-building/same-office support where you'd rather not pay for relay
bandwidth or add relay latency:

- The host advertises itself over **UDP broadcast** (`local_link.py`,
  `LocalHostServer`) and listens for a **direct TCP connection** on the same LAN — no
  relay process sits in the middle for the actual screen/audio/input traffic.
- On the viewer's side, **"🔍 Find on WiFi"** on the Dashboard broadcasts a discovery
  request and lists whatever hosts answer, each with a one-click **Connect**.
- Billing is **not** skipped just because the relay is bypassed, per the requirement
  that starting a session always needs the internet at least once: the host calls
  `/api/session/check` before accepting the connection (same allow/deny/time-limit rules
  `plan.py` gives the relay) and `/api/session/report` once the session ends. If that
  first call can't reach the backend, the session is refused rather than silently
  allowed — a local/offline session is never a way to dodge the plan limits.
- Once connected, everything else — screen sharing, two-way voice, remote input, the
  pointer, recording — works identically to a relay session; none of that code knows or
  cares which transport it's running over.

**On Bluetooth specifically**: this was **not** implemented, and that's a deliberate
scoping call, not an oversight. Classic/BLE Bluetooth's real-world throughput (roughly
1–3 Mbps) can't carry real-time screen sharing at any usable quality — even a heavily
compressed frame stream needs far more than that to stay watchable. Building a
"Bluetooth mode" that technically connects but delivers an unusable slideshow would fail
the "runs without error, professionally" bar this was built to, worse than not having it
at all. On top of the bandwidth ceiling, Windows' BLE *peripheral/advertising* role
(what the host side would need) is notoriously unreliable from Python — there's no
battle-tested library for it the way there is for BLE scanning — so it's also
meaningfully harder to ship correctly. If Bluetooth support becomes a real requirement
later, the practical version of it is **discovery only** (advertise/scan for nearby
devices over BLE, the way the WiFi mode does over UDP broadcast) with the actual session
still running over WiFi/LAN or the relay — not Bluetooth carrying the screen feed itself.

## Known limitations / next steps

- **No TLS yet.** Traffic between the desktop app and the backend is currently
  plaintext TCP. For production, put the API behind a reverse proxy with HTTPS
  (nginx/Caddy + Let's Encrypt) and wrap the relay socket in TLS, or tunnel it through
  the same proxy.
- **One account = one tenant.** Devices belong to a single user account; there's no
  separate "organization with multiple member logins" layer yet. If you need a team of
  people sharing access to the same pool of devices, that's a natural next addition
  (an `organizations` table + membership roles) but wasn't built here to keep scope
  focused on what was requested.
- The original UPnP / public-IP / SSH-tunnel code (in `../server/server.py`) is not
  used here at all — it's fully replaced by the relay model above.
- **The built `.exe` is unsigned, and that's a real distribution risk.** On a machine
  with Windows Smart App Control enabled (the default on new Windows 11 installs), an
  unsigned/unrecognized executable is **blocked outright** — confirmed directly: our
  own freshly-built `LTSupport.exe` was blocked here with "An Application Control
  policy has blocked this file." This isn't specific to this app; it would hit any
  unsigned exe. There's no per-app allowlist workaround once Smart App Control is
  enforced — the fix is getting `LTSupport.exe` code-signed with an Authenticode
  certificate (an EV certificate builds trust fastest; a standard OV cert works but
  needs to build reputation over time/downloads first). Turning Smart App Control off
  on a machine is a one-way switch (Microsoft only lets you re-enable it via a clean
  Windows reinstall) — not something to do just to work around this; sign the exe
  instead before distributing it to real customers.
