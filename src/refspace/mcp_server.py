from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from .engines.router import RefSpaceRouter
from .registry import RefSpaceRegistry, RegistryError
from .sandbox import SandboxViolationError
from .session import SessionScopeManager

MCP_TOOLS_SCHEMA: List[dict] = [
    {
        "name": "refspace_bind_scope",
        "description": (
            "管理当前会话的资料空间引用范围，或首次登记新资料空间。"
            "支持动作：bind/replace（绑定或换成新默认空间）、add（再加上某空间）、"
            "remove（取消某空间）、once（这次只用某空间，回合结束自动恢复原默认）、"
            "clear（取消全部引用）、register_space（首次创建资料空间并关联本机文件夹/文件）。"
            "注意：只有用户明确表达引用、切换、增减意图时才调用，闲聊提及名字不调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["bind", "replace", "add", "remove", "once", "clear", "register_space"],
                    "description": "范围操作类型",
                },
                "spaces": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "目标资料空间名称、简称或编号列表（clear / register_space 时可省略）",
                },
                "register_name": {"type": "string", "description": "action=register_space 时的空间名称"},
                "register_aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "action=register_space 时的可选简称列表",
                },
                "register_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "action=register_space 时要关联的本机文件或文件夹路径列表",
                },
                "bind_after_register": {
                    "type": "boolean",
                    "description": "action=register_space 后是否立即绑定为当前会话默认空间（默认 true）",
                },
                "session_id": {"type": "string", "description": "当前会话标识（Antigravity 下由宿主 Hook 自动注入）"},
                "workbench": {"type": "string", "description": "AI 工作台标识，默认 antigravity"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "refspace_scope_status",
        "description": "查询当前会话绑定的资料空间范围、已登记的全部资料空间清单及来源可读性状态。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_all_spaces": {"type": "boolean", "description": "是否同时返回本机已登记的全部资料空间（默认 true）"},
                "session_id": {"type": "string", "description": "当前会话标识"},
                "workbench": {"type": "string", "description": "AI 工作台标识，默认 antigravity"},
            },
        },
    },
    {
        "name": "refspace_locate",
        "description": "在当前会话已引用的资料空间内按需定位相关文件、Excel 工作表/表头摘要及文档章节目录，不一次性读取全量正文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "可选检索词（文件名、工作表名、字段名或章节关键词）"},
                "category": {
                    "type": "string",
                    "enum": ["all", "table", "doc"],
                    "description": "按资料类型过滤：all / table（Excel/CSV） / doc（MD/DOCX/PDF/TXT/JSON）",
                },
                "limit": {"type": "integer", "description": "最多返回文件数，默认 30"},
                "session_id": {"type": "string", "description": "当前会话标识"},
                "workbench": {"type": "string", "description": "AI 工作台标识，默认 antigravity"},
            },
        },
    },
    {
        "name": "refspace_read_doc",
        "description": "按需读取当前引用空间中某个文档（.md / .txt / .docx / .pdf / .json）的目录大纲或匹配章节段落，并返回来源依据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "目标文件名或路径（如 '补货判断规则.md'）"},
                "query": {"type": "string", "description": "按关键词筛选相关段落"},
                "section_filter": {"type": "string", "description": "按章节标题筛选"},
                "outline_only": {"type": "boolean", "description": "仅返回章节目录大纲，不返回正文片段"},
                "max_passages": {"type": "integer", "description": "最多返回段落数，默认 8"},
                "session_id": {"type": "string", "description": "当前会话标识"},
                "workbench": {"type": "string", "description": "AI 工作台标识，默认 antigravity"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "refspace_query_table",
        "description": (
            "对当前引用空间中的表格（.xlsx / .xlsm / .csv / .tsv）执行结构探测（inspect）、"
            "明细筛选排序（rows）或全范围确定性统计汇总（aggregate：支持 group_by、dedup_by 去重及 count/count_distinct/sum/avg/min/max）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "目标表格文件名或路径"},
                "mode": {
                    "type": "string",
                    "enum": ["inspect", "rows", "aggregate"],
                    "description": "inspect 查看工作表/区域/字段；rows 筛选查看记录；aggregate 执行精确分组聚合计算",
                },
                "sheet_name": {"type": "string", "description": "目标工作表名称（mode=rows/aggregate 时推荐指定）"},
                "region_index": {"type": "integer", "description": "同一工作表内独立数据区域序号，默认 1"},
                "filters": {
                    "type": "object",
                    "description": "逐列筛选条件（多列 AND），例如 {'状态': '发货确认', '数量': {'op': 'gt', 'value': 0}}",
                },
                "keyword": {"type": "string", "description": "跨列关键词筛选"},
                "dedup_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "聚合前按组合键去重（例如拆箱订单按 ['ERP单号', '行号', 'SKU'] 去重后再求和）",
                },
                "group_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "分组字段列表（mode=aggregate）",
                },
                "metrics": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "统计指标列表，如 [{'op': 'sum', 'field': '金额', 'as': '总金额'}, {'op': 'count_distinct', 'field': 'ERP单号', 'as': '订单数'}]",
                },
                "sort_by": {"type": "string", "description": "排序字段"},
                "sort_desc": {"type": "boolean", "description": "是否降序排列"},
                "limit": {"type": "integer", "description": "返回记录或分组上限，默认 30"},
                "session_id": {"type": "string", "description": "当前会话标识"},
                "workbench": {"type": "string", "description": "AI 工作台标识，默认 antigravity"},
            },
            "required": ["file"],
        },
    },
]


