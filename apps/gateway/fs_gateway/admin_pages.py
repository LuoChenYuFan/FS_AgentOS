"""HTML for the platform admin console."""

from __future__ import annotations

import json
from html import escape
from typing import Any


def login_page(error: str = "") -> str:
    err_class = " err" if error else ""
    message = (
        f'<p class="err-msg" role="alert">{escape(error)}</p>'
        if error
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>管理员登录 · 简历管理后台</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
:root {{
  --primary:#3b5bfd; --text-1:#1a1f36; --text-2:#4e5875; --text-3:#8a93a8;
  --border:#e6e9f0; --danger:#ef4444;
}}
body {{
  background:#f5f7fa;
  font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;
  color:var(--text-1); font-size:14px; -webkit-font-smoothing:antialiased;
}}
.login-wrap {{ display:flex; min-height:100vh; }}
.brand {{
  flex:1.15; position:relative; overflow:hidden;
  background:linear-gradient(150deg,#1c2338 0%,#26305a 45%,#34409b 100%);
  display:flex; flex-direction:column; justify-content:space-between;
  padding:44px 56px 40px; color:#fff;
}}
.brand::before {{
  content:''; position:absolute; width:520px; height:520px; border-radius:50%;
  background:radial-gradient(circle,rgba(124,92,255,.55),transparent 68%);
  top:-140px; right:-120px;
}}
.brand::after {{
  content:''; position:absolute; width:420px; height:420px; border-radius:50%;
  background:radial-gradient(circle,rgba(59,91,253,.45),transparent 70%);
  bottom:-160px; left:-100px;
}}
.grid-lines {{
  position:absolute; inset:0; opacity:.16;
  background-image:
    linear-gradient(rgba(255,255,255,.35) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.35) 1px, transparent 1px);
  background-size:46px 46px;
  mask-image:radial-gradient(ellipse at 50% 40%, #000 20%, transparent 78%);
  -webkit-mask-image:radial-gradient(ellipse at 50% 40%, #000 20%, transparent 78%);
}}
.brand > * {{ position:relative; z-index:2; }}
.brand-top {{ display:flex; align-items:center; gap:11px; }}
.brand-top .mark {{
  width:34px; height:34px; border-radius:9px;
  background:linear-gradient(135deg,#3b5bfd,#7c5cff);
  display:flex; align-items:center; justify-content:center;
  font-weight:700; font-size:16px;
  box-shadow:0 4px 14px rgba(59,91,253,.45);
}}
.brand-top span {{ font-size:15.5px; font-weight:600; letter-spacing:.5px; }}
.brand-mid {{ max-width:460px; }}
.brand-mid h1 {{ font-size:34px; font-weight:600; line-height:1.35; letter-spacing:.4px; }}
.brand-mid h1 em {{
  font-style:normal;
  background:linear-gradient(90deg,#8ea2ff,#c4b5ff);
  -webkit-background-clip:text; background-clip:text; color:transparent;
}}
.brand-mid p {{ font-size:14px; line-height:1.9; color:#b9c3e0; margin-top:14px; }}
.brand-cards {{ display:flex; gap:12px; margin-top:32px; }}
.bc {{
  flex:1; background:rgba(255,255,255,.07);
  border:1px solid rgba(255,255,255,.13);
  border-radius:11px; padding:14px 15px;
}}
.bc .n {{ font-size:16px; font-weight:600; letter-spacing:.3px; }}
.bc .l {{ font-size:11.5px; color:#a8b3d0; margin-top:5px; line-height:1.5; }}
.bc .bar {{
  height:3px; border-radius:2px; margin-top:10px;
  background:linear-gradient(90deg,#3b5bfd,#7c5cff);
}}
.brand-bot {{
  display:flex; align-items:center; gap:22px; flex-wrap:wrap;
  font-size:12px; color:#93a0c2;
}}
.brand-bot .dotok {{
  display:inline-block; width:6px; height:6px; border-radius:50%;
  background:#4ade80; margin-right:6px;
  box-shadow:0 0 0 3px rgba(74,222,128,.18);
}}
.panel {{
  width:520px; flex-shrink:0; background:#fff;
  display:flex; align-items:center; justify-content:center;
  padding:40px 56px;
}}
.form-box {{ width:100%; max-width:360px; }}
.form-head {{ margin-bottom:26px; }}
.form-head h2 {{ font-size:23px; font-weight:600; letter-spacing:.3px; }}
.form-head p {{ font-size:12.5px; color:var(--text-3); margin-top:7px; }}
.fi {{ margin-bottom:16px; }}
.fi label {{
  display:block; font-size:12.5px; color:var(--text-2); margin-bottom:7px; font-weight:500;
}}
.inp-line {{
  height:42px; border:1px solid var(--border); border-radius:9px;
  display:flex; align-items:center; padding:0 12px; gap:9px;
  background:#fbfcfe; transition:.15s;
}}
.inp-line:focus-within {{
  border-color:var(--primary); background:#fff;
  box-shadow:0 0 0 3px rgba(59,91,253,.11);
}}
.inp-line svg {{ flex-shrink:0; }}
.inp-line input {{
  flex:1; border:none; outline:none; background:transparent;
  font-size:13.5px; font-family:inherit; color:var(--text-1); height:100%; min-width:0;
}}
.inp-line input::placeholder {{ color:#aab2c4; }}
.eye {{
  border:none; background:transparent; padding:0; cursor:pointer;
  display:flex; align-items:center;
}}
.btn-login {{
  width:100%; height:44px; border:none; border-radius:9px; margin-top:8px;
  background:linear-gradient(90deg,#3b5bfd,#5a72ff);
  color:#fff; font-size:14.5px; font-weight:500; font-family:inherit;
  cursor:pointer; letter-spacing:1px;
  box-shadow:0 6px 18px rgba(59,91,253,.32);
  display:flex; align-items:center; justify-content:center; gap:8px;
}}
.btn-login:hover {{ filter:brightness(1.05); }}
.safe-tip {{
  margin-top:22px; display:flex; align-items:center; gap:6px;
  font-size:11.5px; color:var(--text-3); justify-content:center;
}}
.err {{
  border-color:var(--danger) !important;
  box-shadow:0 0 0 3px rgba(239,68,68,.1) !important;
}}
.err-msg {{
  font-size:12.5px; color:var(--danger); margin:-4px 0 16px;
  display:flex; gap:5px; align-items:center;
}}
@media (max-width: 900px) {{
  .login-wrap {{ flex-direction:column; }}
  .brand {{ padding:28px 24px; min-height:280px; }}
  .brand-mid h1 {{ font-size:26px; }}
  .brand-cards {{ display:none; }}
  .panel {{ width:100%; padding:32px 24px 48px; }}
}}
</style>
</head>
<body>
<div class="login-wrap">
  <section class="brand">
    <div class="grid-lines"></div>
    <div class="brand-top">
      <div class="mark">简</div>
      <span>简历管理后台</span>
    </div>
    <div class="brand-mid">
      <h1>让每一份简历<br><em>都能被精准看见</em></h1>
      <p>按站点管理候选人档案、栏目和知识库文档。面试官打开对应网址即可查看，不用再填账号。</p>
      <div class="brand-cards">
        <div class="bc">
          <div class="n">按人隔离</div>
          <div class="l">简历、文档和检索各自独立</div>
          <div class="bar" style="width:82%"></div>
        </div>
        <div class="bc">
          <div class="n">公开网址</div>
          <div class="l">地址里的人名决定看谁</div>
          <div class="bar" style="width:92%"></div>
        </div>
        <div class="bc">
          <div class="n">独立会话</div>
          <div class="l">同一链接互不影响</div>
          <div class="bar" style="width:64%"></div>
        </div>
      </div>
    </div>
    <div class="brand-bot">
      <span><span class="dotok"></span>系统运行正常</span>
      <span>登录后进入用户简历库</span>
      <span>© 2026 简历管理后台</span>
    </div>
  </section>
  <section class="panel">
    <div class="form-box">
      <div class="form-head">
        <h2>管理员登录</h2>
        <p>请使用管理员账号进入简历管理后台</p>
      </div>
      <form method="post" action="/admin/login">
        {message}
        <div class="fi">
          <label for="username">管理员账号</label>
          <div class="inp-line{err_class}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#8a93a8" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            <input id="username" name="username" autocomplete="username" required placeholder="请输入账号">
          </div>
        </div>
        <div class="fi">
          <label for="password">登录密码</label>
          <div class="inp-line{err_class}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#8a93a8" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            <input id="password" name="password" type="password" autocomplete="current-password" required placeholder="请输入登录密码">
            <button class="eye" type="button" id="toggle-password" aria-label="显示密码">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#8a93a8" stroke-width="2"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
        </div>
        <button class="btn-login" type="submit">
          登 录
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>
        </button>
      </form>
      <div class="safe-tip">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#16a34a" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>
        登录记录可追溯
      </div>
    </div>
  </section>
</div>
<script>
  document.querySelector("#toggle-password").addEventListener("click", () => {{
    const input = document.querySelector("#password");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    document.querySelector("#toggle-password").setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
  }});
</script>
</body>
</html>
"""


def tenant_list_page(
    *,
    users: list[dict[str, Any]],
    stats: dict[str, Any] | None = None,
    notice: str = "",
    error: str = "",
    trash_badge: int = 0,
) -> str:
    stats = stats or {
        "total": len(users),
        "today": 0,
        "incomplete": 0,
        "complete_rate": "0%",
    }
    banner = _banner(notice, error)
    total = int(stats.get("total") or len(users))
    rows_html = _resume_user_rows(users)
    trash_script = """
      <script>
        document.querySelector("#user-table")?.addEventListener("click", async (event) => {
          const button = event.target.closest("[data-trash]");
          if (!button) return;
          const slug = button.dataset.trash;
          const name = button.dataset.name || slug;
          if (!window.confirm(`确定将「${name}」移入回收站？移入后不会出现在用户简历库中。`)) return;
          button.disabled = true;
          const response = await fetch(`/admin/tenants/${encodeURIComponent(slug)}/recycle`, {
            method: "POST",
            headers: { "X-CSRF-Token": csrfToken() },
            credentials: "same-origin",
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) {
            button.disabled = false;
            window.alert(body.detail || "删除失败");
            return;
          }
          window.location.assign(body.location || "/admin");
        });
      </script>
    """
    main = f"""
      <div class="page-head">
        <div>
          <h1>用户简历库</h1>
          <p>管理系统中所有已登记用户的简历信息，点击操作可进入该用户个人界面</p>
        </div>
        <div class="actions">
          <button class="btn" type="button" disabled title="即将支持">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>
            导出 Excel
          </button>
          <button class="btn primary" type="button" id="open-create">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
            新增用户
          </button>
        </div>
      </div>
      {banner}
      <div class="stats">
        <div class="stat-card">
          <div class="ic-box" style="background:#eef2ff">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b5bfd" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/></svg>
          </div>
          <div>
            <div class="num">{total}</div>
            <div class="lbl">简历总数</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="ic-box" style="background:#e8f8ee">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#16a34a" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6"/><path d="M22 11h-6"/></svg>
          </div>
          <div>
            <div class="num">{int(stats.get("today") or 0)}</div>
            <div class="lbl">今日新增登记</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="ic-box" style="background:#fef4e6">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
          </div>
          <div>
            <div class="num">{int(stats.get("incomplete") or 0)}</div>
            <div class="lbl">待完善简历</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="ic-box" style="background:#f3edff">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#7c5cff" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/></svg>
          </div>
          <div>
            <div class="num">{escape(str(stats.get("complete_rate") or "0%"))}</div>
            <div class="lbl">信息完整率</div>
          </div>
        </div>
      </div>
      <div class="toolbar">
        <input class="input search" id="filter-q" placeholder="搜索姓名 / 电话 / 邮箱">
        <div class="field">
          <label>求职意向</label>
          <select class="select" id="filter-intent"><option value="">全部岗位</option></select>
        </div>
        <div class="field">
          <label>状态</label>
          <select class="select" id="filter-status">
            <option value="">全部</option>
            <option value="已完善">已完善</option>
            <option value="待完善">待完善</option>
          </select>
        </div>
        <div style="margin-left:auto;display:flex;gap:10px">
          <button class="btn" type="button" id="filter-reset">重置</button>
          <button class="btn primary" type="button" id="filter-apply">查询</button>
        </div>
      </div>
      <div class="panel">
        <div class="card-head">
          <h3>用户列表</h3>
          <span class="sub" id="row-count">共 {total} 条记录</span>
        </div>
        <div class="table-wrap">
          <table class="grid" id="user-table">
            <thead>
              <tr>
                <th style="width:52px">序号</th>
                <th style="width:210px">姓名</th>
                <th>求职意向</th>
                <th style="width:150px">联系方式</th>
                <th style="width:150px">登记时间</th>
                <th style="width:96px">状态</th>
                <th style="width:120px">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
        </div>
      </div>
      <div class="mask" id="create-mask" hidden>
        <div class="modal" role="dialog" aria-labelledby="create-title">
          <div class="modal-head">
            <h3 id="create-title">新增用户</h3>
            <button type="button" class="x" id="close-create" aria-label="关闭">×</button>
          </div>
          <form id="create-tenant" class="modal-body stack">
            <div class="form-item">
              <label>站点标识 slug（小写，创建后不可改）</label>
              <input class="input" name="slug" required pattern="[a-z0-9]+([a-z0-9-]*[a-z0-9])?" placeholder="例如：yufan2">
            </div>
            <div class="form-item">
              <label>显示名称（可随时在租户页修改）</label>
              <input class="input" name="display_name" required placeholder="例如：张博洋">
            </div>
            <div class="form-item">
              <label>Unity / AI 登录用户名</label>
              <input class="input" name="ai_username" required>
            </div>
            <div class="form-item">
              <label>Unity / AI 登录密码</label>
              <input class="input" name="ai_password" type="password" required>
            </div>
            <p id="status" class="status" hidden></p>
            <div class="modal-foot">
              <button class="btn" type="button" id="cancel-create">取消</button>
              <button class="btn primary" type="submit">创建文件夹</button>
            </div>
          </form>
        </div>
      </div>
      <script>
        const mask = document.querySelector("#create-mask");
        const status = document.querySelector("#status");
        const openCreate = () => {{ mask.hidden = false; }};
        const closeCreate = () => {{ mask.hidden = true; }};
        document.querySelector("#open-create").addEventListener("click", openCreate);
        document.querySelector("#close-create").addEventListener("click", closeCreate);
        document.querySelector("#cancel-create").addEventListener("click", closeCreate);
        document.querySelector("#create-tenant").addEventListener("submit", async (event) => {{
          event.preventDefault();
          const form = event.currentTarget;
          const payload = Object.fromEntries(new FormData(form).entries());
          status.hidden = false;
          status.textContent = "正在创建…";
          status.className = "status";
          const response = await fetch("/admin/tenants", {{
            method: "POST",
            headers: {{
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken(),
            }},
            credentials: "same-origin",
            body: JSON.stringify(payload),
          }});
          const body = await response.json();
          if (!response.ok) {{
            status.textContent = body.detail || "创建失败";
            status.className = "status error";
            return;
          }}
          window.location.assign(body.location || "/admin");
        }});
        const qInput = document.querySelector("#filter-q");
        const intentSelect = document.querySelector("#filter-intent");
        const statusSelect = document.querySelector("#filter-status");
        const intents = new Set();
        document.querySelectorAll("#user-table tbody tr").forEach((row) => {{
          const intent = row.dataset.intent || "";
          if (intent && intent !== "—") intents.add(intent);
        }});
        [...intents].sort().forEach((intent) => {{
          const option = document.createElement("option");
          option.value = intent;
          option.textContent = intent;
          intentSelect.append(option);
        }});
        function applyFilter() {{
          const q = (qInput.value || "").trim().toLowerCase();
          const intent = intentSelect.value;
          const st = statusSelect.value;
          let visible = 0;
          document.querySelectorAll("#user-table tbody tr").forEach((row) => {{
            if (row.dataset.empty === "1") return;
            const hay = (row.dataset.search || "").toLowerCase();
            const okQ = !q || hay.includes(q);
            const okIntent = !intent || row.dataset.intent === intent;
            const okStatus = !st || row.dataset.status === st;
            const show = okQ && okIntent && okStatus;
            row.hidden = !show;
            if (show) visible += 1;
          }});
          document.querySelector("#row-count").textContent = "共 " + visible + " 条记录";
        }}
        document.querySelector("#filter-apply").addEventListener("click", applyFilter);
        document.querySelector("#filter-reset").addEventListener("click", () => {{
          qInput.value = "";
          intentSelect.value = "";
          statusSelect.value = "";
          applyFilter();
        }});
      </script>
      {trash_script}
    """
    return _shell_page(
        title="用户简历库",
        active="users",
        crumb="简历管理 / <b>用户简历库</b>",
        badge=total,
        trash_badge=trash_badge,
        main=main,
    )


def recycle_bin_page(
    *,
    users: list[dict[str, Any]],
    notice: str = "",
    error: str = "",
    badge: int = 0,
) -> str:
    banner = _banner(notice, error)
    total = len(users)
    rows = _recycle_user_rows(users)
    main = f"""
      <div class="page-head">
        <div>
          <h1>回收站</h1>
          <p>已删除用户会出现在这里。可恢复到用户简历库，或彻底清除 PostgreSQL / MinIO / Elasticsearch / Milvus 数据。</p>
        </div>
      </div>
      {banner}
      <div class="panel">
        <div class="card-head">
          <h3>已删除用户</h3>
          <span class="sub">共 {total} 条记录</span>
        </div>
        <div class="table-wrap">
          <table class="grid" id="trash-table">
            <thead>
              <tr>
                <th style="width:52px">序号</th>
                <th style="width:210px">姓名</th>
                <th>求职意向</th>
                <th style="width:150px">联系方式</th>
                <th style="width:150px">登记时间</th>
                <th style="width:160px">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows}
            </tbody>
          </table>
        </div>
      </div>
      <script>
        async function callTenant(slug, path, method) {{
          const response = await fetch(`/admin/tenants/${{encodeURIComponent(slug)}}${{path}}`, {{
            method,
            headers: {{ "X-CSRF-Token": csrfToken() }},
            credentials: "same-origin",
          }});
          const body = await response.json().catch(() => ({{}}));
          if (!response.ok) {{
            window.alert(body.detail || "操作失败");
            return;
          }}
          window.location.assign(body.location || "/admin/recycle-bin");
        }}
        document.querySelector("#trash-table").addEventListener("click", async (event) => {{
          const restore = event.target.closest("[data-restore]");
          const purge = event.target.closest("[data-purge]");
          if (restore) {{
            const slug = restore.dataset.restore;
            const name = restore.dataset.name || slug;
            if (!window.confirm(`确定将「${{name}}」恢复到用户简历库？`)) return;
            restore.disabled = true;
            await callTenant(slug, "/restore", "POST");
            return;
          }}
          if (purge) {{
            const slug = purge.dataset.purge;
            const name = purge.dataset.name || slug;
            if (!window.confirm(`确定彻底删除「${{name}}」？将清除该用户在 PostgreSQL、MinIO、Elasticsearch、Milvus 中的全部数据，且不可恢复。`)) return;
            purge.disabled = true;
            await callTenant(slug, "", "DELETE");
          }}
        }});
      </script>
    """
    return _shell_page(
        title="回收站",
        active="trash",
        crumb="简历管理 / <b>回收站</b>",
        badge=badge,
        trash_badge=total,
        main=main,
    )


def admin_placeholder_page(
    *,
    active: str,
    title: str,
    crumb: str,
    message: str,
    badge: int = 0,
    trash_badge: int = 0,
) -> str:
    main = f"""
      <div class="page-head">
        <div>
          <h1>{escape(title)}</h1>
          <p>{escape(message)}</p>
        </div>
      </div>
      <div class="panel">
        <div class="card-body empty-panel">功能建设中，入口已保留，不影响现有用户简历库与租户管理。</div>
      </div>
    """
    return _shell_page(
        title=title,
        active=active,
        crumb=crumb,
        badge=badge,
        trash_badge=trash_badge,
        main=main,
    )


def _resume_user_rows(users: list[dict[str, Any]]) -> str:
    if not users:
        return (
            '<tr data-empty="1"><td colspan="7" class="empty">'
            "还没有用户。点击右上角「新增用户」创建。</td></tr>"
        )
    rendered: list[str] = []
    for index, user in enumerate(users, start=1):
        status = str(user.get("status") or "待完善")
        status_cls = "green" if status == "已完善" else "orange"
        name = str(user.get("name") or "")
        intent = str(user.get("job_intent") or "—")
        phone = str(user.get("phone_masked") or "—")
        slug = str(user.get("slug") or "")
        search = f"{name} {phone} {intent} {slug}"
        rendered.append(
            f"""
            <tr data-search="{escape(search)}" data-intent="{escape(intent)}" data-status="{escape(status)}">
              <td class="mono">{index:03d}</td>
              <td>
                <div class="user-cell">
                  <div class="avatar" style="background:{escape(str(user.get('avatar_bg') or '#3b5bfd'))}">{escape(str(user.get('avatar_char') or '用'))}</div>
                  <div>
                    <div class="nm">{escape(name)}</div>
                    <div class="mt">{escape(str(user.get('meta') or ''))}</div>
                  </div>
                </div>
              </td>
              <td><span class="tag">{escape(intent)}</span></td>
              <td class="mono">{escape(phone)}</td>
              <td class="time">{escape(str(user.get('registered_at') or '—'))}</td>
              <td><span class="tag {status_cls}">{escape(status)}</span></td>
              <td class="ops">
                <a class="btn text" href="{escape(str(user.get('detail_url') or '#'))}">查看详情</a>
                <button type="button" class="btn text danger-text" data-trash="{escape(slug)}" data-name="{escape(name)}">删除</button>
              </td>
            </tr>
            """
        )
    return "".join(rendered)


def _recycle_user_rows(users: list[dict[str, Any]]) -> str:
    if not users:
        return (
            '<tr data-empty="1"><td colspan="6" class="empty">'
            "回收站是空的。</td></tr>"
        )
    rendered: list[str] = []
    for index, user in enumerate(users, start=1):
        name = str(user.get("name") or "")
        intent = str(user.get("job_intent") or "—")
        phone = str(user.get("phone_masked") or "—")
        slug = str(user.get("slug") or "")
        rendered.append(
            f"""
            <tr>
              <td class="mono">{index:03d}</td>
              <td>
                <div class="user-cell">
                  <div class="avatar" style="background:{escape(str(user.get('avatar_bg') or '#3b5bfd'))}">{escape(str(user.get('avatar_char') or '用'))}</div>
                  <div>
                    <div class="nm">{escape(name)}</div>
                    <div class="mt">{escape(str(user.get('meta') or ''))}</div>
                  </div>
                </div>
              </td>
              <td><span class="tag">{escape(intent)}</span></td>
              <td class="mono">{escape(phone)}</td>
              <td class="time">{escape(str(user.get('registered_at') or '—'))}</td>
              <td class="ops">
                <button type="button" class="btn text" data-restore="{escape(slug)}" data-name="{escape(name)}">恢复</button>
                <button type="button" class="btn text danger-text" data-purge="{escape(slug)}" data-name="{escape(name)}">彻底删除</button>
              </td>
            </tr>
            """
        )
    return "".join(rendered)


def resume_page(
    *,
    slug: str,
    display_name: str,
    published: bool,
    headline: str,
    sections: list[Any],
    notice: str = "",
    error: str = "",
    show_back: bool = True,
    assistant_owner: str = "",
    assistant_capabilities: list[str] | None = None,
) -> str:
    banner = _banner(notice, error)
    back = (
        '<a class="link" href="/admin">← 全部文件夹</a>'
        if show_back
        else ""
    )
    section_payload = [
        {
            "section_key": item.section_key,
            "title": item.title,
            "enabled": item.enabled,
            "sort_order": item.sort_order,
            "content": item.content,
        }
        for item in sections
    ]
    body = f"""
        <main class="card wide">
          <header class="bar">
            <div>
              <p class="mark">FS AgentOS</p>
              <h1 id="tenant-title">{escape(display_name)}</h1>
              <p class="lead">站点 /{escape(slug)} · 简历栏目</p>
              {back}
            </div>
            <button id="logout" type="button">退出</button>
          </header>
          {_tabs(slug, "resume")}
          {banner}
          {_rename_form(
              slug,
              display_name,
              assistant_owner=assistant_owner or display_name,
              assistant_capabilities=assistant_capabilities,
          )}
          <label class="row">
            <input id="published" type="checkbox" {"checked" if published else ""}>
            已发布（Unity 可公开拉取）
          </label>
          <label>求职意向（简历顶部 / 帮助顶部）</label>
          <input id="headline" value="{escape(headline)}" placeholder="求职意向: AI应用开发/AI Agent开发">
          <div id="sections"></div>
          <button id="save" type="button">保存简历</button>
          <p id="status" class="status" hidden></p>
        </main>
        {_logout_script()}
        {_rename_script()}
        <script id="section-data" type="application/json">{json.dumps(section_payload, ensure_ascii=False).replace("</", "<\\/")}</script>
        <script>
          const sectionsRoot = document.querySelector("#sections");
          const status = document.querySelector("#status");
          let sections = JSON.parse(document.querySelector("#section-data").textContent);

          function skillsBody(content) {{
            if (!content || typeof content !== "object") return "";
            if (typeof content.body === "string" && content.body) return content.body;
            const items = Array.isArray(content.items) ? content.items : [];
            return items.map((item) => {{
              if (item == null) return "";
              if (typeof item === "string") return item;
              return item.name || item.line || "";
            }}).filter(Boolean).join("\\n");
          }}

          function render() {{
            sectionsRoot.replaceChildren();
            sections.sort((a, b) => a.sort_order - b.sort_order);
            for (const [index, section] of sections.entries()) {{
              const box = document.createElement("section");
              box.className = "section-card";
              const isSkills = section.section_key === "Skills";
              const isBodySection = isSkills || section.section_key === "Strengths";
              box.innerHTML = `
                <div class="row between">
                  <strong>${{section.section_key}}</strong>
                  <label><input type="checkbox" data-field="enabled" ${{section.enabled ? "checked" : ""}}> 启用</label>
                </div>
                <label>展示名</label>
                <input data-field="title" value="">
                <label>排序</label>
                <input data-field="sort_order" type="number" value="">
                <label>${{isSkills ? "专业技能正文（与 Unity SkillsPanel 整段文本对应）" : isBodySection ? "正文" : "内容 JSON"}}</label>
                <textarea data-field="${{isBodySection ? "body" : "content"}}" rows="${{isSkills ? 12 : 8}}"></textarea>
              `;
              box.querySelector('[data-field="title"]').value = section.title || "";
              box.querySelector('[data-field="sort_order"]').value = String(section.sort_order ?? index);
              if (isSkills) {{
                box.querySelector('[data-field="body"]').value = skillsBody(section.content);
              }} else if (isBodySection) {{
                box.querySelector('[data-field="body"]').value = (section.content && section.content.body) || "";
              }} else {{
                box.querySelector('[data-field="content"]').value = JSON.stringify(section.content || {{}}, null, 2);
              }}
              box.dataset.index = String(index);
              box.dataset.mode = isSkills ? "skills" : isBodySection ? "body" : "json";
              sectionsRoot.append(box);
            }}
          }}

          function collect() {{
            return Array.from(sectionsRoot.querySelectorAll(".section-card")).map((box, index) => {{
              const key = sections[Number(box.dataset.index)].section_key;
              let content = {{}};
              const mode = box.dataset.mode || "json";
              if (mode === "skills" || mode === "body") {{
                content = {{ body: box.querySelector('[data-field="body"]').value || "" }};
              }} else {{
                try {{
                  content = JSON.parse(box.querySelector('[data-field="content"]').value || "{{}}");
                }} catch (_error) {{
                  throw new Error(key + " 的 JSON 无效");
                }}
              }}
              return {{
                section_key: key,
                title: box.querySelector('[data-field="title"]').value,
                enabled: box.querySelector('[data-field="enabled"]').checked,
                sort_order: Number(box.querySelector('[data-field="sort_order"]').value || index),
                content,
              }};
            }});
          }}

          document.querySelector("#save").addEventListener("click", async () => {{
            status.hidden = false;
            try {{
              const payload = {{
                published: document.querySelector("#published").checked,
                headline: document.querySelector("#headline").value,
                sections: collect(),
              }};
              const response = await fetch("/admin/tenants/{escape(slug)}/resume", {{
                method: "PUT",
                headers: {{
                  "Content-Type": "application/json",
                  "X-CSRF-Token": csrfToken(),
                }},
                credentials: "same-origin",
                body: JSON.stringify(payload),
              }});
              const body = await response.json();
              if (!response.ok) {{
                status.textContent = body.detail || "保存失败";
                return;
              }}
              window.location.assign(body.location || "/admin/tenants/{escape(slug)}?tab=resume");
            }} catch (error) {{
              status.textContent = error.message || "保存失败";
            }}
          }});
          render();
        </script>
    """
    return _page(f"{display_name} · 简历", body)


def library_page(
    *,
    documents: list[dict[str, str | int]],
    notice: str = "",
    error: str = "",
    slug: str = "",
    display_name: str = "文档库",
    show_back: bool = True,
    assistant_owner: str = "",
    assistant_capabilities: list[str] | None = None,
) -> str:
    banner = _banner(notice, error)
    rows = _rows(documents)
    back = (
        f'<a class="link" href="/admin">← 全部文件夹</a>'
        if show_back
        else ""
    )
    upload_action = f"/admin/tenants/{escape(slug)}/documents" if slug else "/admin/documents"
    delete_prefix = f"/admin/tenants/{escape(slug)}/documents/" if slug else "/admin/documents/"
    tabs = _tabs(slug, "docs") if slug else ""
    body = f"""
        <main class="card">
          <header class="bar">
            <div>
              <p class="mark">FS AgentOS</p>
              <h1 id="tenant-title">{escape(display_name)}</h1>
              <p class="lead">知识库文档 · 租户隔离</p>
              {back}
            </div>
            <button id="logout" type="button">退出</button>
          </header>
          {tabs}
          {
              _rename_form(
                  slug,
                  display_name,
                  assistant_owner=assistant_owner or display_name,
                  assistant_capabilities=assistant_capabilities,
              )
              if slug
              else ""
          }
          <p class="lead">选择 txt、md、csv、pdf 或 docx。上传成功后完成切片、索引和向量化。</p>
          {banner}
          <form id="upload-form" method="post" action="{upload_action}" enctype="multipart/form-data">
            <input id="file" name="file" type="file" accept=".txt,.md,.markdown,.csv,.pdf,.docx" required>
            <button id="upload" type="submit">上传并入库</button>
          </form>
          <p id="status" class="status" hidden></p>
          <table>
            <thead>
              <tr><th>文件</th><th>类型</th><th>切片</th><th>入库时间</th><th></th></tr>
            </thead>
            <tbody data-delete-prefix="{delete_prefix}">
              {rows}
            </tbody>
          </table>
        </main>
        <dialog id="failure-dialog">
          <h2>删除没有全部完成</h2>
          <p class="lead">下面这些存储删除失败。文档仍留在列表里，对应服务恢复后可以再删一次。</p>
          <ul id="failure-list"></ul>
          <button id="failure-close" type="button">知道了</button>
        </dialog>
        {_logout_script()}
        {_rename_script()}
        <script>
          const status = document.querySelector("#status");
          const deletePrefix = document.querySelector("tbody").dataset.deletePrefix || "/admin/documents/";
          function showStatus(text) {{
            status.hidden = false;
            status.textContent = text;
          }}
          document.querySelector("#upload-form").addEventListener("submit", async (event) => {{
            event.preventDefault();
            const button = document.querySelector("#upload");
            const file = document.querySelector("#file").files[0];
            if (!file) return;
            button.disabled = true;
            showStatus("正在保存、切片、索引并向量化…");
            try {{
              const response = await fetch(event.currentTarget.action, {{
                method: "POST",
                body: new FormData(event.currentTarget),
                headers: {{ "X-CSRF-Token": csrfToken() }},
                credentials: "same-origin",
              }});
              const payload = await response.json();
              if (!response.ok) {{
                showStatus(payload.detail || "上传失败");
                button.disabled = false;
                return;
              }}
              window.location.assign(payload.location || window.location.href);
            }} catch (_error) {{
              showStatus("上传失败，请确认网关和存储服务都已启动。");
              button.disabled = false;
            }}
          }});
          document.querySelector("#failure-close").addEventListener("click", () => {{
            document.querySelector("#failure-dialog").close();
          }});
          document.querySelector("tbody").addEventListener("click", async (event) => {{
            const button = event.target.closest("[data-document-id]");
            if (!button) return;
            const title = button.dataset.title || "文档";
            if (!window.confirm("删除「" + title + "」？原文、切片、索引和向量会一起去掉。")) return;
            button.disabled = true;
            showStatus("正在从 MinIO、Elasticsearch、Milvus 和 PostgreSQL 删除…");
            try {{
              const response = await fetch(
                deletePrefix + encodeURIComponent(button.dataset.documentId)
                  + "?title=" + encodeURIComponent(title),
                {{
                  method: "DELETE",
                  headers: {{ "X-CSRF-Token": csrfToken() }},
                  credentials: "same-origin",
                }},
              );
              const payload = await response.json();
              if (!response.ok) {{
                button.disabled = false;
                if (Array.isArray(payload.failures) && payload.failures.length) {{
                  showFailures(payload.failures);
                  showStatus("有存储没有删除成功");
                  return;
                }}
                showStatus(payload.detail || "删除失败");
                return;
              }}
              window.location.assign(payload.location || window.location.href);
            }} catch (_error) {{
              button.disabled = false;
              showStatus("删除失败，请确认网关和存储服务都已启动。");
            }}
          }});
          function showFailures(failures) {{
            const dialog = document.querySelector("#failure-dialog");
            const list = document.querySelector("#failure-list");
            list.replaceChildren();
            for (const item of failures) {{
              const row = document.createElement("li");
              const name = document.createElement("strong");
              name.textContent = item.store || "未知存储";
              row.append(name, document.createTextNode("：" + (item.reason || "删除失败")));
              list.append(row);
            }}
            if (typeof dialog.showModal === "function") dialog.showModal();
          }}
        </script>
    """
    return _page("文档库", body)


def _tabs(slug: str, active: str) -> str:
    if not slug:
        return ""
    resume_cls = "tab active" if active == "resume" else "tab"
    docs_cls = "tab active" if active == "docs" else "tab"
    return f"""
      <nav class="tabs">
        <a class="{resume_cls}" href="/admin/tenants/{escape(slug)}?tab=resume">简历栏目</a>
        <a class="{docs_cls}" href="/admin/tenants/{escape(slug)}?tab=docs">知识库</a>
      </nav>
    """


def _rename_form(
    slug: str,
    display_name: str,
    *,
    assistant_owner: str = "",
    assistant_capabilities: list[str] | None = None,
) -> str:
    if not slug:
        return ""
    caps = "、".join(assistant_capabilities or [])
    owner = assistant_owner or display_name
    return f"""
      <form id="rename-tenant" class="persona-form" data-slug="{escape(slug)}">
        <label for="display-name">显示名称（文件夹标题）</label>
        <input id="display-name" name="display_name" value="{escape(display_name)}" required>
        <label for="assistant-owner">AI 主人姓名（用于「我是谁」）</label>
        <input id="assistant-owner" name="assistant_owner" value="{escape(owner)}" required placeholder="例如：张博洋">
        <label for="assistant-capabilities">AI 能力列表（用顿号或逗号分隔）</label>
        <input id="assistant-capabilities" name="assistant_capabilities" value="{escape(caps)}" required placeholder="查天气、查路线、查简历信息、发邮件">
        <button type="submit">保存租户与自我介绍</button>
        <p id="rename-status" class="status" hidden></p>
      </form>
    """


def _rename_script() -> str:
    return """
        <script>
          const renameForm = document.querySelector("#rename-tenant");
          if (renameForm) {
            renameForm.addEventListener("submit", async (event) => {
              event.preventDefault();
              const status = document.querySelector("#rename-status");
              const nameInput = document.querySelector("#display-name");
              const ownerInput = document.querySelector("#assistant-owner");
              const capsInput = document.querySelector("#assistant-capabilities");
              const title = document.querySelector("#tenant-title");
              const slug = renameForm.dataset.slug;
              const displayName = (nameInput.value || "").trim();
              const assistantOwner = (ownerInput.value || "").trim();
              const assistantCapabilities = (capsInput.value || "").trim();
              if (!displayName || !assistantOwner || !assistantCapabilities) {
                status.hidden = false;
                status.textContent = "请填写显示名称、主人姓名和能力列表";
                return;
              }
              status.hidden = false;
              status.textContent = "正在保存…";
              const response = await fetch(`/admin/tenants/${slug}`, {
                method: "PATCH",
                headers: {
                  "Content-Type": "application/json",
                  "X-CSRF-Token": csrfToken(),
                },
                credentials: "same-origin",
                body: JSON.stringify({
                  display_name: displayName,
                  assistant_owner: assistantOwner,
                  assistant_capabilities: assistantCapabilities,
                }),
              });
              const body = await response.json();
              if (!response.ok) {
                status.textContent = body.detail || "保存失败";
                return;
              }
              if (title) title.textContent = body.display_name;
              nameInput.value = body.display_name;
              ownerInput.value = body.assistant_owner || "";
              capsInput.value = (body.assistant_capabilities || []).join("、");
              status.textContent = "已保存。Unity 问「你是谁」会使用新的自我介绍。";
            });
          }
        </script>
    """


def _banner(notice: str, error: str) -> str:
    banner = ""
    if notice:
        banner += f'<p class="notice">{escape(notice)}</p>'
    if error:
        banner += f'<p class="error">{escape(error)}</p>'
    return banner


def _logout_script() -> str:
    return """
        <script>
          function csrfToken() {
            const row = document.cookie.split("; ").find((item) => item.startsWith("fs_csrf="));
            return row ? decodeURIComponent(row.slice("fs_csrf=".length)) : "";
          }
          const logout = document.querySelector("#logout");
          if (logout) {
            logout.addEventListener("click", async () => {
              await fetch("/admin/logout", {
                method: "POST",
                headers: { "X-CSRF-Token": csrfToken() },
                credentials: "same-origin",
              });
              window.location.assign("/admin/login");
            });
          }
        </script>
    """


def _rows(documents: list[dict[str, str | int]]) -> str:
    if not documents:
        return '<tr><td colspan="5" class="empty">还没有文档。</td></tr>'
    rendered: list[str] = []
    for item in documents:
        created = str(item.get("created_at") or "").replace("T", " ")
        if len(created) > 19:
            created = created[:19]
        title = str(item.get("title") or "")
        document_id = str(item.get("document_id") or "")
        rendered.append(
            "<tr>"
            f"<td>{escape(title)}</td>"
            f"<td>{escape(_kind(str(item.get('media_type') or '')))}</td>"
            f"<td>{escape(str(item.get('chunk_count') or 0))}</td>"
            f"<td>{escape(created or '—')}</td>"
            "<td>"
            f'<button type="button" class="danger" data-document-id="{escape(document_id)}" '
            f'data-title="{escape(title)}">删除</button>'
            "</td>"
            "</tr>"
        )
    return "".join(rendered)


def _kind(media_type: str) -> str:
    labels = {
        "text/plain": "文本",
        "text/markdown": "Markdown",
        "text/csv": "CSV",
        "application/pdf": "PDF",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
    }
    return labels.get(media_type, media_type or "文件")


def _shell_page(
    *,
    title: str,
    active: str,
    crumb: str,
    main: str,
    badge: int = 0,
    trash_badge: int = 0,
) -> str:
    users_badge = f'<span class="badge">{badge}</span>' if badge else ""
    trash_badge_html = (
        f'<span class="badge">{trash_badge}</span>' if trash_badge else ""
    )
    users_cls = "nav-item active" if active == "users" else "nav-item"
    dash_cls = "nav-item active" if active == "dashboard" else "nav-item"
    parse_cls = "nav-item active" if active == "parse" else "nav-item"
    talent_cls = "nav-item active" if active == "talent" else "nav-item"
    trash_cls = "nav-item active" if active == "trash" else "nav-item"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · 简历管理后台</title>
  <style>{_shell_css()}</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="logo">
      <div class="mark">简</div><span>简历管理后台</span>
    </div>
    <nav class="nav">
      <div class="nav-group">数据看板</div>
      <a class="{dash_cls}" href="/admin/dashboard">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
        概览 Dashboard
      </a>
      <div class="nav-group">简历管理</div>
      <a class="{users_cls}" href="/admin">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        用户简历库
        {users_badge}
      </a>
      <a class="{parse_cls}" href="/admin/parse-records">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M12 18v-6"/><path d="M9 15h6"/></svg>
        简历解析记录
      </a>
      <a class="{talent_cls}" href="/admin/talent-portrait">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3"/></svg>
        人才画像分析
      </a>
      <a class="{trash_cls}" href="/admin/recycle-bin">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
        回收站
        {trash_badge_html}
      </a>
      <div class="nav-group">系统</div>
      <div class="nav-item" title="即将支持">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        系统设置
      </div>
      <div class="nav-item" title="即将支持">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        管理员账号
      </div>
    </nav>
    <div class="sidebar-foot">
      <div class="av">管</div>
      <div>
        <div style="color:#fff;font-size:12.5px">超级管理员</div>
        <div style="font-size:11px;color:#6b7692">platform admin</div>
      </div>
    </div>
  </aside>
  <div class="main">
    <header class="topbar">
      <div class="crumb">{crumb}</div>
      <div class="right">
        <button class="btn" type="button" id="logout">退出</button>
        <div class="avatar-sm">
          <div class="av">管</div><span>管理员</span>
        </div>
      </div>
    </header>
    <div class="content">
      {main}
    </div>
  </div>
</div>
{_logout_script()}
</body>
</html>
"""


def _shell_css() -> str:
    return """
* { margin:0; padding:0; box-sizing:border-box; }
:root{
  --primary:#3b5bfd; --primary-dark:#2f49d4; --primary-light:#eef2ff;
  --success:#16a34a; --success-bg:#e8f8ee; --warning:#f59e0b; --warning-bg:#fef4e6;
  --danger:#ef4444; --danger-bg:#fdecec; --text-1:#1a1f36; --text-2:#4e5875;
  --text-3:#8a93a8; --border:#e6e9f0; --bg:#f5f7fa; --card:#ffffff;
  --radius:10px; --shadow:0 1px 3px rgba(26,31,54,.06), 0 4px 16px rgba(26,31,54,.04);
}
body{
  font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg); color:var(--text-1); font-size:14px;
  -webkit-font-smoothing:antialiased;
}
a { color:inherit; text-decoration:none; }
.layout{ display:flex; min-height:100vh; }
.sidebar{
  width:220px; flex-shrink:0; background:#1c2338; color:#c8cfe0;
  display:flex; flex-direction:column;
}
.sidebar .logo{
  height:60px; display:flex; align-items:center; gap:10px;
  padding:0 20px; border-bottom:1px solid rgba(255,255,255,.08);
}
.sidebar .logo .mark{
  width:30px; height:30px; border-radius:8px;
  background:linear-gradient(135deg,#3b5bfd,#7c5cff);
  display:flex; align-items:center; justify-content:center;
  color:#fff; font-weight:700; font-size:15px;
}
.sidebar .logo span{ color:#fff; font-size:15px; font-weight:600; }
.nav{ padding:14px 10px; flex:1; }
.nav-group{ font-size:11px; color:#6b7692; padding:12px 12px 6px; letter-spacing:1px; }
.nav-item{
  display:flex; align-items:center; gap:10px;
  padding:10px 12px; border-radius:8px;
  font-size:13.5px; cursor:pointer; margin-bottom:2px; color:#c8cfe0;
}
.nav-item .ic{ width:16px; height:16px; opacity:.85; flex-shrink:0; }
.nav-item:hover{ background:rgba(255,255,255,.06); color:#fff; }
.nav-item.active{
  background:linear-gradient(90deg,#3b5bfd,#5a72ff);
  color:#fff; font-weight:500;
  box-shadow:0 2px 8px rgba(59,91,253,.35);
}
.nav-item .badge{
  margin-left:auto; background:var(--danger); color:#fff;
  font-size:10px; padding:1px 6px; border-radius:9px; font-weight:600;
}
.sidebar-foot{
  padding:12px 16px; border-top:1px solid rgba(255,255,255,.08);
  display:flex; align-items:center; gap:10px; font-size:12.5px;
}
.sidebar-foot .av, .avatar-sm .av{
  width:28px; height:28px; border-radius:50%;
  background:#3b5bfd; color:#fff;
  display:flex; align-items:center; justify-content:center; font-size:12px;
}
.main{ flex:1; min-width:0; display:flex; flex-direction:column; }
.topbar{
  height:60px; background:#fff; border-bottom:1px solid var(--border);
  display:flex; align-items:center; padding:0 24px; gap:16px;
}
.crumb{ font-size:13px; color:var(--text-3); }
.crumb b{ color:var(--text-1); font-weight:500; }
.topbar .right{ margin-left:auto; display:flex; align-items:center; gap:14px; }
.avatar-sm{ display:flex; align-items:center; gap:8px; font-size:13px; }
.content{ padding:22px 24px 32px; }
.page-head{ display:flex; align-items:flex-start; margin-bottom:18px; }
.page-head h1{ font-size:20px; font-weight:600; }
.page-head p{ font-size:12.5px; color:var(--text-3); margin-top:5px; }
.page-head .actions{ margin-left:auto; display:flex; gap:10px; }
.btn{
  height:34px; padding:0 14px; border-radius:8px;
  border:1px solid var(--border); background:#fff;
  font-size:13px; color:var(--text-2); cursor:pointer;
  display:inline-flex; align-items:center; gap:6px; font-family:inherit;
}
.btn:hover{ border-color:#c9d0e0; color:var(--text-1); }
.btn.primary{
  background:var(--primary); border-color:var(--primary); color:#fff;
  box-shadow:0 2px 6px rgba(59,91,253,.25);
}
.btn.primary:hover{ background:var(--primary-dark); }
.btn.text{ border:none; background:none; color:var(--primary); padding:0 6px; height:auto; }
.btn.text.danger-text{ color:var(--danger); }
.btn:disabled{ opacity:.55; cursor:not-allowed; }
.ops{ display:flex; align-items:center; gap:8px; white-space:nowrap; }
.stats{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:18px; }
.stat-card{
  background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
  padding:16px 18px; box-shadow:var(--shadow);
  display:flex; align-items:center; gap:14px;
}
.stat-card .ic-box{
  width:42px; height:42px; border-radius:10px;
  display:flex; align-items:center; justify-content:center; flex-shrink:0;
}
.stat-card .num{ font-size:22px; font-weight:600; line-height:1.1; }
.stat-card .lbl{ font-size:12.5px; color:var(--text-3); margin-top:4px; }
.panel, .toolbar{
  background:var(--card); border:1px solid var(--border);
  border-radius:var(--radius); box-shadow:var(--shadow);
}
.toolbar{
  padding:14px 16px; margin-bottom:16px;
  display:flex; align-items:center; gap:10px; flex-wrap:wrap;
}
.field{ display:flex; align-items:center; gap:8px; }
.field label{ font-size:12.5px; color:var(--text-2); white-space:nowrap; }
.input, .select{
  height:34px; border:1px solid var(--border); border-radius:8px;
  padding:0 10px; font-size:13px; color:var(--text-1);
  background:#fff; font-family:inherit; outline:none;
}
.input:focus, .select:focus{ border-color:var(--primary); }
.input.search{
  width:230px; padding-left:32px;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%238a93a8' stroke-width='2.4'><circle cx='11' cy='11' r='7'/><path d='M20 20l-3.5-3.5'/></svg>");
  background-repeat:no-repeat; background-position:10px center;
}
.select{
  padding-right:26px; appearance:none;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238a93a8' stroke-width='3'><path d='M5 9l7 7 7-7'/></svg>");
  background-repeat:no-repeat; background-position:right 9px center;
}
.card-head{
  padding:14px 18px; border-bottom:1px solid var(--border);
  display:flex; align-items:center; gap:10px;
}
.card-head h3{
  font-size:14.5px; font-weight:600;
  display:flex; align-items:center; gap:8px;
}
.card-head h3::before{
  content:''; width:3px; height:14px; background:var(--primary); border-radius:2px;
}
.card-head .sub{ font-size:12px; color:var(--text-3); }
.card-body{ padding:18px; }
.empty-panel{ color:var(--text-3); padding:48px 18px; text-align:center; }
.table-wrap{ overflow:auto; }
table.grid{ width:100%; border-collapse:collapse; }
table.grid th{
  background:#fafbfd; text-align:left; font-size:12.5px; font-weight:500;
  color:var(--text-2); padding:11px 16px; border-bottom:1px solid var(--border);
  white-space:nowrap;
}
table.grid td{
  padding:13px 16px; border-bottom:1px solid #f0f2f7; font-size:13px;
  vertical-align:middle;
}
table.grid tbody tr:hover{ background:#f9faff; }
.user-cell{ display:flex; align-items:center; gap:10px; }
.avatar{
  width:34px; height:34px; border-radius:50%; flex-shrink:0;
  display:flex; align-items:center; justify-content:center;
  color:#fff; font-size:13px; font-weight:500;
}
.user-cell .nm{ font-weight:500; }
.user-cell .mt{ font-size:11.5px; color:var(--text-3); margin-top:2px; }
.tag{
  display:inline-block; padding:2px 9px; border-radius:5px;
  font-size:12px; background:var(--primary-light); color:var(--primary);
}
.tag.green{ background:var(--success-bg); color:var(--success); }
.tag.orange{ background:var(--warning-bg); color:#b47709; }
.mono{ font-family:Consolas,Menlo,monospace; font-size:12.5px; color:var(--text-2); }
.time{ font-size:12.5px; color:var(--text-2); }
.empty{ color:var(--text-3); text-align:center; padding:28px 16px !important; }
.notice, .error, .status{
  margin:0 0 16px; padding:12px 14px; border-radius:10px; font-size:13px;
}
.notice, .status{ background:#e7f6ef; color:#1d5c45; }
.error, .status.error{ background:#fdecec; color:#8f2d2d; }
.mask{
  position:fixed; inset:0; background:rgba(20,25,45,.45);
  display:flex; align-items:center; justify-content:center; z-index:50;
}
.mask[hidden]{ display:none !important; }
.modal{
  width:min(520px, calc(100% - 32px)); background:#fff; border-radius:12px;
  box-shadow:0 20px 50px rgba(20,25,45,.25); overflow:hidden;
}
.modal-head{
  padding:16px 20px; border-bottom:1px solid var(--border);
  display:flex; align-items:center;
}
.modal-head h3{ font-size:15px; font-weight:600; }
.modal-head .x{
  margin-left:auto; cursor:pointer; color:var(--text-3); font-size:18px;
  border:0; background:none; line-height:1;
}
.modal-body{ padding:20px; display:grid; gap:12px; }
.modal-foot{
  display:flex; justify-content:flex-end; gap:10px; margin-top:6px;
}
.form-item label{
  display:block; font-size:12.5px; color:var(--text-2); margin-bottom:6px;
}
.form-item .input{ width:100%; }
.detail-head{
  background:#fff; border:1px solid var(--border); border-radius:var(--radius);
  box-shadow:var(--shadow); padding:20px 22px; margin-bottom:16px;
  display:flex; align-items:center; gap:18px; position:relative; overflow:hidden;
}
.detail-head::before{
  content:''; position:absolute; left:0; top:0; bottom:0; width:4px;
  background:linear-gradient(180deg,#3b5bfd,#7c5cff);
}
.detail-head .avatar{
  width:62px; height:62px; font-size:22px;
  box-shadow:0 4px 12px rgba(59,91,253,.28);
}
.detail-head .who h2{
  font-size:19px; font-weight:600; display:flex; align-items:center; gap:10px; flex-wrap:wrap;
}
.detail-head .who .meta{
  font-size:12.5px; color:var(--text-3); margin-top:7px; display:flex; gap:16px; flex-wrap:wrap;
}
.detail-head .who .meta i{ font-style:normal; color:var(--text-2); }
.detail-head .head-actions{ margin-left:auto; display:flex; gap:10px; flex-wrap:wrap; }
.detail-tabs{ display:flex; gap:8px; margin:0 0 16px; }
.dtab{
  padding:8px 14px; border-radius:999px; border:1px solid var(--border);
  color:var(--text-2); font-size:13px; background:#fff;
}
.dtab.active{
  background:var(--primary); border-color:var(--primary); color:#fff;
  box-shadow:0 2px 6px rgba(59,91,253,.25);
}
.settings-grid{
  display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:end;
}
.check-row{
  display:flex; align-items:center; gap:8px; font-size:13px; color:var(--text-2);
}
.info-grid{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px 20px; }
.info-item .k{ font-size:12px; color:var(--text-3); margin-bottom:5px; }
.info-item .v{ font-size:13.5px; color:var(--text-1); font-weight:500; }
.info-item.edit{ display:grid; gap:8px; }
.info-item .mini{ display:block; font-size:12px; color:var(--text-3); margin:0 0 6px; }
.input.sm{ height:30px; font-size:12px; }
.grid-2{ display:grid; grid-template-columns:1fr 1fr; gap:16px; align-items:start; }
.timeline{ position:relative; }
.exp-item{
  border:1px solid var(--border); border-radius:9px; padding:14px 16px;
  margin-bottom:12px; background:#fff; position:relative;
}
.exp-item:last-child{ margin-bottom:0; }
.exp-item .idx{
  position:absolute; left:-1px; top:-1px; bottom:-1px; width:3px;
  background:var(--primary); border-radius:9px 0 0 9px; opacity:.75;
}
.exp-item .mini{
  display:block; font-size:12px; color:var(--text-3); margin:0 0 6px;
}
.exp-item .input{
  width:100%; display:block; box-sizing:border-box;
}
.exp-item .area, textarea.area{
  width:100%; border:1px solid var(--border); border-radius:8px;
  padding:10px 12px; font-size:13px; font-family:inherit; color:var(--text-1);
  line-height:1.7; resize:vertical; outline:none; min-height:96px;
}
.exp-item .area:focus, textarea.area:focus{ border-color:var(--primary); }
.exp-item .row-act{
  margin-top:10px; padding-top:9px; border-top:1px dashed #e9ecf3;
  display:flex; gap:12px;
}
.exp-item .row-act button, .exp-item .row-act .del{
  border:none; background:none; font-size:12.5px; cursor:pointer;
  color:var(--text-3); font-family:inherit;
}
.exp-item .row-act .del:hover, .info-item .del:hover{ color:var(--danger); }
.count-badge{
  font-size:12px; color:var(--text-3); background:#f2f4f9; padding:3px 9px; border-radius:11px;
}
.stepper{ display:flex; align-items:center; gap:0; }
.pm{
  width:26px; height:26px; border:1px solid #d5dbe8; background:#fff;
  font-size:15px; color:var(--text-2); cursor:pointer;
  display:flex; align-items:center; justify-content:center; font-family:inherit; line-height:1;
  border-radius:7px;
}
.pm:hover{ background:var(--primary); border-color:var(--primary); color:#fff; }
.btn.sm{ height:28px; padding:0 10px; font-size:12.5px; }
.save-bar{
  display:flex; align-items:center; gap:14px; margin-top:8px; flex-wrap:wrap;
}
.save-bar .tip{ font-size:12px; color:var(--text-3); }
.upload-row{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
.upload-row .input{ flex:1; min-width:220px; }
.fail-dialog{
  width:min(520px, calc(100% - 32px)); border:1px solid var(--border);
  border-radius:16px; padding:24px; background:#fff; color:var(--text-1);
}
.fail-dialog::backdrop{ background:rgba(20,25,45,.45); }
.fail-dialog h2{ margin:0 0 8px; font-size:18px; }
.fail-dialog .lead{ color:var(--text-3); margin:0 0 12px; }
#failure-list{ margin:0 0 16px; padding-left:18px; }
@media (max-width: 960px){
  .stats{ grid-template-columns:repeat(2,1fr); }
  .sidebar{ display:none; }
  .grid-2, .info-grid, .settings-grid{ grid-template-columns:1fr; }
  .detail-head{ flex-direction:column; align-items:flex-start; }
  .detail-head .head-actions{ margin-left:0; }
}
"""


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · 管理台</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1c1915;
      --muted: #5e584f;
      --line: #e4dccf;
      --paper: #f6f1e8;
      --card: #fffdf8;
      --accent: #0f6e56;
      --accent-ink: #f4fff9;
      --danger: #8f2d2d;
      --ok: #1d5c45;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--paper);
      color: var(--ink);
      font: 16px/1.5 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    .card {{
      width: min(960px, calc(100% - 32px));
      margin: 48px auto;
      padding: 32px;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
    }}
    .card.wide {{ width: min(1100px, calc(100% - 32px)); }}
    .narrow {{ width: min(440px, calc(100% - 32px)); }}
    .mark {{
      margin: 0 0 8px;
      color: var(--accent);
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{ margin: 0 0 8px; font-size: 32px; font-weight: 650; }}
    h2 {{ margin: 24px 0 12px; font-size: 20px; }}
    .lead {{ margin: 0 0 24px; color: var(--muted); }}
    .bar {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }}
    form, .stack {{ display: grid; gap: 8px; }}
    #upload-form {{ grid-template-columns: 1fr auto; align-items: center; }}
    .rename-row {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 10px;
      align-items: center;
      margin: 0 0 20px;
    }}
    .rename-row .status {{ grid-column: 1 / -1; margin: 0; }}
    .persona-form {{
      display: grid;
      gap: 8px;
      margin: 0 0 20px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
    }}
    .persona-form .status {{ margin: 0; }}
    label {{ font-size: 14px; color: var(--muted); }}
    .row {{ display: flex; gap: 12px; align-items: center; margin: 12px 0; }}
    .row.between {{ justify-content: space-between; }}
    input[type="text"], input[type="password"], input[type="number"], input:not([type]), textarea {{
      width: 100%;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: white;
      color: var(--ink);
      font: inherit;
    }}
    textarea {{ font-family: ui-monospace, Consolas, monospace; font-size: 13px; }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      background: var(--accent);
      color: var(--accent-ink);
      font: inherit;
      cursor: pointer;
    }}
    button:disabled {{ opacity: 0.6; cursor: wait; }}
    #logout, button.danger {{ background: transparent; color: var(--ink); border: 1px solid var(--line); }}
    button.danger {{ color: var(--danger); padding: 6px 12px; }}
    .link {{ color: var(--accent); text-decoration: none; font-size: 14px; }}
    .folder-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 16px;
      margin: 24px 0;
    }}
    .folder {{
      display: grid;
      gap: 6px;
      padding: 20px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: white;
      color: inherit;
      text-decoration: none;
    }}
    .folder span {{ color: var(--muted); font-size: 13px; }}
    .tabs {{ display: flex; gap: 8px; margin: 0 0 20px; }}
    .tab {{
      padding: 8px 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--ink);
      text-decoration: none;
      font-size: 14px;
    }}
    .tab.active {{ background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }}
    .section-card {{
      margin: 16px 0;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 12px;
      display: grid;
      gap: 8px;
    }}
    hr {{ border: 0; border-top: 1px solid var(--line); margin: 28px 0; }}
    dialog {{
      width: min(520px, calc(100% - 32px));
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 24px;
      background: var(--card);
      color: var(--ink);
    }}
    dialog::backdrop {{ background: rgba(28, 25, 21, 0.35); }}
    dialog h2 {{ margin: 0 0 8px; font-size: 22px; }}
    #failure-list {{ margin: 0 0 16px; padding-left: 18px; }}
    #failure-list li {{ margin: 8px 0; }}
    #failure-close {{ background: var(--ink); }}
    .error, .notice, .status {{
      margin: 0 0 16px;
      padding: 12px 14px;
      border-radius: 10px;
    }}
    .error {{ background: #fdecec; color: var(--danger); }}
    .notice, .status {{ background: #e7f6ef; color: var(--ok); }}
    table {{ width: 100%; margin-top: 28px; border-collapse: collapse; }}
    th, td {{ padding: 12px 8px; text-align: left; border-bottom: 1px solid var(--line); }}
    th {{ color: var(--muted); font-size: 13px; font-weight: 600; }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 640px) {{
      #upload-form {{ grid-template-columns: 1fr; }}
      .rename-row {{ grid-template-columns: 1fr; }}
      .persona-form {{ padding: 12px; }}
    }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""
