"""The three tools the model can call.

Their arguments are the trust boundary: a place name, a list of layer names and a date range.
No tool takes coordinates, geometry or a file path, so the model can't invent any.
"""

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import ModelRetry, RunContext
from sqlalchemy import update

from app.config import settings
from app.db import SessionLocal
from app.geo.geocode import geocode
from app.geo.ndvi import fetch_ndvi_product
from app.geo.types import GeoArtifact
from app.geo.vector import Layer, extract_vector_layer
from app.models import Artifact, Session
from app.tasks.state import log_event

logger = logging.getLogger("geoagent.tools")


@dataclass
class RunDeps:
    task_id: str
    session_id: str
    aoi: dict[str, Any] | None


def slugify(text: str) -> str:
    """Create a filename-safe slug from a string."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[-\s]+", "_", text)[:40] or "aoi"


async def record_artifact(task_id: str, art: GeoArtifact) -> None:
    async with SessionLocal() as session:
        session.add(Artifact(task_id=task_id, **asdict(art)))
        await session.commit()


async def save_session_aoi(session_id: str, aoi: dict[str, Any]) -> None:
    """Save the AOI as soon as it is resolved, so a later failure in the same run keeps it."""
    async with SessionLocal() as session:
        await session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                aoi_name=aoi["name"],
                aoi_source=aoi["source"],
                aoi_geometry=aoi["geometry"],
                aoi_bbox=aoi["bbox"],
                aoi_area_km2=aoi["area_km2"],
            )
        )
        await session.commit()


async def resolve_area(
    ctx: RunContext[RunDeps],
    place: Annotated[str, Field(max_length=200, description="The place or city to search for")],
) -> str:
    """Resolve a place name to the session's area of interest.

    Call this first when the user names a new place.
    """
    try:
        aoi = asdict(await geocode(place))
    except ValueError:
        return f"No place found for '{place}'. Ask the user for a more specific name."
    except RuntimeError as e:
        logger.warning("task=%s geocoding failed: %s", ctx.deps.task_id, e)
        return "The geocoding service is not responding right now. Try again later."

    ctx.deps.aoi = aoi
    await save_session_aoi(ctx.deps.session_id, aoi)

    # the resolved name and size go back to the user, so a wrong guess of place is visible
    summary = f"Resolved to {aoi['name']} ({aoi['area_km2']} km²)"
    await log_event(ctx.deps.task_id, "geocoding", summary)
    return summary + "."


async def extract_vector(
    ctx: RunContext[RunDeps],
    layers: Annotated[
        list[Layer],
        Field(min_length=1, max_length=7, description="Vector layers to extract"),
    ],
) -> str:
    """Download OpenStreetMap data for the current area of interest.

    layers: boundary | buildings | roads | waterways | landuse | amenities | natural
    """
    aoi = ctx.deps.aoi
    if not aoi:
        return "No area of interest set. Please specify a location first."

    unique_layers = list(dict.fromkeys(layers))
    area_km2 = aoi["area_km2"]
    if area_km2 > settings.vector_max_area_km2 and unique_layers != ["boundary"]:
        logger.warning("task=%s area too large for vector layers", ctx.deps.task_id)
        return (
            f"Area is {area_km2:.1f} km², which exceeds the maximum allowed "
            f"{settings.vector_max_area_km2} km² for detailed vector extraction. "
            "Please narrow your area of interest or request only the boundary."
        )

    await log_event(
        ctx.deps.task_id, "vector", f"Extracting {', '.join(unique_layers)} for {aoi['name']}"
    )

    slug = slugify(aoi["name"])
    task_dir = settings.runs_dir / ctx.deps.task_id / "vector"
    summaries = []
    failures = []

    for layer in unique_layers:
        try:
            art = await asyncio.to_thread(
                extract_vector_layer,
                aoi=aoi,
                layer=layer,
                output_dir=task_dir,
                slug=slug,
                task_id=ctx.deps.task_id,
            )
        except ValueError as e:
            logger.warning("task=%s layer %s refused: %s", ctx.deps.task_id, layer, e)
            failures.append(f"{layer} failed (too many features, try a smaller area)")
            continue
        except (RuntimeError, OSError) as e:
            logger.warning("task=%s layer %s failed: %s", ctx.deps.task_id, layer, e)
            failures.append(f"{layer} failed (OpenStreetMap download error)")
            continue
        except Exception:
            logger.exception("task=%s layer %s crashed", ctx.deps.task_id, layer)
            failures.append(f"{layer} failed (unexpected error)")
            continue

        await record_artifact(ctx.deps.task_id, art)
        summaries.append(f"{art.meta['feature_count']:,} {layer}")

    parts = []
    if summaries:
        parts.append(f"Extracted {', '.join(summaries)}.")
    if failures:
        parts.append(f"{'; '.join(failures)}.")
    return " ".join(parts)


async def fetch_imagery(
    ctx: RunContext[RunDeps],
    start_date: date,
    end_date: date,
    max_cloud_cover: Annotated[int, Field(ge=0, le=100)] = 20,
) -> str:
    """Fetch Sentinel-2 NDVI imagery for the current area of interest.

    Dates must be ISO format YYYY-MM-DD.
    """
    aoi = ctx.deps.aoi
    if not aoi:
        return "No area of interest set. Please specify a location first."

    # ModelRetry sends the message back to the model so it can fix its arguments
    if start_date > end_date:
        raise ModelRetry("start_date must be on or before end_date.")
    if (end_date - start_date).days > 366:
        raise ModelRetry("The date range can be at most 366 days.")

    await log_event(
        ctx.deps.task_id,
        "raster",
        f"Searching Sentinel-2 imagery for {aoi['name']} ({start_date} to {end_date})",
    )

    try:
        ndvi = await asyncio.to_thread(
            fetch_ndvi_product,
            aoi=aoi,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            output_dir=settings.runs_dir / ctx.deps.task_id / "raster",
            slug=slugify(aoi["name"]),
            max_cloud_cover=max_cloud_cover,
            task_id=ctx.deps.task_id,
        )
    except ValueError as e:
        logger.warning("task=%s no NDVI: %s", ctx.deps.task_id, e)
        return (
            "No clear Sentinel-2 scene for that area and dates, or the area is too large. "
            "Try a wider date range, a higher cloud limit or a smaller area."
        )
    except (RuntimeError, OSError) as e:
        logger.warning("task=%s imagery failed: %s", ctx.deps.task_id, e)
        return "The imagery service failed. Try again later."
    except Exception:
        logger.exception("task=%s imagery crashed", ctx.deps.task_id)
        return "Imagery processing failed unexpectedly. Try a different date range or area."

    await record_artifact(ctx.deps.task_id, ndvi.artifact)
    await record_artifact(ctx.deps.task_id, ndvi.preview_artifact)
    await log_event(
        ctx.deps.task_id,
        "raster",
        f"Generated NDVI at {ndvi.resolution} m ({ndvi.cloud_cover:.1f}% cloud)",
    )

    return (
        f"Composited NDVI from {ndvi.scene_date} ({ndvi.cloud_cover:.1f}% cloud) "
        f"at {ndvi.resolution} m. Mean NDVI: {ndvi.artifact.meta['mean_ndvi']}."
    )
