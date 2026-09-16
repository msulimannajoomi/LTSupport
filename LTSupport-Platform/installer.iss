; Packages the Nuitka --standalone output (dist_nuitka\VantagePoint\, built by
; build_nuitka.py) into a normal Windows installer -- a recipient runs one
; familiar Setup.exe wizard instead of being handed a folder full of loose
; files and subfolders to sort out on their own. Run via build_installer.py,
; not directly, so the standalone build is confirmed to exist first.

#define MyAppName "VantagePoint"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "VantagePoint"
#define MyAppExeName "VantagePoint.exe"

[Setup]
AppId={{749728D0-68AA-4C10-B072-1156ECA809E4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install under AppData, not Program Files -- deliberately avoids the
; UAC "Do you want to allow this app..." admin prompt entirely (see
; PrivilegesRequired below), since the recipient is often a non-technical
; client who'd otherwise be stopped cold by an unexpected admin dialog for an
; unsigned installer.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist_installer
OutputBaseFilename=VantagePoint-Setup
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Every file the standalone build produced -- the .exe plus every .pyd/.dll
; and subfolder it depends on, all landing inside {app} together exactly as
; Nuitka laid them out. The installer is what hides that structure from the
; recipient now, not a reorganized folder layout.
Source: "dist_nuitka\VantagePoint\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[UninstallDelete]
; Inno Setup's default uninstall only removes files it explicitly installed --
; nested subfolders that end up empty (customtkinter/cv2/numpy/PIL/etc., all from
; the Nuitka standalone build's dependency tree) are otherwise left behind. This
; force-removes the whole install directory afterward so nothing lingers.
Type: filesandordirs; Name: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
