import asyncio
import json
from typing import Any

import shapely.geometry
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from shapely.validation import make_valid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.geo.geocode import compute_geodesic_area_km2
from app.models import Artifact, Message, Session, Task, TaskEvent
from app.runner import STATUS_FAILED, STATUS_QUEUED, STATUS_SUCCEEDED, run_task
from app.schemas import (
    AOISchema,
    ArtifactGroupedResponse,
    ArtifactItem,
    MessageCreateRequest,
    MessageCreateResponse,
    MessageResponse,
    SessionCreateResponse,
    SessionDetailResponse,
    TaskResponse,
)

router = APIRouter(prefix="/api")
_background_tasks: set[asyncio.Task[Any]] = set()


@router.post("/sessions", response_model=SessionCreateResponse, status_code=201)
async def create_session(db: AsyncSession = Depends(get_db)):
    session = Session()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionCreateResponse(
        id=session.id,
        aoi=None,
        created_at=session.created_at,
    )


@router.get("/sessions/{sid}", response_model=SessionDetailResponse)
async def get_session(sid: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Session).where(Session.id == sid))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    aoi = None
    if session.aoi_name and session.aoi_geometry and session.aoi_bbox is not None:
        aoi = AOISchema(
            name=session.aoi_name,
            source=session.aoi_source or "unknown",
            area_km2=session.aoi_area_km2 or 0.0,
            bbox=session.aoi_bbox,
            geometry=session.aoi_geometry,
        )

    return SessionDetailResponse(
        id=session.id,
        aoi=aoi,
        created_at=session.created_at,
    )


@router.get("/sessions/{sid}/messages", response_model=list[MessageResponse])
async def get_messages(sid: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Session).where(Session.id == sid))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    stmt = select(Message).where(Message.session_id == sid).order_by(Message.created_at.asc())
    messages = (await db.execute(stmt)).scalars().all()
    return [
        MessageResponse(
            id=m.id,
            session_id=m.session_id,
            task_id=m.task_id,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.post("/sessions/{sid}/messages", response_model=MessageCreateResponse, status_code=202)
async def submit_message(
    sid: str,
    payload: MessageCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Session).where(Session.id == sid))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    task = Task(
        session_id=sid,
        status=STATUS_QUEUED,
        prompt=payload.content,
    )
    db.add(task)
    await db.flush()

    message = Message(
        session_id=sid,
        task_id=task.id,
        role="user",
        content=payload.content,
    )
    db.add(message)
    await db.commit()

    background_task = asyncio.create_task(run_task(task.id))
    _background_tasks.add(background_task)
    background_task.add_done_callback(_background_tasks.discard)

    return MessageCreateResponse(
        task_id=task.id,
        message_id=message.id,
        status=STATUS_QUEUED,
        events_url=f"/api/tasks/{task.id}/events",
    )


def validate_coords_wgs84(coords: Any) -> None:
    """Recursively validate that all coordinates are within WGS84 range."""
    if not isinstance(coords, (list, tuple)):
        return
    is_num_pair = (
        len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    )
    if is_num_pair:
        lon, lat = float(coords[0]), float(coords[1])
        if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
            raise ValueError(f"Coordinate [{lon}, {lat}] is out of WGS84 range")
    else:
        for item in coords:
            validate_coords_wgs84(item)


@router.put("/sessions/{sid}/aoi", response_model=AOISchema, status_code=200)
async def upload_aoi(
    sid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Session).where(Session.id == sid))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")

    if not body or not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Payload must be a GeoJSON object")

    try:
        geom_type = body.get("type")
        features_list = []

        if geom_type == "FeatureCollection":
            raw_features = body.get("features")
            if not raw_features or not isinstance(raw_features, list):
                raise ValueError("FeatureCollection must contain features")
            for feat in raw_features:
                if not isinstance(feat, dict) or not feat.get("geometry"):
                    raise ValueError("Invalid feature in FeatureCollection")
                validate_coords_wgs84(feat["geometry"].get("coordinates"))
                features_list.append(feat["geometry"])
        elif geom_type == "Feature":
            geom = body.get("geometry")
            if not geom or not isinstance(geom, dict):
                raise ValueError("Feature must have geometry")
            validate_coords_wgs84(geom.get("coordinates"))
            features_list.append(geom)
        elif geom_type in ("Polygon", "MultiPolygon", "Point", "LineString", "MultiLineString"):
            validate_coords_wgs84(body.get("coordinates"))
            features_list.append(body)
        else:
            raise ValueError(f"Unsupported GeoJSON type: {geom_type}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        shapely_geoms = [shapely.geometry.shape(g) for g in features_list]
        shapely_geoms = [make_valid(g) for g in shapely_geoms if not g.is_empty]
        if not shapely_geoms:
            raise ValueError("No valid geometry found in GeoJSON")

        if len(shapely_geoms) == 1:
            combined = shapely_geoms[0]
        else:
            combined = shapely.unary_union(shapely_geoms)
            combined = make_valid(combined)

        if combined.is_empty:
            raise ValueError("Resulting geometry is empty")

        bbox = [round(b, 6) for b in combined.bounds]
        area_km2 = compute_geodesic_area_km2(combined)
        geom_mapping = shapely.geometry.mapping(combined)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid geometry: {e}")

    session.aoi_name = "Uploaded area"
    session.aoi_source = "uploaded"
    session.aoi_geometry = geom_mapping
    session.aoi_bbox = bbox
    session.aoi_area_km2 = area_km2

    system_msg = Message(
        session_id=sid,
        role="system",
        content=f"Area of interest updated to Uploaded area ({area_km2} km²)",
    )
    db.add(system_msg)
    await db.commit()

    return AOISchema(
        name=session.aoi_name,
        source=session.aoi_source,
        area_km2=session.aoi_area_km2,
        bbox=session.aoi_bbox,
        geometry=session.aoi_geometry,
    )


@router.get("/tasks/{tid}", response_model=TaskResponse)
async def get_task(tid: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).where(Task.id == tid))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    art_count_stmt = select(func.count(Artifact.id)).where(Artifact.task_id == tid)
    artifact_count = (await db.execute(art_count_stmt)).scalar() or 0

    return TaskResponse(
        id=task.id,
        session_id=task.session_id,
        status=task.status,
        prompt=task.prompt,
        error=task.error,
        created_at=task.created_at,
        finished_at=task.finished_at,
        artifact_count=artifact_count,
    )


