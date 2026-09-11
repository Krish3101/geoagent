# GeoAgent

Ask for GIS data in plain language and get back real files — GeoJSON, GeoPackage, GeoTIFF.
"Get me the boundary of Central Park", then "now the buildings and roads", then "and an
NDVI for July 2024, under 10% cloud".

Normally that means geocoding the place, writing an Overpass or OSMnx query, searching a
STAC catalogue for satellite scenes, filtering by cloud cover, and reprojecting between
coordinate systems. GeoAgent does those steps and hands back the files.

It runs locally for one person: no login, a local SQLite database, started with `uv`.

## What the model is allowed to decide

Only *what* to fetch. It never touches coordinates, geometry or file paths — those live in
the run context, and the tools read them from there. The model picks a place name, a layer
and a date range; Python does every calculation.

This is the whole design. A model that hallucinates a bounding box can't corrupt the
output, because it was never asked for one. A wrong coordinate and a right one look
identical downstream, so the safest thing is for the model never to produce one.

The session remembers the current area of interest between turns, which is what makes "now
the buildings and roads" work without naming the park again. You can also upload your own
GeoJSON polygon and everything after that clips to it.

## Task state doesn't live in the connection

Task state is a real state machine: `queued -> running -> succeeded` or `failed`. The
server-sent events stream *reports* that state, it doesn't define it, so a dropped
connection can't leave a task stuck in the wrong one.

```
app/
  agent.py       tool definitions given to the model
  runner.py      task execution, event logging
  routes.py      API and SSE endpoints
  geo/geocode.py Nominatim lookup, throttled to 1 req/s
  geo/vector.py  OSMnx features, GeoJSON and GeoPackage export
  geo/raster.py  STAC search, cloud filtering, NDVI, GeoTIFF
  models.py      sessions, tasks, events, artifacts
web/             Leaflet map and chat UI
```

## Running it

```bash
./scripts/start.sh
```

The script checks for `uv`, creates `.env` if it's missing, asks for an OpenRouter API key,
syncs dependencies, and serves on http://localhost:8000. By hand:

```bash
cp .env.example .env     # add OPENROUTER_API_KEY
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

`./scripts/reset.sh` clears the database, generated files and caches (`-y` to skip the
prompt).

```bash
uv run pytest -q                # offline
uv run pytest -m network        # hits Nominatim and Planetary Computer
uv run ruff check . && uv run ruff format --check .
```

The offline suite covers the state machine, AOI validation and repair, NDVI arithmetic,
resolution selection for large areas, artifact path traversal, and the SSE replay path.
Network tests are deselected by default so a fresh clone runs clean.

## Limits that are deliberate

Nominatim is rate-limited to one request per second because their usage policy requires it,
so geocoding is slow on purpose rather than by accident.

Large areas are downsampled to 20 m or 60 m resolution and refused outright past a point —
Sentinel-2 at 10 m over a few hundred kilometres is more pixels than this will handle.

Everything is single-user with no auth, so don't put it on a public address.

## License

[MIT](LICENSE)
