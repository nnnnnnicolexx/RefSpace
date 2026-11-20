from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__
from .engines.router import RefSpaceRouter
from .mcp_server import RefSpaceMCPDispatcher
from .registry import RefSpaceRegistry, RegistryError, now_iso
from .sandbox import classify_extension, is_ignored_entry
from .session import SessionScopeManager

UI_DIR = Path(__file__).resolve().parent / "ui"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _compute_stage(config_found: bool, ev: dict, has_sessions: bool) -> tuple[str, str, dict]:
    connection_verified = config_found and bool(ev["last_handshake_at"] or ev["last_tool_call_at"] or has_sessions)
    read_succeeded = connection_verified and bool(ev["last_read_at"])
    if read_succeeded:
        stage, label = "read_succeeded", "读取成功"
    elif connection_verified:
        stage, label = "connection_verified", "连接已验证"
    elif config_found:
        stage, label = "config_found", "配置已发现"
    else:
        stage, label = "unconfigured", "未配置"
    return stage, label, {
        "config_found": config_found,
        "connection_verified": connection_verified,
        "read_succeeded": read_succeeded,
    }


def check_workbuddy_integration(registry: RefSpaceRegistry, session_mgr: SessionScopeManager) -> dict:
    wb_cfg = Path.home() / ".workbuddy" / "mcp.json"
    config_found = False
    if wb_cfg.is_file():
        try:
            data = json.loads(wb_cfg.read_text(encoding="utf-8"))
            servers = data.get("mcpServers") or {}
            config_found = "refspace" in servers and not bool((servers.get("refspace") or {}).get("disabled", False))
        except Exception:
            config_found = False

    ev = registry.get_workbench_events("workbuddy")
    wb_sessions = [s for s in session_mgr.list_active_sessions() if s["workbench"] == "workbuddy"]
    stage, stage_label, steps = _compute_stage(config_found, ev, bool(wb_sessions))
    return {
        "workbench": "WorkBuddy",
        "config_path": str(wb_cfg),
        "connected": config_found,
        "stage": stage,
        "stage_label": stage_label,
        "steps": steps,
        "last_handshake_at": ev["last_handshake_at"],
        "last_read_at": ev["last_read_at"],
        "last_read_summary": ev["last_read_summary"],
    }


def check_antigravity_integration(registry: RefSpaceRegistry, session_mgr: SessionScopeManager) -> dict:
    workspace_plugin = REPO_ROOT / "plugins" / "refspace" / "plugin.json"
    workspace_manifest = REPO_ROOT / ".agents" / "plugins.json"
    workspace_mcp = REPO_ROOT / "plugins" / "refspace" / "mcp_config.json"
    global_plugin = Path.home() / ".gemini" / "config" / "plugins" / "refspace" / "plugin.json"

    ws_ready = workspace_plugin.is_file() and workspace_manifest.is_file() and workspace_mcp.is_file()
    global_ready = global_plugin.is_file()
    config_found = ws_ready or global_ready

    ev = registry.get_workbench_events("antigravity")
    active_sessions = session_mgr.list_active_sessions()
    agy_sessions = [s for s in active_sessions if s["workbench"] == "antigravity"]
    stage, stage_label, steps = _compute_stage(config_found, ev, bool(agy_sessions))

    return {
        "workbench": "Antigravity",
        "connected": config_found,
        "stage": stage,
        "stage_label": stage_label,
        "steps": steps,
        "last_handshake_at": ev["last_handshake_at"],
        "last_tool_call_at": ev["last_tool_call_at"],
        "last_read_at": ev["last_read_at"],
        "last_read_summary": ev["last_read_summary"],
        "scope": "全局与当前工作区均可用" if (ws_ready and global_ready) else ("当前工作区配置已发现" if ws_ready else ("全局配置已发现" if global_ready else "未检测到插件配置")),
        "workspace_plugin_ready": ws_ready,
        "global_plugin_ready": global_ready,
        "hooks_isolation_enabled": (REPO_ROOT / "plugins" / "refspace" / "hooks.json").is_file() or (global_plugin.parent / "hooks.json").is_file(),
        "active_session_count": len(active_sessions),
        "active_sessions": active_sessions,
        "workbuddy": check_workbuddy_integration(registry, session_mgr),
        "runtime_paths": {
            "python_bin": sys.executable,
            "mcp_entry_path": str(REPO_ROOT / "plugins" / "refspace" / "run_mcp.py"),
            "repo_root": str(REPO_ROOT),
        },
        "data_boundary_note": "原文件始终留在本机原位置只读访问；AI 回答问题时，按需提取的片段或统计结果会交给所连接的模型服务处理。",
        "candidate_platforms": [
            {"name": "Claude Desktop / Cursor / Codex", "status": "支持复制「通用一键接入提示词」发给对应客户端自动写入本地 MCP 配置"},
            {"name": "豆包 / 千问等纯网页端", "status": "尚未适配（纯浏览器网页无法直接拉起本机脚本，需后续扩展桥接）"},
        ],
    }


