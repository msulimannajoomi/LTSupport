import os
import subprocess
import sys


def build():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    root = os.path.dirname(os.path.abspath(__file__))
    app_entry = os.path.join(root, "desktop-app", "main.py")
    icon_ico = os.path.join(root, "app_icon.ico")
    icon_png = os.path.join(root, "app_icon.png")

    print("\n" + "=" * 50)
    print("BUILDING VANTAGEPOINT DESKTOP APP")
    print("=" * 50)

    # --onefile: bundle everything into one .exe
    # --windowed: no console window (this is a GUI app)
    # --collect-all: customtkinter/pynput pick their backend dynamically, so a plain
    #                import scan misses files PyInstaller needs to bundle
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        f"--icon={icon_ico}",
        "--name=VantagePoint",
        "--collect-all=customtkinter",
        "--collect-all=pynput",
        "--collect-all=pyaudiowpatch",
        f"--add-data={icon_ico};.",
        f"--add-data={icon_png};.",
        app_entry,
    ]

    print(f"Executing: {' '.join(cmd)}")

    try:
        subprocess.check_call(cmd, cwd=root)
        print("\n" + "=" * 50)
        print("BUILD COMPLETE!")
        print(f"Your executable is at: {os.path.join(root, 'dist', 'VantagePoint.exe')}")
        print("=" * 50)
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error: {e}")


if __name__ == "__main__":
    build()
