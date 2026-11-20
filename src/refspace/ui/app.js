(function () {
  const state = {
    view: "spaces",
    spaces: [],
    sessions: [],
    provenance: [],
    health: null,
    activeSpaceId: null,
    selectedSourceId: null,
    search: "",
    typeFilter: "all",
    confirmCallback: null,
  };

  const $ = (id) => document.getElementById(id);

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toast(message, type = "ok") {
    const box = $("toast-container");
    if (!box) return;
    const item = document.createElement("div");
    item.className = `toast-item ${type === "err" ? "is-err" : ""}`;
    const dotClass = type === "err" ? "is-err" : type === "warn" ? "is-warn" : "is-ok";
    item.innerHTML = `<span class="dot ${dotClass}"></span><span>${esc(message)}</span>`;
    box.appendChild(item);
    setTimeout(() => item.remove(), 2600);
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {}
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }

  function askConfirm(title, message, okLabel, onConfirm) {
    $("confirm-title").textContent = title;
    $("confirm-message").textContent = message;
    $("confirm-ok-btn").textContent = okLabel || "确认执行";
    state.confirmCallback = onConfirm;
    $("confirm-dialog").showModal();
  }

  async function api(path, body = null, allowSoftFailure = false) {
    const opts = body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : { method: "GET" };
    const resp = await fetch(path, opts);
    const data = await resp.json();
    if (!allowSoftFailure && (!resp.ok || data.ok === false)) {
      throw new Error(data.message || "请求失败");
    }
    return data;
  }

  function formatCategory(src) {
    if (src.kind === "folder") return `文件夹 · ${src.item_count || 0} 项`;
    const ext = (src.ext || "").replace(".", "").toUpperCase();
    if (src.category === "table") return ext === "CSV" ? "CSV 表格" : `Excel 表格 (${ext})`;
    if (src.category === "doc") {
      if (ext === "DOCX") return "Word 文档";
      if (ext === "MD") return "Markdown 文档";
      if (ext === "PDF") return "PDF 文档";
      return `文档 (${ext})`;
    }
    return src.ext || "文件";
  }

  function formatScale(src) {
    if (src.kind === "folder") return `${src.item_count || 0} 项受支持文件`;
    const kb = Math.max(1, Math.round((src.last_size || 0) / 1024));
    return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`;
  }

  function statusBadge(status) {
    if (status === "readable") return `<span class="status-pill is-ok"><span class="dot is-ok"></span>可读取</span>`;
    if (status === "updated") return `<span class="status-pill is-info"><span class="dot is-info"></span>有更新</span>`;
    return `<span class="status-pill is-warn"><span class="dot is-warn"></span>需重新关联</span>`;
  }

  function agyStagePill(agy) {
    if (!agy || agy.stage === "unconfigured") return `<span class="status-pill is-warn">未配置</span>`;
    if (agy.stage === "read_succeeded") return `<span class="status-pill is-ok">读取成功</span>`;
    if (agy.stage === "connection_verified") return `<span class="status-pill is-info">连接已验证</span>`;
    return `<span class="status-pill is-neutral">配置已发现</span>`;
  }

  function renderStepperHtml(agy) {
    const s = agy?.steps || {};
    const c1 = s.config_found ? "is-done" : "is-pending";
    const c2 = s.connection_verified ? "is-done" : "is-pending";
    const c3 = s.read_succeeded ? "is-done" : "is-pending";
    return `
      <div class="stepper-row">
        <span class="step-chip ${c1}"><span class="dot ${s.config_found ? "is-ok" : "is-warn"}"></span>① 配置已发现</span>
        <span class="step-arrow">→</span>
        <span class="step-chip ${c2}"><span class="dot ${s.connection_verified ? "is-ok" : "is-info"}"></span>② 连接已验证</span>
        <span class="step-arrow">→</span>
        <span class="step-chip ${c3}"><span class="dot ${s.read_succeeded ? "is-ok" : "is-info"}"></span>③ 读取成功</span>
      </div>`;
  }

  async function refreshAll() {
    const data = await api("/api/state");
    state.spaces = data.spaces || [];
    state.sessions = data.sessions || [];
    state.provenance = data.provenance || [];
    state.health = data.health || null;
    if (!state.activeSpaceId || !state.spaces.some((s) => s.space_id === state.activeSpaceId)) {
      state.activeSpaceId = state.spaces[0]?.space_id || null;
      state.selectedSourceId = null;
    }
    render();
    return data;
  }

  function renderSidebar() {
    $("nav-spaces").classList.toggle("is-active", state.view === "spaces");
    $("nav-connections").classList.toggle("is-active", state.view === "connections");
    $("nav-spaces-count").textContent = String(state.spaces.length);

    const agy = state.health?.antigravity;
    const badge = $("nav-agy-badge");
    if (!agy || agy.stage === "unconfigured") {
      badge.className = "status-pill is-warn";
      badge.textContent = "未配置";
    } else if (agy.stage === "read_succeeded") {
      badge.className = "status-pill is-ok";
      badge.textContent = "读取成功";
    } else if (agy.stage === "connection_verified") {
      badge.className = "status-pill is-info";
      badge.textContent = "连接已验证";
    } else {
      badge.className = "status-pill is-neutral";
      badge.textContent = "配置已发现";
    }

    const srvOk = state.health?.service?.running;
    $("service-dot").className = srvOk ? "dot is-ok" : "dot is-err";
    $("service-label").textContent = srvOk ? "本地服务运行中 · 原件只读" : "本地存储异常";

    const listEl = $("space-list");
    if (!state.spaces.length) {
      listEl.innerHTML = `<div class="meta-line" style="padding:8px;">暂无空间，点击上方「+ 新建」</div>`;
      return;
    }
    listEl.innerHTML = state.spaces
      .map((sp) => {
        const active = state.view === "spaces" && sp.space_id === state.activeSpaceId ? "is-active" : "";
        const aliasTag = sp.aliases?.[0] ? `<span class="mini-tag">${esc(sp.aliases[0])}</span>` : "";
        return `
          <button class="space-item ${active}" type="button" data-space-id="${esc(sp.space_id)}">
            <span class="space-item-main">
              <span class="space-item-name">${esc(sp.name)}</span>
              ${aliasTag}
            </span>
            <span class="nav-count">${sp.source_count}</span>
          </button>`;
      })
      .join("");
  }

  function renderSpacesView() {
    const sp = state.spaces.find((s) => s.space_id === state.activeSpaceId) || null;
    $("btn-add-source").disabled = !sp;
    $("btn-refresh-sources").disabled = !sp;
    $("btn-use-in-ai").hidden = !sp;
    $("btn-clear-search").hidden = !state.search;

    if (!sp) {
      $("active-space-name").textContent = "尚未创建资料空间";
      $("active-space-aliases").hidden = true;
      $("btn-edit-space").hidden = true;
      $("active-space-meta").textContent = "点击左侧「+ 新建」创建第一个资料空间，例如“运营资料（简称：杭州仓）”。";
      $("sources-table-wrap").hidden = true;
      $("sources-tbody").innerHTML = "";
      $("empty-state").hidden = false;
      $("empty-title").textContent = "还没有资料空间";
      $("empty-desc").textContent = "建立空间并关联本机文件夹后，即可在 AI 工作台中直接按空间名称调用。";
      $("btn-reset-filters").hidden = true;
      return;
    }

    $("active-space-name").textContent = sp.name;
    if (sp.aliases?.length) {
      $("active-space-aliases").hidden = false;
      $("active-space-aliases").textContent = `简称：${sp.aliases.join(" / ")}`;
    } else {
      $("active-space-aliases").hidden = true;
    }
    $("btn-edit-space").hidden = false;
    // Keep main header clean: no internal sp_xxx ID exposed here
    $("active-space-meta").textContent = `${sp.source_count} 个本机引用 · 原件保留在原位置`;

    const allSources = sp.sources || [];
    const counts = {
      all: allSources.length,
      folder: allSources.filter((x) => x.category === "folder").length,
      table: allSources.filter((x) => x.category === "table").length,
      doc: allSources.filter((x) => x.category === "doc").length,
    };
    const labels = { all: "全部", folder: "文件夹", table: "表格", doc: "文档" };
    document.querySelectorAll("#type-filters .pill").forEach((btn) => {
      const t = btn.dataset.type;
      btn.textContent = `${labels[t]} (${counts[t] ?? 0})`;
      btn.classList.toggle("is-active", t === state.typeFilter);
    });

    const kw = state.search.trim().toLowerCase();
    const filtered = allSources.filter((src) => {
      if (state.typeFilter !== "all" && src.category !== state.typeFilter) return false;
      if (!kw) return true;
      return src.name.toLowerCase().includes(kw) || src.path.toLowerCase().includes(kw);
    });

    if (!filtered.length) {
      $("sources-table-wrap").hidden = true;
      $("sources-tbody").innerHTML = "";
      $("empty-state").hidden = false;
      const hasFilters = Boolean(kw) || state.typeFilter !== "all";
      $("empty-title").textContent = allSources.length ? "没有匹配的引用项" : "该空间尚未添加本机文件或文件夹";
      $("empty-desc").textContent = allSources.length
        ? `当前筛选条件（${state.typeFilter !== "all" ? labels[state.typeFilter] : "全部"}${kw ? ` · 关键词“${state.search}”` : ""}）下无匹配结果。`
        : "点击右上角「+ 添加引用」关联本机文件夹或单文件。";
      $("btn-reset-filters").hidden = !hasFilters;
      return;
    }

    $("sources-table-wrap").hidden = false;
    $("empty-state").hidden = true;
    // Clean 3-column focus: Name, Type, Status (+ quick relink only if broken, otherwise subtle detail link)
    $("sources-tbody").innerHTML = filtered
      .map((src) => {
        const selected = src.source_id === state.selectedSourceId ? "is-selected" : "";
        const rightCell =
          src.status === "needs_relink"
            ? `<button class="text-btn" type="button" data-action="relink" data-source-id="${esc(src.source_id)}" data-path="${esc(src.path)}">重新关联</button>`
            : `<span class="text-btn is-muted">详情 →</span>`;
        return `
          <tr class="${selected}" data-source-id="${esc(src.source_id)}">
            <td class="col-text"><strong>${esc(src.name)}</strong></td>
            <td class="col-text">${esc(formatCategory(src))}</td>
            <td class="col-text">${statusBadge(src.status)}</td>
            <td class="col-act">${rightCell}</td>
          </tr>`;
      })
      .join("");
  }

  function renderConnectionsView() {
    const h = state.health;
    if (!h) return;
    const agy = h.antigravity;
    const sh = h.sources_health;
    $("conn-checked-meta").textContent = `区分配置文件存在、本机握手连通与实际读取成功三个阶段 · 最近刷新：${h.checked_at || "刚刚"}`;
    $("conn-boundary-bar").textContent = agy.data_boundary_note || "原文件留在本机原位置只读访问；AI 回答问题时，所需内容可能交给所连接的模型处理。";

    $("conn-grid").innerHTML = `
      <div class="conn-card">
        <div class="conn-card-top">
          <span class="conn-name">Antigravity（首发接入平台）</span>
          ${agyStagePill(agy)}
        </div>
        ${renderStepperHtml(agy)}
        <div class="kv-row"><span class="kv-key">配置范围</span><span class="kv-val">${esc(agy.scope)}</span></div>
        <div class="kv-row"><span class="kv-key">最近连接验证</span><span class="kv-val">${esc(agy.last_handshake_at || "尚未执行握手验证")}</span></div>
        <div class="kv-row"><span class="kv-key">最近成功读取</span><span class="kv-val">${esc(agy.last_read_at ? `${agy.last_read_at}${agy.last_read_summary ? `（${agy.last_read_summary}）` : ""}` : "暂无读取记录")}</span></div>
      </div>
      <div class="conn-card">
        <div class="conn-card-top">
          <span class="conn-name">本机引用与存储服务</span>
          ${h.service.running ? '<span class="status-pill is-ok">运行中</span>' : '<span class="status-pill is-warn">异常</span>'}
        </div>
        <div class="kv-row"><span class="kv-key">来源可读状态</span><span class="kv-val">${sh.readable}/${sh.total} 可读 · ${sh.needs_relink} 需重关联</span></div>
        <div class="kv-row"><span class="kv-key">本地记录库</span><span class="kv-val">${esc(h.service.db_path)}</span></div>
        <div class="meta-line" style="margin-top:8px;">仅保存路径引用、表头目录元信息与会话绑定，不另存整份文件副本。</div>
      </div>
      ${(agy.candidate_platforms || [])
        .map(
          (p) => `
        <div class="conn-card">
          <div class="conn-card-top">
            <span class="conn-name">${esc(p.name)}</span>
            <span class="status-pill is-neutral">待逐端验证</span>
          </div>
          <div class="meta-line">${esc(p.status)}</div>
        </div>`
        )
        .join("")}
    `;

    const sessWrap = $("sessions-table-wrap");
    if (!state.sessions.length) {
      sessWrap.innerHTML = `<div class="meta-line">当前尚无已绑定资料空间的活跃会话。在 Antigravity 对话中说出“引用空间名”后将自动按会话记录在此。</div>`;
    } else {
      sessWrap.innerHTML = `<div class="table-scroll"><table class="ref-table">
        <thead><tr><th class="col-text">工作台</th><th class="col-text">会话标识</th><th class="col-text">当前生效范围</th><th class="col-text">持久默认范围</th><th class="col-text">最近更新</th></tr></thead>
        <tbody>${state.sessions
          .map(
            (s) => `<tr>
              <td class="col-text">${esc(s.workbench)}</td>
              <td class="col-text"><code>${esc(s.session_id)}</code></td>
              <td class="col-text">${esc(s.badge)}</td>
              <td class="col-text">${esc(s.default_spaces.map((x) => x.name).join("、") || "无")}</td>
              <td class="col-text">${esc(s.updated_at)}</td>
            </tr>`
          )
          .join("")}</tbody></table></div>`;
    }

    const provWrap = $("provenance-list-wrap");
    if (!state.provenance.length) {
      provWrap.innerHTML = `<div class="meta-line">暂无实际读取记录；在 AI 对话中首次定位或读取空间资料后，时间与文件依据会显示在这里。</div>`;
    } else {
      provWrap.innerHTML = `<div class="table-scroll"><table class="ref-table">
        <thead><tr><th class="col-text">读取时间</th><th class="col-text">会话</th><th class="col-text">操作</th><th class="col-text">读取依据</th></tr></thead>
        <tbody>${state.provenance
          .slice(0, 12)
          .map(
            (p) => `<tr>
              <td class="col-text">${esc(p.created_at)}</td>
              <td class="col-text"><code>${esc(p.session_id.slice(0, 12))}</code></td>
              <td class="col-text">${esc(p.tool_name)}</td>
              <td class="col-text">${esc((p.citations || []).map((c) => `${c.file} · ${c.locator}`).join("；"))}</td>
            </tr>`
          )
          .join("")}</tbody></table></div>`;
    }
  }

  function openUseInAiModal() {
    const sp = state.spaces.find((s) => s.space_id === state.activeSpaceId);
    if (!sp) return;
    const callName = sp.aliases?.[0] || sp.name;
    const agy = state.health?.antigravity || {};
    $("use-ai-title").textContent = `在 AI 中使用「${sp.name}」`;

    const prompts = [
      { label: "首次绑定并提问（后续追问自动沿用）", text: `引用${callName}，先帮我看看里面有哪些表格和文档` },
      { label: "从其他资料切换到当前空间", text: `换成${callName}` },
      { label: "仅当前这一次临时查询（问完恢复原默认）", text: `这次只用${callName}核对一下相关内容` },
      { label: "保留原空间并叠加当前空间", text: `再加上${callName}` },
    ];

    $("use-ai-body").innerHTML = `
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
          <strong>当前 Antigravity 接入状态</strong>
          <div style="display:flex;align-items:center;gap:8px;">
            ${agyStagePill(agy)}
            <button class="secondary-btn" style="height:26px;font-size:12px;padding:0 8px;" type="button" id="modal-verify-mcp-btn">验证连接</button>
          </div>
        </div>
        ${renderStepperHtml(agy)}
        <div class="meta-line">最近成功读取：${esc(agy.last_read_at ? `${agy.last_read_at}${agy.last_read_summary ? `（${agy.last_read_summary}）` : ""}` : "暂无读取记录，复制下方指令到对话发送即可验证")}</div>
      </div>
      <div>
        <div style="font-weight:600;margin-bottom:6px;">直接在 Antigravity 对话框发送以下任一指令：</div>
        <div class="prompt-list">
          ${prompts
            .map(
              (p) => `
            <div class="prompt-card">
              <div>
                <div class="prompt-label">${esc(p.label)}</div>
                <div class="prompt-text">${esc(p.text)}</div>
              </div>
              <button class="secondary-btn" type="button" data-copy-prompt="${esc(p.text)}" style="flex-shrink:0;">复制</button>
            </div>`
            )
            .join("")}
        </div>
      </div>
    `;

    $("modal-verify-mcp-btn")?.addEventListener("click", async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      btn.textContent = "验证中…";
      try {
        const res = await api("/api/antigravity/verify-mcp", {});
        await refreshAll();
        toast(`连接握手通过（已识别 ${res.tool_count} 个工具）`);
        openUseInAiModal();
      } catch (err) {
        toast(err.message, "err");
        btn.disabled = false;
        btn.textContent = "验证连接";
      }
    });

    if (!$("use-ai-dialog").open) {
      $("use-ai-dialog").showModal();
    }
  }

  function promptRemoveSource(sourceId, sourceName) {
    if (!state.activeSpaceId) return;
    askConfirm(
      "移除空间引用",
      `确认从当前资料空间移除「${sourceName}」吗？电脑原路径下的原文件不会被删除或修改。`,
      "确认移除",
      async () => {
        await api("/api/sources/remove", { space_id: state.activeSpaceId, source_id: sourceId });
        if (state.selectedSourceId === sourceId) {
          state.selectedSourceId = null;
          $("drawer-body").innerHTML = "";
        }
        await refreshAll();
        toast(`已从当前空间移除「${sourceName}」`);
      }
    );
  }

  async function openSourceDrawer(sourceId) {
    state.selectedSourceId = sourceId;
    render();
    $("drawer-body").innerHTML = `<div class="meta-line">正在读取引用详情…</div>`;

    try {
      const data = await api(`/api/source-preview?source_id=${encodeURIComponent(sourceId)}`);
      if (state.selectedSourceId !== sourceId) return;
      const src = data.source;
      const sp = state.spaces.find((s) => s.space_id === state.activeSpaceId);

      let previewSummary = "";
      if (data.preview?.kind === "table") {
        const d = data.preview.detail;
        previewSummary =
          `<div class="meta-line">已识别 ${d.sheet_count} 张工作表</div>` +
          d.sheets
            .slice(0, 5)
            .map((sh) => `<div class="kv-row"><span class="kv-key">${esc(sh.sheet_name)}</span><span class="kv-val">${sh.regions.reduce((a, r) => a + r.record_count, 0)} 条记录</span></div>`)
            .join("");
      } else if (data.preview?.kind === "doc") {
        const d = data.preview.detail;
        previewSummary =
          `<div class="meta-line">已识别 ${d.section_count} 个章节段落</div>` +
          (d.outline || [])
            .slice(0, 5)
            .map((o) => `<div class="kv-row"><span class="kv-key">${esc(o.section)}</span><span class="kv-val">${esc(o.locator)}</span></div>`)
            .join("");
      } else if (data.preview?.kind === "folder") {
        previewSummary =
          `<div class="meta-line">目录内发现 ${data.preview.file_count} 个受支持资料文件</div>` +
          (data.preview.files || [])
            .slice(0, 6)
            .map((f) => `<div class="kv-row"><span class="kv-key">${esc(f.name)}</span><span class="kv-val">${esc(f.ext)}</span></div>`)
            .join("");
      } else {
        previewSummary = `<div class="form-error">${esc(data.warning || src.status_note)}</div>`;
      }

      $("drawer-body").innerHTML = `
        <div class="drawer-section">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;">
            <h3 style="margin:0;">${esc(src.name)}</h3>
            ${statusBadge(src.status)}
          </div>
          <div class="kv-row"><span class="kv-key">类型</span><span class="kv-val">${esc(formatCategory(src))}</span></div>
          <div class="kv-row"><span class="kv-key">规模</span><span class="kv-val">${esc(formatScale(src))}</span></div>
          <div class="kv-row"><span class="kv-key">原件修改</span><span class="kv-val">${esc(src.last_mtime_iso || "—")}</span></div>
          <div class="kv-row"><span class="kv-key">最近检查</span><span class="kv-val">${esc(src.last_checked_at || "—")}</span></div>
          <div style="margin-top:8px;display:flex;align-items:center;justify-content:space-between;">
            <span class="kv-key" style="font-size:12px;">本机完整路径</span>
            <button class="text-btn" type="button" id="drawer-copy-path">复制路径</button>
          </div>
          <div class="path-box">${esc(src.path)}</div>
        </div>
        <div class="drawer-section">
          <h3>已识别内容摘要</h3>
          ${previewSummary}
          ${data.preview ? `<button class="ghost-btn sm" style="margin-top:8px;padding-left:0;" type="button" id="drawer-full-inspect">查看完整字段与章节结构 →</button>` : ""}
        </div>
        <div class="drawer-section">
          <h3>维护操作</h3>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button class="secondary-btn" type="button" id="drawer-reveal">在访达中定位</button>
            <button class="secondary-btn" type="button" id="drawer-relink">重新关联路径</button>
            <button class="danger-btn" type="button" id="drawer-remove">移除此引用</button>
          </div>
          <div class="meta-line" style="margin-top:10px;">内部编号：来源 ${esc(src.source_id)}${sp ? ` · 所属空间 ${esc(sp.space_id)}` : ""}</div>
        </div>`;

      $("drawer-copy-path")?.addEventListener("click", async () => {
        await copyText(src.path);
        toast("已复制完整路径");
      });
      $("drawer-reveal")?.addEventListener("click", async (ev) => {
        const btn = ev.currentTarget;
        btn.disabled = true;
        try {
          const r = await api("/api/system/reveal", { path: src.path }, true);
          toast(r.message || `原路径：${src.path}`, r.ok ? "ok" : "warn");
        } finally {
          btn.disabled = false;
        }
      });
      $("drawer-relink")?.addEventListener("click", () => openSourceModal("relink", src.source_id, src.path));
      $("drawer-remove")?.addEventListener("click", () => promptRemoveSource(src.source_id, src.name));
      $("drawer-full-inspect")?.addEventListener("click", () => openFullInspectModal(data));
    } catch (err) {
      $("drawer-body").innerHTML = `<div class="form-error">${esc(err.message)}</div>`;
    }
  }

  async function openFullInspectModal(previewData) {
    const src = previewData.source;
    $("inspect-dialog-title").textContent = `${src.name} · 已识别结构详情`;
    $("inspect-dialog-meta").textContent = `原路径：${src.path} · 最近修改：${src.last_mtime_iso || "—"}`;
    const body = $("inspect-dialog-body");

    if (previewData.preview?.kind === "table") {
      const detail = previewData.preview.detail;
      const warnHtml = (detail.warnings || []).length
        ? `<div class="form-error" style="margin-bottom:12px;">${detail.warnings.map(esc).join("<br/>")}</div>`
        : "";
      body.innerHTML =
        warnHtml +
        detail.sheets
          .map(
            (sh) => `
          <div class="sub-card" style="margin-top:0;margin-bottom:12px;">
            <div class="sub-card-head"><h2>工作表：${esc(sh.sheet_name)}</h2></div>
            ${sh.regions
              .map(
                (rg) => `
              <div class="meta-line" style="margin-bottom:8px;">${esc(rg.region_name)} · 有效记录 ${rg.record_count} 条 · 有值字段 ${rg.non_empty_field_count} 个 · 全空字段 ${rg.empty_field_count} 个</div>
              <div class="table-scroll"><table class="ref-table">
                <thead><tr><th class="col-text">源列</th><th class="col-text">字段名</th><th class="col-text">识别类型</th><th class="col-num">有值记录数</th><th class="col-text">内容样例</th></tr></thead>
                <tbody>${rg.fields
                  .map(
                    (f) => `<tr>
                    <td class="col-text">${esc(f.col)}</td>
                    <td class="col-text"><strong>${esc(f.name)}</strong></td>
                    <td class="col-text">${esc(f.type)}</td>
                    <td class="col-num">${f.non_empty}</td>
                    <td class="col-text">${esc((f.samples || []).join(" / "))}</td>
                  </tr>`
                  )
                  .join("")}</tbody>
              </table></div>`
              )
              .join("")}
          </div>`
          )
          .join("");
    } else if (previewData.preview?.kind === "doc") {
      const detail = previewData.preview.detail;
      body.innerHTML = `<div class="table-scroll"><table class="ref-table">
        <thead><tr><th class="col-text">章节标题</th><th class="col-num">层级</th><th class="col-text">位置</th><th class="col-num">字符长度</th></tr></thead>
        <tbody>${(detail.outline || [])
          .map(
            (o) => `<tr><td class="col-text"><strong>${esc(o.section)}</strong></td><td class="col-num">H${o.level}</td><td class="col-text">${esc(o.locator)}</td><td class="col-num">${o.char_len}</td></tr>`
          )
          .join("")}</tbody></table></div>`;
    } else if (previewData.preview?.kind === "folder") {
      body.innerHTML = `<div class="table-scroll"><table class="ref-table">
        <thead><tr><th class="col-text">文件名</th><th class="col-text">分类</th><th class="col-text">扩展名</th><th class="col-text">修改时间</th></tr></thead>
        <tbody>${(previewData.preview.files || [])
          .map((f) => `<tr><td class="col-text">${esc(f.name)}</td><td class="col-text">${esc(f.category)}</td><td class="col-text">${esc(f.ext)}</td><td class="col-text">${esc(f.mtime_iso)}</td></tr>`)
          .join("")}</tbody></table></div>`;
    } else {
      body.innerHTML = `<div class="form-error">${esc(previewData.warning || src.status_note || "当前无法读取该来源结构")}</div>`;
    }
    $("inspect-dialog").showModal();
  }

  async function renderInModalBrowser(containerId, targetInputId, dirPath = "") {
    const box = $(containerId);
    if (!box) return;
    box.hidden = false;
    box.innerHTML = `<div class="meta-line">正在读取本机目录列表…</div>`;
    try {
      const data = await api(`/api/system/browse-dir?dir=${encodeURIComponent(dirPath)}`);
      const shortcutsHtml = (data.shortcuts || [])
        .map((s) => `<button class="pill" type="button" data-dir-nav="${esc(s.path)}" data-container="${esc(containerId)}" data-target="${esc(targetInputId)}">${esc(s.label)}</button>`)
        .join("");
      const entriesHtml = (data.entries || []).length
        ? data.entries
            .map((ent) => {
              const isFolder = ent.kind === "folder";
              return `<div class="dir-entry">
                <button class="dir-entry-open ${isFolder ? "is-folder" : ""}" type="button"
                  ${isFolder ? `data-dir-nav="${esc(ent.path)}"` : `data-dir-pick="${esc(ent.path)}"`}
                  data-container="${esc(containerId)}" data-target="${esc(targetInputId)}">
                  ${isFolder ? "📁 " : "📄 "}${esc(ent.name)}
                </button>
                <button class="text-btn" type="button" data-dir-pick="${esc(ent.path)}" data-container="${esc(containerId)}" data-target="${esc(targetInputId)}">选定</button>
              </div>`;
            })
            .join("")
        : `<div class="meta-line" style="padding:10px;">此目录下暂无可引用的子文件夹或支持格式文件，可直接选定上方当前目录。</div>`;

      box.innerHTML = `
        <div class="dir-shortcuts">${shortcutsHtml}</div>
        <div class="dir-bar">
          <span class="dir-cur" title="${esc(data.current_dir)}">${esc(data.current_dir)}</span>
          <div style="display:flex;gap:6px;flex-shrink:0;">
            ${data.parent_dir ? `<button class="text-btn" type="button" data-dir-nav="${esc(data.parent_dir)}" data-container="${esc(containerId)}" data-target="${esc(targetInputId)}">↑ 上级</button>` : ""}
            <button class="text-btn" type="button" data-dir-pick="${esc(data.current_dir)}" data-container="${esc(containerId)}" data-target="${esc(targetInputId)}">选定当前文件夹</button>
          </div>
        </div>
        <div class="dir-list">${entriesHtml}</div>
      `;
    } catch (err) {
      box.innerHTML = `<div class="form-error">${esc(err.message)}</div>`;
    }
  }

  function openSpaceModal(space = null) {
    $("space-dialog-title").textContent = space ? "编辑资料空间" : "新建资料空间";
    $("space-form-id").value = space ? space.space_id : "";
    $("space-form-name").value = space ? space.name : "";
    $("space-form-aliases").value = space ? (space.aliases || []).join(", ") : "";
    $("space-form-path").value = "";
    const idHint = $("space-internal-id-hint");
    if (space) {
      idHint.hidden = false;
      idHint.textContent = `内部固定编号：${space.space_id}（修改名称或简称不影响已绑定会话）`;
    } else {
      idHint.hidden = true;
    }
    $("space-initial-path-wrap").hidden = Boolean(space);
    $("btn-delete-space").hidden = !space;
    $("space-form-error").hidden = true;
    $("space-browser").hidden = true;
    $("space-dialog").showModal();
    setTimeout(() => $("space-form-name").focus(), 20);
  }

  function openSourceModal(mode = "add", sourceId = "", initialPath = "") {
    $("source-dialog-title").textContent = mode === "relink" ? "重新关联本机路径" : "添加本机引用";
    $("source-form-mode").value = mode;
    $("source-form-id").value = sourceId;
    $("source-form-path").value = initialPath;
    $("source-form-error").hidden = true;
    $("source-browser").hidden = true;
    $("source-dialog").showModal();
    setTimeout(() => $("source-form-path").focus(), 20);
  }

  function render() {
    renderSidebar();
    $("view-spaces").hidden = state.view !== "spaces";
    $("view-connections").hidden = state.view !== "connections";
    $("detail-drawer").hidden = state.view !== "spaces" || !state.selectedSourceId;
    if (state.view === "spaces") renderSpacesView();
    else renderConnectionsView();
  }

  document.addEventListener("click", async (e) => {
    const closeBtn = e.target.closest("[data-close]");
    if (closeBtn) {
      $(closeBtn.dataset.close)?.close();
      return;
    }

    const copyBtn = e.target.closest("[data-copy-prompt]");
    if (copyBtn) {
      const txt = copyBtn.dataset.copyPrompt;
      await copyText(txt);
      toast(`已复制指令：「${txt}」`);
      return;
    }

    const browseToggle = e.target.closest("[data-browse-toggle]");
    if (browseToggle) {
      const cid = browseToggle.dataset.browseToggle;
      const tid = browseToggle.dataset.target;
      const box = $(cid);
      if (!box.hidden) {
        box.hidden = true;
      } else {
        await renderInModalBrowser(cid, tid, $(tid)?.value || "");
      }
      return;
    }

    const dirNav = e.target.closest("[data-dir-nav]");
    if (dirNav) {
      await renderInModalBrowser(dirNav.dataset.container, dirNav.dataset.target, dirNav.dataset.dirNav);
      return;
    }

    const dirPick = e.target.closest("[data-dir-pick]");
    if (dirPick) {
      const targetInput = $(dirPick.dataset.target);
      if (targetInput) {
        targetInput.value = dirPick.dataset.dirPick;
        $("source-form-error").hidden = true;
        $("space-form-error").hidden = true;
      }
      $(dirPick.dataset.container).hidden = true;
      toast(`已填入路径：${dirPick.dataset.dirPick}`);
      return;
    }

    const pickBtn = e.target.closest("[data-pick]");
    if (pickBtn) {
      const origLabel = pickBtn.textContent;
      pickBtn.disabled = true;
      pickBtn.textContent = "正在拉起访达…";
      try {
        const res = await api("/api/system/pick-path", { kind: pickBtn.dataset.pick }, true);
        if (res.ok && res.picked) {
          $(pickBtn.dataset.target).value = res.picked;
          $("source-form-error").hidden = true;
          $("space-form-error").hidden = true;
          toast(`已选择本机路径：${res.picked}`);
        } else if (res.ok && res.canceled) {
          toast("已取消访达选择", "warn");
        } else {
          const fallbackId = pickBtn.dataset.target === "space-form-path" ? "space-browser" : "source-browser";
          toast(res.message || "已为你展开页面内目录选择器", "warn");
          await renderInModalBrowser(fallbackId, pickBtn.dataset.target, $(pickBtn.dataset.target)?.value || "");
        }
      } finally {
        pickBtn.disabled = false;
        pickBtn.textContent = origLabel;
      }
      return;
    }

    const navBtn = e.target.closest("[data-view]");
    if (navBtn) {
      state.view = navBtn.dataset.view;
      render();
      return;
    }

    const spaceBtn = e.target.closest("[data-space-id]");
    if (spaceBtn) {
      state.view = "spaces";
      state.activeSpaceId = spaceBtn.dataset.spaceId;
      state.selectedSourceId = null;
      render();
      return;
    }

    const actBtn = e.target.closest("[data-action]");
    if (actBtn) {
      e.stopPropagation();
      const sid = actBtn.dataset.sourceId;
      if (actBtn.dataset.action === "relink") {
        openSourceModal("relink", sid, actBtn.dataset.path || "");
      }
      return;
    }

    const row = e.target.closest("#sources-tbody tr[data-source-id]");
    if (row) {
      await openSourceDrawer(row.dataset.sourceId);
    }
  });

  $("btn-use-in-ai").addEventListener("click", () => openUseInAiModal());
  $("btn-new-space").addEventListener("click", () => openSpaceModal(null));
  $("btn-edit-space").addEventListener("click", () => {
    const sp = state.spaces.find((s) => s.space_id === state.activeSpaceId);
    if (sp) openSpaceModal(sp);
  });
  $("btn-add-source").addEventListener("click", () => openSourceModal("add"));
  $("btn-close-drawer").addEventListener("click", () => {
    state.selectedSourceId = null;
    $("drawer-body").innerHTML = "";
    render();
  });

  $("btn-refresh-sources").addEventListener("click", async (e) => {
    if (!state.activeSpaceId) return;
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "检查中…";
    try {
      const res = await api("/api/sources/refresh", { space_id: state.activeSpaceId });
      await refreshAll();
      const list = res.sources || [];
      const broken = list.filter((x) => x.status === "needs_relink").length;
      const updated = list.filter((x) => x.status === "updated").length;
      if (broken > 0) toast(`检查完成：${broken} 项需重新关联`, "warn");
      else if (updated > 0) toast(`检查完成：发现 ${updated} 项原文件已更新`);
      else toast(`检查完成：当前空间 ${list.length} 项原文件均可正常读取`);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      btn.disabled = false;
      btn.textContent = "检查更新";
    }
  });

  $("btn-verify-mcp").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "验证中…";
    try {
      const res = await api("/api/antigravity/verify-mcp", {});
      await refreshAll();
      toast(`本机连接已验证（识别到 ${res.tool_count} 个工具）`);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      btn.disabled = false;
      btn.textContent = "验证本机连接";
    }
  });

  $("btn-refresh-health").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "刷新中…";
    try {
      const data = await refreshAll();
      toast(`状态已更新（当前阶段：${data.health.antigravity.stage_label}）`);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      btn.disabled = false;
      btn.textContent = "刷新状态";
    }
  });

  $("search-input").addEventListener("input", (e) => {
    state.search = e.target.value;
    renderSpacesView();
  });

  $("btn-clear-search").addEventListener("click", () => {
    state.search = "";
    $("search-input").value = "";
    renderSpacesView();
  });

  $("btn-reset-filters").addEventListener("click", () => {
    state.search = "";
    state.typeFilter = "all";
    $("search-input").value = "";
    renderSpacesView();
  });

  $("type-filters").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-type]");
    if (!btn) return;
    state.typeFilter = btn.dataset.type;
    renderSpacesView();
  });

  $("space-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const sid = $("space-form-id").value;
    const name = $("space-form-name").value.trim();
    const errEl = $("space-form-error");
    if (!name) {
      errEl.hidden = false;
      errEl.textContent = "请填写资料空间名称";
      $("space-form-name").focus();
      return;
    }
    const aliases = $("space-form-aliases")
      .value.split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
    const initPath = $("space-form-path").value.trim();
    const submitBtn = $("space-submit-btn");
    submitBtn.disabled = true;
    try {
      if (sid) {
        await api("/api/spaces/update", { space_id: sid, name, aliases });
        toast(`已更新资料空间「${name}」`);
      } else {
        const res = await api("/api/spaces/create", { name, aliases, source_paths: initPath ? [initPath] : [] });
        state.activeSpaceId = res.space.space_id;
        state.selectedSourceId = null;
        toast(`已创建资料空间「${name}」`);
      }
      $("space-dialog").close();
      await refreshAll();
    } catch (err) {
      errEl.hidden = false;
      errEl.textContent = err.message;
    } finally {
      submitBtn.disabled = false;
    }
  });

  $("btn-delete-space").addEventListener("click", () => {
    const sid = $("space-form-id").value;
    const sp = state.spaces.find((s) => s.space_id === sid);
    if (!sid || !sp) return;
    askConfirm(
      "删除资料空间",
      `确认删除资料空间「${sp.name}」吗？只会移除空间与引用记录，本机原文件夹与文件不会被改动。`,
      "确认删除",
      async () => {
        await api("/api/spaces/delete", { space_id: sid });
        if (state.activeSpaceId === sid) {
          state.activeSpaceId = null;
          state.selectedSourceId = null;
        }
        $("space-dialog").close();
        await refreshAll();
        toast(`已删除资料空间「${sp.name}」`);
      }
    );
  });

  $("source-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = $("source-form-mode").value;
    const srcId = $("source-form-id").value;
    const path = $("source-form-path").value.trim();
    const errEl = $("source-form-error");
    if (!path) {
      errEl.hidden = false;
      errEl.textContent = "请填写本机路径，或点击上方按钮从访达 / 页面目录选择器中选定";
      $("source-form-path").focus();
      return;
    }
    const submitBtn = $("source-submit-btn");
    submitBtn.disabled = true;
    try {
      if (mode === "relink") {
        await api("/api/sources/relink", { source_id: srcId, new_path: path });
        toast("已重新关联本机路径");
      } else {
        await api("/api/sources/add", { space_id: state.activeSpaceId, path });
        toast("已添加本机引用");
      }
      $("source-dialog").close();
      await refreshAll();
    } catch (err) {
      errEl.hidden = false;
      errEl.textContent = err.message;
    } finally {
      submitBtn.disabled = false;
    }
  });

  $("confirm-ok-btn").addEventListener("click", async () => {
    const fn = state.confirmCallback;
    state.confirmCallback = null;
    $("confirm-dialog").close();
    if (fn) {
      try {
        await fn();
      } catch (err) {
        toast(err.message, "err");
      }
    }
  });

  refreshAll();
})();
