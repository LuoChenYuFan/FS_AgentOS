"""Platform admin folders, public resume API, and tenant-scoped CMS."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fs_gateway.context import GatewayState
from fs_gateway.main import create_app
from fs_gateway.platform.store import MemoryPlatformStore
from fs_gateway.settings import GatewaySettings
from fs_orchestrator import ThinOrchestrator
from fs_rag_service import build_memory_rag

# Private local seed; gitignored. CI and fresh clones skip seed-content checks.
_RESUME_YUFAN_SEED = (
    Path(__file__).resolve().parents[1] / "deploy" / "seeds" / "resume_yufan.json"
)


def _app() -> tuple[TestClient, GatewayState, object]:
    settings = GatewaySettings(
        jwt_secret="test-secret-must-be-at-least-32-bytes",
        password_iterations=1000,
        cors_origins="http://localhost:8000",
        bootstrap_username="unity",
        bootstrap_password="unity-secret",
        bootstrap_tenant_id="tenant_yufan",
        admin_username="admin",
        admin_password="admin-secret",
        admin_scopes="tasks:write,kb:read,platform:admin",
    )
    state = GatewayState.create(settings, platform=MemoryPlatformStore())
    library = build_memory_rag()
    client = TestClient(create_app(settings, state, ThinOrchestrator(), rag=library))
    return client, state, library


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


@pytest.mark.skipif(
    not _RESUME_YUFAN_SEED.exists(),
    reason="private resume seed not present",
)
def test_public_resume_for_seeded_yufan() -> None:
    client, state, _ = _app()
    missing = client.get("/v1/sites/nobody/resume")
    assert missing.status_code == 404

    published = client.get("/v1/sites/yufan/resume")
    assert published.status_code == 200
    body = published.json()
    assert body["slug"] == "yufan"
    assert body["tenant_id"] == "tenant_yufan"
    keys = [item["section_key"] for item in body["sections"]]
    assert "BasicInfo" in keys
    assert "Strengths" in keys
    work = next(item for item in body["sections"] if item["section_key"] == "WorkExperience")
    cards = work["content"]["cards"]
    assert len(cards) == 2
    assert "锋尚" in cards[0]["title"]
    assert "智联" in cards[1]["title"]

    unity = client.post(
        "/v1/auth/login", json={"username": "unity", "password": "unity-secret"}
    )
    assert unity.status_code == 200
    assert unity.json()["principal"]["tenant_id"] == "tenant_yufan"


def test_platform_admin_sees_folders_and_can_edit_resume() -> None:
    client, state, _ = _app()
    _login(client)
    home = client.get("/admin")
    assert home.status_code == 200
    assert "用户简历库" in home.text
    assert "yufan" in home.text or "张博洋" in home.text
    assert "查看详情" in home.text
    assert "/admin/tenants/yufan" in home.text
    assert "概览 Dashboard" in home.text
    assert "简历解析记录" in home.text
    assert "人才画像分析" in home.text

    workspace = client.get("/admin/tenants/yufan?tab=resume")
    assert workspace.status_code == 200
    assert "简历详情" in workspace.text
    assert "RAG 知识库" in workspace.text
    assert "教育经历" in workspace.text
    assert "专业技能" in workspace.text

    saved = client.put(
        "/admin/tenants/yufan/resume",
        headers=_headers(client),
        json={
            "published": True,
            "headline": "测试标题",
            "sections": [
                {
                    "section_key": "BasicInfo",
                    "title": "基本信息",
                    "enabled": True,
                    "sort_order": 0,
                    "content": {
                        "fields": [
                            {"node": "x", "label": "姓名：", "value": "测试员"},
                            {"node": "j", "label": "籍贯：", "value": "天津"},
                        ]
                    },
                },
                {
                    "section_key": "Strengths",
                    "title": "个人优势",
                    "enabled": False,
                    "sort_order": 6,
                    "content": {"body": "隐藏"},
                },
            ],
        },
    )
    assert saved.status_code == 200
    public = client.get("/v1/sites/yufan/resume").json()
    assert public["headline"] == "测试标题"
    keys = {item["section_key"] for item in public["sections"]}
    assert "BasicInfo" in keys
    assert "Strengths" not in keys
    basic = next(item for item in public["sections"] if item["section_key"] == "BasicInfo")
    assert [item["node"] for item in basic["content"]["fields"]] == [
        "Name",
        "Place of origin",
        "Educational background",
        "Contact phone number",
        "Email",
    ]
    assert basic["content"]["fields"][0]["label"] == "姓名"
    assert basic["content"]["fields"][0]["value"] == "测试员"
    assert basic["content"]["fields"][1]["value"] == "天津"


def test_rename_tenant_display_name() -> None:
    client, state, _ = _app()
    _login(client)
    renamed = client.patch(
        "/admin/tenants/yufan",
        headers=_headers(client),
        json={
            "display_name": "张博洋AI应用开发",
            "assistant_owner": "张博洋",
            "assistant_capabilities": "查天气、查路线、查简历信息、发邮件",
        },
    )
    assert renamed.status_code == 200
    body = renamed.json()
    assert body["display_name"] == "张博洋AI应用开发"
    assert body["assistant_owner"] == "张博洋"
    assert "查酒店" not in body["assistant_capabilities"]
    assert "查简历信息" in body["assistant_capabilities"]
    tenant = state.platform.get_tenant("tenant_yufan")
    assert tenant.display_name == "张博洋AI应用开发"
    assert tenant.assistant_owner == "张博洋"
    home = client.get("/admin/tenants/yufan")
    assert home.status_code == 200
    assert "张博洋" in home.text
    assert "AI 主人姓名" in home.text
    assert client.patch(
        "/admin/tenants/yufan",
        headers=_headers(client),
        json={"display_name": "  "},
    ).status_code == 400


def test_create_tenant_folder_and_upload_docs_isolated() -> None:
    client, state, library = _app()
    _login(client)
    created = client.post(
        "/admin/tenants",
        headers=_headers(client),
        json={
            "slug": "alice",
            "display_name": "Alice",
            "ai_username": "unity_alice",
            "ai_password": "alice-secret",
        },
    )
    assert created.status_code == 200
    assert created.json()["slug"] == "alice"

    uploaded = client.post(
        "/admin/tenants/alice/documents",
        files={"file": ("a.txt", "Alice 的知识".encode(), "text/plain")},
        headers=_headers(client),
    )
    assert uploaded.status_code == 200
    assert library.list_documents(tenant_id="tenant_alice")
    assert library.list_documents(tenant_id="tenant_yufan") == []

    yufan_docs = client.get("/admin/tenants/yufan?tab=docs")
    assert yufan_docs.status_code == 200
    assert "Alice 的知识" not in yufan_docs.text


def test_site_tasks_follow_slug_and_keep_interviewers_apart() -> None:
    client, state, library = _app()
    state.platform.create_tenant(
        tenant_id="tenant_wangbo",
        slug="wangbo",
        display_name="王博",
        ai_username="unity_wangbo",
        ai_password="wangbo-secret",
    )
    library.ingest_text(
        tenant_id="tenant_yufan",
        title="张博洋年假",
        text="查阅制度编号 HR-2024-001\n张博洋的公开年假是五天整",
    )
    library.ingest_text(
        tenant_id="tenant_wangbo",
        title="王博年假",
        text="查阅制度编号 HR-2024-001\n王博的公开年假是三十天整",
    )

    hidden = client.post("/v1/sites/nobody/tasks", json={"text": "你是谁"})
    assert hidden.status_code == 404

    yufan = client.post(
        "/v1/sites/yufan/tasks",
        json={"text": "查阅制度编号 HR-2024-001", "conversation_id": "interviewer-yufan-1"},
    )
    wangbo = client.post(
        "/v1/sites/wangbo/tasks",
        json={"text": "查阅制度编号 HR-2024-001", "conversation_id": "interviewer-wangbo-1"},
    )
    assert yufan.status_code == 200
    assert wangbo.status_code == 200
    assert yufan.json()["slug"] == "yufan"
    assert wangbo.json()["slug"] == "wangbo"
    assert "五天整" in _citation_blob(yufan.json())
    assert "三十天整" not in _citation_blob(yufan.json())
    assert "三十天整" in _citation_blob(wangbo.json())
    assert "五天整" not in _citation_blob(wangbo.json())

    alpha = TestClient(client.app)
    beta = TestClient(client.app)
    left = alpha.post(
        "/v1/sites/yufan/tasks",
        json={"text": "你是谁", "conversation_id": "default"},
    )
    right = beta.post(
        "/v1/sites/yufan/tasks",
        json={"text": "你是谁", "conversation_id": "default"},
    )
    assert left.status_code == 200
    assert right.status_code == 200
    left_id = left.json()["conversation_id"]
    right_id = right.json()["conversation_id"]
    assert left_id != "default"
    assert right_id != "default"
    assert left_id != right_id
    board = client.app.state.orchestrator.dialogue
    left_state = board.load("tenant_yufan", f"guest_{left_id}", conversation_id=left_id)
    right_state = board.load(
        "tenant_yufan", f"guest_{right_id}", conversation_id=right_id
    )
    assert len(left_state.turns) == 2
    assert len(right_state.turns) == 2

    again = alpha.post("/v1/sites/yufan/tasks", json={"text": "你是谁"})
    assert again.status_code == 200
    assert again.json()["conversation_id"] == left_id
    continued = board.load("tenant_yufan", f"guest_{left_id}", conversation_id=left_id)
    still_apart = board.load(
        "tenant_yufan", f"guest_{right_id}", conversation_id=right_id
    )
    assert len(continued.turns) == 4
    assert len(still_apart.turns) == 2


def _citation_blob(payload: dict) -> str:
    context = payload.get("retrieved_context") or {}
    citations = context.get("citations") or []
    return "\n".join(str(item.get("text") or "") for item in citations if isinstance(item, dict))


def test_unpublished_resume_is_hidden() -> None:
    client, state, _ = _app()
    _login(client)
    client.put(
        "/admin/tenants/yufan/resume",
        headers=_headers(client),
        json={"published": False, "headline": "x", "sections": []},
    )
    assert client.get("/v1/sites/yufan/resume").status_code == 404


def test_recycle_restore_and_purge_tenant() -> None:
    client, state, library = _app()
    _login(client)
    created = client.post(
        "/admin/tenants",
        headers=_headers(client),
        json={
            "slug": "bob",
            "display_name": "Bob",
            "ai_username": "unity_bob",
            "ai_password": "bob-secret",
        },
    )
    assert created.status_code == 200
    uploaded = client.post(
        "/admin/tenants/bob/documents",
        files={"file": ("note.txt", "Bob 的知识".encode(), "text/plain")},
        headers=_headers(client),
    )
    assert uploaded.status_code == 200
    assert library.list_documents(tenant_id="tenant_bob")

    recycled = client.post(
        "/admin/tenants/bob/recycle",
        headers=_headers(client),
    )
    assert recycled.status_code == 200
    home = client.get("/admin")
    assert 'data-trash="bob"' not in home.text
    assert state.platform.get_tenant("tenant_bob").status == "trashed"
    assert client.get("/admin/tenants/bob").status_code == 404

    trash = client.get("/admin/recycle-bin")
    assert trash.status_code == 200
    assert "Bob" in trash.text
    assert "回收站" in trash.text

    restored = client.post(
        "/admin/tenants/bob/restore",
        headers=_headers(client),
    )
    assert restored.status_code == 200
    assert state.platform.get_tenant("tenant_bob").status == "active"
    assert "Bob" in client.get("/admin").text

    client.post("/admin/tenants/bob/recycle", headers=_headers(client))
    purged = client.delete("/admin/tenants/bob", headers=_headers(client))
    assert purged.status_code == 200
    assert state.platform.get_tenant("tenant_bob") is None
    assert library.list_documents(tenant_id="tenant_bob") == []
    assert "Bob" not in client.get("/admin/recycle-bin").text
