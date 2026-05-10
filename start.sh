#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Lightweight Voice Demo — Start Script (Linux / macOS)
# Usage: bash start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Check for .env ────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo "⚠️  No .env file found."
    echo "   Please copy .env.example to .env and fill in your API keys:"
    echo "   cp .env.example .env"
    echo "   Then re-run: bash start.sh"
    exit 1
fi

# ── Check virtual environment ─────────────────────────────────────────────────
if [ ! -f venv/bin/python ]; then
    echo "❌ Virtual environment not found. Please run setup first:"
    echo "   bash setup.sh"
    exit 1
fi

# ── Cleanup function ──────────────────────────────────────────────────────────
SERVER_PID=""
CLIENT_PID=""

cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        echo "   Server stopped."
    fi
    if [ -n "$CLIENT_PID" ] && kill -0 "$CLIENT_PID" 2>/dev/null; then
        kill "$CLIENT_PID" 2>/dev/null || true
        echo "   Audio client stopped."
    fi
    echo "👋 Done."
}
trap cleanup EXIT INT TERM

# ── Launch server ─────────────────────────────────────────────────────────────
echo "🚀 Starting server on http://localhost:8000 ..."
venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 &
SERVER_PID=$!

# Give the server a moment to start before launching the client
sleep 2

# ── Launch audio client ───────────────────────────────────────────────────────
echo "🎤 Starting audio client..."
venv/bin/python -m client.main &
CLIENT_PID=$!

# ── Open browser ──────────────────────────────────────────────────────────────
echo "🌐 Opening browser at http://localhost:8000 ..."
sleep 1
if command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:8000" &>/dev/null || true
elif command -v open &>/dev/null; then
    open "http://localhost:8000" || true
else
    echo "   (Could not auto-open browser — please navigate to http://localhost:8000)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✅ Lightweight Voice Demo is running."
echo "   Browser UI : http://localhost:8000"
echo "   Press Ctrl+C to stop all processes."
echo "════════════════════════════════════════════════════════════════════════"

# ── Wait for background processes ─────────────────────────────────────────────
wait "$SERVER_PID" "$CLIENT_PID"
