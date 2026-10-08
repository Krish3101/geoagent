# GeoAgent

Getting map data for a place by hand takes several tools: find the place's outline, write an OpenStreetMap query, search a satellite catalogue, compute a vegetation index, export the files. GeoAgent does it from one sentence, such as "buildings and roads around Central Park" or "NDVI of Central Park, July 2025", and returns GeoJSON or a GeoTIFF on a map, ready to download. Language models invent coordinates, so here the model only chooses which tool to run and Python does every geographic step.


## Run (macOS)

Needs uv (`brew install uv`) and a free OpenRouter key from openrouter.ai/keys.

```bash
cp .env.example .env              # then add OPENROUTER_API_KEY
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and try one of the three example buttons. Without an API key, the page still loads and explains how to add one. Reset data at any time with `rm -rf data`.

## How it works

The AOI (area of interest) is the outline of the place being worked on; each chat session remembers one.

1. The page posts the message to `POST /api/sessions/{session_id}/messages`, which saves it with a `queued` task and returns 202 at once.
2. The page opens the task's event stream and shows each progress line as it arrives.
3. `tasks.py` runs the pydantic-ai agent with the recent messages and the AOI name, under a 300-second limit and at most 6 model requests and 8 tool calls.
4. The model picks tools; Python does the work: `resolve_area` geocodes a place with Nominatim and stores the AOI, `extract_vector` downloads OpenStreetMap layers inside it, `fetch_ndvi` finds the clearest Sentinel-2 image and computes NDVI.
5. Each output file is recorded under `data/runs/<task id>/`, and the model writes a one-line reply.
6. When the stream ends, the page draws the files on the map and lists them for download.

| Tool | Arguments | What Python does |
| --- | --- | --- |
| `resolve_area` | `place`: text, at most 200 characters | Geocodes the place and stores its outline as the AOI |
| `extract_vector` | `layers`: 1 to 7 of boundary, buildings, roads, waterways, landuse, amenities, natural | Downloads those layers inside the AOI (up to 750 km²) |
| `fetch_ndvi` | `start_date`, `end_date`, `max_cloud_cover` 0 to 100 | Computes NDVI from the clearest scene (up to 25 million pixels) |

No tool takes a coordinate, a bounding box, a geometry or a file path, and a test fails if one ever does.

## API

| Method | Path | What it does |
| --- | --- | --- |
| `POST` | `/api/sessions` | Start a chat session |
| `GET` | `/api/sessions/{session_id}` | The session and its AOI |
| `GET` | `/api/sessions/{session_id}/messages` | Chat history |
| `POST` | `/api/sessions/{session_id}/messages` | Send a request: 202 with the task id and events URL, 409 if a task is running |
| `GET` | `/api/tasks/{task_id}/events` | Progress as Server-Sent Events, ending with `done` or `error` |
| `GET` | `/api/tasks/{task_id}/artifacts` | The task's output files with download URLs |
| `GET` | `/api/artifacts/{artifact_id}/download` | One GeoJSON, GeoTIFF or PNG file |
| `GET` | `/api/health` | Server is up |

## Tests

```bash
uv run pytest -q
```

Offline: the tool-schema check, the NDVI formula and the API, with no model or network calls.
