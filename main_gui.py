import tkinter as tk
from tkinter import messagebox, ttk
import threading
import os
import sys
import time
import ctypes

# Enable DPI awareness for sharp UI on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1) # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Add current directory to path so we can import from server and client folders
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from server.server import RemoteServer
    from client.client import RemoteDesktopClient
except ImportError as e:
    print(f"Import Error: {e}")
    RemoteServer = None
    RemoteDesktopClient = None

# --- DESIGN CONSTANTS ---
BG_DARK = "#0f172a"      # Deep Navy/Slate
BG_SECONDARY = "#1e293b" # Muted Blue/Grey
ACCENT = "#38bdf8"       # Radiant Sky Blue
ACCENT_HOVER = "#0ea5e9" # Deepened Sky Blue
TEXT_PRIMARY = "#f8fafc" # Near-White
TEXT_SECONDARY = "#94a3b8" # Muted Slate
# Professional Design Tokens - Deep Cosmic Theme
BG_DARK = "#0f172a"      # Deep Slate Blue
BG_SECONDARY = "#1e293b" # Lighter Slate
BG_CARD = "#1e293b"      # Card Background
ACCENT = "#38bdf8"       # Sky Blue Accent
ACCENT_HOVER = "#7dd3fc"
TEXT_PRIMARY = "#f8fafc" # Near White
TEXT_SECONDARY = "#94a3b8" # Muted Slate
SUCCESS = "#4ade80"      # Modern Emerald
WARNING = "#fbbf24"      # Amber

class HoverButton(tk.Button):
    def __init__(self, master, **kwargs):
        self.normal_bg = kwargs.get('bg', ACCENT)
        self.hover_bg = kwargs.get('activebackground', ACCENT_HOVER)
        self.normal_fg = kwargs.get('fg', BG_DARK)
        
        # Pull or set defaults for premium styling
        kwargs['relief'] = kwargs.get('relief', 'flat')
        kwargs['overrelief'] = kwargs.get('overrelief', 'flat')
        kwargs['borderwidth'] = 0
        kwargs['font'] = kwargs.get('font', ('Segoe UI Bold', 12))
        kwargs['cursor'] = 'hand2'
        
        super().__init__(master, **kwargs)
        
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)

    def on_enter(self, e):
        self.config(bg=self.hover_bg)

    def on_leave(self, e):
        self.config(bg=self.normal_bg)

class ScrollableFrame(tk.Frame):
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.canvas = tk.Canvas(self, bg=BG_DARK, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=BG_DARK)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )

        # Use window create to allow centering logic
        self.window_id = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # Center the frame when canvas resizes
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        # Smooth scrolling
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.scrollable_frame.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.scrollable_frame.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_canvas_configure(self, event):
        # Resize inner frame to match canvas width
        self.canvas.itemconfig(self.window_id, width=event.width)
        # Update scrollregion
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

class LTSupportApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LTSupport - Professional Remote Assistance")
        self.root.geometry("1100x800")
        self.root.state('zoomed')
        self.root.configure(bg=BG_DARK)
        
        self.logo_img = None
        try:
            logo_path = os.path.join(os.path.dirname(__file__), "app_icon.png")
            if os.path.exists(logo_path):
                from PIL import Image, ImageTk
                img = Image.open(logo_path)
                img = img.resize((120, 120), Image.Resampling.LANCZOS)
                self.logo_img = ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"Logo load error: {e}")

        # Styles
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.current_frame = None
        self.server_instance = None
        self.show_home()

    def clear_frame(self):
        if hasattr(self, 'scroll_container') and self.scroll_container:
            self.scroll_container.destroy()
            self.scroll_container = None
        if self.current_frame:
            try:
                self.current_frame.destroy()
            except: pass
            self.current_frame = None
        self.root.update_idletasks()

    def show_home(self):
        self.clear_frame()
        self.scroll_container = ScrollableFrame(self.root, bg=BG_DARK)
        self.scroll_container.pack(fill='both', expand=True)
        
        # Consistent Centering Container
        container = tk.Frame(self.scroll_container.scrollable_frame, bg=BG_DARK)
        container.pack(fill='both', expand=True, padx=40, pady=40)
        
        self.current_frame = tk.Frame(container, bg=BG_DARK)
        self.current_frame.pack(anchor='n', fill='x')
        
        # Header Area
        header = tk.Frame(self.current_frame, bg=BG_DARK)
        header.pack(fill='x', pady=(0, 40))
        
        # Logo with better spacing
        if self.logo_img:
            logo_lbl = tk.Label(header, image=self.logo_img, bg=BG_DARK)
            logo_lbl.pack(pady=(0, 25))
            
        tk.Label(header, text="REMOTE ASSISTANCE", font=('Segoe UI Bold', 28), 
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack()
        tk.Label(header, text="Unified Support & Management Suite", font=('Segoe UI', 12), 
                 fg=TEXT_SECONDARY, bg=BG_DARK).pack(pady=5)

        # Card Container
        cards_row = tk.Frame(self.current_frame, bg=BG_DARK)
        cards_row.pack(fill='x', pady=20)
        
        # Using a consistent horizontal spacing
        self.create_action_card(cards_row, "Establish Host", "Share your screen for assistance", 
                               SUCCESS, self.show_host_config)
        
        # Spacer
        tk.Frame(cards_row, width=40, bg=BG_DARK).pack(side='left')
        
        self.create_action_card(cards_row, "Join Session", "Connect to a remote workspace", 
                               ACCENT, self.show_join_config)

    def create_action_card(self, parent, title, desc, color, command):
        frame = tk.Frame(parent, bg="#1e293b", padx=2, pady=2) # Outer border simulation
        frame.pack(side='left', fill='both', expand=True)
        
        inner = tk.Frame(frame, bg=BG_SECONDARY, padx=40, pady=50, cursor='hand2')
        inner.pack(fill='both', expand=True)
        inner.bind("<Button-1>", lambda e: command())
        
        t_lbl = tk.Label(inner, text=title.upper(), font=('Segoe UI Bold', 14), 
                        fg=color, bg=BG_SECONDARY)
        t_lbl.pack(anchor='w')
        
        d_lbl = tk.Label(inner, text=desc, font=('Segoe UI', 10), 
                        fg=TEXT_SECONDARY, bg=BG_SECONDARY)
        d_lbl.pack(anchor='w', pady=(10, 20))
        
        # Minimalist button indicator
        btn_indicator = tk.Label(inner, text="GOTO →", font=('Segoe UI Black', 10), 
                                fg=color, bg=BG_SECONDARY)
        btn_indicator.pack(anchor='w')

        def on_enter(e): 
            frame.config(bg=color)
            inner.config(bg="#2d3748")
            t_lbl.config(bg="#2d3748")
            d_lbl.config(bg="#2d3748")
            btn_indicator.config(bg="#2d3748")

        def on_leave(e): 
            frame.config(bg="#1e293b")
            inner.config(bg=BG_SECONDARY)
            t_lbl.config(bg=BG_SECONDARY)
            d_lbl.config(bg=BG_SECONDARY)
            btn_indicator.config(bg=BG_SECONDARY)
        
        for widget in (inner, t_lbl, d_lbl, btn_indicator):
            widget.bind("<Button-1>", lambda e: command())
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

    def show_host_config(self):
        self.clear_frame()
        self.scroll_container = ScrollableFrame(self.root, bg=BG_DARK)
        self.scroll_container.pack(fill='both', expand=True)
        
        # Center Container
        # We use a frame inside the scrollable frame that centers itself
        container = tk.Frame(self.scroll_container.scrollable_frame, bg=BG_DARK)
        container.pack(fill='both', expand=True, padx=20, pady=40)
        
        # The actual content card
        # Using pack allows the frame to expand naturally based on content height
        self.current_frame = tk.Frame(container, bg=BG_DARK)
        self.current_frame.pack(anchor='n', ipadx=20) # ipadx adds some minimal width safety

        # Navigation
        tk.Button(self.current_frame, text="←  RETURN TO DASHBOARD", command=self.show_home, 
                  bg=BG_DARK, fg=TEXT_SECONDARY, bd=0, font=('Segoe UI', 10, 'bold'), 
                  cursor='hand2', activebackground=BG_DARK, activeforeground=TEXT_PRIMARY).pack(anchor='w')
        
        tk.Label(self.current_frame, text="Establish Host", 
                 font=('Segoe UI Bold', 24), fg=TEXT_PRIMARY, bg=BG_DARK).pack(anchor='w', pady=(20, 5))
        tk.Label(self.current_frame, text="Configure your network visibility and begin hosting.", 
                 font=('Segoe UI', 11), fg=TEXT_SECONDARY, bg=BG_DARK).pack(anchor='w', pady=(0, 30))

        # Mode Selector Card
        mode_card = tk.Frame(self.current_frame, bg=BG_SECONDARY, padx=40, pady=30)
        mode_card.pack(fill='x', pady=(0, 25))
        
        tk.Label(mode_card, text="CONNECTION MODE", font=('Segoe UI Bold', 10), 
                 fg=ACCENT, bg=BG_SECONDARY).pack(anchor='w', pady=(0, 15))
        
        self.conn_type = tk.StringVar(value="anywhere")
        style_rb = {"bg": BG_SECONDARY, "fg": TEXT_PRIMARY, "selectcolor": BG_DARK, "font": ('Segoe UI Semibold', 11), "bd":0, "indicatoron":0, "padx":30, "pady":12}
        
        rb_frame = tk.Frame(mode_card, bg=BG_SECONDARY)
        rb_frame.pack(fill='x')
        
        tk.Radiobutton(rb_frame, text="Local Network (LAN)", variable=self.conn_type, 
                      value="local", command=self.update_host_id_display, **style_rb).pack(side='left', padx=(0, 15))
        tk.Radiobutton(rb_frame, text="Internet (Public IP)", variable=self.conn_type, 
                      value="anywhere", command=self.update_host_id_display, **style_rb).pack(side='left')

        # Identity Card
        id_card = tk.Frame(self.current_frame, bg=BG_SECONDARY, padx=40, pady=35)
        id_card.pack(fill='x')
        
        tk.Label(id_card, text="SESSION ID (IP ADDRESS)", font=('Segoe UI Bold', 10), 
                 fg=SUCCESS, bg=BG_SECONDARY).pack(anchor='w', pady=(0, 15))
        
        self.conn_id_var = tk.StringVar(value="PENDING INITIALIZATION")
        self.ent_id = tk.Entry(id_card, textvariable=self.conn_id_var, font=('Consolas', 22, 'bold'), 
                              bg=BG_DARK, fg=SUCCESS, bd=0, justify='center', insertbackground=SUCCESS)
        self.ent_id.pack(fill='x', ipady=18)
        # Removed readonly state to allow manual IP override if detection is wrong
        
        ctl_row = tk.Frame(id_card, bg=BG_SECONDARY)
        ctl_row.pack(fill='x', pady=(20, 0))
        
        self.btn_copy = tk.Button(ctl_row, text="COPY SESSION ID", command=self.copy_id, 
                                 bg=BG_DARK, fg=TEXT_PRIMARY, bd=0, padx=30, pady=10, 
                                 font=('Segoe UI Bold', 10), cursor='hand2')
        self.btn_copy.pack(side='left')
        
        self.lbl_status = tk.Label(ctl_row, text="SYSTEM READY", bg=BG_SECONDARY, 
                                  fg=TEXT_SECONDARY, font=('Segoe UI Semibold', 10))
        self.lbl_status.pack(side='right')

        # Action Button
        self.btn_start_host = HoverButton(self.current_frame, text="INITIALIZE SECURE HOST SESSION", 
                                         command=self.start_hosting, bg=ACCENT, fg=BG_DARK)
        self.btn_start_host.pack(fill='x', pady=(40, 10), ipady=15)
        
        # Connection Help
        help_txt = "TIPS: Wait 10-20s for 'SSH Active'. Copy the ID once it appears."
        help_tip = tk.Label(self.current_frame, text=help_txt, font=('Segoe UI', 9), 
                           fg=TEXT_SECONDARY, bg=BG_DARK)
        help_tip.pack(pady=10)

    def update_host_id_display(self):
        if not self.server_instance: return
            
        if self.conn_type.get() == "local":
            self.conn_id_var.set(self.server_instance.local_ip)
            self.lbl_status.config(text="STATUS: LAN DISCOVERY ACTIVE", fg=SUCCESS)
        else:
            if getattr(self.server_instance, 'ngrok_urls', None):
                # Using the SSH tunnel URLs (Pinggy or similar)
                # Show as HOST:PORT1:PORT2 (e.g. tcp.pinggy.io:12345:12346)
                s_url = self.server_instance.ngrok_urls[0] # tcp.pinggy.io:XXXXX
                i_url = self.server_instance.ngrok_urls[1] # tcp.pinggy.io:YYYYY
                if ":" in s_url and ":" in i_url:
                    # Construct combined ID: HOST:PORT1:PORT2
                    # s_url is HOST:PORT1
                    # i_url is HOST:PORT2 -> take only PORT2
                    anywhere_id = f"{s_url}:{i_url.split(':')[-1]}"
                    self.conn_id_var.set(anywhere_id)
                else:
                    self.conn_id_var.set(s_url)
                # Status is updated below by priority logic
            elif self.server_instance.public_ip != "Fetching..." and self.server_instance.public_ip != "Unavailable":
                self.conn_id_var.set(self.server_instance.public_ip)
                self.lbl_status.config(text="STATUS: PUBLIC IP ACTIVE", fg=SUCCESS)
            else:
                self.conn_id_var.set("RESOLVING...")
                self.lbl_status.config(text="STATUS: INITIALIZING...", fg=ACCENT)
        
        # Improved Status Feedback
        try:
            current_status = self.lbl_status.cget("text")
            
            # Helper to safely get attributes
            tun_stat = getattr(self.server_instance, 'tunnel_status', None)
            upnp_stat = getattr(self.server_instance, 'upnp_status', None)

            if self.conn_type.get() == "anywhere":
                # Priority: Tunnel Status > UPnP Status
                if tun_stat:
                    if "Active" in tun_stat:
                        self.lbl_status.config(text=f"STATUS: SECURE TUNNEL ACTIVE", fg=SUCCESS)
                    else:
                        self.lbl_status.config(text=f"STATUS: {tun_stat}", fg=ACCENT)
                elif upnp_stat:
                     # Fallback to UPnP if no tunnel status yet
                     self.lbl_status.config(text=f"STATUS: UPnP {upnp_stat}", fg=WARNING if "Failed" in upnp_stat else ACCENT)
            else:
                # LAN Mode: Show UPnP as primary info
                 if upnp_stat:
                     self.lbl_status.config(text=f"STATUS: LAN (UPnP: {upnp_stat})", fg=SUCCESS)
        except:
             pass

    def start_hosting(self):
        if self.server_instance: return
        self.lbl_status.config(text="STATUS: ALLOCATING RESOURCES...", fg=ACCENT)
        self.btn_start_host.config(state='disabled', bg=BG_SECONDARY, text="SYSTEM INITIALIZING...")
        
        use_tun = (self.conn_type.get() == "anywhere")
        
        def run():
            self.server_instance = RemoteServer()
            launch_overlay = self.server_instance.start(use_tunnel=use_tun)
            
            # Wait for tunnel or IP
            # Extended wait time to 90s for slower connections/retries
            max_wait = 90
            for i in range(max_wait): 
                time.sleep(1)
                self.root.after(0, self.update_host_id_display)
                
                # Feedback on button
                remaining = max_wait - i
                self.root.after(0, lambda r=remaining: self.btn_start_host.config(text=f"CONNECTING... ({r}s)"))
                
                if use_tun:
                    if getattr(self.server_instance, 'ngrok_urls', None): break
                else:
                    # In LAN mode, we don't need to wait for a public IP
                    if self.server_instance.running: break

            self.root.after(0, launch_overlay)
            self.root.after(0, lambda: self.btn_start_host.config(text="HOSTING ACTIVE", state='normal', bg=SUCCESS))
            
        threading.Thread(target=run, daemon=True).start()

    def enter_background_mode(self):
        messagebox.showinfo("Background Mode", "Hosting is now active in the background.\n\nYour UI has been hidden to minimize workspace impact.\nUse the Movable Overlay to manage the session.")
        self.root.withdraw()

    def show_join_config(self):
        self.clear_frame()
        self.scroll_container = ScrollableFrame(self.root, bg=BG_DARK)
        self.scroll_container.pack(fill='both', expand=True)
        
        # Center Container
        container = tk.Frame(self.scroll_container.scrollable_frame, bg=BG_DARK)
        container.pack(fill='both', expand=True, padx=20, pady=40)
        
        self.current_frame = tk.Frame(container, bg=BG_DARK)
        self.current_frame.pack(anchor='n', ipadx=20)
        
        tk.Button(self.current_frame, text="←  RETURN TO DASHBOARD", command=self.show_home, 
                  bg=BG_DARK, fg=TEXT_SECONDARY, bd=0, font=('Segoe UI', 10, 'bold'), 
                  cursor='hand2', activebackground=BG_DARK, activeforeground=TEXT_PRIMARY).pack(anchor='w')
        
        tk.Label(self.current_frame, text="Join Session", 
                 font=('Segoe UI Bold', 24), fg=TEXT_PRIMARY, bg=BG_DARK).pack(anchor='w', pady=(20, 5))
        tk.Label(self.current_frame, text="Enter a Host ID to establish a remote connection.", 
                 font=('Segoe UI', 11), fg=TEXT_SECONDARY, bg=BG_DARK).pack(anchor='w', pady=(0, 30))

        # Join Card
        join_card = tk.Frame(self.current_frame, bg=BG_SECONDARY, padx=50, pady=60,
                            highlightthickness=1, highlightbackground="#334155")
        join_card.pack(fill='x')
        
        tk.Label(join_card, text="ENTER SESSION ID / IP", font=('Segoe UI Bold', 10), 
                 fg=ACCENT, bg=BG_SECONDARY).pack(anchor='w', pady=(0, 20))
        
        input_container = tk.Frame(join_card, bg=BG_DARK, padx=2, pady=2)
        input_container.pack(fill='x')
        
        self.ent_join_id = tk.Entry(input_container, font=('Consolas', 20, 'bold'), 
                                   bg=BG_DARK, fg=ACCENT, insertbackground=ACCENT, 
                                   justify='center', bd=0)
        self.ent_join_id.pack(fill='x', ipady=15, padx=20)
        self.ent_join_id.focus_set()
        
        # Action Button
        self.btn_join = HoverButton(self.current_frame, text="ESTABLISH SECURE CONNECTION", 
                                   command=self.start_client, bg=SUCCESS, fg=BG_DARK)
        self.btn_join.pack(fill='x', pady=(40, 20), ipady=15)

    def start_client(self):
        conn_id = self.ent_join_id.get().strip()
        if not conn_id:
            messagebox.showerror("Validation Error", "Please provide a valid Session ID / IP.")
            return
            
        ip = conn_id
        p_screen = 9999
        p_input = 9998
        
        # Parse serveo.net:PORT1:PORT2 or IP:PORT1:PORT2
        if ":" in conn_id:
            parts = conn_id.split(":")
            ip = parts[0]
            if len(parts) == 3:
                try:
                    p_screen = int(parts[1])
                    p_input = int(parts[2])
                except: pass
            elif len(parts) == 2:
                try:
                    p_screen = int(parts[1])
                    p_input = p_screen - 1 # Fallback
                except: pass
        
        viewer_root = tk.Toplevel(self.root)
        try:
            RemoteDesktopClient(viewer_root, ip, p_screen, p_input)
        except Exception as e:
            messagebox.showerror("Handshake Failed", f"Could not connect to {ip}: {e}")
            viewer_root.destroy()

    def copy_id(self):
        val = self.conn_id_var.get()
        if "RESOLVING" in val or "PENDING" in val: return
        self.root.clipboard_clear()
        self.root.clipboard_append(val)
        messagebox.showinfo("Clipboard", "IP Address copied to secure clipboard.")
        
        # After copying, offer to enter background mode
        if self.server_instance and self.server_instance.running:
            self.root.after(500, self.enter_background_mode)

if __name__ == "__main__":
    root = tk.Tk()
    
    # Premium Window Setup
    try:
        icon_path = os.path.join(os.path.dirname(__file__), "app_icon.png")
        if os.path.exists(icon_path):
            img = tk.PhotoImage(file=icon_path)
            root.iconphoto(False, img)
    except:
        pass
        
    app = LTSupportApp(root)
    root.mainloop()
