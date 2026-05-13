; Demo Voice Assistant — Inno Setup 6 installer script
;
; Build command (run from project root):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\DemoVoiceAssistant.iss
;
; Prerequisite: PyInstaller bundle already built at ..\dist\DemoVoiceAssistant\
; Output:       installer\output\DemoVoiceAssistantSetup.exe

#define MyAppName    "Demo Voice Assistant"
#define MyAppVersion "1.0.0"
#define MyAppExeName "DemoVoiceAssistant.exe"
#define MyAppDir     "..\dist\DemoVoiceAssistant"

[Setup]
AppId={{B7E4A2F1-3C8D-4E9B-A051-F62D8C3E7A90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=DemoVoiceAssistantSetup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0.17763
DisableWelcomePage=yes
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
; SetupIconFile=..\assets\icon.ico

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce

[Files]
Source: "{#MyAppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up the AppData config folder (contains the .env with API keys)
Type: filesandordirs; Name: "{userappdata}\DemoVoiceAssistant"
