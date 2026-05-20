CONTEXT: PyInstaller packaging fixes for DemoVoiceAssistant

Project:
Python FastAPI/uvicorn voice assistant (kokoro TTS, whisper STT, groq/gemini LLM) packaged with PyInstaller --onedir on Windows, distributed via Inno Setup installer. Python 3.11, torch==2.5.1+cpu.
Entry point: launcher.py → uvicorn server in daemon thread → kokoro TTS pipeline

FIXED — do not revert these:
1. DemoVoiceAssistant.spec is the canonical build spec. Key decisions made:

upx=False everywhere — AV detection risk
console=True — debug build, flip to False for release
collect_all('server') — forces server package as real .py files on disk, not buried in PYZ (uvicorn string-based import requires this)
collect_all('kokoro') — kokoro is pure-Python (0 data files). collect_data_files('kokoro') returned 0 and warned. Switched to collect_all so the 7 submodules (pipeline, model, istftnet, modules, custom_stft, __main__) are captured as hidden imports and land as .py files on disk.
certifi_datas = [(certifi.where(), 'certifi')] — required for HTTPS to groq/gemini
runtime_hooks=['hooks/rthook_paths.py'] — inserts sys._MEIPASS into sys.path at startup
PIL excluded as PIL.ImageTk / PIL.ImageQt only, not the whole package
upx_exclude uses exact filenames only — globs are silently ignored by PyInstaller
pkg_resources and pkg_resources.extern removed from hidden imports — cause ERROR log on Python 3.11+
hooks/hook-torch.py (build-time hook) — collects torch DLLs only (binaries=), NOT datas. Spec handles torch explicitly via torch_binaries. Do not add datas= back to this hook — that causes double-copy.
fugashi binaries: collect_dynamic_libs('fugashi') returns 0 but DLLs land in dist correctly via PyInstaller's own binary walker (via fugashi.libs/). fugashi_binaries = [] in spec to silence the spurious [WARNING] in the build log.
hidden_imports includes full torch submodule list matching _run_server() pre-init: torch.version, torch._C, torch.storage, torch.serialization, torch.cuda. These must stay in sync with the pre-init block.

2. hooks/rthook_paths.py — inserts _MEIPASS into sys.path and os.environ:
pythonimport sys, os
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    meipass = sys._MEIPASS
    if meipass not in sys.path:
        sys.path.insert(0, meipass)
    os.environ['MEIPASS'] = meipass

3. server/config.py — must have frozen-aware BASE_PATH and write-safe paths:
pythonif getattr(sys, 'frozen', False):
    BASE_PATH  = sys._MEIPASS
    _WRITE_BASE = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'DemoVoiceAssistant')
else:
    BASE_PATH   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _WRITE_BASE = BASE_PATH

LOG_DIR   = os.path.join(_WRITE_BASE, 'logs')
DATA_DIR  = _WRITE_BASE
CACHE_DIR = os.path.join(_WRITE_BASE, 'cache')
TEMP_DIR  = os.path.join(os.environ.get('TEMP', _WRITE_BASE), 'DemoVoiceAssistant')
PROMPTS_DIR = os.path.join(BASE_PATH, 'server', 'prompts')
CONFIG_DIR  = os.path.join(BASE_PATH, 'config')
UI_DIST_DIR = os.path.join(BASE_PATH, 'ui', 'dist')

4. All file writes across server/ redirected to LOG_DIR/DATA_DIR/CACHE_DIR — never to BASE_PATH or _MEIPASS (those point to Program Files which is read-only for normal users).

5. launcher.py — from server.main import app moved off module level into _run_server(). No application imports at module level.

6. launcher.py — multiprocessing.freeze_support() added as the very first call in __main__ (before main()). Required for PyInstaller --onedir on Windows to prevent fork-bombing when torch/kokoro spawn worker processes.

7. launcher.py — torch pre-init runs inside _run_server() (the server daemon thread), not at module level. This is the CORRECT location. The old _preinit_torch() at module level has been fully removed.

8. hooks/rthook_torch.py — DELETED. Do not recreate. It crashed with OSError from torch._inductor.config calling inspect.getsource() which requires .py source files not present in frozen builds.


PARTIALLY FIXED — still needs runtime confirmation:
Torch circular import fix — implemented correctly in launcher.py, not yet confirmed in a frozen build run:
TTS | synthesis_failed | error="partially initialized module 'torch'
has no attribute 'version' (most likely due to a circular import)"
Root cause: kokoro's first torch.load() call re-enters torch's C-level init path. torch.version (the submodule torch/version.py) is not yet in sys.modules at that point, causing a circular import. torch.__version__ (the string attribute) and torch.version (the submodule) are different things.
Fix implemented in launcher.py _run_server():
pythondef _run_server() -> None:
    # Pre-init torch IN THIS THREAD before server.main import
    import torch
    import torch.version        # submodule, not torch.__version__ string
    import torch.nn
    import torch.nn.functional
    import torch.jit
    import torch._C             # C extension — root of circular import
    import torch.storage        # required by torch.load()
    import torch.serialization  # required by torch.load()
    import torch.cuda           # accessed by torch.version internals on CPU builds
    _ = torch.__version__
    _ = torch.version.__version__
    _ = torch.version.cuda
    _ = torch.version.git_version

    import uvicorn
    from server.main import app
    ...


KNOWN REMAINING RISKS (not yet encountered but likely):

en_core_web_sm spaCy model — verified inside venv at:
  E:\icn\robo\demo\robo-demo\venv\Lib\site-packages\en_core_web_sm\__init__.py
anyio._backends._trio hidden import may cause warnings if trio is not installed — safe to remove if so
After the torch circular import is confirmed fixed, the next likely failure point is kokoro's model download on first synthesis — it hits HuggingFace at runtime; needs network access and correct cache path under %USERPROFILE%\.cache\huggingface


Build command:
batvenv\Scripts\activate
build.bat
build.bat in project root handles: kill running exe → PyInstaller clean build → verify output → download vc_redist if missing → Inno Setup installer build.
File layout after build:
dist\DemoVoiceAssistant\
├── DemoVoiceAssistant.exe
└── _internal\           ← sys._MEIPASS points here
    ├── server\          ← real .py files (from collect_all) + server\prompts\
    ├── kokoro\          ← real .py files (from collect_all('kokoro'))
    ├── ui\dist\
    ├── config\
    └── certifi\
%APPDATA%\DemoVoiceAssistant\   ← all runtime writes go here
    ├── .env             ← API keys
    ├── logs\
    └── cache\