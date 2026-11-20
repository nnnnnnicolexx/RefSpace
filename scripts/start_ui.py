#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from refspace.cli import seed_demo_spaces
from refspace.registry import RefSpaceRegistry, default_db_path
from refspace.server import create_http_server


def main() -> None:
    # Share the exact same default SQLite DB (~/.refspace/refspace.db) as Antigravity MCP & Hooks
    db_path = os.environ.get("REFSPACE_DB_PATH") or default_db_path()
    reg = RefSpaceRegistry(db_path=db_path)
    if not reg.list_spaces():
        seed_demo_spaces(db_path=db_path)

    port = int(os.environ.get("REFSPACE_PORT") or "17890")
    srv = create_http_server(host="127.0.0.1", port=port, db_path=db_path)
    print(f"RefSpace UI listening at http://127.0.0.1:{port} (DB: {db_path})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
