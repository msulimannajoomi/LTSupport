import os
import subprocess
import sys

def build():
    # Ensure pyinstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    print("\n" + "="*50)
    print("BUILDING UNIFIED REMOTE TOOLKIT")
    print("="*50)

    # Base command
    # --onefile: Bundle everything into one EXE
    # --windowed: No console window
    # --icon: Use the custom icon
    # --name: Final name of the EXE
    # --add-data: Include subdirectories
    
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--icon=app_icon.ico",
        "--name=LTSupport",
        "--add-data=server;server",
        "--add-data=client;client",
        "--add-data=app_icon.png;.",
        "main_gui.py"
    ]

    print(f"Executing: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print("\n" + "="*50)
        print("BUILD COMPLETE!")
        print(f"Your executable is at: {os.path.join(os.getcwd(), 'dist', 'LTSupport.exe')}")
        print("="*50)
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error: {e}")

if __name__ == "__main__":
    build()
