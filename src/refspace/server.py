from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__
from .engines.router import RefSpaceRouter
from .mcp_server import RefSpaceMCPDispatcher
from .registry import RefSpaceRegistry, RegistryError
from .session import SessionScopeManager

UI_DIR = Path(__file__).resolve().parent / "ui"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def check_antigravity_integration(registry: RefSpaceRegistry, session_mgr: SessionScopeManager) -> dict:
    workspace_plugin = REPO_ROOT / "plugins" / "refspace" / "plugin.json"
    workspace_manifest = REPO_ROOT / ".agents" / "plugins.json"
    global_plugin = Path.home() / ".gemini" / "config" / "plugins" / "refspace" / "plugin.json"

    ws_ready = workspace_plugin.is_file() and workspace_manifest.is_file()
    global_ready = global_plugin.is_file()
    active_sessions = session_mgr.list_active_sessions()

    return {
        "workbench": "Antigravity",
        "connected": ws_ready or global_ready,
        "scope": "全局与当前工作区均可用" if (ws_ready and global_ready) else ("当前工作区插件就绪" if ws_ready else ("全局插件就绪" if global_ready else "未检测到插件配置")),
        "workspace_plugin_ready": ws_ready,
        "global_plugin_ready": global_ready,
        "hooks_isolation_enabled": (REPO_ROOT / "plugins" / "refspace" / "hooks.json").is_file() or (global_plugin.parent / "hooks.json").is_file(),
        "active_session_count": len(active_sessions),
        "active_sessions": active_sessions,
        "candidate_platforms": [
            {"name": "Claude Desktop / Cursor / Codex", "status": "可通过本地 Stdio / HTTP MCP 接入（待逐端实测会话隔离）"},
            {"name": "豆包 / 千问等网页工作台", "status": "尚未适配（网页端不支持直连本机 MCP，需后续浏览器桥接扩展）"},
        ],
    }


def build_health_report(registry: RefSpaceRegistry, session_mgr: SessionScopeManager) -> dict:
    sources = registry.refresh_sources()
    db_ok = os.path.exists(registry.db_path) and os.access(registry.db_path, os.R_OK | os.W_OK)
    readable_cnt = sum(1 for s in sources if s["status"] in ("readable", "updated"))
    updated_cnt = sum(1 for s in sources if s["status"] == "updated")
    broken_cnt = sum(1 for s in sources if s["status"] == "needs_relink")

    return {
        "ok": True,
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


def _macos_pick_path(pick_kind: str) -> Optional[str]:
    if sys.platform != "darwin":
        return None
    if pick_kind == "folder":
        script = 'POSIX path of (choose folder with prompt "选择要关联到引知 RefSpace 的文件夹（原件保持不动，只读引用）：")'
    else:
        script = 'POSIX path of (choose file with prompt "选择要关联到引知 RefSpace 的文件（原件保持不动，只读引用）：")'
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    val = proc.stdout.strip()
    return val or None


def _reveal_in_finder(path: str) -> bool:
    expanded = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(expanded):
        return False
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", expanded], check=False)
        return True
    return False


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

                # Serve static UI files
                rel = "index.html" if route in ("/", "") else route.lstrip("/")
                target_file = (UI_DIR / rel).resolve()
                if not str(target_file).startswith(str(UI_DIR.resolve())) or not target_file.is_file():
                    self._send_json({"ok": False, "message": "Not Found"}, status=404)
                    return
                ctype, _ = mimetypes.guess_type(str(target_file))
                raw = target_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", (ctype or "application/octet-stream") + ("; charset=utf-8" if str(target_file).endswith((".html", ".css", ".js")) else ""))
                self.send_header("Content-Length", str(len(raw)))
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

                # Streamable HTTP MCP endpoint (/mcp)
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
                    self._send_json({"ok": True, "sources": sources})
                    return

                if route == "/api/system/pick-path":
                    picked = _macos_pick_path(str(body.get("kind") or "folder"))
                    self._send_json({"ok": True, "picked": picked})
                    return

                if route == "/api/system/reveal":
                    ok = _reveal_in_finder(str(body.get("path") or ""))
                    self._send_json({"ok": ok})
                    return

                self._send_json({"ok": False, "message": f"Unknown endpoint: {route}"}, status=404)
            except RegistryError as exc:
                self._send_json({"ok": False, "error_code": exc.code, "message": exc.message, "candidates": exc.candidates}, status=400)
            except Exception as exc:
                self._send_json({"ok": False, "error_code": "SERVER_ERROR", "message": str(exc)}, status=500)

    return ThreadingHTTPServer((host, port), Handler)
