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
  };

  const $ = (id) => document.getElementById(id);

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function api(path, body = null) {
    const opts = body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : { method: "GET" };
    const resp = await fetch(path, opts);
    const data = await resp.json();
    if (!resp.ok || data.ok === false) {
      throw new Error(data.message || "请求失败");
    }
    return data;
  }

  function formatCategory(src) {
    if (src.kind === "folder") return "文件夹";
    if (src.category === "table") return `表格 (${(src.ext || "").replace(".", "").toUpperCase()})`;
    if (src.category === "doc") return `文档 (${(src.ext || "").replace(".", "").toUpperCase()})`;
    return src.ext || "文件";
  }

  function formatScale(src) {
    if (src.kind === "folder") return `${src.item_count || 0} 项支持文件`;
    const kb = Math.max(1, Math.round((src.last_size || 0) / 1024));
    return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`;
  }

  function statusBadge(status) {
    if (status === "readable") return `<span class="status-pill is-ok"><span class="dot is-ok"></span>可读取</span>`;
    if (status === "updated") return `<span class="status-pill is-info"><span class="dot" style="background:#2F5BCE"></span>有更新</span>`;
    return `<span class="status-pill is-warn"><span class="dot is-warn"></span>需重新关联</span>`;
  }

  async function refreshAll() {
    const data = await api("/api/state");
    state.spaces = data.spaces || [];
    state.sessions = data.sessions || [];
    state.provenance = data.provenance || [];
    state.health = data.health || null;
    if (!state.activeSpaceId || !state.spaces.some((s) => s.space_id === state.activeSpaceId)) {
      state.activeSpaceId = state.spaces[0]?.space_id || null;
    }
    render();
  }

  function renderSidebar() {
    $("nav-spaces").classList.toggle("is-active", state.view === "spaces");
    $("nav-connections").classList.toggle("is-active", state.view === "connections");
    $("nav-spaces-count").textContent = String(state.spaces.length);

    const agy = state.health?.antigravity;
    const badge = $("nav-agy-badge");
    if (agy?.connected) {
      badge.className = "status-pill is-ok";
      badge.textContent = "Antigravity 已就绪";
    } else {
      badge.className = "status-pill is-warn";
      badge.textContent = "待接入";
    }

    const srvOk = state.health?.service?.running;
    $("service-dot").className = srvOk ? "dot is-ok" : "dot is-err";
    $("service-label").textContent = srvOk ? "本地服务运行中 · 只读沙箱" : "本地存储异常";

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

    if (!sp) {
      $("active-space-name").textContent = "尚未创建资料空间";
      $("active-space-aliases").hidden = true;
      $("btn-edit-space").hidden = true;
      $("active-space-meta").textContent = "点击左侧「+ 新建」创建第一个资料空间，例如“运营资料（简称：杭州仓）”。";
      $("sources-tbody").innerHTML = "";
      $("empty-state").hidden = false;
      $("empty-title").textContent = "还没有资料空间";
      $("empty-desc").textContent = "建立空间并关联本机文件夹后，即可在 Antigravity 中直接说“引用空间名”。";
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
    $("active-space-meta").textContent = `${sp.source_count} 个本机引用 · 空间编号 ${sp.space_id} · 原件保留在原路径`;

    const kw = state.search.trim().toLowerCase();
    const filtered = (sp.sources || []).filter((src) => {
      if (state.typeFilter !== "all" && src.category !== state.typeFilter) return false;
      if (!kw) return true;
      return src.name.toLowerCase().includes(kw) || src.path.toLowerCase().includes(kw);
    });

    document.querySelectorAll("#type-filters .pill").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.type === state.typeFilter);
    });

    if (!filtered.length) {
      $("sources-tbody").innerHTML = "";
      $("empty-state").hidden = false;
      $("empty-title").textContent = sp.sources.length ? "没有匹配的引用项" : "该空间尚未添加本机文件或文件夹";
      $("empty-desc").textContent = sp.sources.length ? "请调整搜索词或类型筛选条件。" : "点击右上角「+ 添加引用」关联本机文件夹或单文件。";
      return;
    }

    $("empty-state").hidden = true;
    $("sources-tbody").innerHTML = filtered
      .map((src) => {
        const selected = src.source_id === state.selectedSourceId ? "is-selected" : "";
        return `
          <tr class="${selected}" data-source-id="${esc(src.source_id)}">
            <td class="col-text"><strong>${esc(src.name)}</strong></td>
            <td class="col-text">${esc(formatCategory(src))}</td>
            <td class="col-num">${esc(formatScale(src))}</td>
            <td class="col-text path-cell" title="${esc(src.path)}">${esc(src.path)}</td>
            <td class="col-text">${statusBadge(src.status)}</td>
            <td class="col-text">${esc(src.last_checked_at.slice(11, 16) || src.last_checked_at)}</td>
            <td class="col-act">
              <button class="text-btn" type="button" data-action="relink" data-source-id="${esc(src.source_id)}" data-path="${esc(src.path)}">重关联</button>
              <button class="text-btn" type="button" data-action="inspect" data-source-id="${esc(src.source_id)}">结构</button>
            </td>
          </tr>`;
      })
      .join("");
  }

  function renderConnectionsView() {
    const h = state.health;
    if (!h) return;
    const agy = h.antigravity;
    const sh = h.sources_health;
    $("conn-grid").innerHTML = `
      <div class="conn-card">
        <div class="conn-card-top">
          <span class="conn-name">本地引用与只读沙箱服务</span>
          ${h.service.running ? '<span class="status-pill is-ok">运行中</span>' : '<span class="status-pill is-warn">异常</span>'}
        </div>
        <div class="kv-row"><span class="kv-key">本地数据库</span><span class="kv-val">${esc(h.service.db_path)}</span></div>
        <div class="kv-row"><span class="kv-key">来源可读状态</span><span class="kv-val">${sh.readable}/${sh.total} 可读 · ${sh.needs_relink} 需重关联</span></div>
      </div>
      <div class="conn-card">
        <div class="conn-card-top">
          <span class="conn-name">Antigravity（首发验证平台）</span>
          ${agy.connected ? '<span class="status-pill is-ok">插件与 Hooks 已就绪</span>' : '<span class="status-pill is-warn">未检测到插件</span>'}
        </div>
        <div class="kv-row"><span class="kv-key">插件生效范围</span><span class="kv-val">${esc(agy.scope)}</span></div>
        <div class="kv-row"><span class="kv-key">宿主会话物理隔离</span><span class="kv-val">${agy.hooks_isolation_enabled ? "已启用 (PreInvocation / PreToolUse / Stop)" : "未启用"}</span></div>
      </div>
      ${(agy.candidate_platforms || [])
        .map(
          (p) => `
        <div class="conn-card">
          <div class="conn-card-top">
            <span class="conn-name">${esc(p.name)}</span>
            <span class="status-pill is-warn">待适配验收</span>
          </div>
          <div class="meta-line">${esc(p.status)}</div>
        </div>`
        )
        .join("")}
    `;

    const sessWrap = $("sessions-table-wrap");
    if (!state.sessions.length) {
      sessWrap.innerHTML = `<div class="meta-line">当前尚无已绑定资料空间的活跃会话。在 Antigravity 对话中说出“引用空间名”后将自动记录在此。</div>`;
    } else {
      sessWrap.innerHTML = `<table class="ref-table">
        <thead><tr><th class="col-text">工作台</th><th class="col-text">会话标识 (Conversation ID)</th><th class="col-text">当前生效范围</th><th class="col-text">持久默认范围</th><th class="col-text">最近更新</th></tr></thead>
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
          .join("")}</tbody></table>`;
    }

    const provWrap = $("provenance-list-wrap");
    if (!state.provenance.length) {
      provWrap.innerHTML = `<div class="meta-line">暂无任务读取记录。</div>`;
    } else {
      provWrap.innerHTML = `<table class="ref-table">
        <thead><tr><th class="col-text">时间</th><th class="col-text">会话</th><th class="col-text">调用工具</th><th class="col-text">来源依据</th></tr></thead>
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
          .join("")}</tbody></table>`;
    }
  }

  async function openSourceDrawer(sourceId) {
    state.selectedSourceId = sourceId;
    renderSpacesView();
    const drawer = $("detail-drawer");
    drawer.hidden = false;
    $("drawer-body").innerHTML = `<div class="meta-line">正在按需读取结构摘要…</div>`;

    try {
      const data = await api(`/api/source-preview?source_id=${encodeURIComponent(sourceId)}`);
      const src = data.source;
      const sessionsHtml = state.sessions.length
        ? state.sessions
            .slice(0, 4)
            .map(
              (s) => `
            <div class="session-card ${s.temp_active ? "is-once" : ""}">
              <div class="session-id">会话 ${esc(s.session_id.slice(0, 12))}</div>
              <div class="session-scope">${esc(s.badge)}</div>
            </div>`
            )
            .join("")
        : `<div class="meta-line">暂无绑定会话；各窗口将按宿主 Conversation ID 独立隔离。</div>`;

      let previewSummary = "";
      if (data.preview?.kind === "table") {
        const d = data.preview.detail;
        previewSummary = `<div class="meta-line">共识别 ${d.sheet_count} 张工作表</div>` +
          d.sheets.slice(0, 5).map((sh) => `<div class="kv-row"><span class="kv-key">${esc(sh.sheet_name)}</span><span class="kv-val">${sh.regions.reduce((a, r) => a + r.record_count, 0)} 条</span></div>`).join("");
      } else if (data.preview?.kind === "doc") {
        const d = data.preview.detail;
        previewSummary = `<div class="meta-line">共识别 ${d.section_count} 个章节段落</div>` +
          (d.outline || []).slice(0, 5).map((o) => `<div class="kv-row"><span class="kv-key">${esc(o.section)}</span><span class="kv-val">${esc(o.locator)}</span></div>`).join("");
      } else if (data.preview?.kind === "folder") {
        previewSummary = `<div class="meta-line">目录内发现 ${data.preview.file_count} 个受支持资料文件</div>` +
          (data.preview.files || []).slice(0, 6).map((f) => `<div class="kv-row"><span class="kv-key">${esc(f.name)}</span><span class="kv-val">${esc(f.ext)}</span></div>`).join("");
      } else {
        previewSummary = `<div class="form-error">${esc(data.warning || src.status_note)}</div>`;
      }

      $("drawer-body").innerHTML = `
        <div class="drawer-section">
          <h3>Antigravity 会话隔离状态</h3>
          ${sessionsHtml}
        </div>
        <div class="drawer-section">
          <h3>${esc(src.name)}</h3>
          <div class="kv-row"><span class="kv-key">访问模式</span><span class="kv-val">只读按需提取</span></div>
          <div class="kv-row"><span class="kv-key">本机原路径</span><span class="kv-val">${esc(src.path)}</span></div>
          <div class="kv-row"><span class="kv-key">原件修改时间</span><span class="kv-val">${esc(src.last_mtime_iso || "—")}</span></div>
          <div style="display:flex; gap:8px; margin-top:10px; flex-wrap:wrap;">
            <button class="secondary-btn" type="button" id="drawer-reveal">打开原位置</button>
            <button class="secondary-btn" type="button" id="drawer-relink">重新关联</button>
            <button class="danger-btn" type="button" id="drawer-remove">移除引用</button>
          </div>
        </div>
        <div class="drawer-section">
          <h3>按需识别摘要</h3>
          ${previewSummary}
          ${data.preview ? `<button class="ghost-btn sm" style="margin-top:8px;padding-left:0;" type="button" id="drawer-full-inspect">在大弹窗查看完整结构 →</button>` : ""}
        </div>`;

      $("drawer-reveal")?.addEventListener("click", () => api("/api/system/reveal", { path: src.path }));
      $("drawer-relink")?.addEventListener("click", () => openSourceModal("relink", src.source_id, src.path));
      $("drawer-remove")?.addEventListener("click", async () => {
        if (!state.activeSpaceId) return;
        await api("/api/sources/remove", { space_id: state.activeSpaceId, source_id: src.source_id });
        state.selectedSourceId = null;
        drawer.hidden = true;
        await refreshAll();
      });
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
      body.innerHTML = warnHtml + detail.sheets
        .map(
          (sh) => `
          <div class="sub-card" style="margin-top:0;margin-bottom:12px;">
            <div class="sub-card-head"><h2>工作表：${esc(sh.sheet_name)}</h2></div>
            ${sh.regions
              .map(
                (rg) => `
              <div class="meta-line" style="margin-bottom:8px;">${esc(rg.region_name)} · 有效记录 ${rg.record_count} 条 · 有值字段 ${rg.non_empty_field_count} 个 · 全空字段 ${rg.empty_field_count} 个</div>
              <table class="ref-table">
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
              </table>`
              )
              .join("")}
          </div>`
        )
        .join("");
    } else if (previewData.preview?.kind === "doc") {
      const detail = previewData.preview.detail;
      body.innerHTML = `<table class="ref-table">
        <thead><tr><th class="col-text">章节标题</th><th class="col-num">层级</th><th class="col-text">位置</th><th class="col-num">字符长度</th></tr></thead>
        <tbody>${(detail.outline || [])
          .map(
            (o) => `<tr><td class="col-text"><strong>${esc(o.section)}</strong></td><td class="col-num">H${o.level}</td><td class="col-text">${esc(o.locator)}</td><td class="col-num">${o.char_len}</td></tr>`
          )
          .join("")}</tbody></table>`;
    } else if (previewData.preview?.kind === "folder") {
      body.innerHTML = `<table class="ref-table">
        <thead><tr><th class="col-text">文件名</th><th class="col-text">分类</th><th class="col-text">扩展名</th><th class="col-text">修改时间</th></tr></thead>
        <tbody>${(previewData.preview.files || [])
          .map((f) => `<tr><td class="col-text">${esc(f.name)}</td><td class="col-text">${esc(f.category)}</td><td class="col-text">${esc(f.ext)}</td><td class="col-text">${esc(f.mtime_iso)}</td></tr>`)
          .join("")}</tbody></table>`;
    }
    $("inspect-dialog").showModal();
  }

  function openSpaceModal(space = null) {
    $("space-dialog-title").textContent = space ? "编辑资料空间" : "新建资料空间";
    $("space-form-id").value = space ? space.space_id : "";
    $("space-form-name").value = space ? space.name : "";
    $("space-form-aliases").value = space ? (space.aliases || []).join(", ") : "";
    $("space-form-path").value = "";
    $("space-initial-path-wrap").hidden = Boolean(space);
    $("btn-delete-space").hidden = !space;
    $("space-dialog").showModal();
  }

  function openSourceModal(mode = "add", sourceId = "", initialPath = "") {
    $("source-dialog-title").textContent = mode === "relink" ? "重新关联本机路径" : "添加本机引用";
    $("source-form-mode").value = mode;
    $("source-form-id").value = sourceId;
    $("source-form-path").value = initialPath;
    $("source-form-error").hidden = true;
    $("source-dialog").showModal();
  }

  function render() {
    renderSidebar();
    $("view-spaces").hidden = state.view !== "spaces";
    $("view-connections").hidden = state.view !== "connections";
    if (state.view === "spaces") renderSpacesView();
    else renderConnectionsView();
  }

  // Event bindings
  document.addEventListener("click", async (e) => {
    const closeBtn = e.target.closest("[data-close]");
    if (closeBtn) {
      $(closeBtn.dataset.close)?.close();
      return;
    }

    const pickBtn = e.target.closest("[data-pick]");
    if (pickBtn) {
      const res = await api("/api/system/pick-path", { kind: pickBtn.dataset.pick });
      if (res.picked) $(pickBtn.dataset.target).value = res.picked;
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
      $("detail-drawer").hidden = true;
      render();
      return;
    }

    const actBtn = e.target.closest("[data-action]");
    if (actBtn) {
      e.stopPropagation();
      const sid = actBtn.dataset.sourceId;
      if (actBtn.dataset.action === "relink") {
        openSourceModal("relink", sid, actBtn.dataset.path || "");
      } else if (actBtn.dataset.action === "inspect") {
        const data = await api(`/api/source-preview?source_id=${encodeURIComponent(sid)}`);
        await openFullInspectModal(data);
      }
      return;
    }

    const row = e.target.closest("#sources-tbody tr[data-source-id]");
    if (row) {
      await openSourceDrawer(row.dataset.sourceId);
    }
  });

  $("btn-new-space").addEventListener("click", () => openSpaceModal(null));
  $("btn-edit-space").addEventListener("click", () => {
    const sp = state.spaces.find((s) => s.space_id === state.activeSpaceId);
    if (sp) openSpaceModal(sp);
  });
  $("btn-add-source").addEventListener("click", () => openSourceModal("add"));
  $("btn-close-drawer").addEventListener("click", () => {
    state.selectedSourceId = null;
    $("detail-drawer").hidden = true;
    renderSpacesView();
  });

  $("btn-refresh-sources").addEventListener("click", async () => {
    if (!state.activeSpaceId) return;
    await api("/api/sources/refresh", { space_id: state.activeSpaceId });
    await refreshAll();
  });
  $("btn-refresh-health").addEventListener("click", refreshAll);

  $("search-input").addEventListener("input", (e) => {
    state.search = e.target.value;
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
    const aliases = $("space-form-aliases").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const initPath = $("space-form-path").value.trim();
    if (sid) {
      await api("/api/spaces/update", { space_id: sid, name, aliases });
    } else {
      const res = await api("/api/spaces/create", { name, aliases, source_paths: initPath ? [initPath] : [] });
      state.activeSpaceId = res.space.space_id;
    }
    $("space-dialog").close();
    await refreshAll();
  });

  $("btn-delete-space").addEventListener("click", async () => {
    const sid = $("space-form-id").value;
    if (!sid) return;
    await api("/api/spaces/delete", { space_id: sid });
    if (state.activeSpaceId === sid) state.activeSpaceId = null;
    $("space-dialog").close();
    await refreshAll();
  });

  $("source-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = $("source-form-mode").value;
    const srcId = $("source-form-id").value;
    const path = $("source-form-path").value.trim();
    try {
      if (mode === "relink") {
        await api("/api/sources/relink", { source_id: srcId, new_path: path });
      } else {
        await api("/api/sources/add", { space_id: state.activeSpaceId, path });
      }
      $("source-dialog").close();
      await refreshAll();
    } catch (err) {
      const errEl = $("source-form-error");
      errEl.hidden = false;
      errEl.textContent = err.message;
    }
  });

  refreshAll();
})();
