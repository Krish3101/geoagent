from datetime import datetime, timezone

from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.config import LLM_BASE_URL, settings
from app.tools import RunDeps, extract_vector, fetch_ndvi, resolve_area


def build_model() -> OpenAIChatModel:
    # built per run, only after the runner has checked that a key is set
    client = AsyncOpenAI(
        base_url=LLM_BASE_URL,
        api_key=settings.openrouter_api_key,
        timeout=60.0,
        max_retries=1,
        default_headers={"X-Title": "geoagent"},
    )
    return OpenAIChatModel(settings.model, provider=OpenRouterProvider(openai_client=client))


def system_prompt(today: str, aoi: dict | None) -> str:
    # Only the AOI name, source and size go in the prompt, never its coordinates.
    aoi_desc = "None"
    if aoi:
        aoi_desc = f"Name: {aoi['name']}, Source: {aoi['source']}, Area: {aoi['area_km2']} km²"

    return (
        "You are GeoAgent, a conversational assistant that turns requests into downloadable "
        "GIS files.\n"
        f"Today's date: {today}.\n"
        f"Current Area of Interest (AOI): {aoi_desc}.\n\n"
        "Principles:\n"
        "- The LLM routes; Python computes. Never generate coordinates, geometry, or file paths.\n"
        "- If the user names a new location, call resolve_area(place) first.\n"
        "- If an AOI is already set and the user asks for data for the current place, "
        "DO NOT call resolve_area; reuse the current AOI.\n"
        "- For vector layers, call extract_vector(layers=[...]). "
        "Valid layers: boundary, buildings, roads, waterways, landuse, amenities, natural.\n"
        "- For satellite imagery or NDVI, convert relative dates to ISO YYYY-MM-DD "
        "and call fetch_ndvi.\n"
        "- Summarize tool results concisely in 1-2 sentences."
    )


# No model here: run_task passes one in, so importing this module needs no API key.
# Tools run one at a time; parallel calls would race on the same task's events and AOI.
agent = Agent(
    deps_type=RunDeps,
    tools=[
        Tool(resolve_area, sequential=True),
        Tool(extract_vector, sequential=True),
        Tool(fetch_ndvi, sequential=True),
    ],
    model_settings={"parallel_tool_calls": False},
)


@agent.system_prompt
def _system_prompt(ctx: RunContext[RunDeps]) -> str:
    return system_prompt(datetime.now(timezone.utc).strftime("%Y-%m-%d"), ctx.deps.aoi)
