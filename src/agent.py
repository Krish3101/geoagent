from pydantic_ai.models.openai import OpenAIModel
from typing import List, Optional, Any, Dict
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from src.tools.geocoding import geocode_location
from src.tools.vector import fetch_vector_data
from src.tools.raster import fetch_raster_data
from src.config import settings

class Deps(BaseModel):
    run_id: str
    spatial_context: Dict[str, Any] = Field(default_factory=dict)

openrouter_model = OpenAIModel(
    settings.LLM_MODEL,
    base_url='https://openrouter.ai/api/v1',
    api_key=settings.OPENROUTER_API_KEY or ''
)

agent = Agent(
    openrouter_model,
    deps_type=Deps,
    system_prompt='''
You are GeoAgent, an AI geospatial orchestrator.
Workflow:
1. If spatial context is not yet resolved, use `geocode` to obtain it.
2. If vector data (boundary or features) requested, use `fetch_vector`.
3. If raster satellite imagery or NDVI requested, use `fetch_raster`.
Always use existing spatial context when referring to a previously established location or area.
''',
)

@agent.tool
async def geocode(ctx: RunContext[Deps], location: str) -> dict:
    """Gets bounding box and geometry for a location name."""
    from src.api.websockets import manager
    await manager.send_log(ctx.deps.run_id, f"Executing geocode for '{location}'...")
    res = await geocode_location(location)
    ctx.deps.spatial_context["bbox"] = res.get("bbox")
    ctx.deps.spatial_context["geometry"] = res.get("geometry")
    ctx.deps.spatial_context["region_name"] = res.get("resolved_location")
    await manager.send_log(ctx.deps.run_id, f"geocode success for '{res.get('resolved_location')}'.")
    return res

@agent.tool
async def fetch_vector(
    ctx: RunContext[Deps], 
    region_name: str, 
    data_types: List[str], 
    geometry: Optional[dict] = None, 
    bbox: Optional[List[float]] = None
) -> dict:
    """Downloads OpenStreetMap boundaries or features."""
    from src.api.websockets import manager
    await manager.send_log(ctx.deps.run_id, f"Executing fetch_vector for {region_name}...")
    if not geometry and not bbox and ctx.deps.spatial_context:
        geometry = ctx.deps.spatial_context.get("geometry")
        bbox = ctx.deps.spatial_context.get("bbox")
    res = await fetch_vector_data(
        region_name=region_name, 
        data_types=data_types, 
        run_id=ctx.deps.run_id, 
        geometry=geometry, 
        bbox=bbox
    )
    if "geometry" in res:
        ctx.deps.spatial_context["geometry"] = res["geometry"]
    if "bbox" in res:
        ctx.deps.spatial_context["bbox"] = res["bbox"]
    ctx.deps.spatial_context["region_name"] = region_name
    await manager.send_log(ctx.deps.run_id, f"fetch_vector success.")
    return res

@agent.tool
async def fetch_raster(
    ctx: RunContext[Deps], 
    region_name: str, 
    start_date: str, 
    end_date: str, 
    data_types: List[str], 
    cloud_cover_lt: int = 20, 
    max_items: int = 1, 
    bbox: Optional[List[float]] = None, 
    geometry: Optional[dict] = None
) -> dict:
    """Downloads Sentinel-2 satellite imagery or derives NDVI."""
    from src.api.websockets import manager
    await manager.send_log(ctx.deps.run_id, f"Executing fetch_raster for {region_name}...")
    if not bbox and not geometry and ctx.deps.spatial_context:
        bbox = ctx.deps.spatial_context.get("bbox")
        geometry = ctx.deps.spatial_context.get("geometry")
    res = await fetch_raster_data(
        region_name=region_name, 
        start_date=start_date, 
        end_date=end_date, 
        data_types=data_types, 
        run_id=ctx.deps.run_id, 
        cloud_cover_lt=cloud_cover_lt, 
        max_items=max_items, 
        bbox=bbox, 
        geometry=geometry
    )
    ctx.deps.spatial_context["region_name"] = region_name
    await manager.send_log(ctx.deps.run_id, f"fetch_raster success.")
    return res
