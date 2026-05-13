"""
First-launch configuration screen for the Demo Voice Assistant.

Shown on startup when GROQ_API_KEY is not yet configured.
Uses only tkinter (Python standard library — no extra dependencies).

The config file is stored at:
  - Frozen (.exe): %APPDATA%\\DemoVoiceAssistant\\.env
  - Development:   <project_root>\\.env
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox


# ---------------------------------------------------------------------------
# Helpers — read / write the .env file
# ---------------------------------------------------------------------------

def _load_env(env_path: str) -> dict[str, str]:
    """Parse a .env file into a dict. Ignores comments and blank lines."""
    config: dict[str, str] = {}
    if not os.path.exists(env_path):
        return config
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def _save_env(env_path: str, config: dict[str, str]) -> None:
    """Write a dict to a .env file, one KEY=value per line."""
    os.makedirs(os.path.dirname(env_path), exist_ok=True)
    with open(env_path, "w", encoding="utf-8") as fh:
        for key, value in config.items():
            fh.write(f"{key}={value}\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def needs_setup() -> bool:
    """Return True if GROQ_API_KEY is not yet configured."""
    from server.config import CONFIG_PATH
    config = _load_env(CONFIG_PATH)
    return not config.get("GROQ_API_KEY", "").strip()


def run_setup_screen() -> bool:
    """
    Display the first-launch setup window.

    Blocks until the user saves their keys or closes the window.

    Returns:
        True  — user saved a valid Groq API key; app should continue.
        False — user closed the window without saving; app should exit.
    """
    from server.config import CONFIG_PATH

    root = tk.Tk()
    root.title("Demo Voice Assistant — First-Time Setup")
    root.geometry("500x360")
    root.resizable(False, False)
    root.configure(bg="#1a1a2e")

    # Centre on screen
    root.update_idletasks()
    x = (root.winfo_screenwidth() - 500) // 2
    y = (root.winfo_screenheight() - 360) // 2
    root.geometry(f"+{x}+{y}")

    completed = [False]

    # ── Title ────────────────────────────────────────────────────────────────
    tk.Label(
        root,
        text="Voice Assistant Setup",
        font=("Arial", 16, "bold"),
        bg="#1a1a2e",
        fg="white",
    ).pack(pady=(24, 4))

    tk.Label(
        root,
        text="Enter your API keys to get started.",
        font=("Arial", 10),
        bg="#1a1a2e",
        fg="#aaaaaa",
    ).pack(pady=(0, 20))

    # ── Groq API Key (required) ───────────────────────────────────────────────
    frame_groq = tk.Frame(root, bg="#1a1a2e")
    frame_groq.pack(fill="x", padx=40, pady=4)

    tk.Label(
        frame_groq,
        text="Groq API Key  (required)",
        font=("Arial", 10, "bold"),
        bg="#1a1a2e",
        fg="white",
        anchor="w",
    ).pack(fill="x")

    tk.Label(
        frame_groq,
        text="Get a free key at: console.groq.com",
        font=("Arial", 8),
        bg="#1a1a2e",
        fg="#888888",
        anchor="w",
    ).pack(fill="x")

    groq_var = tk.StringVar()
    tk.Entry(
        frame_groq,
        textvariable=groq_var,
        font=("Courier", 10),
        bg="#2a2a3e",
        fg="white",
        insertbackground="white",
        relief="flat",
        bd=6,
    ).pack(fill="x", pady=(4, 0))

    # ── Tavily API Key (optional) ─────────────────────────────────────────────
    frame_tavily = tk.Frame(root, bg="#1a1a2e")
    frame_tavily.pack(fill="x", padx=40, pady=(14, 4))

    tk.Label(
        frame_tavily,
        text="Tavily API Key  (optional — enables web search)",
        font=("Arial", 10),
        bg="#1a1a2e",
        fg="#aaaaaa",
        anchor="w",
    ).pack(fill="x")

    tk.Label(
        frame_tavily,
        text="Get a free key at: app.tavily.com",
        font=("Arial", 8),
        bg="#1a1a2e",
        fg="#888888",
        anchor="w",
    ).pack(fill="x")

    tavily_var = tk.StringVar()
    tk.Entry(
        frame_tavily,
        textvariable=tavily_var,
        font=("Courier", 10),
        bg="#2a2a3e",
        fg="white",
        insertbackground="white",
        relief="flat",
        bd=6,
    ).pack(fill="x", pady=(4, 0))

    # ── Save button ───────────────────────────────────────────────────────────
    def _on_save() -> None:
        groq_key = groq_var.get().strip()

        if not groq_key:
            messagebox.showerror(
                "Missing Key",
                "Groq API Key is required to run the assistant.",
                parent=root,
            )
            return

        if not groq_key.startswith("gsk_"):
            if not messagebox.askyesno(
                "Unexpected Key Format",
                "This doesn't look like a Groq key (expected prefix: 'gsk_').\n"
                "Save anyway?",
                parent=root,
            ):
                return

        # Build config dict — preserve any existing keys not shown in the UI
        existing = _load_env(CONFIG_PATH)
        existing["GROQ_API_KEY"] = groq_key

        tavily_key = tavily_var.get().strip()
        if tavily_key:
            existing["TAVILY_API_KEY"] = tavily_key
        else:
            # Remove stale Tavily key if the field was left blank
            existing.pop("TAVILY_API_KEY", None)

        # Write sensible defaults for keys that are not yet present
        existing.setdefault("LOG_LEVEL", "INFO")
        existing.setdefault("VAD_SILENCE_MS", "600")
        existing.setdefault("SERVER_PORT", "8000")
        existing.setdefault("WS_PORT", "8000")

        _save_env(CONFIG_PATH, existing)
        completed[0] = True
        root.destroy()

    tk.Button(
        root,
        text="Save and Launch",
        command=_on_save,
        font=("Arial", 11, "bold"),
        bg="#4f46e5",
        fg="white",
        activebackground="#4338ca",
        activeforeground="white",
        relief="flat",
        padx=20,
        pady=8,
        cursor="hand2",
        bd=0,
    ).pack(pady=22)

    root.mainloop()
    return completed[0]
