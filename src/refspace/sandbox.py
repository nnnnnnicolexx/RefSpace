from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

SUPPORTED_TABLE_EXTS = {".xlsx", ".xlsm", ".csv", ".tsv"}
SUPPORTED_DOC_EXTS = {".md", ".txt", ".docx", ".pdf", ".json"}
SUPPORTED_EXTS = SUPPORTED_TABLE_EXTS | SUPPORTED_DOC_EXTS
IGNORED_NAMES = {".DS_Store", "Thumbs.db", ".git", "__pycache__", "node_modules"}


class SandboxViolationError(PermissionError):
    """Raised when a path escapes registered read-only source roots."""


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    realpath: str
    size: int
    mtime_ns: int
    mtime_iso: str
    category: str
    ext: str


def is_ignored_entry(name: str) -> bool:
    if not name or name in IGNORED_NAMES:
        return True
    return name.startswith(".") or name.startswith("~$") or name.startswith(".~")


def classify_extension(path: str) -> Optional[str]:
    ext = Path(path).suffix.lower()
    if ext in SUPPORTED_TABLE_EXTS:
        return "table"
    if ext in SUPPORTED_DOC_EXTS:
        return "doc"
    return None


def format_mtime_iso(mtime_ns: int) -> str:
    tz = dt.timezone(dt.timedelta(hours=8))
    return dt.datetime.fromtimestamp(mtime_ns / 1_000_000_000.0, tz=tz).strftime("%Y-%m-%d %H:%M:%S")


def canonicalize_existing_source(raw_path: str) -> Tuple[str, str, str]:
    expanded = os.path.abspath(os.path.expanduser(raw_path.strip()))
    if not os.path.exists(expanded):
        raise FileNotFoundError(f"路径不存在：{expanded}")
    if not os.access(expanded, os.R_OK):
        raise PermissionError(f"无读取权限：{expanded}")
    resolved = os.path.realpath(expanded)
    if not os.access(resolved, os.R_OK):
        raise PermissionError(f"真实目标无读取权限：{resolved}")
    kind = "folder" if os.path.isdir(resolved) else "file"
    return expanded, resolved, kind


def verify_within_allowed_roots(target_path: str, allowed_roots: Iterable[str]) -> str:
    expanded = os.path.abspath(os.path.expanduser(target_path))
    resolved_target = os.path.realpath(expanded)

    canonical_roots = [os.path.realpath(r) for r in allowed_roots if r and os.path.exists(r)]
    if not canonical_roots:
        raise SandboxViolationError("当前范围没有可读取的已授权来源")

    allowed = False
    for c_root in canonical_roots:
        if os.path.isfile(c_root) and resolved_target == c_root:
            allowed = True
            break
        if os.path.isdir(c_root):
            try:
                if os.path.commonpath([c_root, resolved_target]) == c_root:
                    allowed = True
                    break
            except ValueError:
                continue

    if not allowed:
        raise SandboxViolationError(f"越界访问已被只读沙箱拦截：{target_path}")
    if not os.path.exists(resolved_target):
        raise FileNotFoundError(f"原文件不存在或已移走：{target_path}")
    if not os.access(resolved_target, os.R_OK):
        raise PermissionError(f"原文件无读取权限：{target_path}")
    return resolved_target


def capture_file_snapshot(target_path: str, allowed_roots: Iterable[str]) -> FileSnapshot:
    resolved = verify_within_allowed_roots(target_path, allowed_roots)
    if not os.path.isfile(resolved):
        raise IsADirectoryError(f"目标是文件夹而非单文件：{target_path}")
    st = os.stat(resolved)
    cat = classify_extension(resolved)
    if cat is None:
        raise ValueError(f"暂不支持的文件类型：{Path(resolved).suffix}")
    return FileSnapshot(
        path=os.path.abspath(os.path.expanduser(target_path)),
        realpath=resolved,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        mtime_iso=format_mtime_iso(st.st_mtime_ns),
        category=cat,
        ext=Path(resolved).suffix.lower(),
    )


def verify_snapshot_unchanged(snapshot: FileSnapshot) -> bool:
    if not os.path.exists(snapshot.realpath) or not os.access(snapshot.realpath, os.R_OK):
        return False
    st = os.stat(snapshot.realpath)
    return st.st_mtime_ns == snapshot.mtime_ns and st.st_size == snapshot.size


def scan_allowed_files(source_paths: Iterable[str], max_files: int = 2000) -> Tuple[List[FileSnapshot], List[dict]]:
    roots = [os.path.abspath(os.path.expanduser(p)) for p in source_paths if p]
    valid_roots: List[str] = []
    issues: List[dict] = []

    for r in roots:
        if not os.path.exists(r):
            issues.append({"path": r, "status": "needs_relink", "reason": "原路径不存在或已移动"})
        elif not os.access(r, os.R_OK):
            issues.append({"path": r, "status": "needs_relink", "reason": "缺少本机读取权限"})
        else:
            valid_roots.append(r)

    seen = set()
    snapshots: List[FileSnapshot] = []
    for root in valid_roots:
        real_root = os.path.realpath(root)
        if os.path.isfile(real_root):
            cat = classify_extension(real_root)
            if cat and real_root not in seen:
                seen.add(real_root)
                st = os.stat(real_root)
                snapshots.append(
                    FileSnapshot(root, real_root, st.st_size, st.st_mtime_ns, format_mtime_iso(st.st_mtime_ns), cat, Path(real_root).suffix.lower())
                )
            continue

        for dirpath, dirnames, filenames in os.walk(real_root, followlinks=False):
            dirnames[:] = sorted([d for d in dirnames if not is_ignored_entry(d) and not os.path.islink(os.path.join(dirpath, d))])
            for fname in sorted(filenames):
                if is_ignored_entry(fname):
                    continue
                candidate = os.path.join(dirpath, fname)
                if os.path.islink(candidate):
                    try:
                        resolved = verify_within_allowed_roots(candidate, valid_roots)
                    except Exception:
                        issues.append({"path": candidate, "status": "blocked_symlink", "reason": "软链接指向未授权目录，已跳过"})
                        continue
                else:
                    resolved = os.path.realpath(candidate)

                if resolved in seen:
                    continue
                cat = classify_extension(resolved)
                if cat is None:
                    continue
                if not os.access(resolved, os.R_OK):
                    issues.append({"path": candidate, "status": "needs_relink", "reason": "文件不可读取"})
                    continue
                st = os.stat(resolved)
                seen.add(resolved)
                snapshots.append(
                    FileSnapshot(candidate, resolved, st.st_size, st.st_mtime_ns, format_mtime_iso(st.st_mtime_ns), cat, Path(resolved).suffix.lower())
                )
                if len(snapshots) >= max_files:
                    return snapshots, issues

    return snapshots, issues
