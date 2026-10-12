"""Tenant resume detail + RAG library pages (shell UI)."""

from __future__ import annotations

import json
from html import escape
from typing import Any

from fs_gateway.admin_pages import (
    _banner,
    _logout_script,
    _rename_script,
    _shell_page,
)
from fs_gateway.admin_resume_rows import (
    _COMPLETE_KEYS,
    _clean_headline,
    _field_value,
    _section_filled,
)


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
    created_at: str = "",
    badge: int = 0,
    trash_badge: int = 0,
) -> str:
    section_payload = _normalize_sections(sections)
    by_key = {item["section_key"]: item for item in section_payload}
    basic = by_key.get("BasicInfo") or {}
    fields = list((basic.get("content") or {}).get("fields") or [])
    person_name = (
        _field_value(fields, nodes=("Name",), labels=("姓名",))
        or (assistant_owner or "").strip()
        or display_name
        or slug
    )
    job_intent = _clean_headline(headline) or "—"
    complete = all(
        _section_filled(_as_section(by_key.get(key)), key) for key in _COMPLETE_KEYS
    )
    status_label = "已完善" if complete else "待完善"
    status_cls = "green" if complete else "orange"
    avatar = escape(person_name[:1] or "用")
    caps = "、".join(assistant_capabilities or [])
    owner = assistant_owner or display_name
    banner = _banner(notice, error)
    back_btn = (
        '<a class="btn" href="/admin">'
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>返回列表</a>'
        if show_back
        else ""
    )
    payload_json = json.dumps(section_payload, ensure_ascii=False).replace("</", "<\\/")
    main = f"""
      <div class="detail-head">
        <div class="avatar" style="background:linear-gradient(135deg,#3b5bfd,#7c5cff)">{avatar}</div>
        <div class="who">
          <h2 id="tenant-title">{escape(display_name)}
            <span class="tag {status_cls}">{status_label}</span>
            <span class="tag" id="head-intent">{escape(job_intent)}</span>
          </h2>
          <div class="meta">
            <span><i>站点：</i>/{escape(slug)}</span>
            <span><i>登记时间：</i>{escape(created_at or "—")}</span>
            <span><i>姓名：</i>{escape(person_name)}</span>
          </div>
        </div>
        <div class="head-actions">
          {back_btn}
          <button class="btn primary" type="button" id="save">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
              <path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>
            </svg>
            保存简历
          </button>
        </div>
      </div>
      {_detail_tabs(slug, "resume")}
      {banner}
      <p id="status" class="status" hidden></p>

      <div class="panel" style="margin-bottom:16px">
        <div class="card-head">
          <h3>站点与 AI 设置</h3>
        </div>
        <div class="card-body">
          <form id="rename-tenant" class="settings-grid" data-slug="{escape(slug)}">
            <div class="form-item">
              <label for="display-name">显示名称</label>
              <input class="input" id="display-name" name="display_name" value="{escape(display_name)}" required>
            </div>
            <div class="form-item">
              <label for="assistant-owner">AI 主人姓名（用于「我是谁」）</label>
              <input class="input" id="assistant-owner" name="assistant_owner" value="{escape(owner)}" required>
            </div>
            <div class="form-item" style="grid-column:1/-1">
              <label for="assistant-capabilities">AI 能力列表（顿号或逗号分隔）</label>
              <input class="input" id="assistant-capabilities" name="assistant_capabilities" value="{escape(caps)}" required>
            </div>
            <div class="form-item" style="grid-column:1/-1">
              <label for="headline">求职意向</label>
              <input class="input" id="headline" value="{escape(headline)}" placeholder="求职意向: AI应用开发/AI Agent开发">
            </div>
            <label class="check-row">
              <input id="published" type="checkbox" {"checked" if published else ""}>
              已发布（Unity 可公开拉取）
            </label>
            <div>
              <button class="btn" type="submit">保存租户与自我介绍</button>
              <p id="rename-status" class="status" hidden style="margin-top:10px"></p>
            </div>
          </form>
        </div>
      </div>

      <div class="panel" style="margin-bottom:16px">
        <div class="card-head">
          <h3>基本信息</h3>
          <span class="sub">栏目已固定，只填写对应内容</span>
        </div>
        <div class="card-body">
          <div class="info-grid" id="basic-fields"></div>
        </div>
      </div>

      <div class="panel" style="margin-bottom:16px">
        <div class="card-head">
          <h3>专业技能</h3>
          <span class="sub">整段文本，与 Unity Skills 面板对应</span>
        </div>
        <div class="card-body">
          <div class="exp-item">
            <div class="idx"></div>
            <textarea class="area" id="skills-body" rows="10" placeholder="每行一条技能说明…"></textarea>
          </div>
        </div>
      </div>

      <div class="panel" style="margin-bottom:16px">
        <div class="card-head">
          <h3>工作经历</h3>
          <div class="tools">
            <span class="count-badge" id="work-count">0 段</span>
            <div class="stepper">
              <button class="pm" type="button" id="add-work" title="新增">+</button>
            </div>
          </div>
        </div>
        <div class="card-body"><div class="timeline" id="work-list"></div></div>
      </div>
      <div class="panel" style="margin-bottom:16px">
        <div class="card-head">
          <h3>项目经历</h3>
          <div class="tools">
            <span class="count-badge" id="project-count">0 个</span>
            <div class="stepper">
              <button class="pm" type="button" id="add-project" title="新增">+</button>
            </div>
          </div>
        </div>
        <div class="card-body"><div class="timeline" id="project-list"></div></div>
      </div>

      <div class="panel" style="margin-bottom:16px">
        <div class="card-head">
          <h3>教育经历</h3>
          <div class="tools">
            <span class="count-badge" id="edu-count">0 条</span>
            <div class="stepper">
              <button class="pm" type="button" id="add-edu" title="新增">+</button>
            </div>
          </div>
        </div>
        <div class="card-body"><div class="timeline" id="edu-list"></div></div>
      </div>

      <div class="panel" style="margin-bottom:16px">
        <div class="card-head">
          <h3>简历技术介绍</h3>
          <div class="tools">
            <button class="btn sm" type="button" id="add-tech">+ 添加卡片</button>
          </div>
        </div>
        <div class="card-body"><div class="timeline" id="tech-list"></div></div>
      </div>

      <div class="panel" style="margin-bottom:16px">
        <div class="card-head"><h3>个人优势</h3></div>
        <div class="card-body">
          <div class="exp-item">
            <div class="idx" style="background:#7c5cff"></div>
            <textarea class="area" id="strengths-body" rows="8" placeholder="个人优势正文…"></textarea>
          </div>
        </div>
      </div>

      <div class="save-bar">
        <button class="btn primary" type="button" id="save-bottom">保存简历到数据库</button>
        <span class="tip">保存后会写入 PostgreSQL，Unity 拉取公开简历时立即生效。</span>
      </div>

      <script id="section-data" type="application/json">{payload_json}</script>
      {_rename_script()}
      <script>
        const slug = {json.dumps(slug)};
        let sections = JSON.parse(document.querySelector("#section-data").textContent);
        const byKey = () => Object.fromEntries(sections.map((s) => [s.section_key, s]));
        const status = document.querySelector("#status");

        function ensure(key, title, sortOrder, content) {{
          let item = sections.find((s) => s.section_key === key);
          if (!item) {{
            item = {{ section_key: key, title, enabled: true, sort_order: sortOrder, content }};
            sections.push(item);
          }}
          if (!item.content || typeof item.content !== "object") item.content = content;
          return item;
        }}
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
        function esc(text) {{
          return String(text ?? "").replace(/[&<>"']/g, (ch) => ({{
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
          }})[ch]);
        }}

        ensure("BasicInfo", "基本信息", 0, {{ fields: [] }});
        ensure("Skills", "专业技能", 1, {{ body: "" }});
        ensure("WorkExperience", "工作经历", 2, {{ cards: [] }});
        ensure("Projects", "项目经历", 3, {{ cards: [] }});
        ensure("Education", "教育经历", 4, {{ items: [] }});
        ensure("TechIntro", "简历技术介绍", 5, {{ cards: [] }});
        ensure("Strengths", "个人优势", 6, {{ body: "" }});

        const BASIC_FIELDS = [
          {{ node: "Name", label: "姓名", aliases: ["姓名"], placeholder: "例如：王博" }},
          {{ node: "Place of origin", label: "籍贯", aliases: ["籍贯"], placeholder: "例如：天津" }},
          {{ node: "Educational background", label: "学历", aliases: ["学历"], placeholder: "例如：本科" }},
          {{ node: "Contact phone number", label: "联系电话", aliases: ["联系电话", "电话", "手机"], placeholder: "例如：13466313668" }},
          {{ node: "Email", label: "邮箱", aliases: ["邮箱", "邮件"], placeholder: "例如：name@example.com" }},
        ];

        function basicValue(fields, spec) {{
          const rows = Array.isArray(fields) ? fields : [];
          const hit = rows.find((field) => {{
            const node = String(field && field.node || "").trim();
            const label = String(field && field.label || "");
            if (node === spec.node) return true;
            return spec.aliases.some((alias) => label.includes(alias));
          }});
          return hit ? String(hit.value || "") : "";
        }}

        function canonicalBasic(fields) {{
          return BASIC_FIELDS.map((spec) => ({{
            node: spec.node,
            label: spec.label,
            value: basicValue(fields, spec),
          }}));
        }}

        function renderBasic() {{
          const item = ensure("BasicInfo", "基本信息", 0, {{ fields: [] }});
          item.content.fields = canonicalBasic(item.content.fields);
          const root = document.querySelector("#basic-fields");
          root.replaceChildren();
          item.content.fields.forEach((field, index) => {{
            const spec = BASIC_FIELDS[index];
            const box = document.createElement("div");
            box.className = "info-item edit";
            box.innerHTML = `<label class="mini">${{esc(spec.label)}}</label><input class="input" placeholder="${{esc(spec.placeholder)}}">`;
            const input = box.querySelector("input");
            input.value = field.value || "";
            input.addEventListener("input", (event) => {{
              item.content.fields[index].value = event.target.value;
            }});
            root.append(box);
          }});
        }}

        function cardBlock(kind, card, index, onDelete) {{
          const box = document.createElement("div");
          box.className = "exp-item";
          const showSub = kind === "project" || kind === "tech";
          box.innerHTML = `
            <div class="idx" style="${{kind === "project" || kind === "tech" ? "background:#7c5cff" : ""}}"></div>
            <label class="mini">标题</label>
            <input class="input" data-k="title" value="">
            ${{showSub ? '<label class="mini">副标题 / 技术栈</label><input class="input" data-k="subtitle" value="">' : ""}}
            <label class="mini">内容</label>
            <textarea class="area" data-k="body" rows="6"></textarea>
            <div class="row-act"><button type="button" class="del">🗑 删除</button></div>`;
          box.querySelector('[data-k="title"]').value = card.title || "";
          if (showSub) box.querySelector('[data-k="subtitle"]').value = card.subtitle || "";
          box.querySelector('[data-k="body"]').value = card.body || "";
          box.querySelector(".del").addEventListener("click", onDelete);
          box.querySelectorAll("[data-k]").forEach((el) => {{
            el.addEventListener("input", () => {{ card[el.dataset.k] = el.value; }});
          }});
          return box;
        }}

        function renderCards(key, title, sortOrder, listId, countId, unit, kind) {{
          const item = ensure(key, title, sortOrder, {{ cards: [] }});
          if (!Array.isArray(item.content.cards)) item.content.cards = [];
          const root = document.querySelector(listId);
          root.replaceChildren();
          item.content.cards.forEach((card, index) => {{
            root.append(cardBlock(kind, card, index, () => {{
              item.content.cards.splice(index, 1);
              renderCards(key, title, sortOrder, listId, countId, unit, kind);
            }}));
          }});
          const countEl = document.querySelector(countId);
          if (countEl) countEl.textContent = item.content.cards.length + " " + unit;
        }}

        function renderEdu() {{
          const item = ensure("Education", "教育经历", 4, {{ items: [] }});
          if (!Array.isArray(item.content.items)) item.content.items = [];
          const root = document.querySelector("#edu-list");
          root.replaceChildren();
          item.content.items.forEach((row, index) => {{
            const box = document.createElement("div");
            box.className = "exp-item";
            box.innerHTML = `
              <div class="idx" style="background:#16a34a"></div>
              <label class="mini">教育经历</label>
              <input class="input" data-k="line" value="">
              <div class="row-act"><button type="button" class="del">🗑 删除</button></div>`;
            box.querySelector('[data-k="line"]').value = row.line || "";
            box.querySelector('[data-k="line"]').addEventListener("input", (event) => {{
              row.line = event.target.value;
            }});
            box.querySelector(".del").addEventListener("click", () => {{
              item.content.items.splice(index, 1);
              renderEdu();
            }});
            root.append(box);
          }});
          document.querySelector("#edu-count").textContent = item.content.items.length + " 条";
        }}

        function renderAll() {{
          renderBasic();
          document.querySelector("#skills-body").value = skillsBody(
            ensure("Skills", "专业技能", 1, {{ body: "" }}).content
          );
          document.querySelector("#strengths-body").value =
            (ensure("Strengths", "个人优势", 6, {{ body: "" }}).content.body) || "";
          renderCards("WorkExperience", "工作经历", 2, "#work-list", "#work-count", "段", "work");
          renderCards("Projects", "项目经历", 3, "#project-list", "#project-count", "个", "project");
          renderCards("TechIntro", "简历技术介绍", 5, "#tech-list", "#tech-count-dummy", "个", "tech");
          renderEdu();
        }}

        document.querySelector("#add-work").addEventListener("click", () => {{
          ensure("WorkExperience", "工作经历", 2, {{ cards: [] }}).content.cards.push({{ title: "", body: "" }});
          renderCards("WorkExperience", "工作经历", 2, "#work-list", "#work-count", "段", "work");
        }});
        document.querySelector("#add-project").addEventListener("click", () => {{
          ensure("Projects", "项目经历", 3, {{ cards: [] }}).content.cards.push({{ title: "", subtitle: "", body: "" }});
          renderCards("Projects", "项目经历", 3, "#project-list", "#project-count", "个", "project");
        }});
        document.querySelector("#add-tech").addEventListener("click", () => {{
          ensure("TechIntro", "简历技术介绍", 5, {{ cards: [] }}).content.cards.push({{ title: "", subtitle: "", body: "" }});
          renderCards("TechIntro", "简历技术介绍", 5, "#tech-list", "#tech-count-dummy", "个", "tech");
        }});
        document.querySelector("#add-edu").addEventListener("click", () => {{
          ensure("Education", "教育经历", 4, {{ items: [] }}).content.items.push({{ line: "" }});
          renderEdu();
        }});
        document.querySelector("#headline").addEventListener("input", (event) => {{
          const text = (event.target.value || "").replace(/^求职意向\\s*[:：]\\s*/, "").trim() || "—";
          document.querySelector("#head-intent").textContent = text;
        }});
        document.querySelector("#skills-body").addEventListener("input", (event) => {{
          ensure("Skills", "专业技能", 1, {{ body: "" }}).content = {{ body: event.target.value || "" }};
        }});
        document.querySelector("#strengths-body").addEventListener("input", (event) => {{
          ensure("Strengths", "个人优势", 6, {{ body: "" }}).content = {{ body: event.target.value || "" }};
        }});

        async function saveResume() {{
          status.hidden = false;
          status.className = "status";
          status.textContent = "正在保存到数据库…";
          ensure("Skills", "专业技能", 1, {{ body: "" }}).content = {{
            body: document.querySelector("#skills-body").value || ""
          }};
          ensure("Strengths", "个人优势", 6, {{ body: "" }}).content = {{
            body: document.querySelector("#strengths-body").value || ""
          }};
          const basic = ensure("BasicInfo", "基本信息", 0, {{ fields: [] }});
          basic.content = {{ fields: canonicalBasic(basic.content.fields) }};
          const payload = {{
            published: document.querySelector("#published").checked,
            headline: document.querySelector("#headline").value,
            sections: sections.map((item, index) => ({{
              section_key: item.section_key,
              title: item.title || item.section_key,
              enabled: item.enabled !== false,
              sort_order: Number(item.sort_order ?? index),
              content: item.content || {{}},
            }})),
          }};
          const response = await fetch(`/admin/tenants/${{slug}}/resume`, {{
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
            status.className = "status error";
            return;
          }}
          window.location.assign(body.location || `/admin/tenants/${{slug}}?tab=resume`);
        }}
        document.querySelector("#save").addEventListener("click", saveResume);
        document.querySelector("#save-bottom").addEventListener("click", saveResume);
        renderAll();
      </script>
    """
    return _shell_page(
        title=f"{display_name} · 简历详情",
        active="users",
        crumb=(
            '<a href="/admin" style="color:var(--primary)">简历管理</a> / '
            '<a href="/admin" style="color:var(--primary)">用户简历库</a> / '
            "<b>简历详情</b>"
        ),
        badge=badge,
        trash_badge=trash_badge,
        main=main,
    )


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
    created_at: str = "",
    badge: int = 0,
    trash_badge: int = 0,
    published: bool = False,
    headline: str = "",
) -> str:
    banner = _banner(notice, error)
    rows = _doc_rows(documents)
    upload_action = f"/admin/tenants/{escape(slug)}/documents" if slug else "/admin/documents"
    delete_prefix = f"/admin/tenants/{escape(slug)}/documents/" if slug else "/admin/documents/"
    person = (assistant_owner or display_name or slug or "用户").strip()
    job_intent = _clean_headline(headline) or "—"
    back_btn = (
        '<a class="btn" href="/admin">'
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>返回列表</a>'
        if show_back
        else ""
    )
    caps = "、".join(assistant_capabilities or [])
    owner = assistant_owner or display_name
    settings = ""
    if slug:
        settings = f"""
      <div class="panel" style="margin-bottom:16px">
        <div class="card-head"><h3>站点与 AI 设置</h3></div>
        <div class="card-body">
          <form id="rename-tenant" class="settings-grid" data-slug="{escape(slug)}">
            <div class="form-item">
              <label for="display-name">显示名称</label>
              <input class="input" id="display-name" name="display_name" value="{escape(display_name)}" required>
            </div>
            <div class="form-item">
              <label for="assistant-owner">AI 主人姓名（用于「我是谁」）</label>
              <input class="input" id="assistant-owner" name="assistant_owner" value="{escape(owner)}" required>
            </div>
            <div class="form-item" style="grid-column:1/-1">
              <label for="assistant-capabilities">AI 能力列表（顿号或逗号分隔）</label>
              <input class="input" id="assistant-capabilities" name="assistant_capabilities" value="{escape(caps)}" required>
            </div>
            <div>
              <button class="btn" type="submit">保存租户与自我介绍</button>
              <p id="rename-status" class="status" hidden style="margin-top:10px"></p>
            </div>
          </form>
        </div>
      </div>
        """
    main = f"""
      <div class="detail-head">
        <div class="avatar" style="background:linear-gradient(135deg,#3b5bfd,#7c5cff)">{escape(person[:1] or "用")}</div>
        <div class="who">
          <h2 id="tenant-title">{escape(display_name)}
            <span class="tag">RAG 知识库</span>
            <span class="tag">{escape(job_intent)}</span>
          </h2>
          <div class="meta">
            <span><i>站点：</i>/{escape(slug)}</span>
            <span><i>登记时间：</i>{escape(created_at or "—")}</span>
            <span><i>文档数：</i>{len(documents)}</span>
          </div>
        </div>
        <div class="head-actions">{back_btn}</div>
      </div>
      {_detail_tabs(slug, "docs") if slug else ""}
      {banner}
      {settings}
      <div class="panel" style="margin-bottom:16px">
        <div class="card-head">
          <h3>上传文档</h3>
          <span class="sub">支持 txt / md / csv / pdf / docx，上传后自动切片、索引与向量化</span>
        </div>
        <div class="card-body">
          <form id="upload-form" class="upload-row" method="post" action="{upload_action}" enctype="multipart/form-data">
            <input id="file" class="input" name="file" type="file" accept=".txt,.md,.markdown,.csv,.pdf,.docx" required>
            <button id="upload" class="btn primary" type="submit">上传并入库</button>
          </form>
          <p id="status" class="status" hidden style="margin-top:12px"></p>
        </div>
      </div>
      <div class="panel">
        <div class="card-head">
          <h3>知识库文档</h3>
          <span class="sub">共 {len(documents)} 条</span>
        </div>
        <div class="table-wrap">
          <table class="grid">
            <thead>
              <tr><th>文件</th><th>类型</th><th>切片</th><th>入库时间</th><th style="width:100px">操作</th></tr>
            </thead>
            <tbody data-delete-prefix="{delete_prefix}">
              {rows}
            </tbody>
          </table>
        </div>
      </div>
      <dialog id="failure-dialog" class="fail-dialog">
        <h2>删除没有全部完成</h2>
        <p class="lead">下面这些存储删除失败。文档仍留在列表里，对应服务恢复后可以再删一次。</p>
        <ul id="failure-list"></ul>
        <button id="failure-close" class="btn primary" type="button">知道了</button>
      </dialog>
      {_rename_script() if slug else ""}
      <script>
        const status = document.querySelector("#status");
        const deletePrefix = document.querySelector("tbody").dataset.deletePrefix || "/admin/documents/";
        function showStatus(text, isError) {{
          status.hidden = false;
          status.textContent = text;
          status.className = isError ? "status error" : "status";
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
              showStatus(payload.detail || "上传失败", true);
              button.disabled = false;
              return;
            }}
            window.location.assign(payload.location || window.location.href);
          }} catch (_error) {{
            showStatus("上传失败，请确认网关和存储服务都已启动。", true);
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
                showStatus("有存储没有删除成功", true);
                return;
              }}
              showStatus(payload.detail || "删除失败", true);
              return;
            }}
            window.location.assign(payload.location || window.location.href);
          }} catch (_error) {{
            button.disabled = false;
            showStatus("删除失败，请确认网关和存储服务都已启动。", true);
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
    return _shell_page(
        title=f"{display_name} · RAG 知识库",
        active="users",
        crumb=(
            '<a href="/admin" style="color:var(--primary)">简历管理</a> / '
            '<a href="/admin" style="color:var(--primary)">用户简历库</a> / '
            "<b>RAG 知识库</b>"
        ),
        badge=badge,
        trash_badge=trash_badge,
        main=main,
    )


def _detail_tabs(slug: str, active: str) -> str:
    if not slug:
        return ""
    resume_cls = "dtab active" if active == "resume" else "dtab"
    docs_cls = "dtab active" if active == "docs" else "dtab"
    return f"""
      <nav class="detail-tabs">
        <a class="{resume_cls}" href="/admin/tenants/{escape(slug)}?tab=resume">简历详情</a>
        <a class="{docs_cls}" href="/admin/tenants/{escape(slug)}?tab=docs">RAG 知识库</a>
      </nav>
    """


def _normalize_sections(sections: list[Any]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in sections:
        payload.append(
            {
                "section_key": item.section_key,
                "title": item.title,
                "enabled": item.enabled,
                "sort_order": item.sort_order,
                "content": item.content if isinstance(item.content, dict) else {},
            }
        )
    return payload


def _as_section(raw: dict[str, Any] | None) -> Any:
    if not raw:
        return None

    class _Tmp:
        content = raw.get("content") or {}

    return _Tmp()


def _doc_rows(documents: list[dict[str, str | int]]) -> str:
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
            f"<td class='time'>{escape(created or '—')}</td>"
            "<td>"
            f'<button type="button" class="btn text" style="color:var(--danger)" '
            f'data-document-id="{escape(document_id)}" data-title="{escape(title)}">删除</button>'
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
