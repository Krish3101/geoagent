#!/usr/bin/env bash
set -e

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ "$1" != "-y" ]; then
    read -r -p "Delete the database, generated files and caches? (y/N) " answer
    [ "$answer" = "y" ] || [ "$answer" = "Y" ] || exit 0
fi

rm -rf data cache .pytest_cache .ruff_cache
find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} +
echo "Reset done. .env and .venv were kept."
