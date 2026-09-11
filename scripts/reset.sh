#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

FORCE=false
CLEAN_ENV=false

for arg in "$@"; do
    case $arg in
        -y|--yes|-f|--force)
            FORCE=true
            ;;
        --all|-a)
            CLEAN_ENV=true
            ;;
    esac
done

if [ "$FORCE" = false ] && [ -t 0 ]; then
    echo "This will delete the database, generated task artifacts, and runtime caches."
    read -r -p "Continue reset? (y/N): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Cancelled."
        exit 0
    fi
fi

echo "Cleaning up files created during running..."

rm -f data/geoagent.db data/geoagent.db-shm data/geoagent.db-wal
rm -f data/*.db data/*.db-shm data/*.db-wal

rm -rf data/runs
mkdir -p data/runs

find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type f -name "*.pyo" -delete 2>/dev/null || true
rm -rf .pytest_cache .ruff_cache cache 2>/dev/null || true

if [ "$CLEAN_ENV" = true ]; then
    rm -f .env
    echo "[OK] Removed .env configuration file."
fi

echo "[OK] Reset complete. All files created during running have been removed."
