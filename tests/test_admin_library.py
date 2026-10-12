"""The admin site logs in, then stores, chunks, and indexes a file."""

from fastapi.testclient import TestClient
from fs_gateway.context import GatewayState
from fs_gateway.main import create_app
from fs_gateway.platform.store import MemoryPlatformStore
from fs_gateway.settings import GatewaySettings
from fs_orchestrator import ThinOrchestrator
from fs_rag_service import build_memory_rag

TENANT = "tenant_dev"
SLUG = "dev"


def _app() -> tuple[TestClient, object]:
    settings = GatewaySettings(
        jwt_secret="test-secret-must-be-at-least-32-bytes",
        password_iterations=1000,
        cors_origins="http://localhost:8000",
        bootstrap_username="unity",
        bootstrap_password="unity-secret",
        bootstrap_tenant_id=TENANT,
        admin_username="admin",
        admin_password="admin-secret",
        admin_scopes="tasks:write,kb:read,platform:admin",
    )
    state = GatewayState.create(settings, platform=MemoryPlatformStore())
    library = build_memory_rag()
    client = TestClient(create_app(settings, state, ThinOrchestrator(), rag=library))
    return client, library


def test_admin_account_sits_beside_the_unity_account() -> None:
    client, _ = _app()
    unity = client.post("/v1/auth/login", json={"username": "unity", "password": "unity-secret"})
    assert unity.status_code == 200
    assert unity.json()["principal"]["tenant_id"] == TENANT


