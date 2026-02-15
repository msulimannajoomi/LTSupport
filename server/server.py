import socket
import threading
import struct
import io
import time
import json
import subprocess
from PIL import Image
import mss
import pyautogui
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController
import tkinter as tk
import os
import urllib.request
import ctypes

# Enable DPI awareness for sharp UI and accurate coordinate mapping on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Try local import first (for standalone), then package style
try:
    from .overlay import MovableOverlay
except ImportError:
    try:
        from overlay import MovableOverlay
    except ImportError:
        MovableOverlay = None

try:
    import miniupnpc
except ImportError:
    miniupnpc = None

try:
    from pyngrok import ngrok
except ImportError:
    ngrok = None

class RemoteServer:
    def __init__(self, host='0.0.0.0', port_screen=9999, port_input=9998):
        self.host = host
        self.port_screen = port_screen
        self.port_input = port_input
        self.running = False
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.overlay_app = None
        self.ngrok_urls = None
        self.public_ip = "Fetching..."
        self.local_ip = self.get_local_ip()
        self.error = None
        self.upnp_status = "Pending"
        
        # Key Mapping (Tkinter -> Pynput)
        self.KEY_MAPPING = {
            'BackSpace': 'backspace', 'Return': 'enter', 'Tab': 'tab', 'Escape': 'esc',
            'Delete': 'delete', 'Up': 'up', 'Down': 'down', 'Left': 'left', 'Right': 'right',
            'Prior': 'page_up', 'Next': 'page_down', 'Home': 'home', 'End': 'end',
            'Caps_Lock': 'caps_lock', 'Num_Lock': 'num_lock', 'Scroll_Lock': 'scroll_lock',
            'Shift_L': 'shift', 'Shift_R': 'shift_r', 'Control_L': 'ctrl', 'Control_R': 'ctrl_r',
            'Alt_L': 'alt', 'Alt_R': 'alt_r', 'space': 'space', 'Win_L': 'cmd', 'Win_R': 'cmd_r',
            'F1': 'f1', 'F2': 'f2', 'F3': 'f3', 'F4': 'f4', 'F5': 'f5', 'F6': 'f6',
            'F7': 'f7', 'F8': 'f8', 'F9': 'f9', 'F10': 'f10', 'F11': 'f11', 'F12': 'f12',
        }

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP

    def get_public_ip(self):
        providers = ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com", "https://ident.me"]
        for url in providers:
            try:
                with urllib.request.urlopen(url, timeout=3) as response:
                    ip = response.read().decode("utf-8").strip()
                    if ip: 
                        self.public_ip = ip
                        return ip
            except Exception:
                continue
        self.public_ip = "Unavailable"
        return self.public_ip

    def setup_upnp(self):
        if miniupnpc is None: return False
        try:
            upnp = miniupnpc.UPnP()
            upnp.discoverdelay = 200
            if upnp.discover() > 0:
                upnp.selectigd()
                for port in [self.port_screen, self.port_input]:
                    try:
                        upnp.addportmapping(port, 'TCP', self.local_ip, port, f'RemoteDesktop_{port}', '')
                    except Exception as e:
                        if "Success" not in str(e) and "Conflict" not in str(e):
                            print(f"[UPnP] Error: {e}")
                self.upnp_status = "Active"
                return True
            else:
                self.upnp_status = "No IGD"
                return False
        except Exception as e:
            self.upnp_status = "Failed"
            return False

    def log_debug(self, msg):
        """Log debug messages to a file for troubleshooting"""
        try:
            with open("ssh_debug.log", "a") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except: pass

    def setup_ssh_tunnel(self):
        """Creates a single robust SSH tunnel via serveo.net for both ports"""
        self.tunnel_status = "SSH Connecting..."
        max_retries = 3
        self.log_debug("Starting SSH Tunnel setup...")
        
        for attempt in range(max_retries):
            try:
                # Request both ports in one command to avoid rate limits/multiple process issues
                cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30", 
                       "-o", "ServerAliveCountMax=3", 
                       "-R", "0:localhost:9999", "-R", "0:localhost:9998", "serveo.net"]
                
                self.log_debug(f"Attempt {attempt+1}: Running command {' '.join(cmd)}")
                
                # CREATE_NO_WINDOW = 0x08000000 (Windows specific)
                self.ssh_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                                stdin=subprocess.PIPE, text=True, bufsize=1,
                                                creationflags=0x08000000)
                
                self.serveo_urls = [None, None]
                start_time = time.time()
                found_count = 0
                
                # Wait longer for output
                while time.time() - start_time < 45: 
                    line = self.ssh_proc.stdout.readline()
                    if not line: break
                    line = line.strip()
                    if line: 
                        print(f"[SSH] {line}")
                        self.log_debug(f"OUTPUT: {line}")
                    
                    if "Forwarding TCP connections" in line and "serveo.net" in line:
                         # Forwarding TCP connections from serveo.net:12345
                         try:
                             url = line.split("serveo.net:")[-1].strip()
                             full_url = f"serveo.net:{url}"
                             
                             if found_count == 0:
                                 self.serveo_urls[0] = full_url
                                 found_count += 1
                                 self.log_debug(f"Captured Port 1: {full_url}")
                             elif found_count == 1:
                                 # Ensure unique ports or separate lines
                                 if full_url != self.serveo_urls[0]:
                                     self.serveo_urls[1] = full_url
                                     found_count += 1
                                     self.log_debug(f"Captured Port 2: {full_url}")
                                     break # We have both
                         except Exception as e:
                             self.log_debug(f"Parsing Error: {e}")

                    # Check for errors
                    if "Permission denied" in line or "Remote port forwarding failed" in line:
                        self.log_debug("Critical Error detected in output")
                        # Don't break immediately, serveo might partial success, but likely fail
                
                if found_count >= 1:
                    # Even if we only get 1, we can try to use it (shared port? unlikely for serveo)
                    # But ideally we want 2. If we got 2, great.
                    if found_count == 2:
                        self.tunnel_status = "SSH Active"
                        self.ngrok_urls = (self.serveo_urls[0], self.serveo_urls[1])
                        self.log_debug(f"Tunnel Success: {self.ngrok_urls}")
                        return self.ngrok_urls
                    else:
                        self.log_debug(f"Partial success (only {found_count} ports). Retrying...")
                
                self.ssh_proc.terminate()
                self.log_debug("Terminated process due to timeout or partial failure")
                time.sleep(2)
                
            except Exception as e:
                self.log_debug(f"Exception on attempt {attempt+1}: {e}")
                print(f"[SSH] Error on attempt {attempt+1}: {e}")
                time.sleep(2)
        
        self.tunnel_status = "SSH Failed"
        self.log_debug("All attempts failed.")
        return None

    def setup_ngrok(self, token=None):
        return None # ngrok disabled by requester

    def get_screen_resolution(self):
        with mss.mss() as sct:
            rect = sct.monitors[0]
            return rect['width'], rect['height']

    def handle_screen_client(self, conn, addr):
        print(f"[Server] Screen connection from {addr}")
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            try:
                while self.running:
                    img = sct.grab(monitor)
                    pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                    img_byte_arr = io.BytesIO()
                    pil_img.save(img_byte_arr, format='JPEG', quality=50)
                    img_bytes = img_byte_arr.getvalue()
                    size = len(img_bytes)
                    conn.sendall(struct.pack('>L', size) + img_bytes)
            except Exception as e:
                print(f"[Server] Screen connection error with {addr}: {e}")
            finally:
                print(f"[Server] Screen connection closed for {addr}")
                conn.close()

    def start_screen_server(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port_screen))
            sock.listen(5) # Increased backlog
            print(f"[Server] Screen server listening on {self.host}:{self.port_screen}")
            while self.running:
                try:
                    sock.settimeout(1.0)
                    conn, addr = sock.accept()
                    threading.Thread(target=self.handle_screen_client, args=(conn, addr), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[Server] Screen accept error: {e}")
                    break
            sock.close()
        except Exception as e:
            self.error = f"Screen Server Error: {e}"
            print(f"[Server] Screen server failed to start on {self.host}:{self.port_screen}: {e}")

    def handle_input_client(self, conn, addr):
        print(f"[Server] Input connection from {addr}")
        try:
            while self.running:
                size_bytes = conn.recv(4)
                if not size_bytes: break
                size = struct.unpack('>L', size_bytes)[0]
                json_bytes = conn.recv(size)
                data = json.loads(json_bytes.decode('utf-8'))
                self.process_input(data)
        except Exception as e:
            print(f"[Server] Input connection error with {addr}: {e}")
        finally:
            print(f"[Server] Input connection closed for {addr}")
            conn.close()

    def process_input(self, data):
        cmd_type = data.get('type')
        if cmd_type == 'mouse_move':
            scr_w, scr_h = self.get_screen_resolution()
            self.mouse.position = (int(data['x'] * scr_w), int(data['y'] * scr_h))
        elif cmd_type == 'mouse_click':
            btn = {'left': Button.left, 'right': Button.right, 'middle': Button.middle}.get(data['button'], Button.left)
            if data['pressed']: self.mouse.press(btn)
            else: self.mouse.release(btn)
        elif cmd_type in ['key_press', 'key_release']:
            k_name = self.KEY_MAPPING.get(data['key'], data['key'])
            k = getattr(Key, k_name) if hasattr(Key, k_name) else k_name
            if cmd_type == 'key_press': self.keyboard.press(k)
            else: self.keyboard.release(k)
        elif cmd_type == 'overlay_text' and self.overlay_app:
            self.overlay_app.root.after(0, self.overlay_app.update_text, data.get('text', ''))
        elif cmd_type == 'overlay_toggle' and self.overlay_app:
            self.overlay_app.root.after(0, lambda: self.overlay_app.close_overlay() if self.overlay_app.root.state() == 'normal' else self.overlay_app.show_overlay())
        elif cmd_type == 'overlay_style' and self.overlay_app:
            self.overlay_app.root.after(0, self.overlay_app.update_style, data.get('bg'), data.get('fg'), data.get('size'))
        elif cmd_type == 'overlay_move' and self.overlay_app:
            scr_w, scr_h = self.get_screen_resolution()
            # Expecting relative coordinates [0, 1] for movement
            target_x = data.get('x', 0) * scr_w
            target_y = data.get('y', 0) * scr_h
            self.overlay_app.root.after(0, self.overlay_app.update_position, target_x, target_y)
        elif cmd_type == 'overlay_visibility' and self.overlay_app:
            self.overlay_app.root.after(0, self.overlay_app.set_capture_visibility, data.get('visible', True))

    def start_input_server(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port_input))
            sock.listen(1)
            print(f"[Server] Input server listening on {self.port_input}")
            while self.running:
                try:
                    sock.settimeout(1.0)
                    conn, addr = sock.accept()
                    threading.Thread(target=self.handle_input_client, args=(conn, addr), daemon=True).start()
                except socket.timeout:
                    continue
                except:
                    break
            sock.close()
        except Exception as e:
            self.error = f"Input Server Error: {e}"
            print(f"[Server] Input server failed to start: {e}")

    def start(self, use_tunnel=False):
        self.running = True
        # Background IP/UPnP tasks
        threading.Thread(target=self.get_public_ip, daemon=True).start()
        threading.Thread(target=self.setup_upnp, daemon=True).start()
        
        # Core Servers
        threading.Thread(target=self.start_screen_server, daemon=True).start()
        threading.Thread(target=self.start_input_server, daemon=True).start()
        
        if use_tunnel:
            # Check if ssh exists
            try:
                subprocess.run(["ssh", "-V"], capture_output=True, check=True, creationflags=0x08000000)
                # Tunnel in background
                threading.Thread(target=self.setup_ssh_tunnel, daemon=True).start()
            except:
                self.tunnel_status = "SSH Missing"
                print("[SSH] OpenSSH client not found on system.")

        def launch_overlay():
            if MovableOverlay:
                try:
                    root = tk.Toplevel()
                    self.overlay_app = MovableOverlay(root)
                except Exception as e:
                    print(f"[Overlay] Failed to launch: {e}")
            else:
                print("[Overlay] Module not found.")
        
        return launch_overlay

    def stop(self):
        self.running = False
        if ngrok: ngrok.kill()

if __name__ == "__main__":
    srv = RemoteServer()
    root = tk.Tk()
    root.withdraw()
    launch_ov = srv.start()
    launch_ov()
    root.mainloop()