class RefSpaceMCPDispatcher:
    def __init__(self, db_path: Optional[str] = None, default_session_fallback: Optional[str] = None):
        self.registry = RefSpaceRegistry(db_path=db_path)
        self.session_mgr = SessionScopeManager(self.registry)
        self.router = RefSpaceRouter(self.registry, self.session_mgr)
        self.default_session_fallback = default_session_fallback or f"stdio-{os.getppid()}"

    def _resolve_session(self, args: Dict[str, Any]) -> tuple[str, str]:
        wb = str(args.get("workbench") or os.environ.get("REFSPACE_WORKBENCH") or "antigravity").strip()
        sid = str(args.get("session_id") or os.environ.get("REFSPACE_SESSION_ID") or self.default_session_fallback).strip()
        return wb, sid

    def call_tool(self, name: str, args: Optional[Dict[str, Any]]) -> dict:
        arguments = args or {}
        wb, sid = self._resolve_session(arguments)

        try:
            if name == "refspace_bind_scope":
                act = str(arguments.get("action", "")).strip()
                if act == "register_space":
                    r_name = str(arguments.get("register_name") or "").strip()
                    r_aliases = list(arguments.get("register_aliases") or [])
                    r_paths = list(arguments.get("register_paths") or [])
                    if not r_paths:
                        raise RegistryError("MISSING_PATHS", "创建资料空间时请提供至少一个本机文件夹或文件路径（register_paths）")
                    created = self.registry.create_space(name=r_name, aliases=r_aliases, source_paths=r_paths)
                    if arguments.get("bind_after_register", True):
                        st = self.session_mgr.apply_action(wb, sid, "bind", [created["space_id"]])
                        return {"ok": True, "created_space": created, "session": st}
                    return {"ok": True, "created_space": created, "session": self.session_mgr.get_session_state(wb, sid)}

                st = self.session_mgr.apply_action(wb, sid, act, list(arguments.get("spaces") or []))
                return {"ok": True, "session": st}

            if name == "refspace_scope_status":
                st = self.session_mgr.get_session_state(wb, sid)
                out: Dict[str, Any] = {"ok": True, "session": st}
                if arguments.get("include_all_spaces", True):
                    self.registry.refresh_sources()
                    out["registered_spaces"] = [
                        {
                            "space_id": s["space_id"],
                            "name": s["name"],
                            "aliases": s["aliases"],
                            "source_count": s["source_count"],
                            "readable_count": sum(1 for src in s["sources"] if src["status"] in ("readable", "updated")),
                            "needs_relink_count": sum(1 for src in s["sources"] if src["status"] == "needs_relink"),
                        }
                        for s in self.registry.list_spaces()
                    ]
                return out

            if name == "refspace_locate":
                res = self.router.locate_in_scope(
                    wb,
                    sid,
                    query=arguments.get("query"),
                    category=str(arguments.get("category") or "all"),
                    limit=int(arguments.get("limit") or 30),
                )
                return {"ok": True, **res}

            if name == "refspace_read_doc":
                res = self.router.read_doc_in_scope(
                    wb,
                    sid,
                    file=str(arguments.get("file") or ""),
                    query=arguments.get("query"),
                    section_filter=arguments.get("section_filter"),
                    outline_only=bool(arguments.get("outline_only", False)),
                    max_passages=int(arguments.get("max_passages") or 8),
                )
                return {"ok": True, **res}

            if name == "refspace_query_table":
                res = self.router.query_table_in_scope(
                    wb,
                    sid,
                    file=str(arguments.get("file") or ""),
                    mode=str(arguments.get("mode") or "inspect"),
                    sheet_name=arguments.get("sheet_name"),
                    region_index=int(arguments.get("region_index") or 1),
                    filters=arguments.get("filters"),
                    keyword=arguments.get("keyword"),
                    group_by=arguments.get("group_by"),
                    metrics=arguments.get("metrics"),
                    dedup_by=arguments.get("dedup_by"),
                    sort_by=arguments.get("sort_by"),
                    sort_desc=bool(arguments.get("sort_desc", False)),
                    limit=int(arguments.get("limit") or 30),
                )
                return {"ok": True, **res}

            return {"ok": False, "error_code": "UNKNOWN_TOOL", "message": f"未知工具：{name}"}
        except RegistryError as exc:
            cur_state = None
            try:
                cur_state = self.session_mgr.get_session_state(wb, sid)
            except Exception:
                pass
            return {
                "ok": False,
                "error_code": exc.code,
                "message": exc.message,
                "candidates": exc.candidates,
                "preserved_session": cur_state,
            }
        except SandboxViolationError as exc:
            return {"ok": False, "error_code": "SANDBOX_BLOCKED", "message": str(exc)}
        except Exception as exc:
            return {"ok": False, "error_code": "EXECUTION_ERROR", "message": str(exc)}

    def handle_jsonrpc(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no id and require no response
        if msg_id is None:
            return None

        if method == "initialize":
            req_proto = params.get("protocolVersion") or "2024-11-05"
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": req_proto,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "refspace", "version": __version__},
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": MCP_TOOLS_SCHEMA}}

        if method == "tools/call":
            t_name = str(params.get("name") or "")
            t_args = params.get("arguments") or {}
            payload = self.call_tool(t_name, t_args)
            is_err = not bool(payload.get("ok", True))
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                    "isError": is_err,
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def run_stdio_server(db_path: Optional[str] = None) -> None:
    """Run MCP server over stdin/stdout (supports both newline JSON-RPC and Content-Length framing)."""
    dispatcher = RefSpaceMCPDispatcher(db_path=db_path)
    stdin_buf = sys.stdin.buffer
    stdout_buf = sys.stdout.buffer

    while True:
        first_line = stdin_buf.readline()
        if not first_line:
            break

        stripped = first_line.strip()
        if not stripped:
            continue

        use_content_length = False
        if stripped.lower().startswith(b"content-length:"):
            use_content_length = True
            length = int(stripped.split(b":", 1)[1].strip())
            # Consume remaining headers up to empty line
            while True:
                hline = stdin_buf.readline()
                if not hline or hline in (b"\r\n", b"\n"):
                    break
            body = stdin_buf.read(length)
            if not body:
                break
            raw_json = body.decode("utf-8")
        else:
            raw_json = stripped.decode("utf-8")

        try:
            req = json.loads(raw_json)
        except json.JSONDecodeError:
            continue

        resp = dispatcher.handle_jsonrpc(req)
        if resp is not None:
            encoded = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            if use_content_length:
                header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
                stdout_buf.write(header + encoded)
            else:
                stdout_buf.write(encoded + b"\n")
            stdout_buf.flush()
