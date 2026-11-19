#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m py_compile \
  src/refspace/__init__.py \
  src/refspace/__main__.py \
  src/refspace/sandbox.py \
  src/refspace/registry.py \
  src/refspace/session.py \
  src/refspace/mcp_server.py \
  src/refspace/server.py \
  src/refspace/cli.py \
  src/refspace/engines/doc_engine.py \
  src/refspace/engines/table_engine.py \
  src/refspace/engines/router.py \
  plugins/refspace/run_mcp.py \
  plugins/refspace/hooks/lifecycle.py \
  tests/fixture_builder.py \
  tests/run_all_tests.py

python3 tests/run_all_tests.py
