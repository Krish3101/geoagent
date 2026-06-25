# GeoAgent — Geospatial Assistant Powered by Pydantic-AI

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

GeoAgent is a unified geospatial AI assistant built on **FastAPI** and **Pydantic-AI**. It combines LLM-powered natural language understanding with powerful geospatial capabilities to download vector administrative boundaries (via OpenStreetMap) and raster satellite imagery (via Planetary Computer Sentinel-2 data).

---

## 🔥 Recent Optimization & Refactoring

We recently completed a massive unification refactor to eliminate technical debt, simplify deployments, and optimize LLM token usage. Here are the concrete improvements:

- **Massive Code Reduction:** Consolidated 3 duplicate packages (`vector_agent`, `raster_agent`, `shared`) into 1 unified `src/` directory. Removed over **8,800 lines of redundant code**, boilerplate, and duplicate logic.
- **Single Microservice Migration:** Reduced 2 separate FastAPI containers (ports 8001 & 8002) down to **1 unified endpoint** on port `8000`.
- **Token Efficiency:** Minimized the orchestrator LLM prompt from ~300+ tokens to just **~30 words**, saving approximately 150-200 compute tokens *on every single request*.
- **Framework Adoption:** Replaced 3 custom LLM routing loops (`ParsingAgent`, `PlannerAgent`, `ExecutionManager`) with `pydantic-ai`, natively mapping standard Python functions directly to LLM tools without custom Pydantic schemas or registries.

---

## Architecture Design

GeoAgent embraces a **Single Unified Backend** driven by an LLM Agent Framework (`pydantic-ai`).

### System Components

1. **Unified FastAPI Backend**
   - **Responsibility:** Handles both Vector and Raster capabilities through a single API and a shared WebSocket stream.
   - **Agent Framework:** Uses `pydantic-ai` to dynamically select and execute tools based on user prompts.

2. **Geospatial Tools**
   - `geocode(location)`: Converts a place name into geometric constraints (bbox, geometry).
   - `fetch_vector(region, ...)`: Downloads OSM boundaries or spatial features (buildings, roads). Outputs GeoJSON, Shapefiles, and ZIP.
   - `fetch_raster(region, dates, ...)`: Queries Planetary Computer for Sentinel-2 imagery (True Color, NDVI, etc.) using `odc-stac` and `rioxarray`.

### Execution Flow (Agentic Router Pattern)
Unlike older rigid planner-executor pipelines, this architecture utilizes an Agent Framework:
1. **User Request:** The user sends a natural language prompt (e.g., "Get NDVI for Central Park over the last month").
2. **LLM Orchestration:** `pydantic-ai` natively evaluates the prompt, calls `geocode` to get spatial context, and then chains the response into `fetch_raster`.
3. **Real-Time Streaming:** Logs and statuses are streamed back to the frontend in real-time over WebSockets.
### Directory Structure

```text
geo_agent/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── frontend/          # Vanilla JS unified UI
├── tests/             # Pytest automated test suite
└── src/
    ├── main.py        # FastAPI entrypoint (Port 8000)
    ├── agent.py       # pydantic-ai orchestrator
    ├── api/           # HTTP & WebSocket routers
    ├── config/        # Environment configurations
    ├── models/        # SQLAlchemy database models
    ├── services/      # Core background tasks & orchestrator execution
    └── tools/         # Pure async Python functions (fetch_vector, fetch_raster, geocode)
```

---

---

## 🚀 Setup & Execution (Native `uv` Workflow)

The project leverages `uv` for lightning-fast dependency management and a root `Makefile` for one-click automation. 

### Prerequisites
1. Install [uv](https://github.com/astral-sh/uv).
2. Configure your environment: Copy `.env.example` to `.env` in the root directory and set your `OPENROUTER_API_KEY`.

### Quick Start Commands

```bash
# 1. Install all dependencies
make setup

# 2. Run tests
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
