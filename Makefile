.PHONY: setup test run run-docker stop-docker

setup:
	uv sync

test:
	uv run python -m pytest tests/

run:
	@echo "Starting Unified GeoAgent on port 8000..."
	uv run python -m src.main

run-docker:
	docker compose up -d --build

stop-docker:
	docker compose down
