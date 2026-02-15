import socket
import threading
import cv2
import numpy as np
import struct
import json
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
import ctypes

# Enable DPI awareness for sharp UI on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

class RemoteDesktopClient:
    def __init__(self, root, server_ip, port_screen, port_input):
        self.root = root
        self.server_ip = server_ip
        self.port_screen = port_screen
        self.port_input = port_input
        self.running = True
        
        # UI Setup
        self.root.title(f"Remote Desktop Viewer - {server_ip}")
        self.root.geometry("1000x700")
        self.root.state('zoomed') # Full window startup
        
        # Frame for controls
        self.control_frame = tk.Frame(self.root, bg='#1a1a1a')
        self.control_frame.pack(side=tk.TOP, fill=tk.X)
        
        self.btn_overlay = tk.Button(self.control_frame, text="Toggle Overlay", command=self.send_overlay_toggle, bg='#333', fg='white')
        self.btn_overlay.pack(side=tk.LEFT, padx=10, pady=10)
        
        self.text_frame = tk.Frame(self.control_frame, bg='#1a1a1a')
        self.text_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.entry_text = tk.Text(self.text_frame, height=2, font=("Arial", 11), bg='#2a2a2a', fg='#00ff00', insertbackground='white')
        self.entry_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_text.bind("<KeyRelease>", lambda e: self.send_text())

        self.btn_send = tk.Button(self.control_frame, text="Send Text", command=self.send_text, bg='#0055ff', fg='white')
        self.btn_send.pack(side=tk.LEFT, padx=5, pady=10)

        self.btn_clear = tk.Button(self.control_frame, text="Clear", command=self.clear_text, bg='#b91c1c', fg='white')
        self.btn_clear.pack(side=tk.LEFT, padx=5, pady=10)

        self.btn_settings = tk.Button(self.control_frame, text="⚙ Settings", command=self.toggle_settings, bg='#555', fg='white')
        self.btn_settings.pack(side=tk.LEFT, padx=10, pady=10)

        # Settings Frame (Initially Hidden)
        self.settings_frame = tk.Frame(self.root, bg='#222', padx=10, pady=10)
        # We don't pack it yet

        # --- Style Controls ---
        style_title = tk.Label(self.settings_frame, text="OVERLAY STYLE", bg='#222', fg='#aaa', font=("Arial", 9, "bold"))
        style_title.pack(anchor='w', pady=(0, 5))

        color_row = tk.Frame(self.settings_frame, bg='#222')
        color_row.pack(fill='x', pady=5)

        tk.Label(color_row, text="BG:", bg='#222', fg='white').pack(side='left')
        for color in ['black', '#1a365d', '#1c4532', '#702459', '#b91c1c', '#1e293b']:
            btn = tk.Button(color_row, bg=color, width=2, command=lambda c=color: self.send_style(bg=c))
            btn.pack(side='left', padx=2)

        color_row_fg = tk.Frame(self.settings_frame, bg='#222')
        color_row_fg.pack(fill='x', pady=5)
        tk.Label(color_row_fg, text="Text Color:", bg='#222', fg='white').pack(side='left')
        for color in ['white', '#00ff00', '#ffff00', '#0000AA', '#ff0000', '#000000']:
            btn = tk.Button(color_row_fg, bg=color, width=2, command=lambda c=color: self.send_style(fg=c))
            btn.pack(side='left', padx=2)

        size_row = tk.Frame(self.settings_frame, bg='#222')
        size_row.pack(fill='x', pady=5)
        tk.Label(size_row, text="Font Size:", bg='#222', fg='white').pack(side='left')
        self.font_size_var = tk.StringVar(value="20")
        size_ent = tk.Entry(size_row, textvariable=self.font_size_var, width=5)
        size_ent.pack(side='left', padx=5)
        tk.Button(size_row, text="Update", command=lambda: self.send_style(size=self.font_size_var.get())).pack(side='left')

        # --- Visibility Controls ---
        tk.Label(self.settings_frame, text="VIEWING", bg='#222', fg='#aaa', font=("Arial", 9, "bold")).pack(anchor='w', pady=(10, 5))
        self.visible_var = tk.BooleanVar(value=False)
        chk_vis = tk.Checkbutton(self.settings_frame, text="Visible on Screen Share", variable=self.visible_var, 
                                bg='#222', fg='white', selectcolor='#333', activebackground='#222',
                                command=self.send_visibility)
        chk_vis.pack(anchor='w')

        # --- Position Controls ---
        tk.Label(self.settings_frame, text="POSITION PRESETS", bg='#222', fg='#aaa', font=("Arial", 9, "bold")).pack(anchor='w', pady=(10, 5))
        pos_row = tk.Frame(self.settings_frame, bg='#222')
        pos_row.pack(fill='x')
        tk.Button(pos_row, text="Top", command=lambda: self.send_move(0.25, 0.1)).pack(side='left', padx=2)
        tk.Button(pos_row, text="Center", command=lambda: self.send_move(0.25, 0.4)).pack(side='left', padx=2)
        tk.Button(pos_row, text="Bottom", command=lambda: self.send_move(0.25, 0.75)).pack(side='left', padx=2)
        
        # Canvas for screen display
        self.canvas = tk.Canvas(self.root, bg='black', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Input Handling
        self.canvas.bind("<Motion>", self.on_mouse_move)
        self.canvas.bind("<Button>", self.on_mouse_click)
        self.canvas.bind("<ButtonRelease>", self.on_mouse_release)
        self.root.bind("<Key>", self.on_key_press)
        self.root.bind("<KeyRelease>", self.on_key_release)
        
        self.screen_socket = None
        self.input_socket = None
        
        # Connect to Server
        if self.connect_to_server():
            # Start Screen Thread
            self.t_screen = threading.Thread(target=self.receive_screen, daemon=True)
            self.t_screen.start()
        else:
            self.safe_exit("Could not connect to server.")

    def connect_to_server(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"[Client] Attempt {attempt+1}/{max_retries}: Connecting to {self.server_ip}...")
                
                # Screen Socket
                self.screen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.screen_socket.settimeout(3.0)
                self.screen_socket.connect((self.server_ip, self.port_screen))
                
                # Input Socket
                self.input_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.input_socket.settimeout(3.0)
                self.input_socket.connect((self.server_ip, self.port_input))
                
                # Reset timeouts to blocking for normal operation
                self.screen_socket.settimeout(None)
                self.input_socket.settimeout(None)
                print(f"[Client] Connected successfully to {self.server_ip}")
                return True
            except (ConnectionRefusedError, socket.timeout, Exception) as e:
                print(f"[Client] Attempt {attempt+1} failed: {e}")
                if self.screen_socket: self.screen_socket.close()
                if self.input_socket: self.input_socket.close()
                if attempt < max_retries - 1:
                    time.sleep(1)
        return False

    def receive_screen(self):
        try:
            while self.running:
                # Receive size
                size_bytes = self.screen_socket.recv(4)
                if not size_bytes: break
                size = struct.unpack('>L', size_bytes)[0]
                
                # Receive data
                data = b""
                while len(data) < size:
                    packet = self.screen_socket.recv(min(size - len(data), 4096))
                    if not packet: break
                    data += packet
                    
                if len(data) < size: break

                # Decode image
                nparr = np.frombuffer(data, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if frame is not None:
                    cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
                    if cw > 1 and ch > 1:
                        # preserve aspect ratio (letterboxing)
                        fh, fw = frame.shape[:2]
                        raspect = fw / fh
                        caspect = cw / ch
                        
                        if caspect > raspect:
                            # Height is the limiting factor
                            new_h = ch
                            new_w = int(ch * raspect)
                        else:
                            # Width is the limiting factor
                            new_w = cw
                            new_h = int(cw / raspect)
                        
                        frame = cv2.resize(frame, (new_w, new_h))
                        self.last_scale = (new_w / fw, new_h / fh)
                        self.last_offset = ((cw - new_w) // 2, (ch - new_h) // 2)
                        self.display_size = (new_w, new_h)
                    
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame)
                    photo = ImageTk.PhotoImage(image=img)
                    self.root.after(0, self.update_canvas, photo)
        except:
            pass
        finally:
            self.on_disconnect()

    def on_disconnect(self):
        if self.running:
            self.running = False
            self.root.after(0, lambda: self.safe_exit("Server connection lost."))

    def safe_exit(self, msg=None):
        self.running = False
        if msg: messagebox.showwarning("Connection Status", msg)
        try:
            if self.screen_socket: self.screen_socket.close()
            if self.input_socket: self.input_socket.close()
            self.root.destroy()
        except: pass
            
    def update_canvas(self, photo):
        self.photo = photo 
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.delete("all")
        # Center the image
        x_off, y_off = getattr(self, 'last_offset', (0, 0))
        self.canvas.create_image(x_off, y_off, image=photo, anchor=tk.NW)

    def send_input(self, data):
        if self.input_socket and self.running:
            try:
                json_bytes = json.dumps(data).encode('utf-8')
                self.input_socket.sendall(struct.pack('>L', len(json_bytes)) + json_bytes)
            except: pass
                
    def on_mouse_move(self, event):
        # Use the actual image display size for relative mapping
        dw, dh = getattr(self, 'display_size', (1, 1))
        x_off, y_off = getattr(self, 'last_offset', (0, 0))
        
        # Calculate relative to the image, not the whole canvas
        rel_x = (event.x - x_off) / dw
        rel_y = (event.y - y_off) / dh
        
        # Clamp to [0, 1]
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        
        self.send_input({'type': 'mouse_move', 'x': rel_x, 'y': rel_y})

    def on_mouse_click(self, event):
        btn = {1: 'left', 2: 'middle', 3: 'right'}.get(event.num, 'left')
        self.send_input({'type': 'mouse_click', 'button': btn, 'pressed': True})

    def on_mouse_release(self, event):
        btn = {1: 'left', 2: 'middle', 3: 'right'}.get(event.num, 'left')
        self.send_input({'type': 'mouse_click', 'button': btn, 'pressed': False})

    def on_key_press(self, event):
        self.send_input({'type': 'key_press', 'key': event.keysym})

    def on_key_release(self, event):
        self.send_input({'type': 'key_release', 'key': event.keysym})

    def send_overlay_toggle(self):
        self.send_input({'type': 'overlay_toggle'})

    def send_text(self):
        text = self.entry_text.get("1.0", "end-1c")
        self.send_input({'type': 'overlay_text', 'text': text})

    def clear_text(self):
        self.entry_text.delete("1.0", tk.END)
        self.send_text()

    def toggle_settings(self):
        if self.settings_frame.winfo_viewable():
            self.settings_frame.pack_forget()
        else:
            self.settings_frame.pack(side=tk.RIGHT, fill=tk.Y)

    def send_style(self, bg=None, fg=None, size=None):
        data = {'type': 'overlay_style'}
        if bg: data['bg'] = bg
        if fg: data['fg'] = fg
        if size: data['size'] = size
        self.send_input(data)

    def send_move(self, rel_x, rel_y):
        self.send_input({'type': 'overlay_move', 'x': rel_x, 'y': rel_y})

    def send_visibility(self):
        self.send_input({'type': 'overlay_visibility', 'visible': self.visible_var.get()})

if __name__ == "__main__":
    # Standalone testing
    root = tk.Tk()
    # Dummy config
    app = RemoteDesktopClient(root, "127.0.0.1", 9999, 9998)
    root.mainloop()
