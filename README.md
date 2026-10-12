# FS_AgentOS

企业级多 Agent 协作平台（Phase 0 骨架）。

## 环境

- Python **3.13**（见 `.python-version`）
- 虚拟环境：`.venv`
- 包管理：`uv`

```powershell
.\.venv\Scripts\Activate.ps1
uv sync
```

## 启动 Gateway（健康检查）

```powershell
uv run uvicorn fs_gateway.main:app --reload --port 8000
```

访问 `http://127.0.0.1:8000/health`。

## 仓库布局

```
apps/           # 可运行服务
packages/       # 共享库与协议
deploy/helm/    # 部署清单（后续）
docs/architecture/
```
