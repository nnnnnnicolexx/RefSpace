#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    here = Path(__file__).resolve().parent.parent
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


def main() -> None:
    raw_in = sys.stdin.read().strip()
    if not raw_in:
        sys.stdout.write("{}\n")
        return

    try:
        payload = json.loads(raw_in)
    except json.JSONDecodeError:
        sys.stdout.write("{}\n")
        return

    event = str(payload.get("hookEventName") or "")
    conv_id = str(payload.get("conversationId") or "").strip()

    _bootstrap_path()
    try:
        from refspace.registry import RefSpaceRegistry
        from refspace.session import SessionScopeManager

        reg = RefSpaceRegistry()
        sm = SessionScopeManager(reg)
    except Exception:
        if event == "PreToolUse":
            sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
        elif event == "Stop":
            sys.stdout.write(json.dumps({"decision": "stop"}) + "\n")
        else:
            sys.stdout.write("{}\n")
        return

    if event == "PreInvocation":
        if not conv_id:
            sys.stdout.write("{}\n")
            return
        st = sm.get_session_state("antigravity", conv_id)
        if st["effective_spaces"]:
            msg = f"[引知 RefSpace 会话状态] conversationId={conv_id} | {st['badge']}"
            sys.stdout.write(
                json.dumps(
                    {
                        "injectSteps": [
                            {
                                "systemMessage": {
                                    "systemMessage": msg,
                                }
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        else:
            sys.stdout.write("{}\n")
        return

    if event == "PreToolUse":
        tool_call = payload.get("toolCall") or {}
        t_name = str(tool_call.get("name") or "")
        t_args = tool_call.get("args") or {}

        if not conv_id:
            sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
            return

        # Direct eager MCP tool call (e.g. mcp_refspace_bind_scope)
        if t_name.startswith("mcp_refspace_"):
            sys.stdout.write(
                json.dumps(
                    {
                        "decision": "allow",
                        "overwrite": {
                            "session_id": conv_id,
                            "workbench": "antigravity",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return

        # Lazy MCP tool call via call_mcp_tool(ServerName="refspace", ...)
        if t_name == "call_mcp_tool" and str(t_args.get("ServerName") or "") in ("refspace", "refspace_refspace"):
            inner_args = dict(t_args.get("Arguments") or {})
            inner_args["session_id"] = conv_id
            inner_args["workbench"] = "antigravity"
            sys.stdout.write(
                json.dumps(
                    {
                        "decision": "allow",
                        "overwrite": {
                            "Arguments": inner_args,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return

        sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
        return

    if event == "Stop":
        # Automatically roll back any one-shot ('once') temporary override at turn completion
        if conv_id and not bool(payload.get("continuationActive", False)):
            try:
                sm.apply_action("antigravity", conv_id, "clear_temp")
            except Exception:
                pass
        sys.stdout.write(json.dumps({"decision": "stop"}) + "\n")
        return

    sys.stdout.write("{}\n")


if __name__ == "__main__":
    main()
