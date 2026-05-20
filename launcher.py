"""
Demo Voice Assistant — main launcher.

This is the PyInstaller entry point.  It handles three responsibilities:

  1. First-launch setup — if GROQ_API_KEY is not configured, show the Tkinter
     setup screen before anything else starts.

  2. Process orchestration — start the FastAPI/uvicorn server as a background
     thread, then run the AudioClient in the main asyncio event loop.

  3. Browser auto-open — open http://localhost:<SERVER_PORT> in the default
     browser a few seconds after the server starts.

Usage (development):
    python launcher.py

Usage (packaged .exe):
    DemoVoiceAssistant.exe
    DemoVoiceAssistant.exe --mode server   # server only (internal use)
    DemoVoiceAssistant.exe --mode client   # audio client only (internal use)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
import webbrowser

# ---------------------------------------------------------------------------
# IMPORTANT: Do NOT add any application imports here at module level.
# All imports from server.* and client.* must happen inside functions below.
# Reason: torch must be pre-initialized in the server thread (not main thread)
# before server.main is imported, and crash logger + SSL must run first.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Crash logger — write fatal errors to AppData before any UI is available
# ---------------------------------------------------------------------------

def _setup_crash_logger() -> None:
    """Configure a crash log in AppData so silent failures are diagnosable."""
    try:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        log_dir = os.path.join(appdata, "DemoVoiceAssistant")
        os.makedirs(log_dir, exist_ok=True)
        crash_log = os.path.join(log_dir, "crash.log")
        logging.basicConfig(
            filename=crash_log,
            level=logging.ERROR,
            format="%(asctime)s %(levelname)s — %(message)s",
        )
    except Exception:
        pass


_setup_crash_logger()


# ---------------------------------------------------------------------------
# SSL certificate fix
# ---------------------------------------------------------------------------

def _fix_ssl_certs() -> None:
    """
    Point SSL_CERT_FILE at the certifi bundle bundled by PyInstaller.

    Without this, API calls to Groq / Gemini / Tavily can fail with
    CERTIFICATE_VERIFY_FAILED on machines whose system trust store is
    incomplete or misconfigured.
    """
    if not getattr(sys, 'frozen', False):
        return
    try:
        import certifi
        cert_path = certifi.where()
        os.environ.setdefault('SSL_CERT_FILE', cert_path)
        os.environ.setdefault('REQUESTS_CA_BUNDLE', cert_path)
    except Exception:
        pass


_fix_ssl_certs()


# ---------------------------------------------------------------------------
# Parse --mode flag
# ---------------------------------------------------------------------------

def _get_mode() -> str:
    """Return the value of --mode <arg>, or 'all' if not specified."""
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return "all"


# ---------------------------------------------------------------------------
# Server runner (runs in a daemon thread)
# ---------------------------------------------------------------------------

def _run_server() -> None:
    """Start the uvicorn server. Intended to run in a background thread."""
    try:
        # ── Torch pre-init ────────────────────────────────────────────────
        # MUST run in this thread (the server/kokoro thread), not main thread.
        # Reason: torch._C (the C extension) and torch.version must be fully
        # initialized in the same thread that will later call torch.load()
        # via kokoro TTS synthesis. If pre-init runs in a different thread,
        # the C-level initialization state is not visible here and kokoro's
        # first torch.load() call re-enters torch init mid-flight, causing:
        # "partially initialized module 'torch' has no attribute 'version'"
        #
        # torch.version — the submodule (torch/version.py), NOT torch.__version__
        #   (the string). These are different. kokoro accesses torch.version.cuda
        #   and torch.version.git_version internally via torch._C at load time.
        # torch._C — torch's C extension, the real root of the circular import.
        # torch.storage / torch.serialization — required by torch.load().
        # torch.cuda — accessed by torch.version internals even on CPU builds.
        import torch
        import torch.version
        import torch.nn
        import torch.nn.functional
        import torch.jit
        import torch._C
        import torch.storage
        import torch.serialization
        import torch.cuda

        # Verify the specific attributes kokoro accesses during synthesis
        _ = torch.__version__
        _ = torch.version.__version__
        _ = torch.version.cuda
        _ = torch.version.git_version

        # ── Server startup ────────────────────────────────────────────────
        import uvicorn
        from server.main import app
        from server.config import CONFIG_PATH
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=CONFIG_PATH, override=False)

        uvicorn.run(
            app,
            host=os.getenv("SERVER_HOST", "0.0.0.0"),
            port=int(os.getenv("SERVER_PORT", "8000")),
            log_config=None,
        )
    except Exception:
        logging.error("Server thread crashed:\n%s", traceback.format_exc())


# ---------------------------------------------------------------------------
# Browser opener (runs in a daemon thread)
# ---------------------------------------------------------------------------

def _open_browser_after_delay(port: int, delay: float = 3.0) -> None:
    """Wait for the server to start, then open the UI in the default browser."""
    time.sleep(delay)
    url = f"http://localhost:{port}"
    try:
        webbrowser.open(url)
    except Exception as exc:
        logging.warning("Could not open browser: %s", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mode = _get_mode()

    # ── Server-only mode ─────────────────────────────────────────────────────
    if mode == "server":
        _run_server()
        return

    # ── Client-only mode ─────────────────────────────────────────────────────
    if mode == "client":
        from client.main import main as client_main
        asyncio.run(client_main())
        return

    # ── Default: full stack (server + client + browser) ──────────────────────

    # Step 1 — First-launch setup screen
    from server.setup_screen import needs_setup, run_setup_screen

    if needs_setup():
        success = run_setup_screen()
        if not success:
            sys.exit(0)

    # Step 2 — Load env so we can read the port before starting threads
    from server.config import CONFIG_PATH
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=CONFIG_PATH, override=False)

    server_port = int(os.getenv("SERVER_PORT", "8000"))

    # Step 3 — Start the server in a background daemon thread
    server_thread = threading.Thread(target=_run_server, daemon=True, name="server")
    server_thread.start()

    # Step 4 — Open the browser after a short delay
    browser_thread = threading.Thread(
        target=_open_browser_after_delay,
        args=(server_port,),
        daemon=True,
        name="browser-opener",
    )
    browser_thread.start()

    # Step 5 — Run the AudioClient in the main thread's event loop
    try:
        from client.main import main as client_main
        asyncio.run(client_main())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.error("AudioClient crashed:\n%s", traceback.format_exc())


if __name__ == "__main__":
    # REQUIRED for PyInstaller --onedir on Windows.
    # Must be called before any other code when the module is the entry point.
    # Without this, frozen exes that use multiprocessing (torch, kokoro) will
    # spawn infinite child processes instead of worker processes.
    import multiprocessing
    multiprocessing.freeze_support()
    main()