"""Run-scoped control and shared admission, separate from graph scheduling.

Synchronous operations retain admission until they return. Cancellation is
cooperative; late output is rejected after draining, never hard-preempted.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx
from opentelemetry import context as otel_context

from workbench.graphs.research_models import ApplicationEvent, Limits
from workbench.models.errors import ModelConnectionError, ModelTimeoutError
from workbench.observability.tracing import start_span

_WORKER = threading.BoundedSemaphore(1)
_MODEL = threading.BoundedSemaphore(1)
_ACCELERATOR = threading.BoundedSemaphore(1)
# A transport failure cannot establish that remote native decoding stopped.
# Quarantine the shared resource until its owning process/runtime is restarted.
_UNCONFIRMED = threading.Event()


class RunStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskContext:
    run_id: str
    task_id: str
    snapshot_id: str
    deadline: float


current_execution: ContextVar[Execution | None] = ContextVar(
    "atlas_execution", default=None
)
current_task: ContextVar[TaskContext | None] = ContextVar("atlas_task", default=None)


@dataclass
class Execution:
    snapshot_id: str
    limits: Limits
    sink: Callable[[ApplicationEvent], None] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: float = field(default_factory=time.monotonic)
    parent: Any = None
    sequence: int = 0
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    lock: Any = field(default_factory=threading.Lock)

    def check(self):
        if _UNCONFIRMED.is_set():
            raise RuntimeError("resource_not_drained: owned runtime restart required")
        if self.cancel.is_set():
            raise RunStopped("cancelled")
        if time.monotonic() >= self.started + self.limits.run_seconds:
            raise RunStopped("run_deadline")
        task = current_task.get()
        if task and time.monotonic() >= task.deadline:
            raise TimeoutError("task_deadline")

    def emit(self, kind, *, task_id=None, result=None, stage=None):
        with self.lock:
            event = ApplicationEvent(
                kind=kind,
                run_id=self.run_id,
                sequence=self.sequence,
                task_id=task_id,
                result=result,
                stage=stage,
            )
            self.sequence += 1
            if self.sink:
                try:
                    self.sink(event)
                except Exception:
                    pass  # Optional presentation cannot change research semantics.

    def watch_cancellation(self):
        """Progress observer only: never attempts to interrupt native work."""
        stopped = threading.Event()

        def watch():
            while not stopped.wait(0.02):
                if self.cancel.is_set():
                    self.emit("cancelling")
                    return

        thread = threading.Thread(
            target=watch, name="atlas-cancel-progress", daemon=True
        )
        thread.start()

        def stop():
            stopped.set()
            thread.join()

        return stop

    @contextmanager
    def admit(self, gate):
        while True:
            self.check()
            if gate.acquire(timeout=0.02):
                break
        try:
            self.check()
            yield
            self.check()
        finally:
            if gate is not _ACCELERATOR or not _UNCONFIRMED.is_set():
                gate.release()

    @contextmanager
    def bind(self, task=None):
        token = current_execution.set(self)
        task_token = current_task.set(task)
        parent_token = (
            otel_context.attach(self.parent) if self.parent is not None else None
        )
        try:
            yield
        finally:
            if parent_token is not None:
                otel_context.detach(parent_token)
            current_task.reset(task_token)
            current_execution.reset(token)


class AdmittedModel:
    def __init__(self, model):
        self.model = model

    def generate(self, messages, **settings):
        execution = current_execution.get()
        if execution is None:
            raise RuntimeError("model call outside execution")
        with execution.admit(_MODEL), execution.admit(_ACCELERATOR):
            with execution.lock:
                if execution.calls >= 10:
                    raise RuntimeError("model call budget exhausted")
                execution.calls += 1
            started = time.monotonic()
            # Existing transports own request timeouts. This elapsed check does
            # not pretend to interrupt synchronous HTTP/native calls.
            task = current_task.get()
            remaining = min(
                execution.limits.request_seconds,
                execution.started + execution.limits.run_seconds - started,
                task.deadline - started if task else float("inf"),
            )
            execution.check()
            try:
                response = self.model.generate(
                    messages, **{**settings, "request_timeout": remaining}
                )
            except (ModelTimeoutError, ModelConnectionError, httpx.TransportError):
                _UNCONFIRMED.set()
                raise
            execution.tokens_in += max(0, response.tokens_in)
            execution.tokens_out += max(0, response.tokens_out)
            if time.monotonic() - started > execution.limits.request_seconds:
                raise TimeoutError("request_deadline")
            execution.check()
            return response


class AdmittedRetriever:
    def __init__(self, retriever):
        self.retriever = retriever

    def retrieve_hydrated(self, *args, **kwargs):
        execution = current_execution.get()
        with execution.admit(_ACCELERATOR):
            return self.retriever.retrieve_hydrated(*args, **kwargs)


def worker_phase(name, function):
    def node(state):
        execution = current_execution.get()
        if execution:
            execution.check()
            execution.emit("task_stage", task_id=current_task.get().task_id, stage=name)
        with start_span("worker." + name) as span:
            update = function(state)
            report = update.get("validation")
            if report is not None:
                span.set_attribute("validation.status", report.status)
                span.set_attribute("validation.errors", report.errors)
            return update

    return node
