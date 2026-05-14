# DemoVoiceAssistant.spec
# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller build spec for the Demo Voice Assistant.
# Entry point: launcher.py
#
# Build command (run from project root with venv active):
#   pyinstaller DemoVoiceAssistant.spec --clean --noconfirm --log-level DEBUG > builder.log 2>&1
#
# Output: dist\DemoVoiceAssistant\DemoVoiceAssistant.exe
#
# Notes:
#   - CPU-only torch build (torch==2.5.1+cpu) — no CUDA DLLs
#   - Kokoro model weights are downloaded at runtime (HuggingFace cache)
#     because the installed kokoro==0.9.4 does not support local model paths
#   - All app paths resolve via server.config.BASE_PATH (sys.frozen-aware)
#   - UPX disabled: most large DLLs are native and UPX adds AV-detection risk
#     with negligible size gain on this stack

import os
import certifi
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_all

block_cipher = None

# ── Helper: warn on empty collections ────────────────────────────────────────
def guarded_collect(name, collector=collect_data_files):
    result = collector(name)
    if not result:
        print(f"[WARNING] No files collected for: {name} — check the package is installed in your venv")
    return result


# ── Data files ────────────────────────────────────────────────────────────────

# sounddevice ships PortAudio binaries inside _sounddevice_data/
sounddevice_datas   = guarded_collect('_sounddevice_data')

# soundfile ships libsndfile64bit.dll inside _soundfile_data/
soundfile_datas     = guarded_collect('_soundfile_data')

# espeakng_loader ships espeak-ng-data/ dictionaries and espeak-ng.dll
espeakng_datas      = guarded_collect('espeakng_loader')

# misaki G2P data files (language dictionaries, etc.)
misaki_datas        = guarded_collect('misaki')

# kokoro package data (voice configs, etc.)
kokoro_datas        = guarded_collect('kokoro')

# pyopenjtalk ships htsvoice/ data for Japanese TTS
pyopenjtalk_datas   = guarded_collect('pyopenjtalk')

# unidic_lite — Japanese morphological dictionary used by fugashi/misaki
unidic_datas        = guarded_collect('unidic_lite')

# spaCy English model — used by misaki English G2P
# IMPORTANT: verify path before building:
#   python -c "import en_core_web_sm; print(en_core_web_sm.__file__)"
# If printed path is NOT inside your venv's site-packages, replace with:
#   spacy_model_datas = [('C:/full/path/to/en_core_web_sm', 'en_core_web_sm')]
spacy_model_datas   = guarded_collect('en_core_web_sm')

# groq SDK may include JSON schema files
groq_datas          = guarded_collect('groq')

# certifi CA bundle — required for HTTPS calls from groq / google-generativeai
# Without this, SSL verification fails at runtime in the frozen build
certifi_datas       = [(certifi.where(), 'certifi')]

# Pre-built React UI static files
ui_datas = [
    ('ui/dist', 'ui/dist'),
]

# Deployment config and prompt templates
config_datas = [
    ('config/deployment.yaml', 'config'),
    ('server/prompts',         'server/prompts'),
]

# Force server package to land as real .py files on disk (not buried in PYZ).
# Required because uvicorn needs to import 'server.main' by string at runtime,
# and string-based importlib lookups cannot reach inside the PYZ archive.
server_source   = collect_all('server')
server_datas    = server_source[0]
server_binaries = server_source[1]
server_hiddens  = server_source[2]

all_datas = (
    sounddevice_datas
    + soundfile_datas
    + espeakng_datas
    + misaki_datas
    + kokoro_datas
    + pyopenjtalk_datas
    + unidic_datas
    + spacy_model_datas
    + groq_datas
    + certifi_datas
    + ui_datas
    + config_datas
    + server_datas
)


# ── Binaries (native DLLs) ────────────────────────────────────────────────────

# torch CPU ships torch_cpu.dll and other native libs
torch_binaries   = guarded_collect('torch',   collector=collect_dynamic_libs)

# fugashi ships libmecab DLL inside fugashi.libs/
fugashi_binaries = guarded_collect('fugashi', collector=collect_dynamic_libs)

all_binaries = torch_binaries + fugashi_binaries + server_binaries


# ── Hidden imports ────────────────────────────────────────────────────────────
# Modules PyInstaller misses because they are imported dynamically
# (via importlib, inside try/except, or inside conditional branches).

