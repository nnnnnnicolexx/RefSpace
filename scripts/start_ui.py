#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from refspace.cli import seed_demo_spaces
from refspace.registry import RefSpaceRegistry
from refspace.server import create_http_server
from refspace.session import SessionScopeManager


def main() -> None:
    db_path = os.environ.get("REFSPACE_DB_PATH") or str(REPO_ROOT / ".refspace" / "demo.db")
    first_init = not Path(db_path).exists()
    if first_init:
        seed_demo_spaces(db_path=db_path)
        reg = RefSpaceRegistry(db_path=db_path)
        sm = SessionScopeManager(reg)
        sm.apply_action("antigravity", "DEMO-7176", "bind", ["杭州仓"])
        sm.apply_action("antigravity", "DEMO-902A", "bind", ["合同库"])

    port = int(os.environ.get("REFSPACE_PORT") or "17890")
    srv = create_http_server(host="127.0.0.1", port=port, db_path=db_path)
    print(f"RefSpace UI listening at http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
