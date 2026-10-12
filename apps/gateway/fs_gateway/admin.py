"""Browser admin: platform tenant folders, resume CMS, and knowledge library."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fs_common import new_id
from fs_common.workers import run_io
from fs_rag_service import RagService
from fs_rag_service.errors import RetrievalBackendError

from fs_gateway.admin_pages import (
    admin_placeholder_page,
    login_page,
    recycle_bin_page,
    tenant_list_page,
)
from fs_gateway.admin_resume_rows import build_resume_library_rows, resume_library_stats
from fs_gateway.admin_tenant_pages import library_page, resume_page
from fs_gateway.platform.resume_schema import normalize_basic_info
from fs_gateway.auth.guards import require_scope
from fs_gateway.context import (
    REFRESH_COOKIE,
    SESSION_COOKIE,
    GatewayState,
    is_platform_admin,
)
from fs_gateway.cookies import clear_auth_cookies, set_auth_cookies
from fs_gateway.platform.store import validate_slug
from fs_gateway.settings import AuthError


def register_admin(app: FastAPI) -> None:
    @app.get("/admin/login", response_model=None)
    async def admin_login_page(request: Request) -> HTMLResponse | RedirectResponse:
        if _logged_in(request):
            return RedirectResponse("/admin", status_code=303)
        return HTMLResponse(login_page())

    @app.post("/admin/login", response_model=None)
    async def admin_login(request: Request) -> HTMLResponse | RedirectResponse:
        gateway = _gateway(request)
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        try:
            result = gateway.login(username, password)
        except AuthError as exc:
            message = "用户名或密码错误"
            if exc.code == "login_locked":
                message = "登录失败次数过多，请稍后再试"
            return HTMLResponse(login_page(message), status_code=exc.status_code)
        response = RedirectResponse("/admin", status_code=303)
        set_auth_cookies(response, gateway.settings, result)
        return response

    @app.post("/admin/logout")
    async def admin_logout(request: Request) -> JSONResponse:
        gateway = _gateway(request)
        gateway.authenticate(request)
        gateway.logout(
            session_id=request.cookies.get(SESSION_COOKIE),
            access_jti=None,
            refresh_token=request.cookies.get(REFRESH_COOKIE),
        )
        response = JSONResponse({"ok": True})
        clear_auth_cookies(response, gateway.settings)
        return response

    @app.get("/admin", response_model=None)
    async def admin_home(request: Request) -> HTMLResponse | RedirectResponse:
        gateway = _gateway(request)
        try:
            principal = gateway.authenticate(request)
        except AuthError:
            return RedirectResponse("/admin/login", status_code=303)
        require_scope(principal, "tasks:write")
        if is_platform_admin(principal):
            users = build_resume_library_rows(gateway.platform)
            trash = build_resume_library_rows(gateway.platform, status="trashed")
            return HTMLResponse(
                tenant_list_page(
                    users=users,
                    stats=resume_library_stats(users),
                    trash_badge=len(trash),
                )
            )
        tenant = gateway.platform.get_tenant(principal.tenant_id)
        if tenant is None:
            return HTMLResponse(login_page("账号未绑定租户"), status_code=403)
        return RedirectResponse(f"/admin/tenants/{tenant.slug}", status_code=303)

    @app.get("/admin/recycle-bin", response_model=None)
    async def admin_recycle_bin(request: Request) -> HTMLResponse | RedirectResponse:
        gateway = _gateway(request)
        try:
            principal = gateway.authenticate(request)
        except AuthError:
            return RedirectResponse("/admin/login", status_code=303)
        require_scope(principal, "tasks:write")
        if not is_platform_admin(principal):
            tenant = gateway.platform.get_tenant(principal.tenant_id)
            if tenant is None:
                return HTMLResponse(login_page("账号未绑定租户"), status_code=403)
            return RedirectResponse(f"/admin/tenants/{tenant.slug}", status_code=303)
        active_users = build_resume_library_rows(gateway.platform)
        trash_users = build_resume_library_rows(gateway.platform, status="trashed")
        return HTMLResponse(
            recycle_bin_page(
                users=trash_users,
                badge=len(active_users),
            )
        )

    @app.get("/admin/dashboard", response_model=None)
    async def admin_dashboard(request: Request) -> HTMLResponse | RedirectResponse:
        return _admin_placeholder(
            request,
            active="dashboard",
            title="概览 Dashboard",
            crumb="数据看板 / <b>概览 Dashboard</b>",
            message="数据概览功能即将上线，当前可先使用用户简历库管理各租户简历。",
            user_count=True,
        )

    @app.get("/admin/parse-records", response_model=None)
    async def admin_parse_records(request: Request) -> HTMLResponse | RedirectResponse:
        return _admin_placeholder(
            request,
            active="parse",
            title="简历解析记录",
            crumb="简历管理 / <b>简历解析记录</b>",
            message="简历解析记录功能即将上线，入口已保留。",
            user_count=True,
        )

    @app.get("/admin/talent-portrait", response_model=None)
    async def admin_talent_portrait(request: Request) -> HTMLResponse | RedirectResponse:
        return _admin_placeholder(
            request,
            active="talent",
            title="人才画像分析",
            crumb="简历管理 / <b>人才画像分析</b>",
            message="人才画像分析功能即将上线，入口已保留。",
            user_count=True,
        )

    @app.post("/admin/tenants")
    async def admin_create_tenant(request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        if not is_platform_admin(principal):
            raise AuthError("missing permission", code="forbidden", status_code=403)
        body = await request.json()
        slug = str(body.get("slug") or "")
        display_name = str(body.get("display_name") or slug)
        ai_username = str(body.get("ai_username") or "")
        ai_password = str(body.get("ai_password") or "")
        if not ai_username or not ai_password:
            return JSONResponse({"detail": "请填写 AI 账号和密码"}, status_code=400)
        try:
            cleaned = validate_slug(slug)
            tenant = gateway.platform.create_tenant(
                tenant_id=f"tenant_{cleaned}",
                slug=cleaned,
                display_name=display_name,
                ai_username=ai_username,
                ai_password=ai_password,
                ai_user_id=new_id("user"),
                password_iterations=gateway.settings.password_iterations,
            )
            gateway.platform.set_published(tenant.id, True, display_name)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return JSONResponse(
            {
                "tenant_id": tenant.id,
                "slug": tenant.slug,
                "location": f"/admin/tenants/{tenant.slug}",
            }
        )

    @app.patch("/admin/tenants/{slug}")
    async def admin_rename_tenant(slug: str, request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        tenant = _workspace_tenant(gateway, principal, slug)
        body = await request.json()
        display_name = str(body.get("display_name") or "").strip()
        if not display_name:
            return JSONResponse({"detail": "请填写显示名称"}, status_code=400)
        assistant_owner = body.get("assistant_owner")
        assistant_capabilities = body.get("assistant_capabilities")
        owner = (
            str(assistant_owner).strip()
            if assistant_owner is not None
            else None
        )
        caps = None
        if assistant_capabilities is not None:
            from fs_gateway.platform.store import normalize_capabilities

            caps = normalize_capabilities(assistant_capabilities)
        try:
            updated = gateway.platform.update_tenant(
                tenant.id,
                display_name=display_name,
                assistant_owner=owner,
                assistant_capabilities=caps,
            )
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except LookupError:
            return JSONResponse({"detail": "租户不存在"}, status_code=404)
        return JSONResponse(
            {
                "tenant_id": updated.id,
                "slug": updated.slug,
                "display_name": updated.display_name,
                "assistant_owner": updated.assistant_owner,
                "assistant_capabilities": updated.assistant_capabilities,
            }
        )

    @app.post("/admin/tenants/{slug}/recycle")
    async def admin_recycle_tenant(slug: str, request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        if not is_platform_admin(principal):
            raise AuthError("missing permission", code="forbidden", status_code=403)
        tenant = _admin_managed_tenant(gateway, slug, expected_status="active")
        profile = gateway.platform.get_profile(tenant.id)
        headline = profile.headline if profile else ""
        gateway.platform.set_published(tenant.id, False, headline)
        gateway.platform.set_tenant_status(tenant.id, "trashed")
        return JSONResponse(
            {
                "ok": True,
                "slug": tenant.slug,
                "location": "/admin",
            }
        )

    @app.post("/admin/tenants/{slug}/restore")
    async def admin_restore_tenant(slug: str, request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        if not is_platform_admin(principal):
            raise AuthError("missing permission", code="forbidden", status_code=403)
        tenant = _admin_managed_tenant(gateway, slug, expected_status="trashed")
        gateway.platform.set_tenant_status(tenant.id, "active")
        return JSONResponse(
            {
                "ok": True,
                "slug": tenant.slug,
                "location": "/admin/recycle-bin",
            }
        )

    @app.delete("/admin/tenants/{slug}")
    async def admin_purge_tenant(slug: str, request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        if not is_platform_admin(principal):
            raise AuthError("missing permission", code="forbidden", status_code=403)
        tenant = _admin_managed_tenant(gateway, slug, expected_status="trashed")
        library = _library(request)
        try:
            documents = await run_io(library.list_documents, tenant_id=tenant.id)
        except (OSError, psycopg.Error, RetrievalBackendError):
            return JSONResponse(
                {"detail": "暂时读不到知识库文档，无法彻底删除。请确认 PostgreSQL 已启动。"},
                status_code=503,
            )
        failures: list[dict[str, str]] = []
        for document in documents:
            document_id = str(document.get("document_id") or "")
            if not document_id:
                continue
            try:
                store_failures = await run_io(
                    library.delete_document,
                    tenant_id=tenant.id,
                    document_id=document_id,
                )
            except LookupError:
                continue
            except ValueError as exc:
                failures.append({"store": "document", "reason": str(exc)})
                continue
            failures.extend(
                {"store": item.store, "reason": item.reason} for item in store_failures
            )
        if failures:
            return JSONResponse(
                {
                    "detail": "有存储没有删除成功，租户仍留在回收站",
                    "failures": failures,
                },
                status_code=503,
            )
        try:
            gateway.platform.delete_tenant(tenant.id)
        except LookupError:
            return JSONResponse({"detail": "租户不存在"}, status_code=404)
        return JSONResponse(
            {
                "ok": True,
                "deleted": True,
                "slug": tenant.slug,
                "location": "/admin/recycle-bin",
            }
        )

    @app.get("/admin/tenants/{slug}", response_model=None)
    async def admin_tenant_home(
        slug: str, request: Request
    ) -> HTMLResponse | RedirectResponse:
        gateway = _gateway(request)
        try:
            principal = gateway.authenticate(request)
        except AuthError:
            return RedirectResponse("/admin/login", status_code=303)
        require_scope(principal, "tasks:write")
        tenant = _workspace_tenant(gateway, principal, slug)
        tab = request.query_params.get("tab") or "resume"
        notice = ""
        error = ""
        if request.query_params.get("notice") == "stored":
            title = request.query_params.get("title") or "文档"
            chunks = request.query_params.get("chunks") or "0"
            if not chunks.isdigit():
                chunks = "0"
            notice = f"「{title}」已保存，切成 {chunks} 段，并完成索引和向量化。"
            tab = "docs"
        elif request.query_params.get("notice") == "removed":
            title = request.query_params.get("title") or "文档"
            notice = f"「{title}」已从 MinIO、Elasticsearch、Milvus 和 PostgreSQL 删除。"
            tab = "docs"
        elif request.query_params.get("notice") == "saved":
            notice = "简历栏目已保存。"
            tab = "resume"
        profile = gateway.platform.get_profile(tenant.id)
        sections = gateway.platform.list_sections(tenant.id)
        badge = 0
        trash_badge = 0
        if is_platform_admin(principal):
            badge = len(build_resume_library_rows(gateway.platform))
            trash_badge = len(
                build_resume_library_rows(gateway.platform, status="trashed")
            )
        documents: list[dict[str, str | int]] = []
        if tab == "docs":
            try:
                documents = await run_io(
                    _library(request).list_documents, tenant_id=tenant.id
                )
            except (OSError, psycopg.Error, RetrievalBackendError):
                error = "暂时读不到已入库的文档。请确认 PostgreSQL 已启动。"
        if tab == "docs":
            return HTMLResponse(
                library_page(
                    documents=documents,
                    notice=notice,
                    error=error,
                    slug=tenant.slug,
                    display_name=tenant.display_name,
                    show_back=is_platform_admin(principal),
                    assistant_owner=tenant.assistant_owner,
                    assistant_capabilities=tenant.assistant_capabilities,
                    created_at=getattr(tenant, "created_at", "") or "",
                    badge=badge,
                    trash_badge=trash_badge,
                    published=bool(profile.published) if profile else False,
                    headline=profile.headline if profile else "",
                )
            )
        return HTMLResponse(
            resume_page(
                slug=tenant.slug,
                display_name=tenant.display_name,
                published=bool(profile.published) if profile else False,
                headline=profile.headline if profile else "",
                sections=sections,
                notice=notice,
                error=error,
                show_back=is_platform_admin(principal),
                assistant_owner=tenant.assistant_owner,
                assistant_capabilities=tenant.assistant_capabilities,
                created_at=getattr(tenant, "created_at", "") or "",
                badge=badge,
                trash_badge=trash_badge,
            )
        )

    @app.put("/admin/tenants/{slug}/resume")
    async def admin_save_resume(slug: str, request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        tenant = _workspace_tenant(gateway, principal, slug)
        body = await request.json()
        published = bool(body.get("published", True))
        headline = str(body.get("headline") or "")
        gateway.platform.set_published(tenant.id, published, headline)
        sections = body.get("sections")
        if isinstance(sections, list):
            for index, item in enumerate(sections):
                if not isinstance(item, dict):
                    continue
                key = str(item.get("section_key") or "")
                if not key:
                    continue
                content = item.get("content")
                if not isinstance(content, dict):
                    content = {}
                if key == "BasicInfo":
                    content = normalize_basic_info(content)
                gateway.platform.upsert_section(
                    tenant_id=tenant.id,
                    section_key=key,
                    title=str(item.get("title") or key),
                    enabled=bool(item.get("enabled", True)),
                    sort_order=int(item.get("sort_order", index)),
                    content=content,
                )
        return JSONResponse(
            {
                "ok": True,
                "location": f"/admin/tenants/{tenant.slug}?tab=resume&notice=saved",
            }
        )

    @app.post("/admin/tenants/{slug}/documents")
    async def admin_upload(slug: str, request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        tenant = _workspace_tenant(gateway, principal, slug)
        form = await request.form()
        upload = form.get("file")
        filename = str(getattr(upload, "filename", "") or "")
        read = getattr(upload, "read", None)
        if upload is None or not callable(read) or not filename:
            return JSONResponse({"detail": "请选择一个文件"}, status_code=400)
        raw = await read()
        if not isinstance(raw, bytes) or not raw:
            return JSONResponse({"detail": "文件是空的"}, status_code=400)
        library = _library(request)
        try:
            document_id = await library.aingest_file(
                tenant_id=tenant.id,
                filename=filename,
                body=raw,
            )
        except ValueError as exc:
            return JSONResponse({"detail": _file_error(str(exc))}, status_code=400)
        except RetrievalBackendError:
            return JSONResponse(
                {
                    "detail": "存储、索引或向量化失败。请确认 MinIO、Elasticsearch、Milvus 和向量模型都可用。"
                },
                status_code=503,
            )
        chunk_count = len(await run_io(library.catalog.chunk_ids, document_id))
        return JSONResponse(
            {
                "document_id": document_id,
                "title": filename,
                "chunk_count": chunk_count,
                "location": (
                    f"/admin/tenants/{tenant.slug}?tab=docs&notice=stored"
                    f"&title={quote(filename)}&chunks={chunk_count}"
                ),
            }
        )

    @app.delete("/admin/tenants/{slug}/documents/{document_id}")
    async def admin_delete(
        slug: str, document_id: str, request: Request
    ) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        tenant = _workspace_tenant(gateway, principal, slug)
        library = _library(request)
        try:
            failures = await run_io(
                library.delete_document,
                tenant_id=tenant.id,
                document_id=document_id,
            )
        except LookupError:
            return JSONResponse({"detail": "没有这份文档"}, status_code=404)
        except ValueError:
            return JSONResponse({"detail": "请指定要删除的文档"}, status_code=400)
        if failures:
            return JSONResponse(
                {
                    "detail": "有存储没有删除成功",
                    "failures": [
                        {"store": item.store, "reason": item.reason} for item in failures
                    ],
                },
                status_code=503,
            )
        title = request.query_params.get("title") or "文档"
        return JSONResponse(
            {
                "deleted": True,
                "document_id": document_id,
                "location": (
                    f"/admin/tenants/{tenant.slug}?tab=docs&notice=removed"
                    f"&title={quote(title)}"
                ),
            }
        )

    # Backward-compatible aliases used by older tests / bookmarks.
    @app.get("/admin/documents", response_model=None)
    async def admin_documents_redirect(request: Request) -> RedirectResponse:
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/documents")
    async def admin_upload_legacy(request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        if is_platform_admin(principal):
            return JSONResponse(
                {"detail": "请进入具体租户文件夹后再上传文档"},
                status_code=400,
            )
        tenant = gateway.platform.get_tenant(principal.tenant_id)
        if tenant is None:
            return JSONResponse({"detail": "租户不存在"}, status_code=404)
        return await admin_upload(tenant.slug, request)

    @app.delete("/admin/documents/{document_id}")
    async def admin_delete_legacy(document_id: str, request: Request) -> JSONResponse:
        gateway = _gateway(request)
        principal = gateway.authenticate(request)
        require_scope(principal, "tasks:write")
        if is_platform_admin(principal):
            return JSONResponse(
                {"detail": "请进入具体租户文件夹后再删除文档"},
                status_code=400,
            )
        tenant = gateway.platform.get_tenant(principal.tenant_id)
        if tenant is None:
            return JSONResponse({"detail": "租户不存在"}, status_code=404)
        return await admin_delete(tenant.slug, document_id, request)


def _admin_placeholder(
    request: Request,
    *,
    active: str,
    title: str,
    crumb: str,
    message: str,
    user_count: bool = False,
) -> HTMLResponse | RedirectResponse:
    gateway = _gateway(request)
    try:
        principal = gateway.authenticate(request)
    except AuthError:
        return RedirectResponse("/admin/login", status_code=303)
    require_scope(principal, "tasks:write")
    if not is_platform_admin(principal):
        tenant = gateway.platform.get_tenant(principal.tenant_id)
        if tenant is None:
            return HTMLResponse(login_page("账号未绑定租户"), status_code=403)
        return RedirectResponse(f"/admin/tenants/{tenant.slug}", status_code=303)
    badge = len(build_resume_library_rows(gateway.platform)) if user_count else 0
    trash_badge = len(build_resume_library_rows(gateway.platform, status="trashed"))
    return HTMLResponse(
        admin_placeholder_page(
            active=active,
            title=title,
            crumb=crumb,
            message=message,
            badge=badge,
            trash_badge=trash_badge,
        )
    )


def _admin_managed_tenant(
    gateway: GatewayState, slug: str, *, expected_status: str
) -> Any:
    cleaned = slug.strip().lower()
    if cleaned == "platform":
        raise AuthError("tenant not found", code="not_found", status_code=404)
    tenant = gateway.platform.get_tenant_by_slug(cleaned)
    if tenant is None or tenant.status != expected_status:
        raise AuthError("tenant not found", code="not_found", status_code=404)
    return tenant


def _workspace_tenant(gateway: GatewayState, principal: Any, slug: str) -> Any:
    tenant = gateway.platform.get_tenant_by_slug(slug.strip().lower())
    if tenant is None or tenant.status != "active":
        raise AuthError("tenant not found", code="not_found", status_code=404)
    if is_platform_admin(principal):
        return tenant
    if principal.tenant_id != tenant.id:
        raise AuthError("missing permission", code="forbidden", status_code=403)
    return tenant


def _gateway(request: Request) -> GatewayState:
    gateway = request.app.state.gateway
    if not isinstance(gateway, GatewayState):
        raise AuthError("gateway is not ready", code="not_configured", status_code=503)
    return gateway


def _library(request: Request) -> RagService:
    library = request.app.state.rag
    if not isinstance(library, RagService):
        raise AuthError("library is not ready", code="not_configured", status_code=503)
    return library


def _logged_in(request: Request) -> bool:
    try:
        _gateway(request).authenticate(request)
    except AuthError:
        return False
    return True


def _file_error(message: str) -> str:
    if message.startswith("unsupported file type"):
        return "不支持这个文件类型。请上传 txt、md、csv、pdf 或 docx。"
    labels = {
        "file has no text": "文件里没有可读取的文字。",
        "file is not utf-8 text": "文本文件需要是 UTF-8 编码。",
        "could not read the docx": "无法读取这个 Word 文件。",
        "could not read the pdf": "无法读取这个 PDF。",
        "pdf support is not installed": "服务器没有安装 PDF 读取组件。",
        "tenant_id, filename, and file bytes are required": "请选择一个文件。",
    }
    return labels.get(message, "这个文件无法入库。")
