"""Thin Orchestrator: enterprise hooks around LangGraph (kernel lives in agent-runtime)."""

from __future__ import annotations

from typing import Any

from fs_common import new_id
from fs_common.workers import run_io
from fs_eventbus import EventBus, InMemoryEventBus
from fs_memory_service import DialogueBoard

from fs_orchestrator.ledger import DurabilityError, DurableRuns
from fs_orchestrator.postgres import postgres_configured
from fs_orchestrator.schedule import CollaborationService


class ThinOrchestrator:
    """Self-owned surface is intentionally thin.

    Graph traversal / conditional routing belong to LangGraph in agent-runtime.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        durable: DurableRuns | None = None,
        knowledge: Any = None,
        planner: Any = None,
        dialogue: DialogueBoard | None = None,
    ) -> None:
        self._bus = event_bus or InMemoryEventBus()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._durable = durable
        self._knowledge = knowledge
        self._planner = planner
        self.dialogue = dialogue
        self._collaborations: CollaborationService | None = None

    async def run(
        self,
        *,
        task_id: str | None = None,
        input_payload: dict[str, Any],
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        if input_payload.get("route") == "orchestrate":
            return await run_io(
                self._collaboration().execute,
                task_id=task_id,
                payload=input_payload,
            )
        if self._durable is not None:
            return await self._run_durable(
                task_id=task_id, input_payload=input_payload, max_steps=max_steps
            )
        tid = task_id or new_id("task")
        self._tasks[tid] = {"status": "accepted", "input": input_payload}
        await self._bus.publish(
            "tasks.lifecycle", {"task_id": tid, "event": "accepted"}
        )
        return {"task_id": tid, "status": "accepted"}

    async def pause(
        self, task_id: str, *, tenant_id: str | None = None
    ) -> dict[str, str]:
        return await run_io(self._runs().pause, task_id, tenant_id=_tenant(tenant_id))

    async def resume(
        self, task_id: str, *, tenant_id: str | None = None
    ) -> dict[str, str]:
        return await run_io(self._runs().resume, task_id, tenant_id=_tenant(tenant_id))

    async def cancel(
        self, task_id: str, *, tenant_id: str | None = None
    ) -> dict[str, str]:
        try:
            return await run_io(
                self._collaboration().cancel, task_id, tenant_id=_tenant(tenant_id)
            )
        except (KeyError, ValueError):
            return await run_io(
                self._runs().cancel, task_id, tenant_id=_tenant(tenant_id)
            )

    async def approve(
        self, task_id: str, *, tenant_id: str | None = None
    ) -> dict[str, str]:
        return await run_io(
            self._collaboration().approve, task_id, tenant_id=_tenant(tenant_id)
        )

    async def _run_durable(
        self,
        *,
        task_id: str | None,
        input_payload: dict[str, Any],
        max_steps: int | None,
    ) -> dict[str, str]:
        run_id = task_id or new_id("run")
        tenant_id = str(input_payload.get("tenant_id") or "")
        user_id = str(input_payload.get("user_id") or "")
        if not tenant_id or not user_id:
            raise DurabilityError(
                "tenant_id and user_id are required", code="bad_request"
            )
        user_input = str(
            input_payload.get("user_input") or input_payload.get("text") or ""
        )
        idempotency_key = str(input_payload.get("idempotency_key") or run_id)
        result = await run_io(
            self._runs().run,
            run_id=run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            user_input=user_input,
            idempotency_key=idempotency_key,
            session_id=input_payload.get("session_id"),
            max_steps=max_steps,
        )
        persisted = result["run_id"]
        await self._bus.publish(
            "tasks.lifecycle", {"task_id": persisted, "event": result["status"]}
        )
        return {
            "task_id": persisted,
            "run_id": persisted,
            "status": result["status"],
            "current_node": result["current_node"],
        }

    def _collaboration(self) -> CollaborationService:
        if self._collaborations is None:
            self._collaborations = CollaborationService(
                knowledge=self._knowledge,
                planner=self._planner,
                dialogue=self.dialogue,
            )
        return self._collaborations

    def _runs(self) -> DurableRuns:
        if not postgres_configured() and self._durable is None:
            raise DurabilityError("PostgreSQL is not configured", code="not_configured")
        if self._durable is None:
            self._durable = DurableRuns()
        return self._durable


def _tenant(tenant_id: str | None) -> str:
    if not tenant_id:
        raise DurabilityError("tenant_id is required", code="bad_request")
    return tenant_id
