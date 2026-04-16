# GeoAgent

GeoAgent is a split-agent application consisting of two standalone microservices: the **Raster Agent** and the **Vector Agent**. 

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

## Setup Instructions

### Prerequisites
Both agents require Python 3.10+ and a virtual environment.

```bash
# Create a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies for the vector agent
pip install -r vector_agent/requirements.txt

# Install dependencies for the raster agent
pip install -r raster_agent/requirements.txt
```

*(Note: The `raster_agent` requires `python-multipart` which is installed via its requirements.txt or manually via `pip install python-multipart`)*

## Running the Servers

The project is decoupled. You must run each agent from its respective root directory so that absolute Python `src` imports work correctly.

### Start the Vector Agent
Open a terminal, activate your virtual environment, and run:
```bash
cd vector_agent
python -m src.main
```
The Vector Agent UI will be accessible at: **http://localhost:8001/**

### Start the Raster Agent
Open a second terminal, activate your virtual environment, and run:
```bash
cd raster_agent
python -m src.main
```
The Raster Agent UI will be accessible at: **http://localhost:8002/**

## Using the Agents Together

1. Start both servers.
2. Open the **Vector Agent** (`http://localhost:8001`) and enter a prompt to get the boundary of an area. Wait for the task to finish and download the resulting `📄 <filename>.geojson` file from the artifacts panel.
3. Open the **Raster Agent** (`http://localhost:8002`).
4. Click the upload (paperclip) icon and attach the `.geojson` file you just downloaded.
5. Enter a prompt asking for specific imagery inside that boundary (e.g. "Get true color Sentinel-2 imagery").
6. Download your final raster files!
