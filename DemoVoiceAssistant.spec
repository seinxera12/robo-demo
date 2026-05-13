# DemoVoiceAssistant.spec
# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller build spec for the Demo Voice Assistant.
# Entry point: launcher.py
#
# Build command (run from project root with venv active):
#   pyinstaller DemoVoiceAssistant.spec --clean
#
# Output: dist\DemoVoiceAssistant\DemoVoiceAssistant.exe
#
# Notes:
#   - CPU-only torch build (torch==2.5.1+cpu) — no CUDA DLLs
#   - Kokoro model weights are downloaded at runtime (HuggingFace cache)
#     because the installed kokoro==0.9.4 does not support local model paths
#   - All app paths resolve via server.config.BASE_PATH (sys.frozen-aware)

import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# ── Data files ────────────────────────────────────────────────────────────────

# sounddevice ships PortAudio binaries inside _sounddevice_data/
sounddevice_datas = collect_data_files('_sounddevice_data')

# soundfile ships libsndfile64bit.dll inside _soundfile_data/
soundfile_datas = collect_data_files('_soundfile_data')

# espeakng_loader ships espeak-ng-data/ dictionaries and espeak-ng.dll
espeakng_datas = collect_data_files('espeakng_loader')

# misaki G2P data files (language dictionaries, etc.)
misaki_datas = collect_data_files('misaki')

# kokoro package data (voice configs, etc.)
kokoro_datas = collect_data_files('kokoro')

# pyopenjtalk ships htsvoice/ data for Japanese TTS
pyopenjtalk_datas = collect_data_files('pyopenjtalk')

# unidic_lite — Japanese morphological dictionary used by fugashi/misaki
unidic_datas = collect_data_files('unidic_lite')

# spaCy English model (en_core_web_sm) — used by misaki English G2P
spacy_model_datas = collect_data_files('en_core_web_sm')

# groq SDK may include JSON schema files
groq_datas = collect_data_files('groq')

# Pre-built React UI static files
ui_datas = [
    ('ui/dist', 'ui/dist'),
]

# Deployment config and prompt templates
config_datas = [
    ('config/deployment.yaml', 'config'),
    ('server/prompts', 'server/prompts'),
]

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
    + ui_datas
    + config_datas
)

# ── Binaries (native DLLs) ────────────────────────────────────────────────────

# torch CPU ships torch_cpu.dll and other native libs
torch_binaries = collect_dynamic_libs('torch')

# fugashi ships libmecab DLL inside fugashi.libs/
fugashi_binaries = collect_dynamic_libs('fugashi')

all_binaries = torch_binaries + fugashi_binaries

# ── Hidden imports ────────────────────────────────────────────────────────────
# Modules that PyInstaller misses because they are imported dynamically
# (via importlib, inside try/except, or inside conditional branches).

hidden_imports = [
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

    # ── WebSockets ─────────────────────────────────────────────────────────
    'websockets',
    'websockets.legacy',
    'websockets.legacy.server',
    'websockets.legacy.client',

    # ── torch / torchaudio ─────────────────────────────────────────────────
    'torch',
    'torch.jit',

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

    # ── Standard library items sometimes missed ────────────────────────────
    'email.mime.multipart',
    'email.mime.text',
    'pkg_resources',
    'pkg_resources.extern',
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
    hookspath=['hooks'],        # custom hooks in hooks/ folder
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Packages we definitely do not use — trim bundle size
        'matplotlib',
        'numpy.distutils',
        'pytest',
        'pytest_asyncio',
        'IPython',
        'jupyter',
        'notebook',
        'PIL',
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
    upx=True,
    console=False,          # No console window — set True for debugging
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='assets\\icon.ico',          # Uncomment once icon.ico is placed in assets/
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        # Never UPX these — they break or cause false-positive AV detections
        'vcruntime140.dll',
        'vcruntime140_1.dll',
        'msvcp140.dll',
        'python311.dll',
        'torch_cpu.dll',
        'torch_python.dll',
        # Audio DLLs
        '_sounddevice*.pyd',
        'libsndfile_64bit.dll',
        'espeak-ng.dll',
        'libmecab*.dll',
    ],
    name='DemoVoiceAssistant',
)
