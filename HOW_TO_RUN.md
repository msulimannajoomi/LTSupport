# Remote Desktop Application Guide

## 1. Quick Start (Using .EXE files)
If you have the compiled versions in the `dist` folder:
1.  **On the Server PC**: Run `RemoteServer.exe`.
2.  **On the Client PC**: Run `RemoteClient.exe`.
3.  **Enter IP**: Enter the IP displayed on the Server's console.

---

## 2. IP Addresses Explained
When the Server starts, it shows two IPs:
*   **LOCAL (LAN)** e.g., `192.168.18.84`: Use this if both computers are on the **Same WiFi**.
*   **PUBLIC (WAN)** e.g., `154.192.58.18`: Use this if the Client is in a **Different Location**.

---

## 3. Connecting Over the Internet (Public IP)
If you give your friend your **Public IP**, you **MUST** configure your router first. 

### Step A: Access Your Router
1.  Open a browser and type your **Gateway IP** (usually `192.168.18.1` or `192.168.1.1`).
2.  Log in (Check the sticker on your router for the password).

### Step B: Port Forwarding (CRITICAL)
"Allowing all traffic" in the firewall is not enough. You must tell the router where to send the "Remote Desktop" requests.
1.  Look for a section named **Port Forwarding**, **Virtual Server**, or **NAT**.
2.  Add a new rule:
    *   **Protocol**: TCP
    *   **Port Range**: 9998 to 9999
    *   **Local IP**: `192.168.18.84` (Your computer's local IP)
3.  Save and Restart the router if needed.

> [!WARNING]
> **Testing internally**: Most routers do not allow you to connect to your own Public IP from inside your own house. To test if it works, use your Mobile Data (Hotspot) on the Client laptop or ask a friend to try it.

---

## 4. Troubleshooting
*   **Client won't connect?** Try the **Local IP** first. If that works, the problem is your Router/Port Forwarding.
*   **Firewall**: Ensure `RemoteServer.exe` is allowed through Windows Firewall.
