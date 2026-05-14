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
from server.main import app
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
        pass  # If we can't set up logging, carry on regardless


_setup_crash_logger()


# ---------------------------------------------------------------------------
# SSL certificate fix
# ---------------------------------------------------------------------------

def _fix_ssl_certs() -> None:
    """
    Point SSL_CERT_FILE at the certifi bundle bundled by PyInstaller.

    Without this, API calls to Groq / Gemini / Tavily can fail with
    CERTIFICATE_VERIFY_FAILED on machines whose system trust store is
    incomplete or misconfigured.  certifi is always present in the bundle
    because it is a dependency of httpx / requests.
    """
    if not getattr(sys, 'frozen', False):
        return  # dev environment — use system certs as normal
    try:
        import certifi
        cert_path = certifi.where()
        os.environ.setdefault('SSL_CERT_FILE', cert_path)
        os.environ.setdefault('REQUESTS_CA_BUNDLE', cert_path)
    except Exception:
        pass  # non-fatal — API calls will still work on most machines


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
    """Start the uvicorn server.  Intended to run in a background thread."""
    try:
        import uvicorn
        from server.config import CONFIG_PATH
        from dotenv import load_dotenv

        # Ensure env is loaded in this thread before importing server.main
        load_dotenv(dotenv_path=CONFIG_PATH, override=False)

        uvicorn.run(
            app,
            host=os.getenv("SERVER_HOST", "0.0.0.0"),
            port=int(os.getenv("SERVER_PORT", "8000")),
            log_config=None,   # uvicorn access logs suppressed; app uses its own
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

    # ── Server-only mode (used internally, e.g. for subprocess spawning) ────
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
            # User closed the setup window without saving — exit cleanly
            sys.exit(0)

    # Step 2 — Load env so we can read the port before starting threads
    from server.config import CONFIG_PATH
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=CONFIG_PATH, override=False)

    server_port = int(os.getenv("SERVER_PORT", "8000"))

    # Step 3 — Start the server in a background daemon thread
    server_thread = threading.Thread(target=_run_server, daemon=True, name="server")
    server_thread.start()

    # Step 4 — Open the browser after a short delay (non-blocking)
    browser_thread = threading.Thread(
        target=_open_browser_after_delay,
        args=(server_port,),
        daemon=True,
        name="browser-opener",
    )
    browser_thread.start()

    # Step 5 — Run the AudioClient in the main thread's event loop.
    # This keeps the process alive until the user closes the app.
    try:
        from client.main import main as client_main
        asyncio.run(client_main())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.error("AudioClient crashed:\n%s", traceback.format_exc())


if __name__ == "__main__":
    main()
