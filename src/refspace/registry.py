from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import List, Optional

from .sandbox import canonicalize_existing_source, classify_extension, format_mtime_iso, is_ignored_entry


def now_iso() -> str:
    tz = dt.timezone(dt.timedelta(hours=8))
    return dt.datetime.now(tz=tz).strftime("%Y-%m-%d %H:%M:%S")


def normalize_name(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip()).lower()


def default_db_path() -> str:
    env_path = os.environ.get("REFSPACE_DB_PATH")
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    home_dir = Path.home() / ".refspace"
    home_dir.mkdir(parents=True, exist_ok=True)
    return str(home_dir / "refspace.db")


class RegistryError(Exception):
    def __init__(self, code: str, message: str, candidates: Optional[List[dict]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.candidates = candidates or []


class RefSpaceRegistry:
    """SQLite registry storing Spaces, Sources, Aliases, Session Bindings, and Provenance."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or default_db_path()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS spaces (
                    space_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    norm_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS space_aliases (
                    space_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    norm_alias TEXT NOT NULL,
                    PRIMARY KEY (space_id, norm_alias),
                    FOREIGN KEY (space_id) REFERENCES spaces(space_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    realpath TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL,
                    last_mtime_ns INTEGER NOT NULL DEFAULT 0,
                    last_size INTEGER NOT NULL DEFAULT 0,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    status_note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS space_sources (
                    space_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (space_id, source_id),
                    FOREIGN KEY (space_id) REFERENCES spaces(space_id) ON DELETE CASCADE,
                    FOREIGN KEY (source_id) REFERENCES sources(source_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS session_bindings (
                    workbench TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    default_space_ids_json TEXT NOT NULL DEFAULT '[]',
                    temp_space_ids_json TEXT NOT NULL DEFAULT '[]',
                    temp_active INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (workbench, session_id)
                );
                CREATE TABLE IF NOT EXISTS provenance_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workbench TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    space_ids_json TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS integration_events (
                    workbench TEXT PRIMARY KEY,
                    last_handshake_at TEXT NOT NULL DEFAULT '',
                    last_tool_call_at TEXT NOT NULL DEFAULT '',
                    last_read_at TEXT NOT NULL DEFAULT '',
                    last_read_summary TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def create_space(
        self,
        name: str,
        aliases: Optional[List[str]] = None,
        source_paths: Optional[List[str]] = None,
        description: str = "",
        space_id: Optional[str] = None,
    ) -> dict:
        clean_name = (name or "").strip()
        if not clean_name:
            raise RegistryError("INVALID_NAME", "资料空间名称不能为空")
        norm = normalize_name(clean_name)

        with self._connect() as conn:
            dup = conn.execute("SELECT space_id, name FROM spaces WHERE norm_name = ?", (norm,)).fetchone()
            if dup:
                raise RegistryError("DUPLICATE_SPACE_NAME", f"已存在同名资料空间「{dup['name']}」（{dup['space_id']}）")
            sid = space_id or f"sp_{uuid.uuid4().hex[:8]}"
            ts = now_iso()
            conn.execute(
                "INSERT INTO spaces(space_id, name, norm_name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, clean_name, norm, description.strip(), ts, ts),
            )
            for raw_alias in aliases or []:
                a = raw_alias.strip()
                if a and normalize_name(a) != norm:
                    conn.execute(
                        "INSERT OR IGNORE INTO space_aliases(space_id, alias, norm_alias) VALUES (?, ?, ?)",
                        (sid, a, normalize_name(a)),
                    )

        for sp in source_paths or []:
            self.add_source_to_space(sid, sp)
        return self.get_space(sid)

    def update_space(
        self,
        space_id: str,
        name: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> dict:
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM spaces WHERE space_id = ?", (space_id,)).fetchone()
            if not existing:
                raise RegistryError("SPACE_NOT_FOUND", f"未找到编号为 {space_id} 的资料空间")
            new_name = existing["name"] if name is None else name.strip()
            if not new_name:
                raise RegistryError("INVALID_NAME", "资料空间名称不能为空")
            new_norm = normalize_name(new_name)
            dup = conn.execute(
                "SELECT space_id, name FROM spaces WHERE norm_name = ? AND space_id != ?",
                (new_norm, space_id),
            ).fetchone()
            if dup:
                raise RegistryError("DUPLICATE_SPACE_NAME", f"名称与已有空间「{dup['name']}」冲突")
            new_desc = existing["description"] if description is None else description.strip()
            conn.execute(
                "UPDATE spaces SET name = ?, norm_name = ?, description = ?, updated_at = ? WHERE space_id = ?",
                (new_name, new_norm, new_desc, now_iso(), space_id),
            )
            if aliases is not None:
                conn.execute("DELETE FROM space_aliases WHERE space_id = ?", (space_id,))
                for raw_alias in aliases:
                    a = raw_alias.strip()
                    if a and normalize_name(a) != new_norm:
                        conn.execute(
                            "INSERT OR IGNORE INTO space_aliases(space_id, alias, norm_alias) VALUES (?, ?, ?)",
                            (space_id, a, normalize_name(a)),
                        )
        return self.get_space(space_id)

    def delete_space(self, space_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM spaces WHERE space_id = ?", (space_id,))
            conn.execute("DELETE FROM sources WHERE source_id NOT IN (SELECT DISTINCT source_id FROM space_sources)")
            rows = conn.execute("SELECT * FROM session_bindings").fetchall()
            for r in rows:
                defs = [x for x in json.loads(r["default_space_ids_json"]) if x != space_id]
                tmps = [x for x in json.loads(r["temp_space_ids_json"]) if x != space_id]
                t_act = 1 if (r["temp_active"] and tmps) else 0
                conn.execute(
                    "UPDATE session_bindings SET default_space_ids_json=?, temp_space_ids_json=?, temp_active=?, updated_at=? WHERE workbench=? AND session_id=?",
                    (json.dumps(defs), json.dumps(tmps), t_act, now_iso(), r["workbench"], r["session_id"]),
                )

    def _inspect_path_state(self, raw_path: str, prev_mtime_ns: Optional[int] = None) -> dict:
        expanded = os.path.abspath(os.path.expanduser(raw_path.strip()))
        ts = now_iso()
        if not os.path.exists(expanded) or not os.access(expanded, os.R_OK):
            reason = "原路径不存在或已移动，请重新关联" if not os.path.exists(expanded) else "无读取权限，请检查系统授权或重新关联"
            return {
                "path": expanded,
                "realpath": "",
                "kind": "folder" if raw_path.endswith("/") or os.path.isdir(expanded) else "file",
                "status": "needs_relink",
                "last_checked_at": ts,
                "last_mtime_ns": prev_mtime_ns or 0,
                "last_size": 0,
                "item_count": 0,
                "status_note": reason,
            }

        _, resolved, kind = canonicalize_existing_source(expanded)
        st = os.stat(resolved)
        max_mtime = st.st_mtime_ns
        item_count = 1
        if kind == "folder":
            count = 0
            for dirpath, dirnames, filenames in os.walk(resolved, followlinks=False):
                dirnames[:] = [d for d in dirnames if not is_ignored_entry(d) and not os.path.islink(os.path.join(dirpath, d))]
                for fn in filenames:
                    if is_ignored_entry(fn):
                        continue
                    fp = os.path.join(dirpath, fn)
                    if not os.path.islink(fp) and classify_extension(fp) is not None:
                        count += 1
                        try:
                            fst = os.stat(fp)
                            if fst.st_mtime_ns > max_mtime:
                                max_mtime = fst.st_mtime_ns
                        except OSError:
                            pass
            item_count = count
        elif classify_extension(resolved) is None:
            raise RegistryError("UNSUPPORTED_FILE_TYPE", f"暂不支持该文件格式：{Path(resolved).suffix}")

        status = "readable"
        note = f"最近修改：{format_mtime_iso(max_mtime)}"
        if prev_mtime_ns and prev_mtime_ns > 0 and max_mtime != prev_mtime_ns:
            status = "updated"
            note = f"原文件已更新（{format_mtime_iso(max_mtime)}），下次读取自动使用新版本"

        return {
            "path": expanded,
            "realpath": resolved,
            "kind": kind,
            "status": status,
            "last_checked_at": ts,
            "last_mtime_ns": max_mtime,
            "last_size": st.st_size,
            "item_count": item_count,
            "status_note": note,
        }

    def add_source_to_space(self, space_id: str, raw_path: str) -> dict:
        info = self._inspect_path_state(raw_path, prev_mtime_ns=None)
        if info["status"] == "needs_relink":
            raise RegistryError("SOURCE_UNREADABLE", info["status_note"])

        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM spaces WHERE space_id = ?", (space_id,)).fetchone():
                raise RegistryError("SPACE_NOT_FOUND", f"未找到资料空间：{space_id}")
            existing = conn.execute("SELECT source_id FROM sources WHERE path = ?", (info["path"],)).fetchone()
            if existing:
                src_id = existing["source_id"]
                conn.execute(
                    "UPDATE sources SET realpath=?, kind=?, status='readable', last_checked_at=?, last_mtime_ns=?, last_size=?, item_count=?, status_note=? WHERE source_id=?",
                    (info["realpath"], info["kind"], info["last_checked_at"], info["last_mtime_ns"], info["last_size"], info["item_count"], info["status_note"], src_id),
                )
            else:
                src_id = f"src_{uuid.uuid4().hex[:8]}"
                conn.execute(
                    "INSERT INTO sources(source_id, path, realpath, kind, status, last_checked_at, last_mtime_ns, last_size, item_count, status_note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (src_id, info["path"], info["realpath"], info["kind"], "readable", info["last_checked_at"], info["last_mtime_ns"], info["last_size"], info["item_count"], info["status_note"]),
                )
            conn.execute("INSERT OR IGNORE INTO space_sources(space_id, source_id, added_at) VALUES (?, ?, ?)", (space_id, src_id, now_iso()))
            conn.execute("UPDATE spaces SET updated_at = ? WHERE space_id = ?", (now_iso(), space_id))
        return self.get_source(src_id)

    def relink_source(self, source_id: str, new_raw_path: str) -> dict:
        info = self._inspect_path_state(new_raw_path, prev_mtime_ns=None)
        if info["status"] == "needs_relink":
            raise RegistryError("SOURCE_UNREADABLE", info["status_note"])
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM sources WHERE source_id = ?", (source_id,)).fetchone():
                raise RegistryError("SOURCE_NOT_FOUND", f"未找到引用来源：{source_id}")
            if conn.execute("SELECT 1 FROM sources WHERE path = ? AND source_id != ?", (info["path"], source_id)).fetchone():
                raise RegistryError("DUPLICATE_SOURCE_PATH", f"该路径已存在于其他引用记录中：{info['path']}")
            conn.execute(
                "UPDATE sources SET path=?, realpath=?, kind=?, status='readable', last_checked_at=?, last_mtime_ns=?, last_size=?, item_count=?, status_note=? WHERE source_id=?",
                (info["path"], info["realpath"], info["kind"], info["last_checked_at"], info["last_mtime_ns"], info["last_size"], info["item_count"], info["status_note"], source_id),
            )
        return self.get_source(source_id)

    def remove_source_from_space(self, space_id: str, source_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM space_sources WHERE space_id = ? AND source_id = ?", (space_id, source_id))
            conn.execute("DELETE FROM sources WHERE source_id NOT IN (SELECT DISTINCT source_id FROM space_sources)")
            conn.execute("UPDATE spaces SET updated_at = ? WHERE space_id = ?", (now_iso(), space_id))

    def refresh_sources(self, space_id: Optional[str] = None, acknowledge_updates: bool = False) -> List[dict]:
        with self._connect() as conn:
            if space_id:
                rows = conn.execute(
                    "SELECT s.* FROM sources s JOIN space_sources ss ON ss.source_id = s.source_id WHERE ss.space_id = ?",
                    (space_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM sources").fetchall()
            for r in rows:
                prev_mtime = None if acknowledge_updates else r["last_mtime_ns"]
                try:
                    info = self._inspect_path_state(r["path"], prev_mtime_ns=prev_mtime)
                except RegistryError as exc:
                    info = {
                        "realpath": "",
                        "kind": r["kind"],
                        "status": "needs_relink",
                        "last_checked_at": now_iso(),
                        "last_mtime_ns": r["last_mtime_ns"],
                        "last_size": 0,
                        "item_count": 0,
                        "status_note": exc.message,
                    }
                conn.execute(
                    "UPDATE sources SET realpath=?, kind=?, status=?, last_checked_at=?, last_mtime_ns=?, last_size=?, item_count=?, status_note=? WHERE source_id=?",
                    (info["realpath"], info["kind"], info["status"], info["last_checked_at"], info["last_mtime_ns"], info["last_size"], info["item_count"], info["status_note"], r["source_id"]),
                )
        return self.get_space(space_id)["sources"] if space_id else self.list_all_sources()

    def _format_source_row(self, sr: sqlite3.Row) -> dict:
        ext = Path(sr["path"]).suffix.lower()
        cat = "folder" if sr["kind"] == "folder" else (classify_extension(sr["path"]) or "other")
        return {
            "source_id": sr["source_id"],
            "path": sr["path"],
            "name": Path(sr["path"]).name + ("/" if sr["kind"] == "folder" else ""),
            "realpath": sr["realpath"],
            "kind": sr["kind"],
            "category": cat,
            "ext": ext,
            "status": sr["status"],
            "last_checked_at": sr["last_checked_at"],
            "last_mtime_ns": sr["last_mtime_ns"],
            "last_mtime_iso": format_mtime_iso(sr["last_mtime_ns"]) if sr["last_mtime_ns"] else "",
            "last_size": sr["last_size"],
            "item_count": sr["item_count"],
            "status_note": sr["status_note"],
        }

    def _hydrate_space(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
        sid = row["space_id"]
        aliases = [a["alias"] for a in conn.execute("SELECT alias FROM space_aliases WHERE space_id = ? ORDER BY alias", (sid,)).fetchall()]
        src_rows = conn.execute(
            "SELECT s.* FROM sources s JOIN space_sources ss ON ss.source_id = s.source_id WHERE ss.space_id = ? ORDER BY s.kind DESC, s.path ASC",
            (sid,),
        ).fetchall()
        sources = [self._format_source_row(sr) for sr in src_rows]
        return {
            "space_id": sid,
            "name": row["name"],
            "aliases": aliases,
            "description": row["description"],
            "source_count": len(sources),
            "sources": sources,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_space(self, space_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM spaces WHERE space_id = ?", (space_id,)).fetchone()
            if not row:
                raise RegistryError("SPACE_NOT_FOUND", f"未找到资料空间：{space_id}")
            return self._hydrate_space(conn, row)

    def list_spaces(self) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM spaces ORDER BY created_at ASC").fetchall()
            return [self._hydrate_space(conn, r) for r in rows]

    def get_source(self, source_id: str) -> dict:
        with self._connect() as conn:
            sr = conn.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
            if not sr:
                raise RegistryError("SOURCE_NOT_FOUND", f"未找到引用来源：{source_id}")
            space_rows = conn.execute(
                "SELECT sp.space_id, sp.name FROM spaces sp JOIN space_sources ss ON ss.space_id = sp.space_id WHERE ss.source_id = ?",
                (source_id,),
            ).fetchall()
            item = self._format_source_row(sr)
            item["spaces"] = [{"space_id": r["space_id"], "name": r["name"]} for r in space_rows]
            return item

    def list_all_sources(self) -> List[dict]:
        with self._connect() as conn:
            ids = [r["source_id"] for r in conn.execute("SELECT source_id FROM sources ORDER BY path ASC").fetchall()]
        return [self.get_source(i) for i in ids]

    def resolve_space_query(self, query: str) -> dict:
        raw = (query or "").strip()
        if not raw:
            raise RegistryError("INVALID_QUERY", "未指定资料空间名称")
        norm = normalize_name(raw)
        spaces = self.list_spaces()

        for sp in spaces:
            if sp["space_id"].lower() == raw.lower():
                return sp

        exact_name = [sp for sp in spaces if normalize_name(sp["name"]) == norm]
        if len(exact_name) == 1:
            return exact_name[0]

        exact_alias = [sp for sp in spaces if any(normalize_name(a) == norm for a in sp["aliases"])]
        if len(exact_alias) == 1:
            return exact_alias[0]
        if len(exact_alias) > 1:
            raise RegistryError(
                "AMBIGUOUS_SPACE",
                f"简称「{raw}」同时匹配多个资料空间（{'、'.join(s['name'] for s in exact_alias)}），请明确指定；当前引用范围保持不变。",
                candidates=[{"space_id": s["space_id"], "name": s["name"], "aliases": s["aliases"]} for s in exact_alias],
            )

        if len(norm) >= 2:
            partial = []
            for sp in spaces:
                haystacks = [normalize_name(sp["name"])] + [normalize_name(a) for a in sp["aliases"]]
                if any(norm in h or h in norm for h in haystacks if len(h) >= 2):
                    partial.append(sp)
            if len(partial) == 1:
                return partial[0]
            if len(partial) > 1:
                raise RegistryError(
                    "AMBIGUOUS_SPACE",
                    f"「{raw}」同时匹配多个资料空间（{'、'.join(s['name'] for s in partial)}），请指明具体空间；当前引用范围保持不变。",
                    candidates=[{"space_id": s["space_id"], "name": s["name"], "aliases": s["aliases"]} for s in partial],
                )

        raise RegistryError(
            "SPACE_NOT_FOUND",
            f"尚未建立名为「{raw}」的资料空间；请先提供本机文件夹或文件路径进行关联，当前引用范围保持不变。",
        )

    def record_provenance(self, workbench: str, session_id: str, tool_name: str, space_ids: List[str], citations: List[dict]) -> None:
        ts = now_iso()
        wb = (workbench or "antigravity").strip().lower()
        summary_parts = [f"{c.get('file', '')} · {c.get('locator', '')}".strip(" ·") for c in (citations or []) if c.get("file")]
        summary = "；".join(summary_parts[:2])
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO provenance_logs(workbench, session_id, tool_name, space_ids_json, citations_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (wb, session_id, tool_name, json.dumps(space_ids, ensure_ascii=False), json.dumps(citations, ensure_ascii=False), ts),
            )
            conn.execute(
                """
                INSERT INTO integration_events(workbench, last_handshake_at, last_tool_call_at, last_read_at, last_read_summary)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workbench) DO UPDATE SET
                    last_tool_call_at = excluded.last_tool_call_at,
                    last_read_at = excluded.last_read_at,
                    last_read_summary = CASE WHEN excluded.last_read_summary != '' THEN excluded.last_read_summary ELSE integration_events.last_read_summary END
                """,
                (wb, ts, ts, ts, summary),
            )

    def record_workbench_touch(self, workbench: str, kind: str = "handshake", read_summary: str = "") -> None:
        ts = now_iso()
        wb = (workbench or "antigravity").strip().lower()
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM integration_events WHERE workbench = ?", (wb,)).fetchone()
            hs = ts if kind in ("handshake", "tool_call", "read") else (cur["last_handshake_at"] if cur else "")
            tc = ts if kind in ("tool_call", "read") else (cur["last_tool_call_at"] if cur else "")
            rd = ts if kind == "read" else (cur["last_read_at"] if cur else "")
            rs = read_summary if (kind == "read" and read_summary) else (cur["last_read_summary"] if cur else "")
            conn.execute(
                """
                INSERT INTO integration_events(workbench, last_handshake_at, last_tool_call_at, last_read_at, last_read_summary)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workbench) DO UPDATE SET
                    last_handshake_at = excluded.last_handshake_at,
                    last_tool_call_at = excluded.last_tool_call_at,
                    last_read_at = excluded.last_read_at,
                    last_read_summary = excluded.last_read_summary
                """,
                (wb, hs, tc, rd, rs),
            )

    def get_workbench_events(self, workbench: str = "antigravity") -> dict:
        wb = (workbench or "antigravity").strip().lower()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM integration_events WHERE workbench = ?", (wb,)).fetchone()
            if not row:
                return {"workbench": wb, "last_handshake_at": "", "last_tool_call_at": "", "last_read_at": "", "last_read_summary": ""}
            return {
                "workbench": wb,
                "last_handshake_at": row["last_handshake_at"],
                "last_tool_call_at": row["last_tool_call_at"],
                "last_read_at": row["last_read_at"],
                "last_read_summary": row["last_read_summary"],
            }

    def list_recent_provenance(self, limit: int = 20) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM provenance_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [
                {
                    "id": r["id"],
                    "workbench": r["workbench"],
                    "session_id": r["session_id"],
                    "tool_name": r["tool_name"],
                    "space_ids": json.loads(r["space_ids_json"]),
                    "citations": json.loads(r["citations_json"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
