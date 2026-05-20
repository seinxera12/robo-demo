# Packaging the Demo Voice Assistant as a Windows .exe
## Complete Guide — PyInstaller + Inno Setup

**Applies to:** Demo Voice Assistant (lightweight version)  
**Target OS:** Windows 10 / Windows 11 (64-bit)  
**Python version:** 3.11.x  

---

## Table of Contents

1. [Overview & Strategy](#1-overview--strategy)
2. [Prerequisites](#2-prerequisites)
3. [Project Preparation](#3-project-preparation)
4. [PyInstaller Configuration](#4-pyinstaller-configuration)
5. [Building the Bundle](#5-building-the-bundle)
6. [First-Launch Setup Screen](#6-first-launch-setup-screen)
7. [Inno Setup Installer](#7-inno-setup-installer)
8. [Testing the Package](#8-testing-the-package)
9. [Common Issues and Fixes](#9-common-issues-and-fixes)
10. [Optional: Code Signing](#10-optional-code-signing)
11. [Distribution Checklist](#11-distribution-checklist)
12. [Quick Reference Cheatsheet](#12-quick-reference-cheatsheet)

---

## 1. Overview & Strategy

### What you're building

The final output is a single file: `DemoVoiceAssistantSetup.exe`. A client double-clicks it, runs through a standard Windows installer, gets a desktop shortcut, and on first launch enters their Groq API key. That's the entire client experience.

### How it works under the hood

```
DemoVoiceAssistantSetup.exe   ← Inno Setup installer (what you ship)
    │
    └── installs to C:\Program Files\DemoVoiceAssistant\
            │
            ├── DemoVoiceAssistant.exe     ← PyInstaller bundle launcher
            ├── _internal/                 ← All Python deps, DLLs, torch, etc.
            │     ├── torch/
            │     ├── kokoro/
            │     ├── groq/
            │     └── ... (everything bundled)
            ├── ui/                        ← Pre-built React static files
            │     └── dist/
            ├── models/                    ← Kokoro model weights (330 MB)
            │     └── kokoro-v1_19.pth
            └── config/
                  └── .env                ← Created on first launch
```

### Two-phase approach

**Phase A — PyInstaller**: Packages the Python application (server + audio client) into a self-contained folder with its own Python interpreter and all dependencies. Output: `dist/DemoVoiceAssistant/` folder.

**Phase B — Inno Setup**: Takes that folder and wraps it into a standard Windows `.exe` installer with a wizard, license screen, Start Menu entry, and desktop shortcut. Output: `DemoVoiceAssistantSetup.exe`.

### Why not a single-file .exe?

PyInstaller has a `--onefile` mode that bundles everything into one `.exe`. Avoid it for this project because:
- It extracts itself to a temp folder on every launch (~10–20 second cold start for a large app like this)
- Windows Defender is far more aggressive against single-file PyInstaller bundles
- Debugging is much harder
- The folder approach (`--onedir`) launches fast and behaves like a normal installed app

---

## 2. Prerequisites

Everything in this section must be installed on your **build machine** (your developer PC, not the client's PC). The client PC needs nothing installed beyond the final `.exe`.

### 2.1 Python 3.11 (exact version)

Download from python.org. During install:
- ✅ Check "Add Python to PATH"
- ✅ Check "Install for all users" (recommended)
- Choose "Customize installation" → check pip, tcl/tk, py launcher

Verify:
```
python --version
→ Python 3.11.x
```

Do **not** use the Microsoft Store version of Python. PyInstaller has known issues with it.

### 2.2 The project venv with all dependencies installed

```bat
cd C:\path\to\demo-voice-assistant
python -m venv venv
venv\Scripts\activate
pip install uv
uv pip install -r requirements.txt
```

The venv must be fully installed and working before you run PyInstaller. Test it:
```bat
python server/main.py
:: Should start without errors
```

### 2.3 PyInstaller

Install inside the project venv (not globally):
```bat
venv\Scripts\activate
pip install pyinstaller==6.10.0
```

Pin the version. PyInstaller releases occasionally break torch compatibility. 6.10.x is known good with torch 2.x.

### 2.4 Inno Setup 6

Download and install from: https://jrsoftware.org/isdl.php

Use the full version (not the QuickStart Pack — it's missing the preprocessor you'll need).

Default install path: `C:\Program Files (x86)\Inno Setup 6\`

### 2.5 Node.js (for building the React UI — one time only)

Only needed to build `ui/dist/`. If `ui/dist/` is already committed to the repo and up to date, skip this.

```bat
cd ui
npm install
npm run build
:: Output: ui/dist/  ← commit this to the repo
```

After committing `ui/dist/`, Node.js is not needed on any other machine.

---

## 3. Project Preparation

Before running PyInstaller, the project needs a few specific adaptations. These are permanent changes to the codebase, not one-off hacks.

### 3.1 Make all paths relative

This is the single most important step. PyInstaller bundles your app into a folder, and the working directory when launched from an `.exe` is not where you expect it to be.

Add this helper at the top of `server/config.py`:

```python
import sys
import os

def get_base_path() -> str:
    """
    Returns the base directory of the application.
    When running as a PyInstaller bundle: the folder containing the .exe
    When running as a normal Python script: the project root
    """
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        # sys.executable = C:\...\DemoVoiceAssistant.exe
        return os.path.dirname(sys.executable)
    else:
        # Running as normal Python script
        # __file__ = C:\...\demo-voice-assistant\server\config.py
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_PATH = get_base_path()
```

Then use `BASE_PATH` everywhere a file path is constructed:

```python
# ❌ WRONG — breaks in .exe
UI_DIR = "ui/dist"
ENV_FILE = ".env"
MODELS_DIR = "models"

# ✅ CORRECT — works everywhere
UI_DIR = os.path.join(BASE_PATH, "ui", "dist")
ENV_FILE = os.path.join(BASE_PATH, "config", ".env")
MODELS_DIR = os.path.join(BASE_PATH, "models")
```

Audit every `open()`, `os.path`, `pathlib.Path`, and `StaticFiles(directory=...)` call in the codebase. Every single one must use `BASE_PATH`.

### 3.2 Move .env to a config subfolder

The `.env` file cannot live at the root when installed to `C:\Program Files\` — that folder is write-protected for non-admin users. Move config to a user-writable location:

```python
import os

def get_config_path() -> str:
    """User-writable config directory."""
    if getattr(sys, 'frozen', False):
        # Installed app: use AppData\Roaming
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        config_dir = os.path.join(appdata, 'DemoVoiceAssistant')
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, '.env')
    else:
        # Development: use project root .env
        return os.path.join(BASE_PATH, '.env')

CONFIG_PATH = get_config_path()
```

This stores the config at `C:\Users\<username>\AppData\Roaming\DemoVoiceAssistant\.env` — always writable, survives app reinstalls.

### 3.3 Bundle the Kokoro model weights

By default, Kokoro downloads its model from HuggingFace on first use. This requires internet access and takes 30–60 seconds on first launch — a terrible experience in a demo installer.

Pre-download the model during your build process and bundle it:

```bat
:: Run this once on your build machine, inside the venv
python -c "from kokoro import KPipeline; KPipeline(lang_code='a')"
:: This downloads the model to HuggingFace cache
```

Find the downloaded model:
```bat
dir %USERPROFILE%\.cache\huggingface\hub\models--hexgrad--Kokoro-82M\snapshots\
```

Copy the model files into your project:
```
demo-voice-assistant/
└── models/
    └── kokoro/
        ├── kokoro-v1_19.pth
        ├── voices/
        └── config.json
```

Then tell Kokoro to use the local path instead of downloading:

```python
# In server/tts/kokoro_tts.py
KOKORO_MODEL_DIR = os.path.join(BASE_PATH, "models", "kokoro")

# Pass local path to KPipeline
pipeline = KPipeline(lang_code='a', model_dir=KOKORO_MODEL_DIR)
```

Check the Kokoro docs for the exact parameter name — it may be `repo_id` with a local path or a separate `cache_dir` argument depending on your version.

### 3.4 Separate the server and client entry points

PyInstaller builds one entry point per `.spec` file. You have two processes (server and client). You have two options:

**Option A (Recommended): Single launcher that starts both**

Create `launcher.py` at the project root:

```python
"""
Main launcher for the packaged demo.
Starts the server as a subprocess, then starts the audio client.
Opens the browser UI automatically.
"""
import subprocess
import sys
import os
import time
import webbrowser
import threading

BASE_PATH = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
            else os.path.dirname(os.path.abspath(__file__))

def start_server():
    server_exe = os.path.join(BASE_PATH, 'DemoVoiceAssistant.exe')
    if getattr(sys, 'frozen', False):
        # When frozen, server is the same exe with a --server flag
        subprocess.Popen([server_exe, '--mode', 'server'])
    else:
        subprocess.Popen([sys.executable, '-m', 'uvicorn', 
                         'server.main:app', '--port', '8765'])

def open_browser():
    time.sleep(3)  # Wait for server startup
    webbrowser.open('http://localhost:8000')

if __name__ == '__main__':
    mode = None
    if '--mode' in sys.argv:
        idx = sys.argv.index('--mode')
        mode = sys.argv[idx + 1]

    if mode == 'server':
        # Run server only
        import uvicorn
        uvicorn.run("server.main:app", host="0.0.0.0", port=8765)
    elif mode == 'client':
        # Run audio client only
        from client.main import run_client
        run_client()
    else:
        # Default: start everything
        start_server()
        threading.Thread(target=open_browser, daemon=True).start()
        # Run audio client in main process
        from client.main import run_client
        run_client()
```

**Option B: Two separate .exe files**

Build `DemoServer.exe` and `DemoClient.exe` separately, then have Inno Setup launch the server first and the client second via `[Run]` entries. More complex but gives finer control.

Option A is simpler and recommended for a demo.

### 3.5 Add a first-launch setup screen

See Section 6 for the full implementation. The short version: check if `GROQ_API_KEY` is set in config on launch; if not, show a simple Tkinter window asking for it before anything else starts.

---

## 4. PyInstaller Configuration

PyInstaller is controlled by a `.spec` file. Do not use command-line flags directly — a `.spec` file is reproducible and can be committed to the repo.

### 4.1 Generate the initial .spec

```bat
venv\Scripts\activate
pyinstaller --name DemoVoiceAssistant ^
            --icon assets\icon.ico ^
            --noconsole ^
            --onedir ^
            launcher.py
```

This creates `DemoVoiceAssistant.spec`. Now edit it — the generated file is just a starting point.

### 4.2 The final .spec file

```python
# DemoVoiceAssistant.spec
# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# ── Collect data files from packages that need them ─────────────────────────

# Kokoro needs its voice data files
kokoro_datas = collect_data_files('kokoro')

# misaki (G2P) needs its dictionary data
misaki_datas = collect_data_files('misaki')

# soundfile needs its libsndfile DLL
soundfile_datas = collect_data_files('soundfile')

# groq SDK may have JSON schema files
groq_datas = collect_data_files('groq')

# Your UI static files
ui_datas = [
    ('ui/dist', 'ui/dist'),
]

# Your pre-bundled Kokoro model weights
model_datas = [
    ('models/kokoro', 'models/kokoro'),
]

all_datas = (
    kokoro_datas
    + misaki_datas
    + soundfile_datas
    + groq_datas
    + ui_datas
    + model_datas
)

# ── Collect DLLs ─────────────────────────────────────────────────────────────

# torch needs its native .dll files on Windows
torch_libs = collect_dynamic_libs('torch')

all_binaries = torch_libs

# ── Hidden imports ────────────────────────────────────────────────────────────
# PyInstaller misses these because they're dynamically imported

hidden_imports = [
    # FastAPI / uvicorn internals
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    
    # WebSockets
    'websockets',
    'websockets.legacy',
    'websockets.legacy.server',
    'websockets.legacy.client',
    
    # Torch internals (commonly missed)
    'torch',
    'torch.jit',
    'torchaudio',
    'torchaudio.backend',
    'torchaudio.backend.utils',
    
    # sounddevice / PortAudio
    'sounddevice',
    '_sounddevice',
    
    # Google Gemini
    'google.generativeai',
    'google.generativeai.types',
    
    # Groq
    'groq',
    
    # kokoro + misaki
    'kokoro',
    'misaki',
    
    # Standard lib items sometimes missed
    'email.mime.multipart',
    'email.mime.text',
    'pkg_resources',
    'pkg_resources.extern',
    'importlib.metadata',
]

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=hidden_imports,
    hookspath=['hooks'],        # custom hooks folder (see Section 4.3)
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Explicitly exclude things we do NOT use to save space
        'matplotlib',
        'numpy.distutils',
        'pytest',
        'IPython',
        'jupyter',
        'notebook',
        'PIL',            # Pillow — not needed
        'cv2',            # OpenCV — not needed
        'sklearn',        # scikit-learn — not needed
        'scipy',          # not needed
        'pandas',         # not needed
        'tkinter.test',
        '_tkinter',       # We only use tkinter for the setup screen;
                          # remove this line if you use tkinter (Section 6)
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DemoVoiceAssistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,               # Compress binaries (reduces size ~20%)
    console=False,          # No console window (set True for debugging)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\icon.ico',
    version='version_info.txt',   # See Section 4.4
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        'vcruntime140.dll',   # Never UPX these — they break
        'msvcp140.dll',
        'python311.dll',
        'torch*.dll',
    ],
    name='DemoVoiceAssistant',
)
```

### 4.3 Custom hooks folder

Create a `hooks/` folder at the project root. Add hooks for packages that need them.

`hooks/hook-kokoro.py`:
```python
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('kokoro')
hiddenimports = collect_submodules('kokoro')
```

`hooks/hook-misaki.py`:
```python
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('misaki')
hiddenimports = collect_submodules('misaki')
```

`hooks/hook-torch.py`:
```python
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

# torch ships native .dll files that must be explicitly collected
binaries = collect_dynamic_libs('torch')
datas = collect_data_files('torch')
```

### 4.4 Version info file

Create `version_info.txt` (PyInstaller version resource format):

```
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'Your Company Name'),
         StringStruct(u'FileDescription', u'Demo Voice Assistant'),
         StringStruct(u'FileVersion', u'1.0.0'),
         StringStruct(u'InternalName', u'DemoVoiceAssistant'),
         StringStruct(u'OriginalFilename', u'DemoVoiceAssistant.exe'),
         StringStruct(u'ProductName', u'Demo Voice Assistant'),
         StringStruct(u'ProductVersion', u'1.0.0')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [0x409, 1200])])
  ]
)
```

---

## 5. Building the Bundle

### 5.1 Run the build

```bat
venv\Scripts\activate
pyinstaller DemoVoiceAssistant.spec --clean
```

`--clean` deletes previous build artifacts. Always use it. Stale artifacts from prior builds cause mysterious errors.

Expected output:
```
dist\
└── DemoVoiceAssistant\
    ├── DemoVoiceAssistant.exe     (~5–10 MB)
    ├── _internal\                  (~1.5–2 GB — torch + all deps)
    ├── ui\dist\
    └── models\kokoro\
```

The `dist\DemoVoiceAssistant\` folder is what Inno Setup will package. Its total size will be roughly 1.5–2.5 GB.

### 5.2 Quick smoke test

Before building the installer, verify the bundle works:

```bat
:: In a fresh command prompt (no venv activated — simulate a client machine)
cd dist\DemoVoiceAssistant
DemoVoiceAssistant.exe
```

Check:
- App launches without DLL errors
- Browser opens at http://localhost:8000
- Setup screen appears (if no .env exists)
- Server responds on port 8765

If it works here, it will work on the client PC.

### 5.3 Build output structure check

```bat
dir dist\DemoVoiceAssistant
dir dist\DemoVoiceAssistant\_internal
```

Verify these exist:
- `_internal\torch\` — torch Python package
- `_internal\kokoro\` — kokoro package
- `_internal\sounddevice\` or `_sounddevice*.pyd`
- `models\kokoro\kokoro-v1_19.pth` — model weights
- `ui\dist\index.html` — React UI entry point

---

## 6. First-Launch Setup Screen

The client needs to enter their Groq API key once. This is implemented as a Tkinter window that appears before the main app starts, checks if a key exists, and if not, asks for it.

```python
# server/setup_screen.py
"""
First-launch configuration screen.
Shown when GROQ_API_KEY is not set.
Uses only tkinter (built into Python standard library — no extra deps).
"""
import tkinter as tk
from tkinter import ttk, messagebox
import os

def get_env_path() -> str:
    import sys
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        config_dir = os.path.join(appdata, 'DemoVoiceAssistant')
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, '.env')
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')

def load_env(env_path: str) -> dict:
    config = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip().strip('"').strip("'")
    return config

def save_env(env_path: str, config: dict):
    with open(env_path, 'w') as f:
        for k, v in config.items():
            f.write(f'{k}={v}\n')

def needs_setup() -> bool:
    env_path = get_env_path()
    config = load_env(env_path)
    return not config.get('GROQ_API_KEY', '').strip()

def run_setup_screen() -> bool:
    """
    Shows the setup window. Returns True if setup completed, False if cancelled.
    """
    root = tk.Tk()
    root.title("Demo Voice Assistant — First-Time Setup")
    root.geometry("480x340")
    root.resizable(False, False)
    root.configure(bg='#1a1a2e')
    
    # Center on screen
    root.update_idletasks()
    x = (root.winfo_screenwidth() - 480) // 2
    y = (root.winfo_screenheight() - 340) // 2
    root.geometry(f'+{x}+{y}')

    completed = [False]

    # Title
    tk.Label(root, text="Voice Assistant Setup", font=('Arial', 16, 'bold'),
             bg='#1a1a2e', fg='white').pack(pady=(24, 4))
    tk.Label(root, text="Enter your API keys to get started.",
             font=('Arial', 10), bg='#1a1a2e', fg='#aaaaaa').pack(pady=(0, 20))

    # Groq API Key (required)
    frame1 = tk.Frame(root, bg='#1a1a2e')
    frame1.pack(fill='x', padx=40, pady=4)
    tk.Label(frame1, text="Groq API Key  (required)", font=('Arial', 10, 'bold'),
             bg='#1a1a2e', fg='white', anchor='w').pack(fill='x')
    tk.Label(frame1, text="Get free key at: console.groq.com",
             font=('Arial', 8), bg='#1a1a2e', fg='#888888', anchor='w').pack(fill='x')
    groq_var = tk.StringVar()
    groq_entry = tk.Entry(frame1, textvariable=groq_var, font=('Courier', 10),
                          bg='#2a2a3e', fg='white', insertbackground='white',
                          relief='flat', bd=6)
    groq_entry.pack(fill='x', pady=(4, 0))

    # Tavily API Key (optional)
    frame2 = tk.Frame(root, bg='#1a1a2e')
    frame2.pack(fill='x', padx=40, pady=(12, 4))
    tk.Label(frame2, text="Tavily API Key  (optional — enables web search)",
             font=('Arial', 10), bg='#1a1a2e', fg='#aaaaaa', anchor='w').pack(fill='x')
    tavily_var = tk.StringVar()
    tavily_entry = tk.Entry(frame2, textvariable=tavily_var, font=('Courier', 10),
                            bg='#2a2a3e', fg='white', insertbackground='white',
                            relief='flat', bd=6)
    tavily_entry.pack(fill='x', pady=(4, 0))

    # Save button
    def on_save():
        groq_key = groq_var.get().strip()
        if not groq_key:
            messagebox.showerror("Missing Key", "Groq API Key is required.")
            return
        if not groq_key.startswith('gsk_'):
            if not messagebox.askyesno("Check Key",
                    "This doesn't look like a Groq key (should start with 'gsk_'). "
                    "Save anyway?"):
                return
        config = {'GROQ_API_KEY': groq_key}
        tavily_key = tavily_var.get().strip()
        if tavily_key:
            config['TAVILY_API_KEY'] = tavily_key
        config['LOG_LEVEL'] = 'INFO'
        config['VAD_SILENCE_MS'] = '600'
        save_env(get_env_path(), config)
        completed[0] = True
        root.destroy()

    btn = tk.Button(root, text="Save and Launch", command=on_save,
                    font=('Arial', 11, 'bold'), bg='#4f46e5', fg='white',
                    relief='flat', padx=20, pady=8, cursor='hand2')
    btn.pack(pady=20)

    root.mainloop()
    return completed[0]
```

Call this from `launcher.py` before starting anything else:

```python
# In launcher.py, at the top of the main block
from server.setup_screen import needs_setup, run_setup_screen

if needs_setup():
    success = run_setup_screen()
    if not success:
        sys.exit(0)  # User closed setup window
```

---

## 7. Inno Setup Installer

### 7.1 The .iss script

Create `installer/DemoVoiceAssistant.iss`:

```iss
; Demo Voice Assistant — Inno Setup Script
; Requires Inno Setup 6.x

#define MyAppName "Demo Voice Assistant"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Your Company Name"
#define MyAppExeName "DemoVoiceAssistant.exe"
#define MyAppDir "..\dist\DemoVoiceAssistant"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://yourcompany.com
AppSupportURL=https://yourcompany.com/support
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\installer\output
OutputBaseFilename=DemoVoiceAssistantSetup
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0.17763
UninstallDisplayIcon={app}\{#MyAppExeName}
DisableWelcomePage=no
LicenseFile=..\LICENSE.txt

; Estimated install size (bytes) — set roughly correct for progress bar
ExtraDiskSpaceRequired=2500000000

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; Main application bundle — recursive, preserves folder structure
Source: "{#MyAppDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

; Desktop shortcut (optional, selected in Tasks above)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
; Launch after install (optional)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up the AppData config on uninstall
Type: filesandordirs; Name: "{userappdata}\DemoVoiceAssistant"

[Code]
// Check for existing instance before launching installer
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

// Show a reminder about the API key if first install
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Nothing needed — app handles first-launch setup itself
  end;
end;
```

### 7.2 Build the installer

Open Inno Setup Compiler, open `DemoVoiceAssistant.iss`, then press F9 (Build). Or from the command line:

```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\DemoVoiceAssistant.iss
```

Output: `installer\output\DemoVoiceAssistantSetup.exe`

This is the file you ship to clients. Total size: typically 600 MB – 1.2 GB (depends on torch).

### 7.3 Optional: reduce installer size

The main contributor to installer size is torch. The CPU-only torch build is already much smaller than the CUDA build, but you can trim further:

```bat
:: After building the PyInstaller bundle, before running Inno Setup,
:: delete torch test files and examples that are bundled unnecessarily
rd /s /q "dist\DemoVoiceAssistant\_internal\torch\test"
rd /s /q "dist\DemoVoiceAssistant\_internal\torch\utils\benchmark"

:: Also remove CUDA DLLs that CPU torch incorrectly includes
del "dist\DemoVoiceAssistant\_internal\torch\lib\torch_cuda*.dll" 2>NUL
```

This can save 200–400 MB.

---

## 8. Testing the Package

Test in this exact order. Do not skip steps.

### 8.1 Test on your own machine (clean environment)

Open a fresh command prompt where the venv is NOT activated. Navigate to `dist\DemoVoiceAssistant\` and launch:
```bat
DemoVoiceAssistant.exe
```
If it works here, it's correctly self-contained.

### 8.2 Test the installer on your own machine

Run `DemoVoiceAssistantSetup.exe` as if you were a client. Install to `C:\Program Files\`. Launch from the desktop shortcut. Confirm the setup screen appears and saves the key correctly.

### 8.3 Test on a machine WITHOUT Python installed

This is the real test. The bundle must work with zero prerequisites. Use a second machine, a fresh VM, or Windows Sandbox:

```
Windows Sandbox (built into Windows 10/11 Pro):
1. Open "Windows Sandbox" from Start Menu
2. Copy DemoVoiceAssistantSetup.exe into the Sandbox
3. Run the installer
4. Launch the app
5. Verify end-to-end voice works
```

Windows Sandbox is perfect for this — it's a fresh, disposable Windows environment with nothing installed.

### 8.4 Test on a machine with a different username

The AppData path is user-specific. Confirm `.env` is created at:
`C:\Users\<DIFFERENT_USERNAME>\AppData\Roaming\DemoVoiceAssistant\.env`

### 8.5 Test uninstall

Run the uninstaller from Add/Remove Programs. Confirm:
- App folder removed from `C:\Program Files\`
- AppData config removed (per the `[UninstallDelete]` section)
- Desktop shortcut removed
- Start Menu entry removed

---

## 9. Common Issues and Fixes

This section is the most important part of the guide. These are the issues you will actually encounter.

---

### Issue 1: `ModuleNotFoundError` on launch

**Symptom:** App launches then immediately crashes with a Python traceback mentioning a missing module.

**Why it happens:** PyInstaller does static analysis to find imports. Dynamically imported modules (imported via `importlib.import_module()`, inside `try/except ImportError`, or inside a conditional) are missed.

**Fix:**
1. Add the missing module to `hiddenimports` in the `.spec` file
2. Rebuild: `pyinstaller DemoVoiceAssistant.spec --clean`

```python
# In .spec hiddenimports list, add e.g.:
'uvicorn.protocols.websockets.websockets_impl',
'groq._models',
'google.generativeai.types.generation_types',
```

**How to find what's missing:**
```bat
:: Build with console=True temporarily to see the traceback
:: In .spec, change: console=False  →  console=True
:: Rebuild, run, read the error
```

---

### Issue 2: Missing DLL errors (`torch_cpu.dll not found`, `libsndfile64bit.dll`)

**Symptom:** Windows dialog: "The code execution cannot proceed because FILENAME.dll was not found."

**Why it happens:** Native DLLs are not automatically detected by PyInstaller's analysis.

**Fix for torch DLLs:**
```python
# In .spec, ensure collect_dynamic_libs is collecting properly:
from PyInstaller.utils.hooks import collect_dynamic_libs
torch_libs = collect_dynamic_libs('torch')

# Then in Analysis():
binaries=torch_libs,
```

**Fix for soundfile (libsndfile):**
```python
# soundfile ships libsndfile as a data file, not a binary
# Ensure collect_data_files('soundfile') is in your datas list
soundfile_datas = collect_data_files('soundfile')
```

**Fix for PortAudio (sounddevice):**
```python
# sounddevice ships PortAudio DLLs bundled on Windows
# They should be collected automatically via collect_dynamic_libs
sounddevice_libs = collect_dynamic_libs('sounddevice')
all_binaries = torch_libs + sounddevice_libs
```

---

### Issue 3: App silently does nothing (no window, no error)

**Symptom:** The .exe appears in Task Manager briefly then disappears. No window, no error.

**Why it happens:** An exception is raised before the UI appears, but with `console=False`, there's no console to show it.

**Fix:**
1. Temporarily set `console=True` in the `.spec` and rebuild
2. Run the `.exe` from a command prompt to see the traceback
3. Fix the issue
4. Set `console=False` again and rebuild final

Alternatively, add exception logging to `launcher.py`:
```python
import traceback
import logging

logging.basicConfig(
    filename=os.path.join(os.environ.get('APPDATA', '.'), 
                         'DemoVoiceAssistant', 'crash.log'),
    level=logging.ERROR
)

try:
    main()
except Exception as e:
    logging.error("Fatal crash:\n" + traceback.format_exc())
    raise
```

---

### Issue 4: Kokoro model not found at runtime

**Symptom:** TTS fails silently or with `FileNotFoundError: kokoro-v1_19.pth`.

**Why it happens:** Kokoro's default path resolution looks for the model relative to the package location, which changes inside a PyInstaller bundle.

**Fix:** Force the model path explicitly:
```python
# server/tts/kokoro_tts.py
import sys, os

def get_model_dir():
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base, 'models', 'kokoro')

MODEL_DIR = get_model_dir()
# Pass MODEL_DIR to KPipeline constructor
```

Also verify the model files are present in the bundle:
```bat
dir dist\DemoVoiceAssistant\models\kokoro
```

---

### Issue 5: WebSocket server fails to start (port already in use)

**Symptom:** Server crashes on startup with `[Errno 10048] Only one usage of each socket address is permitted`.

**Why it happens:** Either a previous instance is still running, or another application is using port 8765/8000.

**Fix in launcher.py:**
```python
import socket

def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) != 0

def kill_old_instance():
    """Kill any existing instance before launching."""
    import subprocess
    # Windows: find and kill process using our ports
    for port in [8000, 8765]:
        if not is_port_free(port):
            subprocess.run(
                f'for /f "tokens=5" %a in '
                f'(\'netstat -aon ^| find ":{port}"\') '
                f'do taskkill /F /PID %a',
                shell=True, capture_output=True
            )

# Call before starting server:
kill_old_instance()
```

Also: add a tray icon with a "Quit" option so users can properly close the app rather than just closing the browser.

---

### Issue 6: Windows Defender flags the .exe as malware

**Symptom:** SmartScreen shows "Windows protected your PC" or Defender quarantines the file.

**Why it happens:** PyInstaller bundles are structurally similar to malware packers. Unsigned executables from unknown publishers always trigger this.

**Fixes (in order of effectiveness):**

1. **Code signing** (permanent fix): See Section 10. A valid code signing certificate eliminates this completely.

2. **Submit to Microsoft for analysis** (free, takes 1–3 days):
   - Go to: https://www.microsoft.com/en-us/wdsi/filesubmission
   - Submit your `.exe` and report it as a false positive
   - Microsoft whitelists it in their cloud database

3. **UPX exclusions** (partial fix): Disable UPX on sensitive DLLs:
   ```python
   # In .spec COLLECT():
   upx_exclude=['python311.dll', 'vcruntime140.dll', '_sounddevice*.pyd']
   ```

4. **Rebuild without UPX** (partial fix):
   ```python
   # In .spec, set upx=False everywhere
   ```

---

### Issue 7: App works on your machine but fails on client PC

**Symptom:** Works perfectly on your build machine, fails on a fresh client PC.

**Why it happens:** Your build machine has extra DLLs installed (Visual C++ Redistributables, NVIDIA drivers, etc.) that the client PC doesn't have.

**Fix: Include Visual C++ Redistributables in the installer.**

Add to your Inno Setup `.iss`:
```iss
[Files]
; Include VC++ Redist installer
Source: "vcredist_x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Run]
; Install VC++ Redist silently before app starts
Filename: "{tmp}\vcredist_x64.exe"; Parameters: "/quiet /norestart"; \
    StatusMsg: "Installing Visual C++ Runtime..."; \
    Flags: waituntilterminated
```

Download `vcredist_x64.exe` (Visual C++ 2015-2022 Redistributable) from Microsoft and place it next to your `.iss` file.

---

### Issue 8: Microphone not accessible from installed location

**Symptom:** sounddevice raises `PaErrorCode -9999` or no audio devices found.

**Why it happens:** Microphone access on Windows requires app-level permission. Apps installed to `C:\Program Files\` sometimes need explicit microphone permission granted.

**Fix:**
1. In Windows Settings → Privacy → Microphone → ensure "Allow desktop apps to access microphone" is ON
2. If the issue persists, add a manifest file to the `.exe` requesting microphone capability:

```xml
<!-- DemoVoiceAssistant.exe.manifest -->
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
</assembly>
```

Reference it in the `.spec`:
```python
exe = EXE(
    ...
    manifest='DemoVoiceAssistant.exe.manifest',
    ...
)
```

---

### Issue 9: Slow first launch (30+ seconds)

**Symptom:** App takes a very long time to show the first window.

**Why it happens:** PyInstaller's `--onefile` mode (if you used it) extracts to temp on every launch. OR the Kokoro model is being downloaded at first launch instead of using the bundled version.

**Fix for --onefile:** Switch to `--onedir` (already recommended in this guide).

**Fix for Kokoro download:** Ensure the model is bundled correctly (Section 3.3) and the path override is working (Issue 4 fix).

**Fix for general slow startup:** PyInstaller bundles can be slow to start the first time due to DLL loading. This is normal for torch-based apps. Expected range: 5–15 seconds. Add a splash screen:

```python
# In launcher.py, before imports
import pyi_splash  # Available when splash screen is configured in .spec

# At startup:
pyi_splash.update_text('Loading models...')
# ... do startup work ...
pyi_splash.close()
```

Configure in `.spec`:
```python
exe = EXE(
    ...
    splash='assets/splash.png',  # 400x300 PNG
    ...
)
```

---

### Issue 10: Google Gemini or Groq SSL errors

**Symptom:** API calls fail with `SSL: CERTIFICATE_VERIFY_FAILED`.

**Why it happens:** PyInstaller bundles Python's SSL certificates but they may not match the system's trust store. Or `certifi` is not being picked up correctly.

**Fix:**
```python
# In launcher.py, very top, before any imports:
import certifi
import os
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
```

Also add `certifi` to hidden imports in `.spec`:
```python
hiddenimports = [
    ...
    'certifi',
    ...
]
```

---

## 10. Optional: Code Signing

Code signing is optional but strongly recommended if you're shipping to clients who aren't developers. It eliminates SmartScreen warnings and makes the app look professional.

### 10.1 Certificate options

| Option | Cost | Setup Time | Notes |
|---|---|---|---|
| **DigiCert Standard** | ~$300/year | 1–3 days (verification) | Most trusted. EV cert ($500+) gets instant SmartScreen reputation. |
| **Sectigo (Comodo)** | ~$100–200/year | 1–3 days | Good alternative to DigiCert. |
| **SSL.com** | ~$100/year | 1–3 days | Competitive pricing. |
| **Self-signed** | Free | Instant | Only useful for internal use. Clients will still see SmartScreen. |

For demos, a standard OV (Organization Validated) cert is sufficient.

### 10.2 Sign the .exe

After buying and downloading your certificate (usually a `.pfx` file):

```bat
:: Using Windows SDK signtool (comes with Visual Studio)
signtool sign ^
  /f "your-certificate.pfx" ^
  /p "your-pfx-password" ^
  /t "http://timestamp.digicert.com" ^
  /d "Demo Voice Assistant" ^
  dist\DemoVoiceAssistant\DemoVoiceAssistant.exe
```

Sign BOTH the `.exe` and the installer:
```bat
signtool sign /f cert.pfx /p password /t http://timestamp.digicert.com ^
  dist\DemoVoiceAssistant\DemoVoiceAssistant.exe

signtool sign /f cert.pfx /p password /t http://timestamp.digicert.com ^
  installer\output\DemoVoiceAssistantSetup.exe
```

The `/t` (timestamp) flag is important — it means the signature remains valid even after the certificate expires.

### 10.3 Inno Setup signs during build

Add to your `.iss` to sign automatically during the Inno Setup build:
```iss
[Setup]
SignTool=signtool sign /f "C:\path\to\cert.pfx" /p "password" /t "http://timestamp.digicert.com" /d "Demo Voice Assistant" $f
SignedUninstaller=yes
```

---

## 11. Distribution Checklist

Before sending the installer to a client, verify everything on this list.

### Build verification
- [ ] `pyinstaller DemoVoiceAssistant.spec --clean` completes without warnings
- [ ] `dist\DemoVoiceAssistant\DemoVoiceAssistant.exe` exists and launches
- [ ] Kokoro model files present in `dist\DemoVoiceAssistant\models\kokoro\`
- [ ] React UI present in `dist\DemoVoiceAssistant\ui\dist\index.html`
- [ ] All hidden imports added — no `ModuleNotFoundError` on launch

### Testing verification
- [ ] Tested on own machine with venv deactivated
- [ ] Tested via Inno Setup installer (not just the raw folder)
- [ ] Tested on Windows Sandbox or fresh VM (no Python installed)
- [ ] Setup screen appears and saves API key correctly
- [ ] Full voice round-trip works (speak → transcribe → respond → audio)
- [ ] Barge-in interrupt works
- [ ] Text input fallback works
- [ ] Browser opens automatically on launch
- [ ] App shuts down cleanly
- [ ] Uninstaller works and cleans up correctly

### Packaging
- [ ] Installer size is reasonable (< 1.5 GB ideally)
- [ ] `.exe` is signed (if code signing certificate available)
- [ ] Installer is signed
- [ ] Version number is set correctly in `.spec` and `.iss`
- [ ] Company name / publisher name is set correctly

### Documentation for the client
- [ ] README included: "double-click the installer, follow the prompts, get API key from console.groq.com"
- [ ] Support contact included
- [ ] API key signup instructions included (30-second process at console.groq.com)

---

## 12. Quick Reference Cheatsheet

```
FULL BUILD SEQUENCE (run in order):
════════════════════════════════════════════════════════

1. PREPARE
   cd demo-voice-assistant
   venv\Scripts\activate

2. BUILD UI (if changed)
   cd ui && npm run build && cd ..

3. PRE-DOWNLOAD KOKORO MODEL (first time only)
   python -c "from kokoro import KPipeline; KPipeline('a')"
   :: Then copy from HuggingFace cache to models/kokoro/

4. BUILD PYINSTALLER BUNDLE
   pyinstaller DemoVoiceAssistant.spec --clean
   :: Output: dist\DemoVoiceAssistant\

5. SMOKE TEST
   cd dist\DemoVoiceAssistant
   DemoVoiceAssistant.exe    ← run without venv active
   cd ..\..

6. TRIM BUNDLE SIZE (optional)
   rd /s /q dist\DemoVoiceAssistant\_internal\torch\test

7. SIGN THE EXE (if certificate available)
   signtool sign /f cert.pfx /p password /t http://timestamp.digicert.com ...

8. BUILD INSTALLER
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\DemoVoiceAssistant.iss
   :: Output: installer\output\DemoVoiceAssistantSetup.exe

9. SIGN THE INSTALLER (if certificate available)
   signtool sign /f cert.pfx /p password ...

10. TEST INSTALLER
    Run DemoVoiceAssistantSetup.exe in Windows Sandbox


COMMON COMMANDS:
════════════════
Rebuild from scratch:
  pyinstaller DemoVoiceAssistant.spec --clean

Debug mode (shows console):
  Change console=False → console=True in .spec, rebuild

Find what module is missing:
  Run with console=True, read the traceback

Check bundle contents:
  dir dist\DemoVoiceAssistant\_internal | findstr torch
  dir dist\DemoVoiceAssistant\models

Check installer size:
  dir installer\output\DemoVoiceAssistantSetup.exe


EXPECTED FILE SIZES:
════════════════════
PyInstaller bundle folder:   1.5 – 2.5 GB
Inno Setup installer (.exe): 600 MB – 1.2 GB  (LZMA2 compression)
Installed size on client PC: 1.8 – 3 GB


COMMON PYINSTALLER ISSUES SUMMARY:
═══════════════════════════════════
ModuleNotFoundError    → add to hiddenimports in .spec
DLL not found          → add to binaries via collect_dynamic_libs
Slow launch            → use --onedir (never --onefile)
Silent crash           → set console=True to see traceback
Defender flags it      → code sign or submit to Microsoft
SSL errors             → set SSL_CERT_FILE=certifi.where()
Port conflict          → kill old instance in launcher.py
Model not found        → use sys.frozen path detection
Write permission error → use AppData for config, not install dir
```

---

*End of Packaging Guide*  
*Demo Voice Assistant v1.0*
