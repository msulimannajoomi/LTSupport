import glob
import os
import subprocess
import sys


def _find_iscc():
    """Locates Inno Setup's compiler (ISCC.exe). Not always on PATH even after
    installing (winget installs it per-user under AppData, not into PATH) -- checks
    the common install locations directly rather than assuming."""
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    # Last resort: PATH itself, in case it was added there some other way.
    from shutil import which
    return which("ISCC") or which("ISCC.exe")


def build():
    root = os.path.dirname(os.path.abspath(__file__))
    standalone_dir = os.path.join(root, "dist_nuitka", "VantagePoint")
    exe_path = os.path.join(standalone_dir, "VantagePoint.exe")

    if not os.path.isfile(exe_path):
        print("No standalone build found at:")
        print(f"  {exe_path}")
        print("Run 'python build_nuitka.py' first to produce it, then re-run this script.")
        return

    iscc = _find_iscc()
    if not iscc:
        print("Inno Setup's compiler (ISCC.exe) was not found.")
        print("Install it with: winget install --id JRSoftware.InnoSetup")
        print("or download it from https://jrsoftware.org/isdl.php, then re-run this script.")
        return

    print("\n" + "=" * 50)
    print("BUILDING VANTAGEPOINT INSTALLER (Inno Setup)")
    print("=" * 50)

    iss_path = os.path.join(root, "installer.iss")
    cmd = [iscc, iss_path]
    print(f"Executing: {' '.join(cmd)}")

    try:
        subprocess.check_call(cmd, cwd=root)
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error: {e}")
        return

    outputs = glob.glob(os.path.join(root, "dist_installer", "*.exe"))
    print("\n" + "=" * 50)
    print("BUILD COMPLETE!")
    if outputs:
        print(f"Your installer is at: {outputs[0]}")
    print("This one file is everything a recipient needs -- they run it, click through")
    print("the install wizard (no admin prompt -- installs per-user), and get a normal")
    print("Start Menu / Desktop shortcut. No folder of loose files to hand them.")
    print("=" * 50)


if __name__ == "__main__":
    build()
