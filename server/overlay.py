import tkinter as tk
import threading
import tkinter.font as tkfont
import ctypes

class MovableOverlay:
    def __init__(self, root):
        self.root = root
        self.root.title("Remote Message Overlay")
        
        # Initial Setup: 50% width, centered
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        initial_width = int(screen_width * 0.5)
        initial_height = int(screen_height * 0.15) # Start with reasonable height, user can resize
        x = int((screen_width - initial_width) / 2)
        y = int(screen_height * 0.1)
        
        self.root.geometry(f"{initial_width}x{initial_height}+{x}+{y}")
        self.root.overrideredirect(True)  # Remove window decorations (frameless)
        self.root.attributes("-topmost", True)  # Always on top
        self.root.attributes("-alpha", 0.5) 
        self.root.configure(bg='black')
        self.bg_color = 'black'
        self.fg_color = '#0000AA'
        self.is_hidden_from_capture = True

        # Apply Window Display Affinity (Hide from Screen Capture/Zoom)
        self.apply_affinity()

        # Main Container Frame
        self.frame = tk.Frame(self.root, bg='black', relief='flat', bd=0)
        self.frame.pack(fill='both', expand=True)

        # 1. Draggable Header (Top) -> Container Frame
        self.header = tk.Frame(self.frame, bg='black')
        self.header.pack(side='top', fill='x')

        # Drag Handle (Takes up most space)
        self.drag_handle = tk.Label(self.header, text="::", bg='black', fg='#555555', cursor="fleur")
        self.drag_handle.pack(side='left', fill='x', expand=True)
        
        # Close Button (Top Right)
        self.close_btn = tk.Label(self.header, text=" X ", bg='black', fg='#FF5555', cursor="hand2", font=("Arial", 10, "bold"))
        self.close_btn.pack(side='right')
        
        # Bind dragging to both the frame and the handle for better UX
        for widget in (self.header, self.drag_handle):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<ButtonRelease-1>", self.stop_move)
            widget.bind("<B1-Motion>", self.do_move)
            
        # Bind Close Action
        self.close_btn.bind("<ButtonPress-1>", self.close_overlay)

        # 2. Resizable Grip (Bottom Right)
        # Using a small label frame or canvas as grip
        self.grip_frame = tk.Frame(self.frame, bg='black', cursor="sizing")
        self.grip_frame.pack(side='bottom', fill='x')
        
        self.grip = tk.Label(self.grip_frame, text="◢", bg='black', fg='#333333', cursor="bottom_right_corner")
        self.grip.pack(side='right', anchor='se')
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<ButtonRelease-1>", self.stop_resize)
        self.grip.bind("<B1-Motion>", self.do_resize)

        # 3. Text Entry (Middle) -> Now a Multiline Text Area
        # Use simple initial font, will auto-adjust
        self.font_size = 14
        self.custom_font = tkfont.Font(family="Arial", size=self.font_size, weight="bold")
        
        # Using tk.Text for multiline support
        # Styling: Black BG, Medium Blue Text. 
        # Combined with Alpha 0.3, this should be "ghostly" but readable to user.
        self.entry = tk.Text(self.frame, bg='black', fg='#0000AA', font=self.custom_font, wrap=tk.WORD, bd=0)
        self.entry.pack(side='top', fill='both', expand=True, padx=5, pady=0)
        # Placeholder text removed as requested
        # self.entry.insert("1.0", "Type instructions here...\nThey will wrap automatically.")
        
        # Bind resize event to auto-adjust font
        self.root.bind('<Configure>', self.on_window_resize)

        # State variables
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0
        self.start_x = 0
        self.start_y = 0

    # Moving Logic
    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def stop_move(self, event):
        self.x = None
        self.y = None

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")

    # Resizing Logic
    def start_resize(self, event):
        self.start_x = event.x_root
        self.start_y = event.y_root
        # Handle cases where width/height might not be updated yet
        self.width = self.root.winfo_width()
        self.height = self.root.winfo_height()

    def stop_resize(self, event):
        pass

    def do_resize(self, event):
        delta_x = event.x_root - self.start_x
        delta_y = event.y_root - self.start_y
        
        new_w = max(200, self.width + delta_x)
        new_h = max(100, self.height + delta_y)
        
        self.root.geometry(f"{new_w}x{new_h}")

    # Auto Font Logic
    def on_window_resize(self, event):
        # Only react if the event is from the root window (ignoring child widgets)
        if event.widget == self.root:
            # Calculate new font size based on height
            # For multiline instructions, we want to see roughly 10-15 lines of text
            new_height = event.height
            
            # Heuristic: Height / 15 gives space for header + ~10 lines
            target_size = int(new_height / 15)
            
            # Clamping font size limits
            if target_size < 10: target_size = 10
            if target_size > 40: target_size = 40
            
            if target_size != self.font_size:
                self.font_size = target_size
                self.custom_font.configure(size=self.font_size)

    def apply_affinity(self):
        """
        Sets the window display affinity to exclude it from screen capture.
        WDA_EXCLUDEFROMCAPTURE = 0x00000011 (Windows 10 Version 2004+)
        """
        try:
            # Constant for WDA_EXCLUDEFROMCAPTURE
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            
            # Get the HWND (Window Handle)
            # update_idletasks needed to ensure the window exists and has an ID
            self.root.update_idletasks() 
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())

            # Call SetWindowDisplayAffinity
            result = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            
            if result:
                print("Successfully hid overlay from screen capture.")
            else:
                print("Failed to set window affinity. Error code:", ctypes.GetLastError())
        except Exception as e:
            print(f"Error applying window affinity: {e}")

    def update_text(self, text):
        """Update the text field programmatically (if needed from client)"""
        # For Text widget, indices are "line.char" string
        self.entry.delete("1.0", tk.END)
        self.entry.insert("1.0", text)

    def update_style(self, bg=None, fg=None, size=None):
        """Update background color, foreground color, and font size"""
        if bg:
            self.bg_color = bg
            self.root.configure(bg=bg)
            self.frame.configure(bg=bg)
            self.header.configure(bg=bg)
            self.drag_handle.configure(bg=bg)
            self.close_btn.configure(bg=bg)
            self.grip_frame.configure(bg=bg)
            self.grip.configure(bg=bg)
            self.entry.configure(bg=bg, insertbackground=fg if fg else self.fg_color)
        if fg:
            self.fg_color = fg
            self.entry.configure(fg=fg, insertbackground=fg)
        if size:
            try:
                self.font_size = int(size)
                self.custom_font.configure(size=self.font_size)
            except: pass

    def update_position(self, x, y):
        """Update window position"""
        self.root.geometry(f"+{int(x)}+{int(y)}")

    def set_capture_visibility(self, visible_on_capture):
        """Toggle whether the window is hidden from screen capture"""
        self.is_hidden_from_capture = not visible_on_capture
        # To change affinity, we usually need to recreate or reset it.
        # WDA_NONE = 0
        # WDA_EXCLUDEFROMCAPTURE = 0x00000011
        affinity = 0x00000011 if not visible_on_capture else 0
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity)
        except: pass

    def close_overlay(self, event=None):
        """Hide the overlay window instead of destroying it to keep server alive"""
        self.root.withdraw()

    def show_overlay(self):
        """Show the overlay window"""
        self.root.deiconify()
        self.root.attributes("-topmost", True)

# Function to run the overlay in a separate thread so it doesn't block the main server loop
def run_overlay():
    root = tk.Tk()
    app = MovableOverlay(root)
    root.mainloop()

if __name__ == "__main__":
    run_overlay()