hidden_imports = server_hiddens + [
    # ── uvicorn internals ──────────────────────────────────────────────────
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

    # ── async runtime (uvicorn + httpx depend on these) ────────────────────
    'anyio',
    'anyio._backends._asyncio',
    'anyio._backends._trio',
    'sniffio',

    # ── HTTP clients (groq + google-generativeai use httpx internally) ─────
    'httpx',
    'httpx._transports',
    'httpx._transports.default',
    'httpx._transports.asgi',
    'httpcore',
    'httpcore._async',
    'httpcore._async.connection',
    'httpcore._async.connection_pool',
    'httpcore._async.http11',
    'httpcore._sync',
    'httpcore._sync.connection',
    'httpcore._sync.connection_pool',
    'httpcore._sync.http11',

    # ── WebSockets ─────────────────────────────────────────────────────────
    'websockets',
    'websockets.legacy',
    'websockets.legacy.server',
    'websockets.legacy.client',

    # ── torch ──────────────────────────────────────────────────────────────
    'torch',
    'torch.jit',
    'torch.version',           # ← add
    'torch.nn',                # ← add
    'torch.nn.functional',     # ← add
    'torch.nn.modules',        # ← add
    'torch.nn.modules.rnn',    # ← add (kokoro uses RNN layers)
    'torch.distributions',     # ← add
    'torch.backends',          # ← add
    'torch.backends.cpu',      # ← add

    # ── Audio I/O ──────────────────────────────────────────────────────────
    'sounddevice',
    '_sounddevice',
    '_sounddevice_data',
    'soundfile',
    '_soundfile',
    '_soundfile_data',

    # ── TTS stack ──────────────────────────────────────────────────────────
    'kokoro',
    'kokoro.pipeline',
    'kokoro.model',
    'misaki',
    'misaki.en',
    'misaki.ja',
    'espeakng_loader',
    'pyopenjtalk',
    'fugashi',
    'unidic_lite',
    'jaconv',
    'mojimoji',
    'num2words',

    # ── spaCy (misaki English G2P dependency) ──────────────────────────────
    'spacy',
    'en_core_web_sm',

    # ── Google Gemini ──────────────────────────────────────────────────────
    'google.generativeai',
    'google.generativeai.types',
    'google.generativeai.types.generation_types',

    # ── Groq ───────────────────────────────────────────────────────────────
    'groq',
    'groq._models',

    # ── Tavily ─────────────────────────────────────────────────────────────
    'tavily',

    # ── SSL / certificates ─────────────────────────────────────────────────
    'certifi',
    'ssl',

    # ── Standard library items sometimes missed ────────────────────────────
    'email.mime.multipart',
    'email.mime.text',
    'importlib.metadata',
    'yaml',
]


# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=hidden_imports,
    hookspath=['hooks'],
    hooksconfig={},
    # rthook_paths.py runs before any app code — inserts _MEIPASS into sys.path
    # so pkg_resources and string-based importlib lookups find bundled packages
    runtime_hooks=['hooks/rthook_paths.py','hooks/rthook_torch.py',],
    excludes=[
        'matplotlib',
        'numpy.distutils',
        'pytest',
        'pytest_asyncio',
        'IPython',
        'jupyter',
        'notebook',
        # Excluding only the GUI/Qt submodules of PIL, not the whole package.
        # Remove these two lines if you get an ImportError for PIL at runtime.
        'PIL.ImageTk',
        'PIL.ImageQt',
        'cv2',
        'sklearn',
        'scipy',
        'pandas',
        'tkinter.test',
        'torchaudio',
        'torio',
        'torchvision',
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
    upx=False,          # UPX disabled — AV risk outweighs size gain on this stack
    # ── CONSOLE MODE ──────────────────────────────────────────────────────
    # console=True  → keep during development so crashes are visible
    # console=False → flip only for the final user-facing release build
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\icon.ico',
    # version_info.txt must exist in the project root.
    # See earlier instructions for the file contents, or remove this line.
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,          # UPX disabled
    upx_exclude=[
        # Kept for reference if UPX is re-enabled later.
        # IMPORTANT: exact filenames only — globs are silently ignored.
        'vcruntime140.dll',
        'vcruntime140_1.dll',
        'msvcp140.dll',
        'python311.dll',
        'torch_cpu.dll',
        'torch_python.dll',
        # Exact names — verify against your dist/ folder after first build
        '_sounddevice.pyd',
        'libsndfile_64bit.dll',
        'espeak-ng.dll',
        'libmecab.dll',
    ],
    name='DemoVoiceAssistant',
)