#!/usr/bin/env bash
set -e

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v uv &>/dev/null; then
    echo "Error: uv is not installed. Install it with:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

if [ ! -f .env ]; then
    echo "Creating .env from .env.example."
    cp .env.example .env
fi

if ! grep -qE '^OPENROUTER_API_KEY=.+' .env; then
    echo "Warning: OPENROUTER_API_KEY is empty in .env, so the agent can't answer yet."
    echo "Get a free key at https://openrouter.ai/keys and add it there."
fi

uv sync
echo "Starting GeoAgent on http://localhost:8000"
exec uv run uvicorn app.main:app --reload --port 8000