def test_library_requires_login_then_stores_a_document() -> None:
    client, library = _app()
    locked = client.get("/admin", follow_redirects=False)
    assert locked.status_code == 303
    assert locked.headers["location"] == "/admin/login"

    page = client.get("/admin/login")
    assert page.status_code == 200
    assert "管理员登录" in page.text

    rejected = client.post(
        "/admin/login",
        data={"username": "admin", "password": "wrong"},
        follow_redirects=False,
    )
    assert rejected.status_code == 401
    assert "用户名或密码错误" in rejected.text

    login = client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin-secret"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/admin"

    home = client.get("/admin")
    assert home.status_code == 200
    assert "用户简历库" in home.text

    foreign = client.post(
        f"/admin/tenants/{SLUG}/documents",
        files={"file": ("差旅制度.txt", "差旅住宿不超过800元".encode(), "text/plain")},
        headers={
            "X-CSRF-Token": client.cookies.get("fs_csrf"),
            "Origin": "https://evil.example",
        },
    )
    assert foreign.status_code == 403
    assert foreign.json()["detail"] == "origin not allowed"

    uploaded = client.post(
        f"/admin/tenants/{SLUG}/documents",
        files={"file": ("差旅制度.txt", "差旅住宿不超过800元".encode(), "text/plain")},
        headers={
            "X-CSRF-Token": client.cookies.get("fs_csrf"),
            "Origin": "http://testserver",
        },
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["chunk_count"] >= 1
    assert library.list_documents(tenant_id=TENANT)[0]["title"] == "差旅制度.txt"

    library_page = client.get(f"/admin/tenants/{SLUG}?tab=docs")
    assert library_page.status_code == 200
    assert "差旅制度.txt" in library_page.text
    assert "上传并入库" in library_page.text

    rejected_file = client.post(
        f"/admin/tenants/{SLUG}/documents",
        files={"file": ("notes.zip", b"not a document", "application/zip")},
        headers={"X-CSRF-Token": client.cookies.get("fs_csrf")},
    )
    assert rejected_file.status_code == 400
    assert "不支持这个文件类型" in rejected_file.json()["detail"]

    logged_out = client.post(
        "/admin/logout",
        headers={"X-CSRF-Token": client.cookies.get("fs_csrf")},
    )
    assert logged_out.status_code == 200
    again = client.get("/admin", follow_redirects=False)
    assert again.status_code == 303


def test_admin_deletes_one_document_from_every_store() -> None:
    client, library = _app()
    _login(client)
    kept = _upload(client, "员工手册.txt", "员工手册规定差旅住宿不超过800元")
    removed = _upload(client, "差旅制度.txt", "差旅住宿不超过800元")
    page = client.get(f"/admin/tenants/{SLUG}?tab=docs")
    assert "删除" in page.text
    assert 'id="failure-dialog"' in page.text

    deleted_id = removed["document_id"]
    deleted_chunks = set(library.catalog.chunk_ids(deleted_id))
    assert deleted_chunks
    response = client.delete(
        f"/admin/tenants/{SLUG}/documents/{deleted_id}?title=差旅制度.txt",
        headers=_headers(client),
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    titles = [item["title"] for item in library.list_documents(tenant_id=TENANT)]
    assert titles == ["员工手册.txt"]
    assert library.catalog.get_document(deleted_id, tenant_id=TENANT) is None
    assert kept["document_id"] != deleted_id
    remaining_chunks = {
        chunk.chunk_id
        for chunk in library.catalog.chunks.values()
    }
    assert deleted_chunks.isdisjoint(remaining_chunks)
    lexical_ids = {row[0] for row in library.lexical._docs.get(TENANT, [])}
    vector_ids = {row[0] for row in library.vectors._rows.get(TENANT, [])}
    assert deleted_chunks.isdisjoint(lexical_ids)
    assert deleted_chunks.isdisjoint(vector_ids)
    assert all(deleted_id not in key for key in library.objects.bodies)
    library_page = client.get(
        f"/admin/tenants/{SLUG}?tab=docs&notice=removed&title=差旅制度.txt"
    )
    assert "已从 MinIO、Elasticsearch、Milvus 和 PostgreSQL 删除" in library_page.text
    assert "员工手册.txt" in library_page.text
    assert 'data-title="差旅制度.txt"' not in library_page.text


def test_delete_returns_every_store_that_failed() -> None:
    client, library = _app()
    _login(client)
    uploaded = _upload(client, "差旅制度.txt", "差旅住宿不超过800元")
    document_id = uploaded["document_id"]

    def fail_milvus(tenant_id: str, chunk_ids: list[str]) -> None:
        del tenant_id, chunk_ids
        raise RuntimeError("milvus connection refused")

    def fail_minio(key: str) -> None:
        del key
        raise RuntimeError("minio bucket unreachable")

    library.vectors.delete = fail_milvus
    library.objects.delete = fail_minio
    response = client.delete(
        f"/admin/tenants/{SLUG}/documents/{document_id}",
        headers=_headers(client),
    )
    assert response.status_code == 503
    stores = {item["store"]: item["reason"] for item in response.json()["failures"]}
    assert "milvus connection refused" in stores["Milvus"]
    assert "minio bucket unreachable" in stores["MinIO"]
    assert "Elasticsearch" not in stores
    assert "PostgreSQL" not in stores
    assert library.list_documents(tenant_id=TENANT)[0]["document_id"] == document_id
    assert library.objects.bodies
    assert library.vectors._rows.get(TENANT)
    assert library.lexical._docs.get(TENANT) == []

    missing = client.delete(
        f"/admin/tenants/{SLUG}/documents/doc_missing", headers=_headers(client)
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "没有这份文档"


def test_postgres_failure_is_reported_after_the_other_stores_succeed() -> None:
    client, library = _app()
    _login(client)
    uploaded = _upload(client, "差旅制度.txt", "差旅住宿不超过800元")

    def fail_postgres(document_id: str, *, tenant_id: str) -> None:
        del document_id, tenant_id
        raise RuntimeError("postgres unavailable")

    library.catalog.delete_document = fail_postgres
    response = client.delete(
        f"/admin/tenants/{SLUG}/documents/{uploaded['document_id']}",
        headers=_headers(client),
    )
    assert response.status_code == 503
    failures = response.json()["failures"]
    assert [item["store"] for item in failures] == ["PostgreSQL"]
    assert "postgres unavailable" in failures[0]["reason"]
    assert library.list_documents(tenant_id=TENANT)


def _login(client: TestClient) -> None:
    login = client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin-secret"},
        follow_redirects=False,
    )
    assert login.status_code == 303


def _headers(client: TestClient) -> dict[str, str]:
    return {
        "X-CSRF-Token": client.cookies.get("fs_csrf"),
        "Origin": "http://testserver",
    }


def _upload(client: TestClient, name: str, text: str) -> dict[str, object]:
    uploaded = client.post(
        f"/admin/tenants/{SLUG}/documents",
        files={"file": (name, text.encode(), "text/plain")},
        headers=_headers(client),
    )
    assert uploaded.status_code == 200
    return uploaded.json()
