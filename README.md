# GeoAgent — Geospatial Microservices Platform

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-Optional-DC382D?logo=redis&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

GeoAgent is a **split-agent geospatial platform** built as two independent FastAPI microservices. It combines an LLM-powered chat interface with real geospatial processing, producing GeoJSON administrative boundaries (Vector Agent) and clipped Sentinel-2 satellite raster imagery (Raster Agent).

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI + Uvicorn |
| Geospatial Processing | GeoPandas, Rasterio, Shapely, OSMnx |
| LLM Integration | OpenRouter API (Gemma-3, configurable) |
| State Management | Redis (optional, falls back to in-memory) |
| Auth | Bearer Token (configurable secret) |
| Real-time | WebSockets (log streaming) |
| Persistence | SQLAlchemy (SQLite default / PostgreSQL) |
| Package Manager | `uv` |
| Testing | Pytest |

---

These agents have been refactored into independent FastAPI services that handle focused geospatial tasks.

## Agents Overview

### 1. Vector Agent
The Vector Agent is responsible for geocoding regions and fetching administrative boundaries or spatial features.
It operates on **Port 8001**.

**Use Cases:**
- "Get the boundary of Berlin, Germany."
- "Fetch the geometry for Central Park, New York."

The output is provided as downloadable `.geojson` and `.shp` files.

### 2. Raster Agent
The Raster Agent clips satellite imagery based on an uploaded boundary `geojson` file. It operates on **Port 8002**.

**Use Cases:**
- "Get true color imagery for this region."
- "Download Sentinel-2 NDVI data for this boundary."

The output is provided as `.tif` raster imagery.

## Setup & Installation

Each agent is designed to run completely independently. Both agents depend on the local shared utility package, `geoagent-shared`.

### 1. Install using `uv` (Recommended)

Ensure `uv` is installed on your system. To set up each agent:

**For the Vector Agent:**
```bash
cd vector_agent
# Create a virtual environment and activate it
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install the local shared dependency as an editable package
uv pip install -e ../shared

# Install the rest of the dependencies
uv pip install -r requirements.txt
```

**For the Raster Agent:**
```bash
cd raster_agent
# Create a virtual environment and activate it
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install the local shared dependency as an editable package
uv pip install -e ../shared

# Install the rest of the dependencies
uv pip install -r requirements.txt
```

---

## Configuration & Environment Variables

Create a `.env` file in the root of the respective agent directory (`vector_agent/.env` and `raster_agent/.env`) to customize settings.

### 1. Token Authentication
Endpoints like `/api/chat` and `/api/upload_geometry` are protected by bearer token authentication.
- **Variable**: `GEO_AGENT_SECRET_TOKEN`
- **Default**: `default_secret_token_123`
- **How it works**: Incoming requests must include either an `Authorization: Bearer <token>` header or an `X-Auth-Token: <token>` header. Set this variable to your desired secure token.

### 2. Redis State Maps
State management (task statuses, WebSocket connections, logs) can be synchronized across instances using Redis.
- **Variable**: `REDIS_URL`
- **Example**: `redis://localhost:6379/0`
- **How it works**: If `REDIS_URL` is set, the agents will use Redis hashes (`geoagent:task_statuses`) and sets (`geoagent:active_ws_connections`) along with Redis Pub/Sub for log streaming. If `REDIS_URL` is empty or unreachable, agents will automatically fallback to in-memory state tracking.

### 3. Other Settings
- `OPENROUTER_API_KEY`: API key for accessing LLM capabilities via OpenRouter.
- `LLM_MODEL`: LLM model name (defaults to `google/gemma-3-27b-it`).
- `DATABASE_URL`: Connection string for SQLAlchemy (defaults to `sqlite:///./geoagent.db`).

---

## Running the Servers

Run each agent from its respective root directory so that absolute Python `src` imports work correctly.

### Start the Vector Agent
```bash
cd vector_agent
export PYTHONPATH=.
uv run python -m src.main
```
The Vector Agent UI will be accessible at: **http://localhost:8001/**

### Start the Raster Agent
```bash
cd raster_agent
export PYTHONPATH=.
uv run python -m src.main
```
The Raster Agent UI will be accessible at: **http://localhost:8002/**

---

## Running Tests

To verify your environment and dependencies, run the test suites:

**Vector Agent Tests:**
```bash
cd vector_agent
export PYTHONPATH=.
uv run pytest
```

**Raster Agent Tests:**
```bash
cd raster_agent
export PYTHONPATH=.
uv run pytest
```

---

## Using the Agents Together

1. Start both servers.
2. Open the **Vector Agent** (`http://localhost:8001`) and enter a prompt to get the boundary of an area. Wait for the task to finish and download the resulting `📄 <filename>.geojson` file from the artifacts panel.
3. Open the **Raster Agent** (`http://localhost:8002`).
4. Click the upload (paperclip) icon and attach the `.geojson` file you just downloaded.
5. Enter a prompt asking for specific imagery inside that boundary (e.g. "Get true color Sentinel-2 imagery").
6. Download your final raster files!
