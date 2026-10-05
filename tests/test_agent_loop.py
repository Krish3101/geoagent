"""The real agent loop, driven offline by a FunctionModel instead of an LLM."""

import asyncio
import re

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.agent import agent, system_prompt
from app.config import settings
from app.geo.types import GeoArtifact, GeocodeResult, NDVIResult
from app.models import Artifact, Message, Session, Task, TaskEvent
from app.tasks import runner
from app.tasks.runner import run_task

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[4.81, 52.31], [4.92, 52.31], [4.92, 52.42], [4.81, 52.42], [4.81, 52.31]]],
}
BBOX = [4.81, 52.31, 4.92, 52.42]
FORBIDDEN_NAME = re.compile(r"bbox|coord|geom|path|lat|lon|wkt|file", re.IGNORECASE)


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    # run_task refuses to start without a key; the FunctionModel never uses it
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")


async def make_task(factory, prompt: str = "test", aoi: bool = False) -> str:
    async with factory() as db:
        s = Session()
        if aoi:
            s.aoi_name, s.aoi_source, s.aoi_area_km2 = "Amsterdam", "geocoded", 50.0
            s.aoi_geometry, s.aoi_bbox = SQUARE, BBOX
        db.add(s)
        await db.flush()
        t = Task(session_id=s.id, status="queued", prompt=prompt)
        db.add(t)
        await db.commit()
        return t.id


async def load(factory, task_id: str):
    async with factory() as db:
        task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        reply = (await db.execute(select(Message).where(Message.task_id == task_id))).scalar_one()
        seqs = (
            await db.execute(
                select(TaskEvent.seq).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
            )
        ).scalars()
        arts = (await db.execute(select(Artifact).where(Artifact.task_id == task_id))).scalars()
        return task, reply, list(seqs), list(arts)


def script(*responses: list):
    """A fake model that returns the given tool calls one turn at a time, then 'done'."""
    turns = list(responses)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if turns:
            return ModelResponse(parts=turns.pop(0))
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(respond)


def fake_geocode_result(place: str) -> GeocodeResult:
    return GeocodeResult(name=place, source="geocoded", geometry=SQUARE, bbox=BBOX, area_km2=50.0)


def fake_vector(aoi, layer, output_dir, slug, task_id=""):
    return GeoArtifact(
        kind="vector",
        filename=f"{slug}_{layer}.geojson",
        relative_path=f"runs/{task_id}/vector/{slug}_{layer}.geojson",
        size_bytes=100,
        bounds=BBOX,
        meta={"layer": layer, "feature_count": 3, "crs": "EPSG:4326"},
    )


def test_tool_schema_has_no_coordinate_or_path_parameters():
    tools = agent._function_toolset.tools
    assert set(tools) == {"resolve_area", "extract_vector", "fetch_imagery"}

    for tool_name, tool in tools.items():
        for prop_name, prop in tool.tool_def.parameters_json_schema["properties"].items():
            assert not FORBIDDEN_NAME.search(prop_name), f"{tool_name}.{prop_name}"
            # a list of numbers would be a coordinate pair or a bbox
            if prop.get("type") == "array":
                assert prop["items"].get("type") not in ("number", "integer")


def test_tools_are_marked_sequential():
    for tool_name, tool in agent._function_toolset.tools.items():
        assert tool.sequential is True, tool_name


def test_prompt_has_no_aoi_coordinates():
    aoi = {"name": "Amsterdam", "source": "geocoded", "area_km2": 50.0, "bbox": BBOX}
    prompt = system_prompt("2026-10-05", aoi)
    assert "Amsterdam" in prompt
    for value in BBOX:
        assert str(value) not in prompt


async def test_parallel_tool_calls_run_in_order_with_gapless_events(test_env, monkeypatch):
    async def geocode(place):
        return fake_geocode_result(place)

    monkeypatch.setattr("app.tools.geocode", geocode)
    monkeypatch.setattr("app.tools.extract_vector_layer", fake_vector)
    factory = test_env["session_factory"]
    task_id = await make_task(factory)

    # the model asks for everything in one response, as some models do despite the setting
    model = script(
        [
            ToolCallPart("resolve_area", {"place": "Soho"}),
            ToolCallPart("extract_vector", {"layers": ["buildings"]}),
            ToolCallPart("extract_vector", {"layers": ["roads"]}),
            ToolCallPart("extract_vector", {"layers": ["boundary"]}),
        ]
    )
    with agent.override(model=model):
        await run_task(task_id)

    task, reply, seqs, arts = await load(factory, task_id)
    assert task.status == "succeeded"
    assert reply.content == "done"
    # starting, geocoding, 3 x vector, done
    assert seqs == [1, 2, 3, 4, 5, 6]
    assert sorted(a.meta["layer"] for a in arts) == ["boundary", "buildings", "roads"]


