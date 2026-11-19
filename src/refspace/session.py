from __future__ import annotations

import json
from typing import List, Optional

from .registry import RefSpaceRegistry, RegistryError, now_iso


class SessionScopeManager:
    """Manages per-(workbench, session_id) persistent default scope and one-shot overrides."""

    def __init__(self, registry: RefSpaceRegistry):
        self.registry = registry

    def _load_raw(self, workbench: str, session_id: str) -> dict:
        wb = (workbench or "antigravity").strip().lower()
        sid = (session_id or "").strip()
        if not sid:
            raise RegistryError("MISSING_SESSION_ID", "缺少会话标识（session_id），无法隔离会话引用范围")

        with self.registry._connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_bindings WHERE workbench = ? AND session_id = ?",
                (wb, sid),
            ).fetchone()
            if not row:
                return {
                    "workbench": wb,
                    "session_id": sid,
                    "default_space_ids": [],
                    "temp_space_ids": [],
                    "temp_active": False,
                    "updated_at": "",
                }
            return {
                "workbench": wb,
                "session_id": sid,
                "default_space_ids": json.loads(row["default_space_ids_json"]),
                "temp_space_ids": json.loads(row["temp_space_ids_json"]),
                "temp_active": bool(row["temp_active"]),
                "updated_at": row["updated_at"],
            }

    def _save_raw(
        self,
        workbench: str,
        session_id: str,
        default_space_ids: List[str],
        temp_space_ids: List[str],
        temp_active: bool,
    ) -> None:
        wb = (workbench or "antigravity").strip().lower()
        sid = session_id.strip()
        ts = now_iso()
        with self.registry._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_bindings(workbench, session_id, default_space_ids_json, temp_space_ids_json, temp_active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(workbench, session_id) DO UPDATE SET
                    default_space_ids_json = excluded.default_space_ids_json,
                    temp_space_ids_json = excluded.temp_space_ids_json,
                    temp_active = excluded.temp_active,
                    updated_at = excluded.updated_at
                """,
                (
                    wb,
                    sid,
                    json.dumps(default_space_ids, ensure_ascii=False),
                    json.dumps(temp_space_ids, ensure_ascii=False),
                    1 if temp_active else 0,
                    ts,
                ),
            )

    def _resolve_and_verify_spaces(self, queries: List[str], require_readable: bool = True) -> List[dict]:
        if not queries:
            raise RegistryError("EMPTY_SPACE_LIST", "请指定至少一个资料空间名称")
        resolved_spaces: List[dict] = []
        seen_ids = set()
        for q in queries:
            sp = self.registry.resolve_space_query(q)
            # Refresh disk status before switching so broken spaces don't silently replace valid scope
            self.registry.refresh_sources(sp["space_id"])
            sp = self.registry.get_space(sp["space_id"])
            if require_readable:
                if not sp["sources"]:
                    raise RegistryError("SPACE_EMPTY", f"资料空间「{sp['name']}」尚未关联任何本机文件或文件夹；当前引用范围保持不变。")
                readable_sources = [s for s in sp["sources"] if s["status"] in ("readable", "updated")]
                if not readable_sources:
                    raise RegistryError(
                        "SOURCE_UNREADABLE",
                        f"资料空间「{sp['name']}」关联的本机来源均不可读取（可能已移动或无权限），请先重新关联；当前引用范围保持不变。",
                    )
            if sp["space_id"] not in seen_ids:
                seen_ids.add(sp["space_id"])
                resolved_spaces.append(sp)
        return resolved_spaces

    def get_session_state(self, workbench: str, session_id: str) -> dict:
        raw = self._load_raw(workbench, session_id)
        space_map = {sp["space_id"]: sp for sp in self.registry.list_spaces()}
        default_spaces = [space_map[i] for i in raw["default_space_ids"] if i in space_map]
        temp_spaces = [space_map[i] for i in raw["temp_space_ids"] if i in space_map]
        effective_spaces = temp_spaces if (raw["temp_active"] and temp_spaces) else default_spaces

        return {
            "workbench": raw["workbench"],
            "session_id": raw["session_id"],
            "mode": "once_override" if (raw["temp_active"] and temp_spaces) else ("persistent_default" if default_spaces else "unbound"),
            "temp_active": bool(raw["temp_active"] and temp_spaces),
            "effective_spaces": [
                {"space_id": s["space_id"], "name": s["name"], "aliases": s["aliases"], "source_count": s["source_count"]}
                for s in effective_spaces
            ],
            "default_spaces": [
                {"space_id": s["space_id"], "name": s["name"], "aliases": s["aliases"], "source_count": s["source_count"]}
                for s in default_spaces
            ],
            "temp_spaces": [
                {"space_id": s["space_id"], "name": s["name"], "aliases": s["aliases"], "source_count": s["source_count"]}
                for s in temp_spaces
            ],
            "updated_at": raw["updated_at"],
            "badge": self._format_badge(default_spaces, temp_spaces if raw["temp_active"] else []),
        }

    @staticmethod
    def _format_badge(default_spaces: List[dict], temp_spaces: List[dict]) -> str:
        if temp_spaces:
            t_label = "、".join(s["name"] for s in temp_spaces)
            d_label = "、".join(s["name"] for s in default_spaces) if default_spaces else "无"
            return f"本次临时引用：{t_label}（回合结束后恢复默认：{d_label}）"
        if default_spaces:
            return "当前引用：" + "、".join(s["name"] for s in default_spaces)
        return "当前未绑定资料空间"

    def apply_action(
        self,
        workbench: str,
        session_id: str,
        action: str,
        space_queries: Optional[List[str]] = None,
    ) -> dict:
        """Execute one of the 7 canonical scope actions atomically.

        Actions:
        - bind / replace : set persistent default scope to space_queries, clear temp
        - add            : keep existing default scope and append space_queries, clear temp
        - remove         : remove matching space_queries from default scope, clear temp
        - once           : set temporary scope for this turn ONLY; keep default_space_ids intact
        - clear_temp     : end temporary override and restore default_space_ids
        - clear          : clear all default and temporary references for this session
        """
        act = (action or "").strip().lower()
        current = self._load_raw(workbench, session_id)
        queries = [q.strip() for q in (space_queries or []) if q and q.strip()]

        if act in ("bind", "replace"):
            resolved = self._resolve_and_verify_spaces(queries, require_readable=True)
            new_ids = [s["space_id"] for s in resolved]
            self._save_raw(workbench, session_id, new_ids, [], False)
            state = self.get_session_state(workbench, session_id)
            state["message"] = f"本会话已绑定默认引用：{'、'.join(s['name'] for s in resolved)}"
            return state

        if act == "add":
            resolved = self._resolve_and_verify_spaces(queries, require_readable=True)
            merged_ids = list(current["default_space_ids"])
            for s in resolved:
                if s["space_id"] not in merged_ids:
                    merged_ids.append(s["space_id"])
            self._save_raw(workbench, session_id, merged_ids, [], False)
            state = self.get_session_state(workbench, session_id)
            state["message"] = "已加入当前会话默认范围：" + "、".join(x["name"] for x in state["default_spaces"])
            return state

        if act == "remove":
            resolved = self._resolve_and_verify_spaces(queries, require_readable=False)
            remove_ids = {s["space_id"] for s in resolved}
            kept_ids = [i for i in current["default_space_ids"] if i not in remove_ids]
            self._save_raw(workbench, session_id, kept_ids, [], False)
            state = self.get_session_state(workbench, session_id)
            if state["default_spaces"]:
                state["message"] = "已移除指定空间，当前保留默认引用：" + "、".join(x["name"] for x in state["default_spaces"])
            else:
                state["message"] = "已移除指定空间，当前会话无默认引用"
            return state

        if act == "once":
            resolved = self._resolve_and_verify_spaces(queries, require_readable=True)
            temp_ids = [s["space_id"] for s in resolved]
            # Crucial invariant: default_space_ids is NEVER modified by 'once'
            self._save_raw(workbench, session_id, current["default_space_ids"], temp_ids, True)
            state = self.get_session_state(workbench, session_id)
            state["message"] = state["badge"]
            return state

        if act == "clear_temp":
            if current["temp_active"] or current["temp_space_ids"]:
                self._save_raw(workbench, session_id, current["default_space_ids"], [], False)
            state = self.get_session_state(workbench, session_id)
            state["message"] = "单次临时引用已结束，恢复会话默认范围"
            return state

        if act in ("clear", "clear_all"):
            self._save_raw(workbench, session_id, [], [], False)
            state = self.get_session_state(workbench, session_id)
            state["message"] = "已清空当前会话的全部资料引用"
            return state

        raise RegistryError("INVALID_ACTION", f"不支持的会话操作类型：{action}")

    def resolve_effective_sources(self, workbench: str, session_id: str) -> dict:
        """Return active spaces, allowed source paths, and any broken-source warnings."""
        state = self.get_session_state(workbench, session_id)
        eff_ids = [s["space_id"] for s in state["effective_spaces"]]
        if not eff_ids:
            raise RegistryError(
                "NO_ACTIVE_SCOPE",
                "当前会话尚未引用任何资料空间。请先指定空间（例如“引用杭州仓”），或提供本机文件夹路径创建空间。",
            )

        allowed_paths: List[str] = []
        source_warnings: List[dict] = []
        spaces_detail: List[dict] = []

        for sid in eff_ids:
            self.registry.refresh_sources(sid)
            sp = self.registry.get_space(sid)
            spaces_detail.append(sp)
            for src in sp["sources"]:
                if src["status"] in ("readable", "updated"):
                    allowed_paths.append(src["path"])
                else:
                    source_warnings.append(
                        {
                            "space_name": sp["name"],
                            "source_id": src["source_id"],
                            "path": src["path"],
                            "status": src["status"],
                            "reason": src["status_note"],
                        }
                    )

        if not allowed_paths:
            raise RegistryError(
                "ALL_SOURCES_UNREADABLE",
                "当前引用空间的原文件或文件夹均无法读取（可能已移走或无权限），请先在管理端重新关联；不会返回旧缓存充当新结果。",
            )

        return {
            "state": state,
            "spaces": spaces_detail,
            "allowed_paths": allowed_paths,
            "source_warnings": source_warnings,
        }

    def list_active_sessions(self) -> List[dict]:
        with self.registry._connect() as conn:
            rows = conn.execute(
                "SELECT workbench, session_id FROM session_bindings ORDER BY updated_at DESC"
            ).fetchall()
        results = []
        for r in rows:
            st = self.get_session_state(r["workbench"], r["session_id"])
            if st["default_spaces"] or st["temp_spaces"]:
                results.append(st)
        return results