def _verify_local_mcp_subprocess(db_path: str) -> dict:
    plugin_dir = REPO_ROOT / "plugins" / "refspace"
    entry = plugin_dir / "run_mcp.py"
    if not entry.is_file():
        raise RegistryError("MCP_ENTRY_MISSING", f"未找到 MCP 启动入口：{entry}")
    env = dict(os.environ)
    env["REFSPACE_DB_PATH"] = db_path
    env["PLUGIN_ROOT"] = str(plugin_dir)
    reqs = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "antigravity-selfcheck"}}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        + "\n"
    )
    proc = subprocess.run([sys.executable, str(entry)], input=reqs, capture_output=True, text=True, env=env, timeout=8)
    if proc.returncode != 0:
        raise RegistryError("MCP_HANDSHAKE_FAILED", f"MCP 进程异常退出：{proc.stderr.strip()}")
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RegistryError("MCP_HANDSHAKE_FAILED", "MCP 进程未返回完整握手响应")
    tools_payload = json.loads(lines[1])
    tools = [t.get("name") for t in (tools_payload.get("result", {}).get("tools") or [])]
    return {"ok": True, "tool_count": len(tools), "tools": tools, "verified_at": now_iso()}


def build_health_report(registry: RefSpaceRegistry, session_mgr: SessionScopeManager) -> dict:
    sources = registry.refresh_sources()
    db_ok = os.path.exists(registry.db_path) and os.access(registry.db_path, os.R_OK | os.W_OK)
    readable_cnt = sum(1 for s in sources if s["status"] in ("readable", "updated"))
    updated_cnt = sum(1 for s in sources if s["status"] == "updated")
    broken_cnt = sum(1 for s in sources if s["status"] == "needs_relink")

    return {
        "ok": True,
        "checked_at": now_iso(),
        "version": __version__,
        "service": {
            "running": db_ok,
            "db_path": registry.db_path,
            "mode": "read_only_sandbox",
        },
        "sources_health": {
            "total": len(sources),
            "readable": readable_cnt,
            "updated": updated_cnt,
            "needs_relink": broken_cnt,
        },
        "antigravity": check_antigravity_integration(registry, session_mgr),
    }


def _macos_pick_path(pick_kind: str) -> dict:
    if sys.platform != "darwin":
        return {"ok": False, "error_code": "PICKER_UNAVAILABLE", "message": "当前系统不支持访达弹窗，请使用下方目录浏览或直接输入路径"}

    prompt_txt = (
        "选择要关联到引知 RefSpace 的文件夹（原件保持不动，只读引用）："
        if pick_kind == "folder"
        else "选择要关联到引知 RefSpace 的文件（原件保持不动，只读引用）："
    )
    choose_cmd = f'choose folder with prompt "{prompt_txt}"' if pick_kind == "folder" else f'choose file with prompt "{prompt_txt}"'
    script = f'tell application "SystemUIServer"\nactivate\nPOSIX path of ({choose_cmd})\nend tell'
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if proc.returncode == 0:
        val = proc.stdout.strip()
        return {"ok": True, "picked": val or None, "canceled": not bool(val)}

    stderr = (proc.stderr or "").strip()
    if "-128" in stderr or "User canceled" in stderr or "用户已取消" in stderr:
        return {"ok": True, "picked": None, "canceled": True}

    return {
        "ok": False,
        "error_code": "PICKER_UNAVAILABLE",
        "message": "当前服务运行在受限沙箱中，无法拉起系统访达弹窗；已为你展开页面内目录选择器。",
    }


