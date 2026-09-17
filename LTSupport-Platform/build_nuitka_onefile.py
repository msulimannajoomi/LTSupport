import os
import subprocess
import sys


def build():
    """Builds a single-file VantagePoint.exe -- the alternative to build_nuitka.py's
    --standalone folder output. Convenient (one file to send), but Windows
    Defender's heuristics have been observed flagging onefile's self-extract-to-temp
    startup pattern as a false-positive "dropper" (Program:Win32/Contebrew.A!ml,
    Trojan:Win32/Cloxer -- confirmed via Get-MpThreatDetection against real builds
    from this same project, not a guess). That's why build_nuitka.py switched to
    --standalone as the default: this script exists purely as an option for when a
    single file is worth that risk (e.g. quick internal testing on a machine/folder
    that's already Defender-excluded), not for handing to someone else cold."""
    try:
        import nuitka  # noqa: F401
    except ImportError:
        print("Nuitka not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nuitka"])

    root = os.path.dirname(os.path.abspath(__file__))
    app_entry = os.path.join(root, "desktop-app", "main.py")
    icon_ico = os.path.join(root, "app_icon.ico")
    output_dir = os.path.join(root, "dist_nuitka_onefile")

    print("\n" + "=" * 50)
    print("BUILDING VANTAGEPOINT DESKTOP APP (Nuitka, single-file)")
    print("=" * 50)

    cmd = [
        sys.executable, "-m", "nuitka",
        "--onefile",
        # Nuitka's onefile payload compression defaults to zstd level 22 (its
        # max), which is memory-hungry enough to crash with "Allocation error:
        # not enough memory" compressing a ~230MB payload on a machine that's
        # low on free RAM at build time. --low-memory drops that to level 3 --
        # still real compression (much smaller than an uncompressed exe), just
        # without the huge working-memory requirement level 22 needs.
        "--low-memory",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={icon_ico}",
        f"--include-data-files={icon_ico}=app_icon.ico",
        "--output-dir=" + output_dir,
        "--output-filename=VantagePoint.exe",
        "--company-name=VantagePoint",
        "--product-name=VantagePoint",
        "--file-version=1.0.0.0",
        "--product-version=1.0.0.0",
        "--enable-plugins=tk-inter",
        "--include-package=customtkinter",
        "--include-package-data=customtkinter",
        "--include-package=pynput",
        "--include-package-data=pynput",
        "--include-package=cv2",
        # sounddevice.py itself is a plain module (no data of its own), but it
        # loads the PortAudio DLL out of the separate _sounddevice_data
        # package at runtime -- that's the one that actually needs bundling.
        "--include-package=_sounddevice_data",
        "--include-package-data=_sounddevice_data",
        "--include-package=mss",
        "--include-package=pyautogui",
        # pyaudiowpatch's __init__.py loads its native extension via a top-level
        # `import _portaudiowpatch` (a standalone .pyd next to site-packages, not
        # nested inside the pyaudiowpatch package folder) -- explicit here for the
        # same reason as _sounddevice_data above, rather than trusting Nuitka's
        # plain import-following to catch a module imported inside a try/except.
        "--include-package=pyaudiowpatch",
        "--include-module=_portaudiowpatch",
        "--assume-yes-for-downloads",
        app_entry,
    ]

    print(f"Executing: {' '.join(cmd)}")

    try:
        subprocess.check_call(cmd, cwd=root)
        print("\n" + "=" * 50)
        print("BUILD COMPLETE!")
        print(f"Your executable is at: {os.path.join(output_dir, 'VantagePoint.exe')}")
        print("Note: this build is more likely to trip antivirus false-positives than")
        print("build_nuitka.py's --standalone output -- see the module docstring above.")
        print("=" * 50)
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error: {e}")


if __name__ == "__main__":
    build()
