#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "vendor",
        here.parent.parent / "src",
    ]
    env_src = os.environ.get("REFSPACE_SRC_DIR")
    if env_src:
        candidates.insert(0, Path(env_src).expanduser().resolve())
    for c in candidates:
        if (c / "refspace").is_dir():
            sys.path.insert(0, str(c))
            return


if __name__ == "__main__":
    _bootstrap_path()
    from refspace.mcp_server import run_stdio_server

    run_stdio_server()