def _reveal_in_finder(path: str) -> dict:
    expanded = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(expanded):
        return {"ok": False, "message": f"原路径不存在，请先重新关联：{expanded}"}
    if sys.platform == "darwin":
        proc = subprocess.run(["open", "-R", expanded], capture_output=True, text=True)
        if proc.returncode == 0:
            return {"ok": True, "path": expanded, "message": f"已在访达中定位：{Path(expanded).name}"}
        return {
            "ok": False,
            "error_code": "REVEAL_BLOCKED",
            "path": expanded,
            "message": f"当前受限环境无法自动拉起访达窗口，原文件本机路径为：{expanded}",
        }
    return {"ok": False, "path": expanded, "message": f"原文件本机路径：{expanded}"}


def _browse_local_directory(raw_dir: Optional[str]) -> dict:
    start = os.path.abspath(os.path.expanduser((raw_dir or "").strip() or str(Path.home())))
    if os.path.isfile(start):
        start = os.path.dirname(start)
    if not os.path.isdir(start):
        start = str(Path.home())
    if not os.access(start, os.R_OK):
        raise RegistryError("DIR_UNREADABLE", f"无权限读取目录：{start}")

    parent = str(Path(start).parent) if Path(start).parent != Path(start) else None
    entries: List[dict] = []
    try:
        names = sorted(os.listdir(start))
    except OSError as exc:
        raise RegistryError("DIR_UNREADABLE", f"无法列出目录（{exc}）：{start}")

    for name in names:
        if is_ignored_entry(name):
            continue
        full = os.path.join(start, name)
        if os.path.isdir(full):
            entries.append({"name": name + "/", "path": full, "kind": "folder", "selectable": True})
        elif os.path.isfile(full):
            cat = classify_extension(full)
            if cat is not None:
                entries.append({"name": name, "path": full, "kind": "file", "category": cat, "selectable": True})

    entries.sort(key=lambda x: (0 if x["kind"] == "folder" else 1, x["name"].lower()))
    shortcuts = [
        {"label": "当前项目目录", "path": str(REPO_ROOT)},
        {"label": "演示样例目录", "path": str(REPO_ROOT / ".refspace" / "demo-files")},
        {"label": "个人主目录 (~)", "path": str(Path.home())},
        {"label": "下载 (Downloads)", "path": str(Path.home() / "Downloads")},
        {"label": "文稿 (Documents)", "path": str(Path.home() / "Documents")},
    ]
    shortcuts = [s for s in shortcuts if os.path.isdir(s["path"]) and os.access(s["path"], os.R_OK)]
    return {"ok": True, "current_dir": start, "parent_dir": parent, "shortcuts": shortcuts, "entries": entries[:200]}


