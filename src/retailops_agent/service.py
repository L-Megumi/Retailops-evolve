from __future__ import annotations

import asyncio
import hmac
import json
import os
import threading
import uuid
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent import RetailOpsAgent
from .evolution import CapabilityEvolver
from .llm import OpenAICompatibleModel
from .planner import ModelPlanner
from .plugin_manager import PluginManager
from .task_store import TaskStore


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=10000)
    mode: Literal["analysis", "assistant"] = "analysis"
    orders: str | None = Field(default=None, min_length=1, max_length=240)
    refunds: str | None = Field(default=None, min_length=1, max_length=240)
    platforms: list[Literal["jd", "suning", "retailops"]] = Field(default_factory=list, max_length=3)
    business_scope: Literal["marketplace", "self_operated", "platform", "internal"] | None = None
    as_of: str | None = None
    required_results: list[str] = Field(default_factory=list, max_length=8)
    planner: Literal["rule", "model"] = "rule"
    evolve: bool = False

    @model_validator(mode="after")
    def check_inputs(self):
        if self.mode == "analysis" and (not self.orders or not self.refunds):
            raise ValueError("analysis mode requires orders and refunds")
        if bool(self.orders) != bool(self.refunds):
            raise ValueError("orders and refunds must be supplied together")
        if self.as_of is not None:
            date.fromisoformat(self.as_of)
        return self


def _csv_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate.suffix.lower() != ".csv":
        raise ValueError("CSV paths must remain inside the configured data root")
    if not candidate.is_file() or candidate.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("CSV is missing or exceeds 4 MiB")
    return candidate


class TaskRunner:
    def __init__(self, store: TaskStore, factory, data_root: Path, workers: int = 2):
        self.store, self.factory, self.data_root = store, factory, data_root
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="retailops")
        self.capacity = threading.BoundedSemaphore(64)
        self.scheduled: set[str] = set()
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.dispatcher = None

    def start(self):
        def dispatch():
            while not self.stopping.wait(0.25):
                self.store.recover_stale()
                for task_id in self.store.queued():
                    if not self.submit(task_id):
                        break
        self.dispatcher = threading.Thread(target=dispatch, daemon=True)
        self.dispatcher.start()

    def submit(self, task_id: str) -> bool:
        with self.lock:
            if task_id in self.scheduled:
                return True
            if self.stopping.is_set() or not self.capacity.acquire(blocking=False):
                return False
            self.scheduled.add(task_id)
            try:
                self.executor.submit(self._run, task_id)
            except RuntimeError:
                self.scheduled.remove(task_id)
                self.capacity.release()
                raise
        return True

    def _run(self, task_id: str):
        owner = uuid.uuid4().hex
        stop = threading.Event()
        heartbeat = None
        try:
            if not self.store.claim(task_id, owner):
                return
            def pulse():
                while not stop.wait(15):
                    self.store.heartbeat(task_id, owner)
            heartbeat = threading.Thread(target=pulse, daemon=True)
            heartbeat.start()
            request = self.store.get(task_id)["request"]
            agent = self.factory(request)
            kwargs = {"required_results": tuple(request.get("required_results", [])),
                      "on_event": lambda event: self.store.append(task_id, event, owner)}
            if request.get("mode") == "assistant":
                kwargs.update(platforms=tuple(request.get("platforms", [])),
                              business_scope=request.get("business_scope"), as_of=request.get("as_of"))
            orders = _csv_path(self.data_root, request["orders"]) if request.get("orders") else None
            refunds = _csv_path(self.data_root, request["refunds"]) if request.get("refunds") else None
            trace = agent.run(request["task"], orders, refunds, **kwargs)
            payload = json.loads(json.dumps(asdict(trace), default=lambda value: str(value) if isinstance(value, Decimal) else value))
            payload["ok"] = trace.ok
            self.store.finish(task_id, owner, result=payload)
        except Exception as exc:
            self.store.finish(task_id, owner, error=f"{type(exc).__name__}: {str(exc)[:500]}")
        finally:
            stop.set()
            if heartbeat:
                heartbeat.join(timeout=1)
            with self.lock:
                self.scheduled.discard(task_id)
            self.capacity.release()

    def close(self):
        self.stopping.set()
        if self.dispatcher:
            self.dispatcher.join(timeout=2)
        self.executor.shutdown(wait=True)


