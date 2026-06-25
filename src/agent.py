from pydantic_ai.models.openai import OpenAIModel
import os
from typing import List, Optional, Any
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel
from src.tools.geocoding import geocode_location
from src.tools.vector import fetch_vector_data
from src.tools.raster import fetch_raster_data

class Deps(BaseModel):
    run_id: str

openrouter_model = OpenAIModel(
    'google/gemma-3-27b-it',
    base_url='https://openrouter.ai/api/v1',
    api_key=os.environ.get('OPENROUTER_API_KEY', '')
)

agent = Agent(
    openrouter_model,
    deps_type=Deps,
    system_prompt='''
You are GeoAgent, an AI geospatial orchestrator.
Workflow:
1. If region requested, `geocode` it.
2. If vector data requested, `fetch_vector`.
3. If raster imagery requested, `fetch_raster`.
''',
)

@agent.tool
async def geocode(ctx: RunContext[Deps], location: str) -> dict:
    """Gets bounding box and geometry for a location name."""
    from src.api.websockets import manager
    await manager.send_log(ctx.deps.run_id, f"Executing geocode for {location}...")
    res = await geocode_location(location)
    await manager.send_log(ctx.deps.run_id, f"geocode success.")
    return res

@agent.tool
async def fetch_vector(ctx: RunContext[Deps], region_name: str, data_types: List[str], geometry: Optional[dict] = None, bbox: Optional[List[float]] = None) -> dict:
    """Downloads OpenStreetMap boundaries or features."""
    from src.api.websockets import manager
    await manager.send_log(ctx.deps.run_id, f"Executing fetch_vector...")
    res = await fetch_vector_data(region_name=region_name, data_types=data_types, run_id=ctx.deps.run_id, geometry=geometry, bbox=bbox)
    await manager.send_log(ctx.deps.run_id, f"fetch_vector success.")
    return res

@agent.tool
async def fetch_raster(ctx: RunContext[Deps], region_name: str, start_date: str, end_date: str, data_types: List[str], cloud_cover_lt: int = 20, max_items: int = 1, bbox: Optional[List[float]] = None, geometry: Optional[dict] = None) -> dict:
    """Downloads Sentinel-2 satellite imagery."""
    from src.api.websockets import manager
    await manager.send_log(ctx.deps.run_id, f"Executing fetch_raster...")
    res = await fetch_raster_data(region_name=region_name, start_date=start_date, end_date=end_date, data_types=data_types, run_id=ctx.deps.run_id, cloud_cover_lt=cloud_cover_lt, max_items=max_items, bbox=bbox, geometry=geometry)
    await manager.send_log(ctx.deps.run_id, f"fetch_raster success.")
    return res
