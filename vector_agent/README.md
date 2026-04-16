# GeoAgent V4 - Vector Agent

The Vector Agent is responsible for handling geospatial vector data (e.g., GeoJSON, Shapefiles) within the GeoAgent V4 architecture.

## Setup Instructions

### 1. Environment Configuration

To run this application on any machine, you need to configure your environment variables. We have provided an `.env.example` template for this purpose.

1.  Copy the `.env.example` file to create your own `.env` file:
    ```bash
    cp .env.example .env
    ```
2.  Open the newly created `.env` file and replace the placeholder values with your actual credentials.
    *   **Crucial:** You *must* provide your own `OPENROUTER_API_KEY`.

### 2. Installation

1.  Create and activate a virtual environment (recommended):
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```
2.  Install the required dependencies:
    ```bash
    pip install -r requirements.txt
    ```

### 3. Running the Agent
(*Note: Adjust the specific run command based on your application's entry point, for example using `uvicorn`, `fastapi`, or a specific python script.*)
```bash
# Example:
# uvicorn src.main:app --reload
```