def create_app(*, data_root: Path, manifest: Path, state_path: Path,
               allow_evolution: bool = False, agent_factory=None, token: str | None = None,
               knowledge_path: Path | None = None, embedding_model: str | None = None,
               retrieval_mode: str = "hybrid") -> FastAPI:
    store = TaskStore(state_path)
    token = token if token is not None else os.environ.get("RETAILOPS_SERVICE_TOKEN", "")
    knowledge = None
    if knowledge_path is not None:
        from .knowledge import FastEmbedEncoder, KnowledgeStore
        encoder = FastEmbedEncoder(embedding_model) if embedding_model else None
        knowledge = KnowledgeStore.from_path(knowledge_path, encoder=encoder)

    def factory(request):
        needs_model = request["planner"] == "model" or request["evolve"]
        model = OpenAICompatibleModel.deepseek_from_env() if needs_model else None
        manager = PluginManager.from_manifest(manifest, semantic_model=model,
                                             cache_path=state_path.parent / "route-cache.json")
        analysis = RetailOpsAgent(ModelPlanner(model) if request["planner"] == "model" else None,
                              plugin_manager=manager,
                              evolver=CapabilityEvolver(model, state_path.parent / "evolution") if request["evolve"] else None)
        if request.get("mode") == "assistant":
            from .assistant import RetailOpsAssistant
            return RetailOpsAssistant(knowledge, analysis_agent=analysis, model=model,
                                      retrieval_mode=retrieval_mode)
        return analysis

    runner = TaskRunner(store, agent_factory or factory, data_root)

    @asynccontextmanager
    async def lifespan(app):
        store.recover_stale()
        runner.start()
        for task_id in store.queued():
            if not runner.submit(task_id):
                break
        yield
        await asyncio.to_thread(runner.close)

    app = FastAPI(title="RetailOps Agent", lifespan=lifespan)
    app.state.store, app.state.runner = store, runner

    def authenticate(authorization: str | None = Header(default=None)):
        if token and not hmac.compare_digest(authorization or "", "Bearer " + token):
            raise HTTPException(401, "invalid service token")

    @app.get("/health")
    def health():
        return {"status": "ok", "sandbox": "linux-seccomp", "evolution_enabled": allow_evolution,
                "knowledge_enabled": knowledge is not None,
                "dense_retrieval_enabled": knowledge is not None and embedding_model is not None}

    @app.post("/tasks", status_code=202, dependencies=[Depends(authenticate)])
    def submit(body: TaskRequest):
        if body.evolve and not allow_evolution:
            raise HTTPException(403, "online evolution is disabled by the server")
        if body.mode == "assistant" and knowledge is None and agent_factory is None:
            raise HTTPException(503, "knowledge is not configured")
        try:
            if body.orders:
                _csv_path(data_root, body.orders)
                _csv_path(data_root, body.refunds)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        task_id = store.create(body.model_dump())
        if not runner.submit(task_id):
            # It remains durably queued; no accepted task is discarded on saturation.
            return {"task_id": task_id, "status": "queued", "dispatch": "deferred"}
        return {"task_id": task_id, "status": "queued"}

    @app.get("/tasks/{task_id}", dependencies=[Depends(authenticate)])
    def get_task(task_id: str):
        task = store.get(task_id)
        if task is None:
            raise HTTPException(404, "unknown task")
        return task

    @app.get("/tasks/{task_id}/events", dependencies=[Depends(authenticate)])
    async def events(task_id: str, request: Request, after: int = 0,
                     last_event_id: str | None = Header(default=None)):
        if store.get(task_id) is None:
            raise HTTPException(404, "unknown task")
        try:
            cursor = max(0, after, int(last_event_id or 0))
        except ValueError as exc:
            raise HTTPException(400, "invalid Last-Event-ID") from exc

        async def stream():
            current = cursor
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(store.events, task_id, current)
                for row in rows:
                    current = row["id"]
                    yield f"id: {current}\nevent: trace\ndata: {json.dumps(row['event'], ensure_ascii=False)}\n\n"
                task = await asyncio.to_thread(store.get, task_id)
                if task["status"] in {"succeeded", "failed", "interrupted", "needs_input", "insufficient_evidence"} and not rows:
                    yield f"event: done\ndata: {json.dumps({'status': task['status']})}\n\n"
                    break
                await asyncio.sleep(0.1)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
