import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_db
from app.models import Artifact, ChatSession, Message, Task
from app.schemas import (
    AOISchema,
    ArtifactItem,
    MessageCreateRequest,
    MessageCreateResponse,
    MessageResponse,
    SessionResponse,
)
from app.tasks import ACTIVE, STATUS_QUEUED, run_task, stream_task_events

router = APIRouter(prefix="/api")
_background_tasks: set[asyncio.Task[Any]] = set()

MIME_TYPES = {
    ".geojson": "application/geo+json",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".png": "image/png",
}


async def get_session_or_404(db: AsyncSession, session_id: str) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(db: AsyncSession = Depends(get_db)):
    session = ChatSession()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionResponse(
        id=session.id,
        aoi=None,
        created_at=session.created_at,
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await get_session_or_404(db, session_id)

    aoi = None
    if session.aoi_name and session.aoi_geometry and session.aoi_bbox is not None:
        aoi = AOISchema(
            name=session.aoi_name,
            source=session.aoi_source or "unknown",
            area_km2=session.aoi_area_km2 or 0.0,
            bbox=session.aoi_bbox,
            geometry=session.aoi_geometry,
        )

    return SessionResponse(
        id=session.id,
        aoi=aoi,
        created_at=session.created_at,
    )


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def get_messages(session_id: str, db: AsyncSession = Depends(get_db)):
    await get_session_or_404(db, session_id)

    # the task status lets the page show replies of failed tasks as errors
    stmt = (
        select(Message, Task.status)
        .outerjoin(Task, Message.task_id == Task.id)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        MessageResponse(
            id=m.id,
            session_id=m.session_id,
            task_id=m.task_id,
            task_status=status,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
        )
        for m, status in rows
    ]


@router.post(
    "/sessions/{session_id}/messages", response_model=MessageCreateResponse, status_code=202
)
async def submit_message(
    session_id: str,
    payload: MessageCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    await get_session_or_404(db, session_id)

    running = await db.execute(
        select(Task.id).where(Task.session_id == session_id, Task.status.in_(ACTIVE)).limit(1)
    )
    if running.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="A task is already running for this session. Please wait for it to finish.",
        )

    task = Task(session_id=session_id, status=STATUS_QUEUED, prompt=payload.content)
    db.add(task)
    await db.flush()
    message = Message(session_id=session_id, task_id=task.id, role="user", content=payload.content)
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


@router.get("/tasks/{task_id}/events")
async def get_task_events(task_id: str, request: Request):
    # No Depends(get_db) here: it would hold a pooled connection for the whole stream.
    async with SessionLocal() as db:
        found = (await db.execute(select(Task.id).where(Task.id == task_id))).scalar_one_or_none()
    if not found:
        raise HTTPException(status_code=404, detail="Task not found")

    return StreamingResponse(
        stream_task_events(task_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tasks/{task_id}/artifacts", response_model=list[ArtifactItem])
async def get_task_artifacts(task_id: str, db: AsyncSession = Depends(get_db)):
    task_res = await db.execute(select(Task.id).where(Task.id == task_id))
    if not task_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Task not found")

    stmt = select(Artifact).where(Artifact.task_id == task_id).order_by(Artifact.created_at.asc())
    artifacts = (await db.execute(stmt)).scalars().all()

    return [
        ArtifactItem(
            id=art.id,
            kind=art.kind,
            filename=art.filename,
            size_bytes=art.size_bytes,
            bounds=art.bounds,
            meta=art.meta,
            download_url=f"/api/artifacts/{art.id}/download",
        )
        for art in artifacts
    ]


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Artifact).where(Artifact.id == artifact_id))
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

    media_type = MIME_TYPES.get(target_path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path=target_path,
        filename=artifact.filename,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )
