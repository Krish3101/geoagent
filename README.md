# GeoAgent — Geospatial Assistant Powered by Pydantic-AI

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

GeoAgent is a unified geospatial AI assistant built on **FastAPI** and **Pydantic-AI**. It combines LLM-powered natural language understanding with robust geospatial capabilities to seamlessly fetch vector administrative boundaries (via OpenStreetMap) and raster satellite imagery (via Planetary Computer Sentinel-2 data).

---

## 🔥 Key Features

- **Agentic Routing:** Utilizes `pydantic-ai` to natively map pure Python functions into LLM tools, eliminating rigid planner-executor pipelines and complex orchestration loops.
- **Unified Backend:** A single FastAPI service handles both vector and raster capabilities, exposed via a unified REST API and real-time WebSocket stream.
- **Token Efficient:** Highly optimized LLM prompts (under ~30 words) ensure rapid, cost-effective inference on every request.
- **Real-Time Streaming:** Features a robust state-manager (backed by Redis or an in-memory fallback) that streams logs and task statuses to the UI via WebSockets, ensuring zero missed updates even on late connections.

---

## 🏗️ Architecture Design

GeoAgent embraces a modular backend driven by modern async Python patterns.

### System Components

1. **FastAPI Application (`src/main.py`)**
   - Serves the frontend, exposes REST endpoints, and manages WebSocket connections for live logs.
2. **LLM Orchestrator (`src/agent.py`)**
   - Leverages `pydantic-ai` to dynamically evaluate user prompts, select appropriate spatial tools, and execute workflows asynchronously.
3. **Geospatial Tools (`src/tools/`)**
   - `geocode(location)`: Converts textual place names into precise geometric constraints (bounding boxes, GeoJSON).
   - `fetch_vector(region, ...)`: Extracts OSM boundaries and spatial features (buildings, roads), outputting GeoJSON and Shapefiles.
   - `fetch_raster(region, dates, ...)`: Queries Planetary Computer for Sentinel-2 imagery (True Color, NDVI) using `odc-stac` and `rioxarray`.

### Directory Structure

```text
geo_agent/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── frontend/          # Vanilla JS unified UI
├── tests/             # Pytest automated test suite (20/20 passing)
└── src/
    ├── main.py        # FastAPI entrypoint (Port 8000)
    ├── agent.py       # pydantic-ai orchestrator
    ├── api/           # HTTP & WebSocket routers
    ├── config/        # Environment configurations
    ├── models/        # SQLAlchemy database models
    ├── services/      # Core background tasks & orchestrator execution
    └── tools/         # Pure async Python geospatial functions
```

---

## 🚀 Setup & Execution (Native `uv` Workflow)

The project leverages [uv](https://github.com/astral-sh/uv) for lightning-fast dependency management and a root `Makefile` for one-click automation. 

### Prerequisites
1. Install `uv`.
2. Copy `.env.example` to `.env` in the root directory and configure your `OPENROUTER_API_KEY`.

### Quick Start Commands

```bash
# 1. Install all dependencies
make setup

# 2. Run the test suite
make test

# 3. Start the unified agent backend
make run
```

You can now visit the single UI at:
- **GeoAgent UI:** http://localhost:8000

### Docker Support
Run the application (alongside the Redis state-manager) seamlessly via Docker Compose:
```bash
make run-docker
```
