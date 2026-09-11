#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Starting GeoAgent Environment Setup"

if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed."
    echo "Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

if [ ! -f .env ]; then
    echo "[INFO] No .env file found. Creating .env from .env.example..."
    if [ -f .env.example ]; then
        cp .env.example .env
    else
        cat << 'EOF' > .env
OPENROUTER_API_KEY=
LLM_MODEL=anthropic/claude-sonnet-4.5
LLM_BASE_URL=https://openrouter.ai/api/v1
DATA_DIR=./data
NOMINATIM_USER_AGENT=geoagent/1.0 (dev@localhost)
VECTOR_MAX_AREA_KM2=750
RASTER_MAX_PIXELS=40000000
LOG_LEVEL=INFO
EOF
    fi
    echo "[OK] .env created."
fi

current_key=$(grep -E '^OPENROUTER_API_KEY=' .env 2>/dev/null | cut -d '=' -f2- | tr -d '\r" '"'" || true)

if [ -t 0 ] || [ -p /dev/stdin ]; then
    if [ -n "$current_key" ]; then
        masked_key="${current_key:0:6}...${current_key: -4}"
        echo ""
        echo "[CONFIG] OPENROUTER_API_KEY is currently set: $masked_key"
        read -r -p "Enter API key to update (leave blank to keep current, type 'none' to remove): " user_input || user_input=""
        if [ "$user_input" = "none" ] || [ "$user_input" = "clear" ]; then
            python3 -c"
with open('.env', 'r') as f:
    lines = f.readlines()
with open('.env', 'w') as f:
    for line in lines:
        if line.startswith('OPENROUTER_API_KEY='):
            f.write('OPENROUTER_API_KEY=\n')
        else:
            f.write(line)
"
            echo "[OK] API key cleared. Project will run without an API key."
        elif [ -n "$user_input" ]; then
            python3 -c"
import sys
key = sys.argv[1]
with open('.env', 'r') as f:
    lines = f.readlines()
found = False
with open('.env', 'w') as f:
    for line in lines:
        if line.startswith('OPENROUTER_API_KEY='):
            f.write(f'OPENROUTER_API_KEY={key}\n')
            found = True
        else:
            f.write(line)
    if not found:
        f.write(f'OPENROUTER_API_KEY={key}\n')
" "$user_input"
            echo "[OK] Saved OPENROUTER_API_KEY to .env."
        else
            echo "[OK] Keeping current API key."
        fi
    else
        echo ""
        read -r -p "Enter OPENROUTER_API_KEY (leave blank to run without API key): " user_input || user_input=""
        if [ -n "$user_input" ]; then
            python3 -c"
import sys
key = sys.argv[1]
with open('.env', 'r') as f:
    lines = f.readlines()
found = False
with open('.env', 'w') as f:
    for line in lines:
        if line.startswith('OPENROUTER_API_KEY='):
            f.write(f'OPENROUTER_API_KEY={key}\n')
            found = True
        else:
            f.write(line)
    if not found:
        f.write(f'OPENROUTER_API_KEY={key}\n')
" "$user_input"
            echo "[OK] Saved OPENROUTER_API_KEY to .env."
        else
            echo "[INFO] No API key entered. Project will run without an API key."
        fi
    fi
else
    if [ -z "$current_key" ]; then
        echo "[INFO] Non-interactive mode: running without an API key."
    else
        echo "[INFO] Non-interactive mode: using existing configured API key."
    fi
fi

echo ""
echo "[INFO] Syncing dependencies..."
uv sync

echo ""
echo "Starting GeoAgent on http://localhost:8000"
echo "Press Ctrl+C to stop the server"
uv run uvicorn app.main:app --reload --port 8000