def create_http_server(host: str = "127.0.0.1", port: int = 17890, db_path: Optional[str] = None) -> ThreadingHTTPServer:
    registry = RefSpaceRegistry(db_path=db_path)
    session_mgr = SessionScopeManager(registry)
    router = RefSpaceRouter(registry, session_mgr)
    mcp_dispatcher = RefSpaceMCPDispatcher(db_path=registry.db_path, default_session_fallback="http-default")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send_json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw) if raw.strip() else {}

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path
            qs = parse_qs(parsed.query)

            try:
                if route == "/api/health":
                    self._send_json(build_health_report(registry, session_mgr))
                    return
                if route == "/api/state":
                    health = build_health_report(registry, session_mgr)
                    self._send_json(
                        {
                            "ok": True,
                            "health": health,
                            "spaces": registry.list_spaces(),
                            "sessions": session_mgr.list_active_sessions(),
                            "provenance": registry.list_recent_provenance(limit=25),
                        }
                    )
                    return
                if route == "/api/source-preview":
                    src_id = (qs.get("source_id") or [""])[0]
                    self._send_json({"ok": True, **router.inspect_single_source_preview(src_id)})
                    return
                if route == "/api/system/browse-dir":
                    raw_d = (qs.get("dir") or [""])[0]
                    self._send_json(_browse_local_directory(raw_d))
                    return

                rel = "index.html" if route in ("/", "") else route.lstrip("/")
                target_file = (UI_DIR / rel).resolve()
                if not str(target_file).startswith(str(UI_DIR.resolve())) or not target_file.is_file():
                    self._send_json({"ok": False, "message": "Not Found"}, status=404)
                    return
                ctype, _ = mimetypes.guess_type(str(target_file))
                raw = target_file.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    (ctype or "application/octet-stream") + ("; charset=utf-8" if str(target_file).endswith((".html", ".css", ".js")) else ""),
                )
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            except RegistryError as exc:
                self._send_json({"ok": False, "error_code": exc.code, "message": exc.message}, status=400)
            except Exception as exc:
                self._send_json({"ok": False, "error_code": "SERVER_ERROR", "message": str(exc)}, status=500)

        def do_POST(self) -> None:
            route = urlparse(self.path).path
            try:
                body = self._read_json()

                if route == "/mcp":
                    hdr_sid = self.headers.get("Mcp-Session-Id") or self.headers.get("X-RefSpace-Session-Id")
                    if hdr_sid and isinstance(body.get("params"), dict) and isinstance(body["params"].get("arguments"), dict):
                        body["params"]["arguments"].setdefault("session_id", hdr_sid)
                    resp = mcp_dispatcher.handle_jsonrpc(body)
                    if resp is None:
                        self.send_response(202)
                        self.end_headers()
                    else:
                        self._send_json(resp, status=200)
                    return

                if route == "/api/spaces/create":
                    sp = registry.create_space(
                        name=str(body.get("name") or ""),
                        aliases=list(body.get("aliases") or []),
                        source_paths=list(body.get("source_paths") or []),
                        description=str(body.get("description") or ""),
                    )
                    self._send_json({"ok": True, "space": sp})
                    return

                if route == "/api/spaces/update":
                    sp = registry.update_space(
                        space_id=str(body.get("space_id") or ""),
                        name=body.get("name"),
                        aliases=body.get("aliases"),
                        description=body.get("description"),
                    )
                    self._send_json({"ok": True, "space": sp})
                    return

                if route == "/api/spaces/delete":
                    registry.delete_space(str(body.get("space_id") or ""))
                    self._send_json({"ok": True})
                    return

                if route == "/api/sources/add":
                    src = registry.add_source_to_space(str(body.get("space_id") or ""), str(body.get("path") or ""))
                    self._send_json({"ok": True, "source": src})
                    return

                if route == "/api/sources/relink":
                    src = registry.relink_source(str(body.get("source_id") or ""), str(body.get("new_path") or ""))
                    self._send_json({"ok": True, "source": src})
                    return

                if route == "/api/sources/remove":
                    registry.remove_source_from_space(str(body.get("space_id") or ""), str(body.get("source_id") or ""))
                    self._send_json({"ok": True})
                    return

                if route == "/api/sources/refresh":
                    sid = body.get("space_id") or None
                    ack = bool(body.get("acknowledge_updates", False))
                    sources = registry.refresh_sources(space_id=sid, acknowledge_updates=ack)
                    self._send_json({"ok": True, "sources": sources, "checked_at": now_iso()})
                    return

                if route == "/api/system/pick-path":
                    res = _macos_pick_path(str(body.get("kind") or "folder"))
                    self._send_json(res, status=200)
                    return

                if route == "/api/system/reveal":
                    res = _reveal_in_finder(str(body.get("path") or ""))
                    self._send_json(res, status=200)
                    return

                if route == "/api/antigravity/verify-mcp":
                    res = _verify_local_mcp_subprocess(registry.db_path)
                    self._send_json(res, status=200)
                    return

                self._send_json({"ok": False, "message": f"Unknown endpoint: {route}"}, status=404)
            except RegistryError as exc:
                self._send_json({"ok": False, "error_code": exc.code, "message": exc.message, "candidates": exc.candidates}, status=400)
            except Exception as exc:
                self._send_json({"ok": False, "error_code": "SERVER_ERROR", "message": str(exc)}, status=500)

    return ThreadingHTTPServer((host, port), Handler)
