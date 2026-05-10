#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Lightweight Voice Demo — Setup Script (Linux / macOS)
# Usage: bash setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

echo "🔍 Checking for Python 3.11..."

# Locate python3.11 or python3 that reports 3.11.x
PYTHON_BIN=""
for candidate in python3.11 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" = "3" ] && [ "$minor" = "11" ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Python 3.11 not found. Please install Python 3.11 and try again."
    echo "   On macOS:  brew install python@3.11"
    echo "   On Ubuntu: sudo apt install python3.11"
    exit 1
fi

echo "✅ Found Python 3.11: $($PYTHON_BIN --version)"

# ── Check for uv ──────────────────────────────────────────────────────────────
echo ""
echo "🔍 Checking for uv..."
if ! command -v uv &>/dev/null; then
    echo "❌ uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for the rest of this script
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "❌ uv installation failed. Please install uv manually:"
        echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
fi
echo "✅ Found uv: $(uv --version)"

# ── Create virtual environment ────────────────────────────────────────────────
echo ""
echo "📦 Creating virtual environment with Python 3.11..."
uv venv --python 3.11 venv/
echo "✅ Virtual environment created at venv/"

# ── Install dependencies ──────────────────────────────────────────────────────
echo ""
echo "📥 Installing dependencies from requirements.txt..."
echo "   (This may take a few minutes — PyTorch CPU wheels are large)"
uv pip install --python venv/bin/python -r requirements.txt
echo "✅ Dependencies installed."

# ── Download Kokoro model ─────────────────────────────────────────────────────
echo ""
echo "🤖 Downloading Kokoro TTS model (English)..."
echo "   (First run triggers automatic model download — ~500 MB)"
venv/bin/python -c "
from kokoro import KPipeline
print('Downloading Kokoro English model...')
KPipeline(lang_code='a')
print('Kokoro model ready.')
"
echo "✅ Kokoro model downloaded and ready."

# ── Copy .env if needed ───────────────────────────────────────────────────────
echo ""
if [ ! -f .env ]; then
    echo "⚠️  No .env file found."
    echo "   Copy .env.example to .env and fill in your API keys:"
    echo "   cp .env.example .env"
else
    echo "✅ .env file found."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Copy and edit your environment file (if not done already):"
echo "       cp .env.example .env"
echo "       # Then open .env and set GROQ_API_KEY=<your key>"
echo ""
echo "  2. Start the demo:"
echo "       bash start.sh"
echo "════════════════════════════════════════════════════════════════════════"
