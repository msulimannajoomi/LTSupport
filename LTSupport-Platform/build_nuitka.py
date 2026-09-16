import os
import shutil
import subprocess
import sys


def build():
    try:
        import nuitka  # noqa: F401
    except ImportError:
        print("Nuitka not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nuitka"])

    root = os.path.dirname(os.path.abspath(__file__))
    app_entry = os.path.join(root, "desktop-app", "main.py")
    icon_ico = os.path.join(root, "app_icon.ico")
    output_dir = os.path.join(root, "dist_nuitka")

    print("\n" + "=" * 50)
    print("BUILDING VANTAGEPOINT DESKTOP APP (Nuitka)")
    print("=" * 50)

    # Compiles the actual Python source to C, then to a native executable --
    # unlike PyInstaller (build_exe.py), there is no Python bytecode left in
    # the result for a decompiler to reverse; an attacker has to reverse
    # compiled machine code instead, the same category of effort as a C/C++
    # binary. Every flag below exists to make the *behavior* of the result
    # identical to the PyInstaller build, not to change anything about it:
    #   --standalone            a folder containing the exe + its dependencies,
    #                           rather than --onefile's single self-extracting
    #                           exe -- deliberate: onefile's self-extract-to-temp
    #                           step is exactly the pattern Windows Defender's
    #                           heuristics flag as a dropper (confirmed via
    #                           Get-MpThreatDetection against real onefile
    #                           builds -- Program:Win32/Contebrew.A!ml and
    #                           Trojan:Win32/Cloxer, both false positives on the
    #                           packing pattern itself, not this code). Standalone
    #                           never unpacks anything at runtime, so there's
    #                           nothing for that heuristic to catch. Trade-off:
    #                           distribute/share the whole output folder (zip
    #                           it), not a single .exe file.
    #   --windows-console-mode=disable   no console window, same as --windowed there
    #   --windows-icon-from-ico          same icon file, same as --icon there
    #   --include-data-files             bundles app_icon.ico so resource_path()
    #                                    (see desktop-app/resources.py) finds it
    #                                    at runtime exactly like it does today
    #   --enable-plugins=tk-inter        tkinter's Tcl/Tk runtime files
    #   --include-package(-data)=...     customtkinter/pynput/sounddevice/cv2 all
    #                                    load platform backends or ship non-.py
    #                                    data files that a plain import scan
    #                                    would miss -- same reason build_exe.py
    #                                    passes --collect-all for the first two.
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
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
        "--assume-yes-for-downloads",
        app_entry,
    ]

    print(f"Executing: {' '.join(cmd)}")

    try:
        subprocess.check_call(cmd, cwd=root)
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error: {e}")
        return

    # Nuitka names the standalone output folder after the entry script
    # ("main.dist", since the entry point is desktop-app/main.py) -- renamed
    # to something a recipient would actually recognize as this app.
    raw_dist = os.path.join(output_dir, "main.dist")
    final_dist = os.path.join(output_dir, "VantagePoint")
    if os.path.isdir(raw_dist):
        if os.path.isdir(final_dist):
            shutil.rmtree(final_dist)
        os.rename(raw_dist, final_dist)

    print("\n" + "=" * 50)
    print("BUILD COMPLETE!")
    print(f"Your app folder is at: {final_dist}")
    print(f"Run it via: {os.path.join(final_dist, 'VantagePoint.exe')}")
    print("To share it, zip the whole 'VantagePoint' folder -- every file in it is required,")
    print("not just the .exe.")
    print("=" * 50)


if __name__ == "__main__":
    build()
