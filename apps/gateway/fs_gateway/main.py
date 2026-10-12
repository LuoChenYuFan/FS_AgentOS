"""FastAPI entrypoint. Requests are authenticated before they reach the orchestrator."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fs_agent_runtime.trip_memory import (
    BUSY_REPLY,
    canned_reply,
    resolve_utterance,
    slot_patch_from_text,
)
from fs_common import AuthMethod, ModelQuotaExhausted, Principal, new_id
from fs_common.contracts.intent import INTENT_UNKNOWN, IntentDecisionRecord, IntentRoute
from fs_common.workers import run_io
from fs_intent_funnel import (
    DecisionLog,
    FunnelConfig,
    IntentFunnel,
    KnowledgeHandoff,
    LayerAttempt,
    MemoryDecisionLog,
    PostgresDecisionLog,
    StructuredIntentModel,
    default_funnel,
    unknown_user_message,
)
from fs_llm_gateway import chat_for_tier, dashscope_chat_from_env
from fs_memory_service import DialogueBoard, MemoryService, PostgresMemoryStore
from fs_orchestrator import ThinOrchestrator
from fs_orchestrator.chat_planner import ChatPlanner
from fs_orchestrator.ledger import DurabilityError, DurableRuns
from fs_orchestrator.postgres import connect, load_local_env, postgres_configured
from fs_rag_service import RagService, RetrievingKnowledge, build_rag_service

from fs_gateway.admin import register_admin
from fs_gateway.auth.guards import require_knowledge_scope, require_scope
from fs_gateway.context import (
    CSRF_HEADER,
    REFRESH_COOKIE,
    SESSION_COOKIE,
    GatewayState,
    default_platform_store,
    new_trace_id,
)
from fs_gateway.cookies import clear_auth_cookies, set_auth_cookies
from fs_gateway.platform.store import assistant_persona
from fs_gateway.rag_skill import RagQuerySkill
from fs_gateway.settings import AuthError, GatewaySettings
from fs_gateway.sites import (
    attach_visitor_cookie,
    guest_user_id,
    mint_visitor_id,
    register_sites,
    visitor_from_request,
)


async def _document_from_upload(
    request: Request,
    library: RagService,
    principal: Any,
) -> dict[str, str]:
    form = await request.form()
    scope = form.get("knowledge_scope")
    if isinstance(scope, str) and scope:
        require_knowledge_scope(principal, scope)
    upload = form.get("file")
    filename = str(getattr(upload, "filename", "") or "")
    read = getattr(upload, "read", None)
    if upload is None or not callable(read) or not filename:
        raise AuthError("file is required", code="bad_request", status_code=400)
    raw = await read()
    if not isinstance(raw, bytes) or not raw:
        raise AuthError("file is required", code="bad_request", status_code=400)
    try:
        document_id = await library.aingest_file(
            tenant_id=principal.tenant_id,
            filename=filename,
            body=raw,
        )
    except ValueError as exc:
        raise AuthError(str(exc), code="bad_request", status_code=400) from exc
    return {"document_id": document_id}


def create_app(
    settings: GatewaySettings | None = None,
    state: GatewayState | None = None,
    orchestrator: ThinOrchestrator | None = None,
    funnel: IntentFunnel | None = None,
    decisions: DecisionLog | None = None,
    knowledge: KnowledgeHandoff | None = None,
    rag: RagService | None = None,
    rag_chat: object | None = None,
) -> FastAPI:
    load_local_env()
    resolved = settings or GatewaySettings()
    gateway = state or GatewayState.create(resolved, platform=default_platform_store())
    library = rag or build_rag_service()
    if rag_chat is None and rag is None:
        rag_chat = dashscope_chat_from_env("RAG_MODEL")
    intent_chat = dashscope_chat_from_env("INTENT_MODEL")
    planner_chat = chat_for_tier("cheap")
    planner = ChatPlanner(planner_chat) if planner_chat is not None else None
    knowledge_skill = RagQuerySkill(library)
    dialogue = _dialogue_board()
    if orchestrator is None and postgres_configured():
        orchestrator = ThinOrchestrator(
            durable=DurableRuns(),
            knowledge=knowledge_skill,
            planner=planner,
            dialogue=dialogue,
        )
    runner = orchestrator or ThinOrchestrator(
        knowledge=knowledge_skill, planner=planner, dialogue=dialogue
    )
    if runner.dialogue is None:
        runner.dialogue = dialogue
    if funnel is None and intent_chat is not None:
        config = FunnelConfig(skip_small_llm=True)
        funnel = IntentFunnel(
            large_model=StructuredIntentModel(
                intent_chat, minimum=config.large_llm_min_confidence
            ),
            config=config,
        )
    classifier = funnel or default_funnel()
    decision_log = decisions if decisions is not None else _decision_log()
    knowledge_exit = (
        knowledge if knowledge is not None else RetrievingKnowledge(library)
    )
    app = FastAPI(title="FS_AgentOS Gateway", version="0.1.0")
    app.state.gateway = gateway
    app.state.orchestrator = runner
    app.state.funnel = classifier
    app.state.decisions = decision_log
    app.state.knowledge = knowledge_exit
    app.state.rag = library
    app.state.site_runs = {}
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            CSRF_HEADER,
            "X-API-Key",
            "Idempotency-Key",
            "X-Trace-Id",
        ],
    )
    register_sites(app)

    @app.exception_handler(ModelQuotaExhausted)
    async def model_quota(_request: Request, exc: ModelQuotaExhausted) -> JSONResponse:
        message = str(exc)
        return JSONResponse(
            status_code=200,
            content={
                "status": "unavailable",
                "reply": message,
                "detail": message,
                "code": "insufficient_quota",
            },
        )

    @app.exception_handler(AuthError)
    async def auth_error(_request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": str(exc), "code": exc.code}
        )

    @app.exception_handler(DurabilityError)
    async def durability_error(_request: Request, exc: DurabilityError) -> JSONResponse:
        status = {
            "not_found": 404,
            "conflict": 409,
            "bad_request": 400,
            "not_configured": 503,
        }.get(exc.code, 400)
        return JSONResponse(
            status_code=status, content={"detail": str(exc), "code": exc.code}
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "gateway"}

    @app.post("/v1/auth/login")
    async def login(body: dict[str, Any]) -> JSONResponse:
        result = gateway.login(
            str(body.get("username") or ""), str(body.get("password") or "")
        )
        return _credential_response(gateway, result)

    @app.post("/v1/auth/refresh")
    async def refresh(
        request: Request, body: dict[str, Any] | None = None
    ) -> JSONResponse:
        payload = body or {}
        token = str(
            payload.get("refresh_token") or request.cookies.get(REFRESH_COOKIE) or ""
        )
        result = gateway.refresh(token)
        return _credential_response(gateway, result)

    @app.post("/v1/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        principal = gateway.authenticate(request)
        access_jti = _access_jti(gateway, request)
        gateway.logout(
            session_id=request.cookies.get(SESSION_COOKIE),
            access_jti=access_jti,
            refresh_token=request.cookies.get(REFRESH_COOKIE),
        )
        _ = principal
        response = JSONResponse({"status": "logged_out"})
        clear_auth_cookies(response, gateway.settings)
        return response

    @app.get("/v1/me")
    async def me(request: Request) -> dict[str, Any]:
        principal = gateway.authenticate(request)
        return principal.model_dump(mode="json")

    @app.post("/v1/api-keys")
    async def create_api_key(
        request: Request, body: dict[str, Any] | None = None
    ) -> dict[str, str]:
        principal = gateway.authenticate(request)
        payload = body or {}
        scopes = payload.get("scopes")
        if scopes is not None and not isinstance(scopes, list):
            raise AuthError(
                "scopes must be a list", code="bad_request", status_code=400
            )
        raw_key, key_id = gateway.issue_api_key(principal, scopes)
        return {"api_key": raw_key, "key_id": key_id}

    @app.post("/v1/api-keys/{key_id}/revoke")
    async def revoke_api_key(key_id: str, request: Request) -> dict[str, str]:
        principal = gateway.authenticate(request)
        require_scope(principal, "apikeys:manage")
        if not gateway.api_keys.revoke(key_id, tenant_id=principal.tenant_id):
            raise AuthError("api key not found", code="not_found", status_code=404)
        return {"key_id": key_id, "status": "revoked"}

    async def _serve_task(
        request: Request,
        *,
        principal: Principal,
        payload: dict[str, Any],
        conversation_id: str,
        idempotency_key: str,
        trace_id: str,
        track_idempotency: bool,
        site_slug: str | None = None,
        visitor_id: str | None = None,
    ) -> JSONResponse:
        headers = {"X-Trace-Id": trace_id, "Idempotency-Key": idempotency_key}
        if visitor_id:
            headers["X-Conversation-Id"] = visitor_id

        def _respond(response: dict[str, Any]) -> JSONResponse:
            if track_idempotency:
                gateway.finish_task(principal, payload, idempotency_key, response)
            body = dict(response)
            if visitor_id and site_slug:
                body["conversation_id"] = visitor_id
                body["slug"] = site_slug
                run_id = str(body.get("run_id") or "")
                if run_id:
                    app.state.site_runs[run_id] = (principal.tenant_id, principal.user_id)
            result = JSONResponse(body, headers=headers)
            if visitor_id and site_slug:
                attach_visitor_cookie(result, gateway.settings, site_slug, visitor_id)
            return result

        spoken = str(payload.get("text") or payload.get("user_input") or "")
        deadline = time.monotonic() + _TASK_BUDGET_SECONDS
        board = runner.dialogue or dialogue
        remembered = board.load(
            principal.tenant_id, principal.user_id, conversation_id=conversation_id
        )
        continued = resolve_utterance(spoken, remembered.slots, remembered.turns)
        asked = canned_reply(continued)
        if asked:
            _remember(
                board,
                principal.tenant_id,
                principal.user_id,
                spoken,
                asked,
                slot_patch_from_text(spoken),
                conversation_id=conversation_id,
            )
            return _respond(_plain_reply(asked, trace_id))
        text = continued or spoken
        try:
            judged = await _within(deadline, classifier.adecide(text))
        except TimeoutError:
            _remember(
                board,
                principal.tenant_id,
                principal.user_id,
                spoken,
                BUSY_REPLY,
                conversation_id=conversation_id,
            )
            return _respond(_plain_reply(BUSY_REPLY, trace_id))
        record = IntentDecisionRecord(
            id=new_id("intent"),
            tenant_id=principal.tenant_id,
            raw_text=text,
            latency_ms=judged.latency_ms,
            attempt_reasons=_attempt_reasons(judged.attempts),
            **judged.decision.model_dump(),
        )
        await run_io(decision_log.write, record)
        if judged.decision.route is IntentRoute.UNKNOWN:
            response = _unknown_response(record.id, trace_id, judged.attempts)
            _remember(
                board,
                principal.tenant_id,
                principal.user_id,
                spoken,
                str(response.get("message") or ""),
                conversation_id=conversation_id,
            )
            return _respond(response)
        try:
            if judged.decision.intent == "chat":
                persona = assistant_persona(
                    gateway.platform.get_tenant(principal.tenant_id)
                )
                reply = await _within(
                    deadline, _chat_reply(intent_chat, text, persona=persona)
                )
                response = {
                    "status": "done",
                    "intent": "chat",
                    "route": judged.decision.route.value,
                    "decision_id": record.id,
                    "trace_id": trace_id,
                    "reply": reply,
                }
                _remember(
                    board,
                    principal.tenant_id,
                    principal.user_id,
                    spoken,
                    str(reply or ""),
                    conversation_id=conversation_id,
                )
                return _respond(response)
            if judged.decision.route is IntentRoute.KNOWLEDGE:
                accepted = await _within(
                    deadline,
                    _accept_knowledge(
                        knowledge_exit,
                        tenant_id=principal.tenant_id,
                        text=text,
                        intent=judged.decision.intent,
                    ),
                )
                persona = assistant_persona(
                    gateway.platform.get_tenant(principal.tenant_id)
                )
                reply = await _within(
                    deadline, _rag_reply(rag_chat, text, accepted, persona=persona)
                )
                if reply:
                    accepted = {**accepted, "reply": reply}
                response = {**accepted, "decision_id": record.id, "trace_id": trace_id}
                _remember(
                    board,
                    principal.tenant_id,
                    principal.user_id,
                    spoken,
                    str(reply or ""),
                    {"knowledge": {"subject": spoken[:32]}},
                    conversation_id=conversation_id,
                )
                return _respond(response)
            run_id = new_id("run")
            enriched = {
                **payload,
                "text": text,
                "user_text": spoken,
                "conversation_id": conversation_id,
                "tenant_id": principal.tenant_id,
                "user_id": principal.user_id,
                "principal": principal.model_dump(mode="json"),
                "trace_id": trace_id,
                "idempotency_key": idempotency_key,
                "intent": judged.decision.intent,
                "route": judged.decision.route.value,
            }
            result = await _within(
                deadline, runner.run(task_id=run_id, input_payload=enriched)
            )
        except TimeoutError:
            _remember(
                board,
                principal.tenant_id,
                principal.user_id,
                spoken,
                BUSY_REPLY,
                conversation_id=conversation_id,
            )
            return _respond(_plain_reply(BUSY_REPLY, trace_id))
        await run_io(decision_log.attach_run, record.id, result["task_id"])
        response = {
            "task_id": result["task_id"],
            "run_id": result["task_id"],
            "status": result["status"],
            "intent": judged.decision.intent,
            "route": judged.decision.route.value,
            "decision_id": record.id,
            "trace_id": trace_id,
            "reply": _visible_reply(result),
            "blocks": result.get("blocks", []),
        }
        if result.get("status") == "awaiting_approval":
            response["needs_confirmation"] = True
        return _respond(response)

    @app.post("/v1/tasks")
    async def create_task(
        request: Request, body: dict[str, Any] | None = None
    ) -> JSONResponse:
        principal = gateway.authenticate(request)
        payload = _client_payload(body or {})
        idempotency_key = request.headers.get("idempotency-key") or new_id("idem")
        trace_id = new_trace_id(request)
        replay = gateway.begin_task(principal, payload, idempotency_key)
        if replay is not None:
            return JSONResponse(
                replay,
                headers={"X-Trace-Id": trace_id, "Idempotency-Key": idempotency_key},
            )
        conversation_id = str(payload.get("conversation_id") or "default")
        return await _serve_task(
            request,
            principal=principal,
            payload=payload,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            track_idempotency=True,
        )

    @app.post("/v1/sites/{slug}/tasks")
    async def create_site_task(
        slug: str, request: Request, body: dict[str, Any] | None = None
    ) -> JSONResponse:
        """Public Q&A for one published resume. The slug picks the person."""
        published = gateway.platform.public_resume(slug.strip().lower())
        if published is None:
            return JSONResponse(
                {"detail": "resume not found", "code": "not_found"},
                status_code=404,
            )
        payload = _client_payload(body or {})
        visitor_id = visitor_from_request(
            request, payload, gateway.settings.jwt_secret
        ) or mint_visitor_id(gateway.settings.jwt_secret)
        site_slug = str(published["slug"])
        tenant_id = str(published["tenant_id"])
        quota_key = f"site:{tenant_id}:{visitor_id}"
        try:
            gateway.site_question_limiter.admit(quota_key)
        except AuthError as exc:
            return _site_question_limited(
                gateway.settings,
                site_slug,
                visitor_id,
                str(exc),
                gateway.site_question_limiter.retry_after(quota_key),
            )
        principal = Principal(
            user_id=guest_user_id(visitor_id),
            tenant_id=tenant_id,
            scopes=["tasks:write", "kb:read"],
            auth_method=AuthMethod.COOKIE,
        )
        return await _serve_task(
            request,
            principal=principal,
            payload=payload,
            conversation_id=visitor_id,
            idempotency_key=new_id("idem"),
            trace_id=new_trace_id(request),
            track_idempotency=False,
            site_slug=site_slug,
            visitor_id=visitor_id,
        )

    @app.post("/v1/documents")
    async def create_document(request: Request) -> dict[str, str]:
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" in content_type.lower():
            return await _document_from_upload(request, library, principal)
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise AuthError(
                "title and text are required", code="bad_request", status_code=400
            ) from exc
        if not isinstance(payload, dict):
            raise AuthError(
                "title and text are required", code="bad_request", status_code=400
            )
        knowledge_scope = payload.get("knowledge_scope")
        if isinstance(knowledge_scope, str) and knowledge_scope:
            require_knowledge_scope(principal, knowledge_scope)
        title = str(payload.get("title") or "")
        text = str(payload.get("text") or "")
        if not title or not text.strip():
            raise AuthError(
                "title and text are required", code="bad_request", status_code=400
            )
        document_id = await library.aingest_text(
            tenant_id=principal.tenant_id, title=title, text=text
        )
        return {"document_id": document_id}

    @app.post("/v1/tasks/{run_id}/approve")
    async def approve_task(run_id: str, request: Request) -> dict[str, str]:
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        try:
            return await runner.approve(run_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise AuthError(
                "run is not waiting for approval", code="not_found", status_code=404
            ) from exc
        except ValueError as exc:
            raise AuthError(str(exc), code="bad_request", status_code=400) from exc

    @app.post("/v1/tasks/{run_id}/pause")
    async def pause_task(run_id: str, request: Request) -> dict[str, str]:
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        return await runner.pause(run_id, tenant_id=principal.tenant_id)

    @app.post("/v1/tasks/{run_id}/resume")
    async def resume_task(run_id: str, request: Request) -> dict[str, str]:
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        return await runner.resume(run_id, tenant_id=principal.tenant_id)

    @app.post("/v1/tasks/{run_id}/cancel")
    async def cancel_task(run_id: str, request: Request) -> dict[str, str]:
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        try:
            return await runner.cancel(run_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise AuthError(
                "run is not waiting for approval", code="not_found", status_code=404
            ) from exc
        except ValueError as exc:
            raise AuthError(str(exc), code="bad_request", status_code=400) from exc
        except DurabilityError as exc:
            raise AuthError(str(exc), code=exc.code, status_code=404) from exc

    def _published_site(slug: str) -> dict[str, Any]:
        published = gateway.platform.public_resume(slug.strip().lower())
        if published is None:
            raise AuthError("resume not found", code="not_found", status_code=404)
        return published

    async def _site_visitor(request: Request) -> str:
        body: dict[str, Any] | None = None
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            try:
                parsed = await request.json()
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                body = parsed
        visitor_id = visitor_from_request(request, body, gateway.settings.jwt_secret)
        if visitor_id is None:
            raise AuthError(
                "conversation_id is required",
                code="unauthorized",
                status_code=401,
            )
        return visitor_id

    def _owned_site_run(run_id: str, tenant_id: str, user_id: str) -> None:
        owner = app.state.site_runs.get(run_id)
        if owner != (tenant_id, user_id):
            raise AuthError(
                "run is not waiting for approval",
                code="not_found",
                status_code=404,
            )

    @app.post("/v1/sites/{slug}/tasks/{run_id}/approve")
    async def approve_site_task(slug: str, run_id: str, request: Request) -> dict[str, str]:
        published = _published_site(slug)
        visitor_id = await _site_visitor(request)
        tenant_id = str(published["tenant_id"])
        user_id = guest_user_id(visitor_id)
        gateway.rate_limiter.check(f"site:{tenant_id}:{visitor_id}")
        _owned_site_run(run_id, tenant_id, user_id)
        try:
            return await runner.approve(run_id, tenant_id=tenant_id)
        except KeyError as exc:
            raise AuthError(
                "run is not waiting for approval", code="not_found", status_code=404
            ) from exc
        except ValueError as exc:
            raise AuthError(str(exc), code="bad_request", status_code=400) from exc

    @app.post("/v1/sites/{slug}/tasks/{run_id}/cancel")
    async def cancel_site_task(slug: str, run_id: str, request: Request) -> dict[str, str]:
        published = _published_site(slug)
        visitor_id = await _site_visitor(request)
        tenant_id = str(published["tenant_id"])
        user_id = guest_user_id(visitor_id)
        gateway.rate_limiter.check(f"site:{tenant_id}:{visitor_id}")
        _owned_site_run(run_id, tenant_id, user_id)
        try:
            return await runner.cancel(run_id, tenant_id=tenant_id)
        except KeyError as exc:
            raise AuthError(
                "run is not waiting for approval", code="not_found", status_code=404
            ) from exc
        except ValueError as exc:
            raise AuthError(str(exc), code="bad_request", status_code=400) from exc
        except DurabilityError as exc:
            raise AuthError(str(exc), code=exc.code, status_code=404) from exc

    register_admin(app)
    return app


def _decision_log() -> DecisionLog:
    if postgres_configured():
        return PostgresDecisionLog()
    return MemoryDecisionLog()


_TASK_BUDGET_SECONDS = 20.0
_RAG_EMPTY = "资料里没有找到这个问题的答案。"


async def _within(deadline: float, awaitable: Any) -> Any:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    return await asyncio.wait_for(awaitable, left)


def _dialogue_board() -> DialogueBoard:
    if postgres_configured():
        return DialogueBoard(MemoryService(PostgresMemoryStore(connect)))
    return DialogueBoard.in_memory()


def _remember(
    board: DialogueBoard | None,
    tenant_id: str,
    user_id: str,
    user_text: str,
    assistant_text: str,
    slot_patch: dict[str, dict[str, str]] | None = None,
    *,
    conversation_id: str = "default",
) -> None:
    if board is None:
        return
    try:
        board.commit(
            tenant_id,
            user_id,
            user_text=user_text,
            assistant_text=assistant_text,
            slot_patch=slot_patch,
            conversation_id=conversation_id,
        )
    except Exception:
        return


def _plain_reply(reply: str, trace_id: str) -> dict[str, str]:
    return {"status": "done", "reply": reply, "trace_id": trace_id}


def _site_question_limited(
    settings: GatewaySettings,
    site_slug: str,
    visitor_id: str,
    message: str,
    retry_after: int,
) -> JSONResponse:
    """Chat-shaped reply so the resume page can show the sentence."""
    response = JSONResponse(
        status_code=200,
        content={
            "status": "done",
            "reply": message,
            "detail": message,
            "code": "rate_limited",
            "conversation_id": visitor_id,
            "slug": site_slug,
        },
        headers={"Retry-After": str(retry_after)},
    )
    attach_visitor_cookie(response, settings, site_slug, visitor_id)
    return response


def _visible_reply(result: dict[str, Any]) -> str:
    reply = str(result.get("reply") or "").strip()
    if reply:
        return reply
    blocks = result.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            body = block.get("body")
            if not isinstance(body, dict):
                continue
            summary = body.get("summary")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
    return "这次没有得到可用的结果，请换一种说法再试一次。"


async def _accept_knowledge(
    knowledge: object, *, tenant_id: str, text: str, intent: str
) -> dict[str, object]:
    accept = getattr(knowledge, "aaccept", None)
    if callable(accept):
        payload = await accept(tenant_id=tenant_id, text=text, intent=intent)
    else:
        payload = await run_io(
            knowledge.accept, tenant_id=tenant_id, text=text, intent=intent
        )
    if not isinstance(payload, dict):
        raise TypeError("knowledge handoff did not return an object")
    return payload


async def _rag_reply(
    chat: object | None,
    question: str,
    payload: dict[str, object],
    *,
    persona: dict[str, str | list[str]] | None = None,
) -> str | None:
    if chat is None:
        return None
    passages = _citation_texts(payload)
    if not passages:
        return _RAG_EMPTY
    voice = persona or assistant_persona(None)
    numbered = "\n\n".join(
        f"[{index}] {text}" for index, text in enumerate(passages, start=1)
    )
    messages = [
        {"role": "system", "content": str(voice["rag_system"])},
        {"role": "user", "content": f"问题：{question}\n\n资料：\n{numbered}"},
    ]
    try:
        raw = await _complete_chat(chat, messages, enable_thinking=False)
    except ModelQuotaExhausted as exc:
        return str(exc)
    except (OSError, RuntimeError, TypeError, ValueError):
        return passages[0]
    if not isinstance(raw, str) or not raw.strip():
        return passages[0]
    return raw.strip()


def _citation_texts(payload: dict[str, object]) -> list[str]:
    context = payload.get("retrieved_context")
    if not isinstance(context, dict):
        return []
    citations = context.get("citations")
    if not isinstance(citations, list):
        return []
    texts: list[str] = []
    for item in citations:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return texts


async def _complete_chat(
    chat: object, messages: list[dict[str, str]], *, enable_thinking: bool | None = None
) -> object:
    complete = getattr(chat, "acomplete", None)
    if callable(complete):
        try:
            return await complete(messages, enable_thinking=enable_thinking)
        except TypeError:
            return await complete(messages)
    complete = getattr(chat, "complete", None)
    if not callable(complete):
        raise TypeError("chat model cannot complete a message")
    try:
        return complete(messages, enable_thinking=enable_thinking)
    except TypeError:
        return await run_io(complete, messages)


async def _chat_reply(
    chat: object,
    text: str,
    *,
    persona: dict[str, str | list[str]] | None = None,
) -> str:
    voice = persona or assistant_persona(None)
    identity = str(voice["identity"])
    fallback = str(voice["fallback"])
    if _asks_who(text):
        return identity
    messages = [
        {"role": "system", "content": str(voice["chat_system"])},
        {"role": "user", "content": text},
    ]
    complete = getattr(chat, "acomplete", None)
    if not callable(complete):
        complete = getattr(chat, "complete", None)
        if not callable(complete):
            return fallback
        try:
            raw = complete(messages)
        except ModelQuotaExhausted as exc:
            return str(exc)
        except (OSError, RuntimeError, TypeError, ValueError):
            return fallback
    else:
        try:
            raw = await complete(messages)
        except ModelQuotaExhausted as exc:
            return str(exc)
        except (OSError, RuntimeError, TypeError, ValueError):
            return fallback
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    return raw.strip()


def _asks_who(text: str) -> bool:
    asked = text.strip().rstrip("？?！!。.")
    return asked == "你是谁"


def _attempt_reasons(attempts: list[LayerAttempt]) -> list[dict[str, str]]:
    return [
        {"layer": item.layer.value, "failure_reason": item.failure_reason}
        for item in attempts
    ]


def _unknown_response(
    decision_id: str, trace_id: str, attempts: list[LayerAttempt]
) -> dict[str, str]:
    return {
        "status": "unknown",
        "intent": INTENT_UNKNOWN,
        "route": IntentRoute.UNKNOWN.value,
        "decision_id": decision_id,
        "message": unknown_user_message(attempts),
        "trace_id": trace_id,
    }


def _credential_response(gateway: GatewayState, result: Any) -> JSONResponse:
    response = JSONResponse(
        {
            "access_token": result.access_token,
            "refresh_token": result.refresh_token,
            "token_type": "Bearer",
            "expires_in": result.expires_in,
            "csrf_token": result.csrf_token,
            "principal": result.principal.model_dump(mode="json"),
        }
    )
    set_auth_cookies(response, gateway.settings, result)
    return response


def _client_payload(body: dict[str, Any]) -> dict[str, Any]:
    blocked = {"principal", "user_id", "tenant_id", "trace_id", "idempotency_key"}
    return {key: value for key, value in body.items() if key not in blocked}


def _access_jti(gateway: GatewayState, request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        return gateway.tokens.decode(token, expected_type="access").jti
    except AuthError:
        return None


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("fs_gateway.main:app", host="0.0.0.0", port=8000, reload=True)
