import socket
import threading
import pyautogui # For screen size
from PIL import ImageGrab
import io
import struct
import time

class ScreenSharer:
    def __init__(self, server_ip, port):
        self.server_ip = server_ip
        self.port = port
        self.running = False
        self.socket = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._send_screen)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.socket:
            self.socket.close()

    def _send_screen(self):
        while self.running:
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.connect((self.server_ip, self.port))
                print(f"Connected to viewer at {self.server_ip}:{self.port}")
                
                while self.running:
                    # Capture screen
                    screenshot = ImageGrab.grab()
                    
                    # Convert to bytes
                    img_byte_arr = io.BytesIO()
                    screenshot.save(img_byte_arr, format='JPEG', quality=50) # Low quality for speed
                    img_bytes = img_byte_arr.getvalue()
                    
                    # Send size first (4 bytes), then data
                    size = len(img_bytes)
                    size_bytes = struct.pack('>L', size)
                    
                    self.socket.sendall(size_bytes + img_bytes)
                    
                    # Control frame rate (approx 10-15 FPS)
                    time.sleep(0.05)
                    
            except Exception as e:
                print(f"Connection error: {e}")
                time.sleep(2) # Retry delay
            finally:
                if self.socket:
                    self.socket.close()
