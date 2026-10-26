"""试点执行入口（在本机跑；真跑时经 ssh 端口转发连测试机代理）。

    cd scripts/express-operator
    ~/pearnly-devtools/xp-pilot/venv/bin/python -m pilot.run_pilot --mode haiku_fixed --drafts drafts.jsonl --limit 4
    ……加 --fake 用离线模拟器（不连测试机、不花钱）

退出码：0 做完；5 公司没确认；10 满 10 美元要先报主会话（报完带 --ack 再跑）；20 要人处理；30 Express 崩了 / 网络报错；
40 到预算上限。已做过的单（结果文件里有）自动跳过，停下后原样再跑就从断点接着录。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

from . import draft as D
from . import engine as E
from .ledger import DEFAULT_PATH, HARD_CAP, Ledger

HERE = Path(__file__).resolve().parent
BOOK = r"\\accserver\d$\account\69EXP\test"
RUNS = Path.home() / "pearnly-devtools" / "xp-pilot" / "runs"
KEY_FILES = [Path.home() / ".config" / "pearnly-claude-ocr" / "runtime.env",
             Path.home() / ".config" / "pearnly-claude-ocr" / "runtime.env.bak-20261022-124749"]
KEY_VAR = "PEARNLY_ANTHROPIC_API_KEY"
KEY_SUFFIX = "6qaa"  # 用户指定的那把（尾号不分大小写）
COMPANY_MARK = r"69EXP\TEST"


def load_key() -> str:
    """只从本机已存的配置里找尾号 6qaa 的那把；不打印、不写盘。"""
    for path in KEY_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(KEY_VAR + "="):
                value = line.split("=", 1)[1].strip().strip("\"'")
                if value.lower().endswith(KEY_SUFFIX):
                    return value
    raise SystemExit("本机配置里没找到尾号 6qaa 的密钥：问主会话")


def done_ids(results: Path) -> set:
    if not results.exists():
        return set()
    return {json.loads(line)["doc_id"] for line in results.read_text(encoding="utf-8").splitlines() if line.strip()}


def preflight(agent, out: Path, confirmed: bool) -> None:
    """开跑前：代理在、前台能拉到 Express、打开的是试点公司（读得到状态栏文字就核文字，读不到就要人看截图确认）。"""
    import base64
    reply = agent.act([{"do": "ping"}, {"do": "activate"}, {"do": "wait", "ms": 400},
                       {"do": "screenshot", "size": [1280, 720]}])
    shot = reply["results"][3]
    (out / "preflight.png").write_bytes(base64.b64decode(shot["png"]))
    texts = " ".join(" ".join([w["title"]] + w["texts"]) for w in reply.get("windows", [])).upper()
    if COMPANY_MARK in texts:
        print(f"公司核对：窗口文字里有 {COMPANY_MARK}")
        return
    if not confirmed:
        print(f"窗口文字里读不到 {COMPANY_MARK}：先看 {out / 'preflight.png'} 确认是「บริษัท Pearnly จำกัด」"
              "（69EXP\\test），再带 --company-confirmed 跑")
        raise SystemExit(5)
    print("公司核对：已由人看截图确认")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True, choices=sorted(E.MODES))
    p.add_argument("--drafts", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--types", default="", help="只跑这些单种，逗号分隔，如 RR,IV")
    p.add_argument("--run", default="", help="结果目录名（默认 模式-日期）")
    p.add_argument("--book", default=BOOK)
    p.add_argument("--presave", default="auto", choices=["auto", "always", "never"],
                   help="保存前截图给 AI 核对：auto = 本张有格子读不到才看；always / never")
    p.add_argument("--ack", action="store_true", help="主会话已收到满 10 美元的报告，继续")
    p.add_argument("--hard-cap", default=str(HARD_CAP))
    p.add_argument("--company-confirmed", action="store_true")
    p.add_argument("--fake", action="store_true", help="离线模拟器 + 假模型（不连测试机、不花钱）")
    p.add_argument("--fake-hidden-dialogs", action="store_true")
    p.add_argument("--fake-readable", default="win32", choices=["win32", "uia", "none"])
    p.add_argument("--ledger", default="")
    p.add_argument("--probe", default="", help="只探测不录：存 Win32 / pywinauto win32 / uia 三种读法在当前画面读到的东西，参数是名字")
    args = p.parse_args(argv)

    flows = json.loads((HERE / "flows.json").read_text(encoding="utf-8"))
    drafts = D.load(Path(args.drafts)) if args.drafts else D.samples()
    if args.types:
        keep = set(args.types.upper().split(","))
        drafts = [d for d in drafts if d.doc_type in keep]
    order = {t: i for i, t in enumerate(("RR", "HP", "IV", "HS"))}
    drafts.sort(key=lambda d: order[d.doc_type])  # 同种单连着录，单据窗口只开一次
    if args.limit:
        drafts = drafts[:args.limit]
    run_dir = (RUNS.parent / "fake_runs" if args.fake else RUNS) / (args.run or f"{args.mode}-{time.strftime('%m%d')}")
    run_dir.mkdir(parents=True, exist_ok=True)
    results = run_dir / "results.jsonl"
    ledger_path = Path(args.ledger) if args.ledger else (run_dir / "ledger.jsonl" if args.fake else DEFAULT_PATH)
    ledger = Ledger(ledger_path, hard_cap=Decimal(args.hard_cap))
    if args.ack:
        ledger.ack()
    pending = ledger.pending_milestone()
    if pending:
        print(f"累计已花 {ledger.total} 美元（过了 {pending} 美元）：先报主会话，再带 --ack 继续")
        return 10

    if args.fake:
        from .fake_express import FakeClaude, FakeExpress
        agent = FakeExpress(flows, dialogs_visible=not args.fake_hidden_dialogs, readable=args.fake_readable)
        claude = FakeClaude(agent)
        return _loop(args, flows, drafts, agent, claude, ledger, run_dir, results)

    from .agent_client import AgentClient, Tunnel, remote_token
    from .models import Claude
    if args.probe:
        with Tunnel():
            agent = AgentClient(remote_token(), timeout=180)
            reply = agent.act([{"do": "activate"}, {"do": "wait", "ms": 500}, {"do": "probe"}, {"do": "state"}])
        out = RUNS / "probe"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.probe}.json").write_text(json.dumps(reply, ensure_ascii=False, indent=1), encoding="utf-8")
        trees = reply["results"][2].get("trees", {})
        for name, t in trees.items():
            print(name, {k: t.get(k) for k in ("ms", "count", "with_text", "error")})
        print("焦点（Win32 / UIA）：", reply["results"][3].get("focus"), reply["results"][3].get("uia"))
        return 0
    claude = Claude(load_key())
    with Tunnel():
        agent = AgentClient(remote_token())
        preflight(agent, run_dir, args.company_confirmed)
        return _loop(args, flows, drafts, agent, claude, ledger, run_dir, results)


def _loop(args, flows, drafts, agent, claude, ledger, run_dir, results) -> int:
    eng = E.Engine(agent, claude, ledger, flows, args.book, args.mode, run_dir, presave_check=args.presave)
    skip = done_ids(results)
    codes = {"need_human": 20, "crash": 30, "budget": 40}
    for d in drafts:
        if d.doc_id in skip:
            continue
        if hasattr(claude, "draft"):
            claude.draft = d
        if hasattr(agent, "current_doc_id"):
            agent.current_doc_id = d.doc_id
        rec = eng.run(d)
        with results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        mark = "✓" if rec["readback_ok"] else "✗"
        print(f"{mark} {d.doc_id} {d.doc_type} {rec['outcome']} {rec['seconds']}s 代录 ${rec['usd']} 合计 ${rec['usd_total']} "
              f"截图给AI {rec['shots_ai']} 接手 {len(rec['fallbacks'])} 单号 {rec['docnum'] or '-'}"
              + (f" 对答案 {'✓' if rec['answer_ok'] else '✗'}" if 'answer_ok' in rec else "")
              + (f" 卡在 {rec['stuck_at']}" if rec["stuck_at"] else "")
              + (f" 差异 {rec['diffs']}" if rec["diffs"] else ""))
        if rec["outcome"] in codes:
            print(f"停下：{rec['outcome']}（累计 {ledger.total} 美元）")
            return codes[rec["outcome"]]
        if ledger.pending_milestone():
            print(f"累计已花 {ledger.total} 美元：先报主会话，再带 --ack 继续")
            return 10
    print(f"做完，累计 {ledger.total} 美元；结果 {results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
