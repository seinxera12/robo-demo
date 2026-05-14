; Demo Voice Assistant — Inno Setup 6 installer script
;
; Build command (run from project root):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\DemoVoiceAssistant.iss
;
; Prerequisites:
;   1. PyInstaller bundle already built at ..\dist\DemoVoiceAssistant\
;   2. vc_redist.x64.exe placed in this installer\ folder
;      Download: https://aka.ms/vs/17/release/vc_redist.x64.exe
;
; Output: installer\output\DemoVoiceAssistantSetup.exe

#define MyAppName      "Demo Voice Assistant"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "Your Company Name"
#define MyAppExeName   "DemoVoiceAssistant.exe"
#define MyAppDir       "..\dist\DemoVoiceAssistant"

[Setup]
; IMPORTANT: This GUID identifies your app in the Windows registry.
; Every distinct product must have its own unique GUID — never reuse.
; Generate a new one in PowerShell: [guid]::NewGuid()
AppId={{B7E4A2F1-3C8D-4E9B-A051-F62D8C3E7A90}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://yourcompany.com
AppSupportURL=https://yourcompany.com/support
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=DemoVoiceAssistantSetup
; Uncomment once assets\icon.ico exists:
; SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
; Windows 10 1809 minimum — required for torch + modern audio APIs
MinVersion=10.0.17763
DisableWelcomePage=yes
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
; Close the app automatically if running during install or update.
; Without this, installer fails to overwrite the locked .exe.
CloseApplications=yes
CloseApplicationsFilter=DemoVoiceAssistant.exe
RestartApplications=no
; Approximate installed size for Windows Add/Remove Programs (2.5 GB)
ExtraDiskSpaceRequired=2500000000

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Additional icons:"; Flags: checkedonce

[Files]
; Main application bundle — full _internal\ structure preserved
Source: "{#MyAppDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; VC++ 2015-2022 redistributable — silently installed if missing.
; Required for vcruntime140.dll which the app depends on.
; Place vc_redist.x64.exe in this installer\ folder before building.
Source: "vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
; Start Menu
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; Uninstall entry in Start Menu
Name: "{autoprograms}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Desktop shortcut (optional, user controls via Tasks above)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
; Install VC++ runtime silently first, only if not already present
Filename: "{tmp}\vc_redist.x64.exe"; \
    Parameters: "/install /quiet /norestart"; \
    Check: VCRedistNeedsInstall; \
    StatusMsg: "Installing Visual C++ Runtime..."; \
    Flags: waituntilterminated

; Offer to launch the app after installation completes
Filename: "{app}\{#MyAppExeName}"; \
    Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the AppData config folder on uninstall.
; This contains the .env file with the user's API keys (Groq, Tavily).
; The user is warned about this via the uninstaller's default confirmation.
Type: filesandordirs; Name: "{userappdata}\DemoVoiceAssistant"

[Code]
// ── VC++ Redistributable check ────────────────────────────────────────────
// Checks the registry for the VS 2022 x64 runtime.
// Returns True if it needs to be installed, False if already present.
function VCRedistNeedsInstall: Boolean;
begin
  Result := not RegKeyExists(
    HKLM,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64'
  );
end;

// ── Initialization check ──────────────────────────────────────────────────
// Placeholder for any pre-install checks you may want to add later
// (e.g. checking available disk space, Windows version beyond MinVersion).
function InitializeSetup(): Boolean;
begin
  Result := True;
end;