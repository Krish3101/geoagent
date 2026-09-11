import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.config import settings
from app.db import AsyncSessionLocal
from app.geo.geocode import geocode
from app.geo.raster import Product, fetch_raster_product
from app.geo.vector import Layer, extract_vector_layer
from app.models import Artifact
from app.runner import log_event


@dataclass
class RunDeps:
    task_id: str
    session_id: str
    aoi: dict[str, Any] | None
    aoi_changed: bool = False


def get_model() -> OpenAIChatModel:
    api_key = settings.openrouter_api_key or "sk-placeholder"
    client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=api_key)
    provider = OpenRouterProvider(openai_client=client)
    return OpenAIChatModel(settings.llm_model, provider=provider)


agent = Agent(
    get_model(),
    deps_type=RunDeps,
)


@agent.system_prompt
def build_system_prompt(ctx: RunContext[RunDeps]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    aoi_desc = "None"
    if ctx.deps.aoi:
        aoi = ctx.deps.aoi
        aoi_desc = (
            f"Name: {aoi.get('name')}, "
            f"Source: {aoi.get('source')}, "
            f"Area: {aoi.get('area_km2')} km², "
            f"BBox: {aoi.get('bbox')}"
        )

    return (
        f"You are GeoAgent, a conversational assistant that turns requests into downloadable "
        f"GIS files.\n"
        f"Today's date: {today}.\n"
        f"Current Area of Interest (AOI): {aoi_desc}.\n\n"
        f"Principles:\n"
        f"- The LLM routes; Python computes. Never generate coordinates, geometry, or file paths.\n"
        f"- If the user names a new location, call resolve_area(place) first.\n"
        f"- If an AOI is already set and the user asks for data for the current place, "
        f"DO NOT call resolve_area; reuse the current AOI.\n"
        f"- For vector layers, call extract_vector(layers=[...]). "
        f"Valid layers: boundary, buildings, roads, waterways, landuse, amenities, natural.\n"
        f"- For satellite imagery or NDVI, convert relative dates to ISO YYYY-MM-DD "
        f"and call fetch_imagery.\n"
        f"- Summarize tool results concisely in 1-2 sentences."
    )


def slugify(text: str) -> str:
    """Create a filename-safe slug from a string."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[-\s]+", "_", text)[:40] or "aoi"


async def record_artifact(
    task_id: str,
    kind: str,
    filename: str,
    relative_path: str,
    size_bytes: int,
    bounds: list[float],
    meta: dict[str, Any],
) -> None:
    """Save an artifact record to the database."""
    async with AsyncSessionLocal() as session:
        artifact = Artifact(
            task_id=task_id,
            kind=kind,
            filename=filename,
            relative_path=relative_path,
            size_bytes=size_bytes,
            bounds=bounds,
            meta=meta,
        )
        session.add(artifact)
        await session.commit()


@agent.tool
async def resolve_area(ctx: RunContext[RunDeps], place: str) -> str:
    """Resolve a place name to the session's area of interest.

    Call this first when the user names a new place.
    """
    try:
        aoi_data = await geocode(place)
    except Exception as e:
        return f"Could not find location '{place}': {e}"

    ctx.deps.aoi = aoi_data
    ctx.deps.aoi_changed = True

    name = aoi_data["name"]
    area_km2 = aoi_data["area_km2"]
    await log_event(ctx.deps.task_id, "geocoding", f"Resolved to {name} ({area_km2} km²)")
    return f"Resolved to {name} — {area_km2} km²."


@agent.tool
async def extract_vector(ctx: RunContext[RunDeps], layers: list[Layer]) -> str:
    """Download OpenStreetMap data for the current area of interest.

    layers: boundary | buildings | roads | waterways | landuse | amenities | natural
    """
    if not ctx.deps.aoi:
        return "No area of interest set. Please specify a location first."

    aoi = ctx.deps.aoi
    area_km2 = aoi.get("area_km2", 0.0)
    has_non_boundary = any(lyr != "boundary" for lyr in layers)

    if area_km2 > settings.vector_max_area_km2 and has_non_boundary:
        return (
            f"Area is {area_km2:.1f} km², which exceeds the maximum allowed "
            f"{settings.vector_max_area_km2} km² for detailed vector extraction. "
            "Please narrow your area of interest or request only the boundary."
        )

    await log_event(
        ctx.deps.task_id, "vector", f"Extracting {', '.join(layers)} for {aoi.get('name')}"
    )

    slug = slugify(aoi.get("name", "aoi"))
    task_dir = settings.runs_dir / ctx.deps.task_id / "vector"
    task_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    summaries = []

    for layer in layers:
        try:
            artifacts, refined_aoi = await asyncio.to_thread(
                extract_vector_layer,
                aoi=aoi,
                layer=layer,
                output_dir=task_dir,
                slug=slug,
            )
        except Exception as e:
            return f"Failed to extract layer '{layer}': {e}"

        for art in artifacts:
            rel_path = f"runs/{ctx.deps.task_id}/vector/{art['filename']}"
            await record_artifact(
                task_id=ctx.deps.task_id,
                kind="vector",
                filename=art["filename"],
                relative_path=rel_path,
                size_bytes=art["size_bytes"],
                bounds=art["bounds"],
                meta=art["meta"],
            )
            total_files += 1

        feature_count = artifacts[0]["meta"]["feature_count"] if artifacts else 0
        summaries.append(f"{feature_count:,} {layer}")

        if refined_aoi:
            ctx.deps.aoi = refined_aoi
            ctx.deps.aoi_changed = True

    return f"Extracted {', '.join(summaries)}. Wrote {total_files} files."


@agent.tool
async def fetch_imagery(
    ctx: RunContext[RunDeps],
    start_date: str,
    end_date: str,
    products: list[Product],
    max_cloud_cover: int = 20,
) -> str:
    """Fetch Sentinel-2 imagery for the current area of interest.

    products: true_color | ndvi. Dates are ISO YYYY-MM-DD.
    """
    if not ctx.deps.aoi:
        return "No area of interest set. Please specify a location first."

    # Validate ISO date strings
    for d_str in (start_date, end_date):
        try:
            datetime.strptime(d_str, "%Y-%m-%d")
        except ValueError:
            return f"Invalid date format '{d_str}'. Must be ISO format YYYY-MM-DD."

    aoi = ctx.deps.aoi
    await log_event(
        ctx.deps.task_id,
        "raster",
        f"Searching Sentinel-2 imagery for {aoi.get('name')} ({start_date} to {end_date})",
    )

    slug = slugify(aoi.get("name", "aoi"))
    task_dir = settings.runs_dir / ctx.deps.task_id / "raster"
    task_dir.mkdir(parents=True, exist_ok=True)

    results_info = []

    for product in products:
        try:
            artifact, resolution, best_day, avg_cloud = await asyncio.to_thread(
                fetch_raster_product,
                aoi=aoi,
                start_date=start_date,
                end_date=end_date,
                product=product,
                output_dir=task_dir,
                slug=slug,
                max_cloud_cover=max_cloud_cover,
            )
        except Exception as e:
            return f"Failed to fetch imagery: {e}"

        rel_path = f"runs/{ctx.deps.task_id}/raster/{artifact['filename']}"
        await record_artifact(
            task_id=ctx.deps.task_id,
            kind="raster",
            filename=artifact["filename"],
            relative_path=rel_path,
            size_bytes=artifact["size_bytes"],
            bounds=artifact["bounds"],
            meta=artifact["meta"],
        )

        results_info.append((product, resolution, best_day, avg_cloud))
        await log_event(
            ctx.deps.task_id,
            "raster",
            f"Generated {product} at {resolution}m ({avg_cloud:.1f}% cloud)",
        )

    if not results_info:
        return "No products generated."

    _, res, day, cloud = results_info[0]
    return f"Composited scenes from {day} ({cloud:.1f}% cloud) at {res} m."