async def test_slow_tool_hits_the_deadline_and_frees_the_slot(test_env, monkeypatch):
    async def slow_geocode(place):
        await asyncio.sleep(10)

    monkeypatch.setattr("app.tools.geocode", slow_geocode)
    monkeypatch.setattr(settings, "task_timeout_s", 1)
    factory = test_env["session_factory"]
    task_id = await make_task(factory)

    with agent.override(model=script([ToolCallPart("resolve_area", {"place": "Paris"})])):
        await asyncio.wait_for(run_task(task_id), timeout=5)

    task, reply, _, _ = await load(factory, task_id)
    assert task.status == "failed"
    assert task.error == "timeout"
    assert "timed out" in reply.content
    assert runner._task_semaphore._value == 2


async def test_looping_model_stops_at_the_usage_limit(test_env, monkeypatch):
    async def geocode(place):
        return fake_geocode_result(place)

    monkeypatch.setattr("app.tools.geocode", geocode)
    factory = test_env["session_factory"]
    task_id = await make_task(factory)

    def always_call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("resolve_area", {"place": "Paris"})])

    with agent.override(model=FunctionModel(always_call)):
        await run_task(task_id)

    task, reply, _, _ = await load(factory, task_id)
    assert task.status == "failed"
    assert task.error == "usage_limit"
    assert (
        reply.content == "That request needed too many steps. Please ask for less in one message."
    )


async def test_bad_dates_are_sent_back_to_the_model(test_env, monkeypatch):
    calls = []

    def fake_ndvi(aoi, start_date, end_date, output_dir, slug, max_cloud_cover, task_id):
        calls.append((start_date, end_date))
        tif = GeoArtifact("raster", "a_ndvi.tif", "runs/x/a_ndvi.tif", 10, BBOX, {"mean_ndvi": 0.4})
        png = GeoArtifact("raster", "a_ndvi_preview.png", "runs/x/a_ndvi_preview.png", 5, BBOX)
        return NDVIResult(tif, png, resolution=10, scene_date="2025-07-14", cloud_cover=3.2)

    monkeypatch.setattr("app.tools.fetch_ndvi_product", fake_ndvi)
    factory = test_env["session_factory"]
    task_id = await make_task(factory, aoi=True)

    retries = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        if isinstance(last, ModelRequest):
            retries.extend(p for p in last.parts if isinstance(p, RetryPromptPart))
        step = len(messages)
        if step == 1:  # start after end
            args = {"start_date": "2025-07-31", "end_date": "2025-07-01"}
        elif step == 3:
            args = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
        else:
            return ModelResponse(parts=[TextPart("NDVI ready")])
        return ModelResponse(parts=[ToolCallPart("fetch_imagery", args)])

    with agent.override(model=FunctionModel(respond)):
        await run_task(task_id)

    task, reply, _, arts = await load(factory, task_id)
    assert len(retries) == 1
    assert "on or before end_date" in str(retries[0].content)
    assert calls == [("2025-07-01", "2025-07-31")]
    assert task.status == "succeeded"
    assert reply.content == "NDVI ready"
    assert len(arts) == 2


async def test_model_sees_no_coordinates_and_no_failure_text(test_env):
    factory = test_env["session_factory"]
    task_id = await make_task(factory, "Now the roads", aoi=True)
    async with factory() as db:
        task = await db.get(Task, task_id)
        failed = Task(session_id=task.session_id, status="failed", prompt="x")
        db.add(failed)
        await db.flush()
        db.add(Message(session_id=task.session_id, task_id=failed.id, role="user", content="x"))
        db.add(
            Message(
                session_id=task.session_id,
                task_id=failed.id,
                role="assistant",
                content="Something went wrong (ref: 123).",
            )
        )
        await db.commit()

    seen = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(repr(messages))
        return ModelResponse(parts=[TextPart("ok")])

    with agent.override(model=FunctionModel(respond)):
        await run_task(task_id)

    assert "Amsterdam" in seen[0]
    assert "Something went wrong" not in seen[0]
    for value in BBOX:
        assert str(value) not in seen[0]


async def test_database_error_in_a_tool_is_not_shown_to_the_user(test_env, monkeypatch):
    async def broken_geocode(place):
        raise OperationalError("SELECT secret FROM sessions", {}, Exception("disk I/O error"))

    monkeypatch.setattr("app.tools.geocode", broken_geocode)
    factory = test_env["session_factory"]
    task_id = await make_task(factory)

    with agent.override(model=script([ToolCallPart("resolve_area", {"place": "Paris"})])):
        await run_task(task_id)

    task, reply, _, _ = await load(factory, task_id)
    assert task.status == "failed"
    assert task.error == "internal_error"
    assert reply.content == f"Something went wrong (ref: {task_id})."
    assert "SELECT" not in reply.content
