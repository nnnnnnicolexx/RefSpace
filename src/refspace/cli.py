from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .mcp_server import run_stdio_server
from .registry import RefSpaceRegistry, RegistryError
from .server import build_health_report, create_http_server
from .session import SessionScopeManager


def install_global_antigravity_plugin(target_root: Path | None = None) -> dict:
    repo_root = Path(__file__).resolve().parent.parent.parent
    src_plugin = repo_root / "plugins" / "refspace"
    src_pkg = repo_root / "src" / "refspace"
    dest_plugin = (target_root or (Path.home() / ".gemini" / "config" / "plugins")) / "refspace"

    dest_plugin.parent.mkdir(parents=True, exist_ok=True)
    if dest_plugin.exists():
        shutil.rmtree(dest_plugin)
    shutil.copytree(src_plugin, dest_plugin)
    vendor_dir = dest_plugin / "vendor" / "refspace"
    vendor_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_pkg, vendor_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return {"installed_to": str(dest_plugin), "dir": dest_plugin.name}


def seed_demo_spaces(db_path: str | None = None) -> dict:
    repo_root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(repo_root / "tests"))
    from fixture_builder import create_demo_environment

    demo_root = repo_root / ".refspace" / "demo-files"
    paths = create_demo_environment(demo_root)
    reg = RefSpaceRegistry(db_path=db_path)

    created = []
    hz_sources = [
        paths["hangzhou_dir"],
        str(Path(paths["hangzhou_dir"]) / "11月出库与库存明细.xlsx"),
        str(Path(paths["hangzhou_dir"]) / "仓储结算与赔付规则.docx"),
        str(Path(paths["hangzhou_dir"]) / "补货判断规则.md"),
    ]
    for name, aliases, src_list in [
        ("运营资料", ["杭州仓"], hz_sources),
        ("客户合同库", ["合同库"], [paths["contract_dir"], str(Path(paths["contract_dir"]) / "供应商框架协议_示例.pdf")]),
        ("库存资料", ["库存表"], [paths["inventory_dir"], str(Path(paths["inventory_dir"]) / "实时库存快照.csv")]),
    ]:
        try:
            sp = reg.create_space(name=name, aliases=aliases, source_paths=src_list)
            created.append(sp["name"])
        except RegistryError:
            existing = reg.resolve_space_query(name)
            for p in src_list:
                reg.add_source_to_space(existing["space_id"], p)
    return {"demo_files_dir": str(demo_root), "seeded_spaces": created, "db_path": reg.db_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="refspace", description="引知 RefSpace 本地资料引用中心命令行入口")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mcp = sub.add_parser("mcp", help="以 Stdio 模式启动 MCP 服务")
    p_mcp.add_argument("--db", default=None, help="自定义 SQLite 路径")

    p_serve = sub.add_parser("serve", help="启动本地 HTML 管理端与 Streamable HTTP MCP 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=17890)
    p_serve.add_argument("--db", default=None)

    p_health = sub.add_parser("health", help="输出本机服务、来源可读性与 Antigravity 接入检测结果")
    p_health.add_argument("--db", default=None)

    p_inst = sub.add_parser("install-plugin", help="将 RefSpace 插件同步安装到本机全局 ~/.gemini/config/plugins/refspace")
    p_inst.add_argument("--target", default=None)

    p_seed = sub.add_parser("seed-demo", help="生成虚构多格式演示资料并登记示例资料空间（保存在已 Git 忽略的 .refspace/ 下）")
    p_seed.add_argument("--db", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "mcp":
        run_stdio_server(db_path=args.db)
        return 0

    if args.cmd == "health":
        reg = RefSpaceRegistry(db_path=args.db)
        sm = SessionScopeManager(reg)
        print(json.dumps(build_health_report(reg, sm), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "install-plugin":
        t_root = Path(args.target).expanduser().resolve() if args.target else None
        res = install_global_antigravity_plugin(t_root)
        print(json.dumps({"ok": True, **res}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "seed-demo":
        res = seed_demo_spaces(db_path=args.db)
        print(json.dumps({"ok": True, **res}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "serve":
        srv = create_http_server(host=args.host, port=args.port, db_path=args.db)
        print(f"引知 RefSpace 管理界面已启动：http://{args.host}:{args.port} （MCP 端点：http://{args.host}:{args.port}/mcp）", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
