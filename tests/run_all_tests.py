#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from fixture_builder import build_sample_xlsx, create_demo_environment
from refspace.engines.router import RefSpaceRouter
from refspace.mcp_server import RefSpaceMCPDispatcher
from refspace.registry import RefSpaceRegistry
from refspace.sandbox import SandboxViolationError, verify_within_allowed_roots
from refspace.server import build_health_report
from refspace.session import SessionScopeManager


def run_hook(plugin_dir: Path, db_path: str, payload: dict) -> dict:
    env = dict(os.environ)
    env["REFSPACE_DB_PATH"] = db_path
    env["PLUGIN_ROOT"] = str(plugin_dir)
    proc = subprocess.run(
        [sys.executable, "./hooks/lifecycle.py"],
        cwd=str(plugin_dir),
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return json.loads(proc.stdout.strip())


def test_stage1_antigravity_mcp_and_hooks(tmp_root: Path, env_paths: dict, db_path: str) -> None:
    plugin_dir = REPO_ROOT / "plugins" / "refspace"
    env = dict(os.environ)
    env["REFSPACE_DB_PATH"] = db_path
    env["PLUGIN_ROOT"] = str(plugin_dir)

    # 1. Verify PreToolUse hook overwrites spoofed/missing session_id with host conversationId!
    pre_tool_out = run_hook(
        plugin_dir,
        db_path,
        {
            "hookEventName": "PreToolUse",
            "conversationId": "DEMO-7176",
            "toolCall": {
                "id": "call_1",
                "name": "mcp_refspace_bind_scope",
                "args": {"action": "bind", "spaces": ["杭州仓"], "session_id": "SPOOFED-WRONG-ID"},
            },
        },
    )
    assert pre_tool_out["decision"] == "allow"
    assert pre_tool_out["overwrite"]["session_id"] == "DEMO-7176"
    assert pre_tool_out["overwrite"]["workbench"] == "antigravity"

    # Also verify lazy call_mcp_tool overwrite
    lazy_out = run_hook(
        plugin_dir,
        db_path,
        {
            "hookEventName": "PreToolUse",
            "conversationId": "DEMO-902A",
            "toolCall": {
                "id": "call_2",
                "name": "call_mcp_tool",
                "args": {"ServerName": "refspace", "ToolName": "refspace_scope_status", "Arguments": {}},
            },
        },
    )
    assert lazy_out["overwrite"]["Arguments"]["session_id"] == "DEMO-902A"

    # 2. Launch real Stdio MCP subprocess via plugins/refspace/run_mcp.py and execute multi-window flow
    proc = subprocess.Popen(
        [sys.executable, str(plugin_dir / "run_mcp.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )

    def rpc(req_id: int, method: str, params: dict) -> dict:
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        return json.loads(line)

    try:
        init_resp = rpc(1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "antigravity-test", "version": "1.0"}})
        assert init_resp["result"]["serverInfo"]["name"] == "refspace"

        tools_resp = rpc(2, "tools/list", {})
        tool_names = [t["name"] for t in tools_resp["result"]["tools"]]
        assert tool_names == [
            "refspace_bind_scope",
            "refspace_scope_status",
            "refspace_locate",
            "refspace_read_doc",
            "refspace_query_table",
        ]

        # Register two spaces + bind Window A (DEMO-7176 -> 杭州仓) and Window B (DEMO-902A -> 客户合同库)
        r_a = rpc(
            3,
            "tools/call",
            {
                "name": "refspace_bind_scope",
                "arguments": {
                    "action": "register_space",
                    "register_name": "运营资料",
                    "register_aliases": ["杭州仓"],
                    "register_paths": [env_paths["hangzhou_dir"]],
                    "session_id": "DEMO-7176",
                },
            },
        )
        data_a = json.loads(r_a["result"]["content"][0]["text"])
        assert data_a["ok"] is True
        assert data_a["session"]["default_spaces"][0]["name"] == "运营资料"

        r_b = rpc(
            4,
            "tools/call",
            {
                "name": "refspace_bind_scope",
                "arguments": {
                    "action": "register_space",
                    "register_name": "客户合同库",
                    "register_aliases": ["合同库"],
                    "register_paths": [env_paths["contract_dir"]],
                    "session_id": "DEMO-902A",
                },
            },
        )
        data_b = json.loads(r_b["result"]["content"][0]["text"])
        assert data_b["ok"] is True

        # Verify Window A and Window B are completely isolated!
        loc_a = json.loads(rpc(5, "tools/call", {"name": "refspace_locate", "arguments": {"session_id": "DEMO-7176"}})["result"]["content"][0]["text"])
        loc_b = json.loads(rpc(6, "tools/call", {"name": "refspace_locate", "arguments": {"session_id": "DEMO-902A"}})["result"]["content"][0]["text"])
        names_a = {f["file_name"] for f in loc_a["files"]}
        names_b = {f["file_name"] for f in loc_b["files"]}
        assert names_a == {"11月出库与库存明细.xlsx", "补货判断规则.md", "仓储结算与赔付规则.docx"}
        assert names_b == {"供应商框架协议_示例.pdf"}
        assert "secret.md" not in names_a and "escape_link.md" not in names_a

        # Verify PreInvocation injects active scope ONLY for bound conversations
        pre_inv_a = run_hook(plugin_dir, db_path, {"hookEventName": "PreInvocation", "conversationId": "DEMO-7176"})
        assert "运营资料" in pre_inv_a["injectSteps"][0]["systemMessage"]["systemMessage"]
        pre_inv_unbound = run_hook(plugin_dir, db_path, {"hookEventName": "PreInvocation", "conversationId": "UNBOUND-999"})
        assert pre_inv_unbound == {}

        # Verify 'once' ('这次只用合同库') in Window A reads 合同库 for this turn, then Stop hook auto-restores 杭州仓!
        once_res = json.loads(
            rpc(7, "tools/call", {"name": "refspace_bind_scope", "arguments": {"action": "once", "spaces": ["合同库"], "session_id": "DEMO-7176"}})["result"]["content"][0]["text"]
        )
        assert once_res["session"]["mode"] == "once_override"
        assert once_res["session"]["effective_spaces"][0]["name"] == "客户合同库"
        assert once_res["session"]["default_spaces"][0]["name"] == "运营资料"

        pdf_read = json.loads(
            rpc(8, "tools/call", {"name": "refspace_read_doc", "arguments": {"file": "供应商框架协议_示例.pdf", "query": "月结", "session_id": "DEMO-7176"}})["result"]["content"][0]["text"]
        )
        assert pdf_read["ok"] is True
        assert "月结 30 天" in pdf_read["passages"][0]["untrusted_data"]

        # Turn ends -> Antigravity fires Stop hook -> auto-clears temp override!
        stop_out = run_hook(plugin_dir, db_path, {"hookEventName": "Stop", "conversationId": "DEMO-7176", "continuationActive": False})
        assert stop_out["decision"] == "stop"

        after_stop = json.loads(
            rpc(9, "tools/call", {"name": "refspace_scope_status", "arguments": {"session_id": "DEMO-7176"}})["result"]["content"][0]["text"]
        )
        assert after_stop["session"]["mode"] == "persistent_default"
        assert [s["name"] for s in after_stop["session"]["effective_spaces"]] == ["运营资料"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_stage2_and_3_registry_sandbox_and_transitions(tmp_root: Path, env_paths: dict, db_path: str) -> None:
    disp = RefSpaceMCPDispatcher(db_path=db_path)
    reg = disp.registry

    # Sandbox blocks outside path and '../' traversal
    try:
        verify_within_allowed_roots(env_paths["outside_secret"], [env_paths["hangzhou_dir"]])
        raise AssertionError("Should have blocked outside path")
    except SandboxViolationError:
        pass

    # Register 3rd space '库存资料' + test 'add' and 'remove'
    reg.create_space(name="库存资料", aliases=["库存表"], source_paths=[env_paths["inventory_dir"]])
    add_res = disp.call_tool("refspace_bind_scope", {"action": "add", "spaces": ["库存表"], "session_id": "DEMO-7176"})
    assert [s["name"] for s in add_res["session"]["default_spaces"]] == ["运营资料", "库存资料"]

    rem_res = disp.call_tool("refspace_bind_scope", {"action": "remove", "spaces": ["库存资料"], "session_id": "DEMO-7176"})
    assert [s["name"] for s in rem_res["session"]["default_spaces"]] == ["运营资料"]

    # Rename '运营资料' -> '华东运营中心': immutable space_id keeps DEMO-7176 bound!
    hz_sp = reg.resolve_space_query("杭州仓")
    reg.update_space(hz_sp["space_id"], name="华东运营中心", aliases=["杭州仓", "萧山仓"])
    st_after_rename = disp.call_tool("refspace_scope_status", {"session_id": "DEMO-7176"})
    assert st_after_rename["session"]["default_spaces"][0]["name"] == "华东运营中心"

    # Ambiguity protection: create '客户访谈库' so query '客户' matches both '客户合同库' and '客户访谈库'
    reg.create_space(name="客户访谈库", aliases=[], source_paths=[env_paths["inventory_dir"]])
    amb_res = disp.call_tool("refspace_bind_scope", {"action": "replace", "spaces": ["客户"], "session_id": "DEMO-7176"})
    assert amb_res["ok"] is False and amb_res["error_code"] == "AMBIGUOUS_SPACE"
    # Previous scope MUST remain intact!
    assert amb_res["preserved_session"]["default_spaces"][0]["name"] == "华东运营中心"

    # Moved folder detection + relink recovery
    moved_dir = Path(env_paths["inventory_dir"]).parent / "inventory_moved"
    shutil.move(env_paths["inventory_dir"], moved_dir)
    inv_sp = reg.resolve_space_query("库存资料")
    broken_bind = disp.call_tool("refspace_bind_scope", {"action": "replace", "spaces": ["库存资料"], "session_id": "DEMO-7176"})
    assert broken_bind["ok"] is False and broken_bind["error_code"] == "SOURCE_UNREADABLE"
    assert broken_bind["preserved_session"]["default_spaces"][0]["name"] == "华东运营中心"

    # Relink moved folder -> space works again!
    src_id = inv_sp["sources"][0]["source_id"]
    reg.relink_source(src_id, str(moved_dir))
    relinked_bind = disp.call_tool("refspace_bind_scope", {"action": "add", "spaces": ["库存资料"], "session_id": "DEMO-7176"})
    assert relinked_bind["ok"] is True
    disp.call_tool("refspace_bind_scope", {"action": "remove", "spaces": ["库存资料"], "session_id": "DEMO-7176"})


def test_stage4_dual_track_fidelity_and_live_update(env_paths: dict, db_path: str) -> None:
    disp = RefSpaceMCPDispatcher(db_path=db_path)

    # 1. Document track (.md + .docx) & prompt-injection immunity
    md_res = disp.call_tool("refspace_read_doc", {"file": "补货判断规则.md", "query": "安全库存", "session_id": "DEMO-7176"})
    assert md_res["ok"] is True
    assert "低于 20 件" in md_res["passages"][0]["untrusted_data"]
    assert md_res["citations"][0]["file"] == "补货判断规则.md"

    docx_res = disp.call_tool("refspace_read_doc", {"file": "仓储结算与赔付规则.docx", "query": "破损", "session_id": "DEMO-7176"})
    assert docx_res["ok"] is True
    assert "2000 元" in docx_res["passages"][0]["untrusted_data"]

    # Reading injection text inside MD does NOT alter session scope
    disp.call_tool("refspace_read_doc", {"file": "补货判断规则.md", "query": "换成客户合同库", "session_id": "DEMO-7176"})
    st = disp.call_tool("refspace_scope_status", {"session_id": "DEMO-7176"})
    assert st["session"]["default_spaces"][0]["name"] == "华东运营中心"

    # 2. Spreadsheet track (.xlsx) fidelity checks
    insp = disp.call_tool("refspace_query_table", {"file": "11月出库与库存明细.xlsx", "mode": "inspect", "session_id": "DEMO-7176"})
    assert insp["ok"] is True
    sh1 = insp["sheets"][0]
    # Ignored 1,048,576 empty rows -> exact 3 records in Region 1, and side-by-side summary separated into Region 2!
    assert sh1["region_count"] == 2
    assert sh1["regions"][0]["record_count"] == 3
    assert sh1["regions"][0]["empty_fields"] == ["预留空列"]
    assert sh1["regions"][1]["record_count"] == 1
    # Uncached formula warning in Sheet 2
    assert any("缺少缓存计算值" in w for w in insp["warnings"])

    rows_res = disp.call_tool("refspace_query_table", {"file": "11月出库与库存明细.xlsx", "mode": "rows", "sheet_name": "出库明细", "session_id": "DEMO-7176"})
    recs = rows_res["records"]
    # Leading zero segmented format: 7 -> '00007'
    assert recs[0]["仓库编码"] == "00007"
    # 18-digit SKU preserved as string with leading zeros
    assert recs[0]["SKU"] == "009876543210987654"
    # Merged cell C2:C3 ('供应商甲') expanded to row 3, while genuine blank J3 ('备注') stays None!
    assert recs[1]["供应商"] == "供应商甲"
    assert recs[1]["备注"] is None
    # Numeric 0 and boolean False preserved!
    assert recs[0]["破损数"] == 0
    assert recs[0]["是否加急"] is False
    # Localized numFmtId=58 date formatted
    assert recs[0]["发运日期"].startswith("2026-")

    # Split-box deduplication: raw sum(应发单据数) would be 10+10+15=35, but dedup_by=['ERP单号','SKU'] gives 10+15=25 == sum(批次实发数)!
    agg_dedup = disp.call_tool(
        "refspace_query_table",
        {
            "file": "11月出库与库存明细.xlsx",
            "mode": "aggregate",
            "sheet_name": "出库明细",
            "dedup_by": ["ERP单号", "SKU"],
            "metrics": [{"op": "sum", "field": "应发单据数", "as": "去重应发总数"}],
            "session_id": "DEMO-7176",
        },
    )
    assert agg_dedup["groups"][0]["去重应发总数"] == 25

    agg_supplier = disp.call_tool(
        "refspace_query_table",
        {
            "file": "11月出库与库存明细.xlsx",
            "mode": "aggregate",
            "sheet_name": "出库明细",
            "group_by": ["供应商"],
            "metrics": [{"op": "sum", "field": "批次实发数", "as": "实发合计"}],
            "sort_by": "实发合计",
            "sort_desc": True,
            "session_id": "DEMO-7176",
        },
    )
    by_sup = {g["供应商"]: g["实发合计"] for g in agg_supplier["groups"]}
    assert by_sup == {"供应商乙": 15, "供应商甲": 10}

    # 3. Modify original Excel on disk -> next query automatically detects update and computes fresh sum!
    time.sleep(0.02)
    build_sample_xlsx(Path(env_paths["xlsx_path"]), extra_outbound_qty=8)
    agg_updated = disp.call_tool(
        "refspace_query_table",
        {
            "file": "11月出库与库存明细.xlsx",
            "mode": "aggregate",
            "sheet_name": "出库明细",
            "metrics": [{"op": "sum", "field": "批次实发数", "as": "最新总实发"}],
            "session_id": "DEMO-7176",
        },
    )
    assert agg_updated["groups"][0]["最新总实发"] == 33

    # 4. Health check & provenance log verification
    health = build_health_report(disp.registry, disp.session_mgr)
    assert health["service"]["running"] is True
    assert health["antigravity"]["connected"] is True
    assert health["antigravity"]["active_session_count"] == 2
    assert len(disp.registry.list_recent_provenance()) >= 4


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="refspace_test_") as tmp:
        tmp_root = Path(tmp)
        db_path = str(tmp_root / "test_refspace.db")
        env_paths = create_demo_environment(tmp_root / "fixtures")

        test_stage1_antigravity_mcp_and_hooks(tmp_root, env_paths, db_path)
        print("✓ 阶段 1 通过：Antigravity MCP 协议、PreInvocation/PreToolUse/Stop 钩子、双窗口隔离与单次引用自动回滚")

        test_stage2_and_3_registry_sandbox_and_transitions(tmp_root, env_paths, db_path)
        print("✓ 阶段 2 & 3 通过：只读路径沙箱、软链防逃逸、固定编号改名保留、歧义/失联回滚保护与路径重关联")

        test_stage4_dual_track_fidelity_and_live_update(env_paths, db_path)
        print("✓ 阶段 4 & 5 通过：MD/DOCX/PDF/XLSX/CSV 双轨提取、合并格/前导零/0/false/公式告警保真、原件修改即时重读与真实健康检测")

    print("ALL REFSPACE AUTOMATED CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
