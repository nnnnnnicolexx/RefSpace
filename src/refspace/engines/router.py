from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..registry import RefSpaceRegistry, RegistryError
from ..sandbox import FileSnapshot, capture_file_snapshot, scan_allowed_files
from ..session import SessionScopeManager
from .doc_engine import parse_document_structure, query_document
from .table_engine import parse_workbook, query_table


class RefSpaceRouter:
    """Dispatches on-demand file location, document reading, and spreadsheet calculation within active session scope."""

    def __init__(self, registry: RefSpaceRegistry, session_mgr: SessionScopeManager):
        self.registry = registry
        self.session_mgr = session_mgr

    def _resolve_target_snapshot(self, file_ref: str, allowed_paths: List[str]) -> FileSnapshot:
        raw = (file_ref or "").strip()
        if not raw:
            raise RegistryError("MISSING_FILE", "请指定要读取的文件名或路径")

        # Direct path inside allowed roots
        if os.path.isabs(raw) or raw.startswith("~"):
            return capture_file_snapshot(raw, allowed_paths)

        # Match by relative suffix or basename across scanned allowed files
        files, _ = scan_allowed_files(allowed_paths)
        norm_q = raw.lower()
        exact_base = [f for f in files if Path(f.path).name.lower() == norm_q]
        if len(exact_base) == 1:
            return exact_base[0]
        if len(exact_base) > 1:
            raise RegistryError(
                "AMBIGUOUS_FILE",
                f"当前引用范围内存在多个同名文件「{raw}」，请传入更具体的相对路径："
                + "、".join(f.path for f in exact_base[:5]),
            )

        suffix_matches = [f for f in files if f.path.lower().endswith(norm_q) or norm_q in Path(f.path).name.lower()]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(suffix_matches) > 1:
            raise RegistryError(
                "AMBIGUOUS_FILE",
                f"文件名关键词「{raw}」匹配多个文件，请指明其中一个："
                + "、".join(Path(f.path).name for f in suffix_matches[:6]),
            )

        raise RegistryError("FILE_NOT_IN_SCOPE", f"在当前会话引用的资料空间中未找到文件「{raw}」")

    def locate_in_scope(
        self,
        workbench: str,
        session_id: str,
        query: Optional[str] = None,
        category: str = "all",
        include_schema_summary: bool = True,
        limit: int = 30,
    ) -> dict:
        ctx = self.session_mgr.resolve_effective_sources(workbench, session_id)
        files, scan_issues = scan_allowed_files(ctx["allowed_paths"])
        cat_filter = (category or "all").strip().lower()
        if cat_filter in ("table", "doc"):
            files = [f for f in files if f.category == cat_filter]

        tokens = [t.lower() for t in re.split(r"[\s,，、;；]+", (query or "").strip()) if t]
        scored = []
        for f in files:
            fname = Path(f.path).name
            score = 1 if not tokens else 0
            meta_summary: Dict[str, Any] = {}

            if include_schema_summary and len(files) <= 40:
                try:
                    if f.category == "table":
                        wb = parse_workbook(f)
                        sheet_briefs = []
                        all_fields: List[str] = []
                        for sh in wb["sheets"]:
                            rg_rows = sum(r["record_count"] for r in sh["regions"])
                            fnames = [fd["name"] for r in sh["regions"] for fd in r["fields"] if fd["non_empty"] > 0]
                            all_fields.extend(fnames)
                            sheet_briefs.append({"sheet": sh["sheet_name"], "records": rg_rows, "sample_fields": fnames[:8]})
                        meta_summary = {"sheets": sheet_briefs}
                        hay = (fname + " " + " ".join(s["sheet"] for s in sheet_briefs) + " " + " ".join(all_fields)).lower()
                    else:
                        doc = parse_document_structure(f)
                        headings = [s["section"] for s in doc["sections"][:12]]
                        meta_summary = {"sections": headings, "section_count": doc["section_count"]}
                        body_preview = " ".join(s["text"][:400] for s in doc["sections"][:5])
                        hay = (fname + " " + " ".join(headings) + " " + body_preview).lower()
                except Exception as exc:
                    meta_summary = {"parse_error": str(exc)}
                    hay = fname.lower()
            else:
                hay = (fname + " " + f.path).lower()

            if tokens:
                for tok in tokens:
                    if tok in fname.lower():
                        score += 6
                    elif tok in hay:
                        score += 3
            if score > 0:
                scored.append((score, f, meta_summary))

        scored.sort(key=lambda item: (-item[0], Path(item[1].path).name))
        items = [
            {
                "file_name": Path(f.path).name,
                "path": f.path,
                "category": f.category,
                "ext": f.ext,
                "size_bytes": f.size,
                "mtime_iso": f.mtime_iso,
                "summary": meta,
            }
            for (_, f, meta) in scored[: max(1, min(limit, 100))]
        ]

        return {
            "scope_badge": ctx["state"]["badge"],
            "effective_spaces": ctx["state"]["effective_spaces"],
            "total_files_in_scope": len(files),
            "matched_count": len(items),
            "files": items,
            "warnings": ctx["source_warnings"] + scan_issues,
        }

    def read_doc_in_scope(
        self,
        workbench: str,
        session_id: str,
        file: str,
        query: Optional[str] = None,
        section_filter: Optional[str] = None,
        outline_only: bool = False,
        max_passages: int = 8,
    ) -> dict:
        ctx = self.session_mgr.resolve_effective_sources(workbench, session_id)
        snap = self._resolve_target_snapshot(file, ctx["allowed_paths"])
        if snap.category != "doc":
            raise RegistryError("WRONG_TRACK", f"「{Path(snap.path).name}」是表格文件，请使用 refspace_query_table 执行查询或统计")

        res = query_document(snap, query=query, section_filter=section_filter, outline_only=outline_only, max_passages=max_passages)
        res["scope_badge"] = ctx["state"]["badge"]
        res["source_warnings"] = ctx["source_warnings"]
        cits = res.get("citations") or ([res["citation"]] if "citation" in res else [])
        self.registry.record_provenance(workbench, session_id, "refspace_read_doc", [s["space_id"] for s in ctx["state"]["effective_spaces"]], cits)
        return res

    def query_table_in_scope(
        self,
        workbench: str,
        session_id: str,
        file: str,
        mode: str = "inspect",
        sheet_name: Optional[str] = None,
        region_index: int = 1,
        filters: Optional[Dict[str, Any]] = None,
        keyword: Optional[str] = None,
        group_by: Optional[List[str]] = None,
        metrics: Optional[List[dict]] = None,
        dedup_by: Optional[List[str]] = None,
        sort_by: Optional[str] = None,
        sort_desc: bool = False,
        limit: int = 30,
    ) -> dict:
        ctx = self.session_mgr.resolve_effective_sources(workbench, session_id)
        snap = self._resolve_target_snapshot(file, ctx["allowed_paths"])
        if snap.category != "table":
            raise RegistryError("WRONG_TRACK", f"「{Path(snap.path).name}」是文档文件，请使用 refspace_read_doc 提取内容")

        res = query_table(
            snap,
            mode=mode,
            sheet_name=sheet_name,
            region_index=region_index,
            filters=filters,
            keyword=keyword,
            group_by=group_by,
            metrics=metrics,
            dedup_by=dedup_by,
            sort_by=sort_by,
            sort_desc=sort_desc,
            limit=limit,
        )
        res["scope_badge"] = ctx["state"]["badge"]
        res["source_warnings"] = ctx["source_warnings"]
        self.registry.record_provenance(
            workbench,
            session_id,
            "refspace_query_table",
            [s["space_id"] for s in ctx["state"]["effective_spaces"]],
            [res["citation"]],
        )
        return res

    def inspect_single_source_preview(self, source_id: str) -> dict:
        """Used by the local HTML Management Drawer to show real recognized sheets/sections on demand."""
        src = self.registry.get_source(source_id)
        if src["status"] == "needs_relink":
            return {"source": src, "preview": None, "warning": src["status_note"]}
        if src["kind"] == "folder":
            files, issues = scan_allowed_files([src["path"]], max_files=100)
            return {
                "source": src,
                "preview": {
                    "kind": "folder",
                    "file_count": len(files),
                    "files": [
                        {"name": Path(f.path).name, "category": f.category, "ext": f.ext, "mtime_iso": f.mtime_iso}
                        for f in files[:30]
                    ],
                },
                "issues": issues,
            }

        snap = capture_file_snapshot(src["path"], [src["path"]])
        if snap.category == "table":
            info = query_table(snap, mode="inspect")
            return {"source": src, "preview": {"kind": "table", "detail": info}}
        info = query_document(snap, outline_only=True)
        return {"source": src, "preview": {"kind": "doc", "detail": info}}