@router.get("/tasks/{tid}/events")
async def get_task_events(
    tid: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == tid))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        last_seq = after_seq
        terminal_stages = {"done", "error"}

        while True:
            if await request.is_disconnected():
                break

            stmt = (
                select(TaskEvent)
                .where(TaskEvent.task_id == tid, TaskEvent.seq > last_seq)
                .order_by(TaskEvent.seq.asc())
            )
            events = (await db.execute(stmt)).scalars().all()

            reached_terminal = False
            for ev in events:
                last_seq = ev.seq
                data = {
                    "seq": ev.seq,
                    "stage": ev.stage,
                    "message": ev.message,
                    "created_at": ev.created_at.isoformat(),
                }
                yield f"data: {json.dumps(data)}\n\n"
                if ev.stage in terminal_stages:
                    reached_terminal = True

            if reached_terminal:
                break

            # Check task status in DB
            task_chk = await db.execute(select(Task.status).where(Task.id == tid))
            curr_status = task_chk.scalar_one_or_none()
            if curr_status in (STATUS_SUCCEEDED, STATUS_FAILED) and not events:
                # If task is finished and no more events exist, close stream
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tasks/{tid}/artifacts", response_model=ArtifactGroupedResponse)
async def get_task_artifacts(tid: str, db: AsyncSession = Depends(get_db)):
    task_res = await db.execute(select(Task).where(Task.id == tid))
    if not task_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Task not found")

    stmt = select(Artifact).where(Artifact.task_id == tid).order_by(Artifact.created_at.asc())
    artifacts = (await db.execute(stmt)).scalars().all()

    grouped = ArtifactGroupedResponse()
    for art in artifacts:
        item = ArtifactItem(
            id=art.id,
            filename=art.filename,
            size_bytes=art.size_bytes,
            bounds=art.bounds,
            meta=art.meta,
            download_url=f"/api/artifacts/{art.id}/download",
        )
        if art.kind == "vector":
            grouped.vector.append(item)
        elif art.kind == "raster":
            grouped.raster.append(item)

    return grouped


@router.get("/artifacts/{aid}/download")
async def download_artifact(aid: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Artifact).where(Artifact.id == aid))
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Path traversal protection: ensure file is inside runs_dir
    runs_dir = settings.runs_dir.resolve()
    rel = artifact.relative_path
    if rel.startswith("runs/"):
        rel = rel[5:]
    target_path = (runs_dir / rel).resolve()

    try:
        target_path.relative_to(runs_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: invalid file path")

    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")

    is_geojson = target_path.suffix == ".geojson"
    media_type = "application/geo+json" if is_geojson else "application/octet-stream"
    return FileResponse(
        path=target_path,
        filename=artifact.filename,
        media_type=media_type,
    )
