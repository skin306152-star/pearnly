# -*- coding: utf-8 -*-
"""实机验证（开发工具，不进安装包）：在 rec-laptop 上用正式代录包 ``companion.express_operator`` 录一批，不调 AI。

只录试点账套（服务器版 69EXP 那套 Express 的 69EXP\\test，主会话 10-26 指定），别的路径一律拒绝。Express 要先由用户本人
打开、登录、进试点公司（自动登录的登录窗口样子还没核过，见配方 ``login.verified``），本脚本只接管已开着的 Express；
跑完正常退出 Express（释放授权名额）。要在桌面已登录用户的交互会话里跑（``lab_task.ps1`` 建一次性计划任务），SSH 会话
发不了键。

    python lab_run.py --src C:\\XpPilot\\companion_src --drafts C:\\XpPilot\\lab\\drafts.jsonl --out C:\\XpPilot\\lab\\run1
        [--limit 8] [--types RR,HP,IV,HS] [--probe-only] [--keep-open]

输出：<out>\\batch.json（批次落盘）、<out>\\snapshot.json（最后一次进度）、<out>\\evidence\\（存档截图）、<out>\\log.txt。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

BOOKS = {r"\\accserver\account\69exp\test", r"\\accserver\d$\account\69exp\test",
         r"\\accserver\account\69exp\test2", r"\\accserver\d$\account\69exp\test2"}  # TEST2：打开「按商品记增值税」的试点账套（主会话 10-29 授权）；测试机桌面快捷方式走 d$
COMPANY = "บริษัท Pearnly จำกัด"  # 状态栏里的公司名（逐字核对）


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--drafts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--book", default=r"\\accserver\ACCOUNT\69EXP\test")
    ap.add_argument("--express-exe", default=r"\\accserver\d$\ACCOUNT\69EXP\ExpressI.exe",
                    help="只认这套程序开着的 Express：登录、选公司前核程序文件夹（10-29 进错 70EXP 之后加）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--types", default="RR,HP,IV,HS")
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--background", action="store_true", help="不碰真键盘鼠标，直接给 Express 发消息")
    ap.add_argument("--keep-precheck-issues", action="store_true", help="实验用：预检有问题的单也照录（测放弃与作废闸）")
    ap.add_argument("--front-notepad", action="store_true", help="实验用：开一个记事本放在最前面，看 Express 在后面能不能录")
    args = ap.parse_args(argv)
    if os.path.normpath(args.book).lower().rstrip("\\") not in BOOKS:
        print("refused: not the pilot book")
        return 2
    sys.path.insert(0, args.src)
    from companion.express_operator import batch as BT
    from companion.express_operator import recipe as R
    from companion.express_operator.book import DbfBook
    from companion.express_operator.draft import Draft
    from companion.express_operator.engine import Engine
    from companion.express_operator.evidence import Evidence, prune
    from companion.express_operator.precheck import account_period, check
    from companion.express_operator.presence import Presence, WindowsSignals
    from companion.express_operator.probe import probe_form
    from companion.express_operator.session import Session
    from companion.express_operator.win32_desk import WindowsDesk

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log_file = (out / "log.txt").open("a", encoding="utf-8")

    def log(message: str) -> None:
        log_file.write(f"{time.strftime('%H:%M:%S')} {message}\n")
        log_file.flush()

    fields = {f.name for f in dataclasses.fields(Draft)}
    types = set(args.types.split(","))
    drafts = []
    for line in Path(args.drafts).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            raw = json.loads(line)
            if raw["doc_type"] in types:
                drafts.append(Draft(**{k: v for k, v in raw.items() if k in fields}))
    if args.limit:
        drafts = drafts[:args.limit]
    recipe = R.load()
    notepad = None
    if args.front_notepad:
        import subprocess
        notepad = subprocess.Popen(["notepad.exe"])
        time.sleep(2)
    if args.background:
        from companion.express_operator.bg_desk import BackgroundDesk
        desk = BackgroundDesk()
    else:
        desk = WindowsDesk()
    cursor_before = desk.cursor()
    fg_before = WindowsDesk.foreground(desk)
    book = DbfBook(args.book)
    open_exes = {w.get("exe", "") for w in desk.windows(any_instance=True)}
    if not open_exes:
        print("refused: Express is not open (the user opens and logs in)")  # 本脚本不启动 Express
        return 2
    session = Session(desk, recipe, express_exe=args.express_exe, account_dir=args.book, company_name=COMPANY,
                      credential_target="lab")
    state = BT.BatchState.new(COMPANY, "TEST", args.book, drafts)
    store = BT.BatchStore(out)
    issues = check(drafts, book, recipe, period=account_period(args.book))
    for d in state.docs:
        d.issues = issues.get(d.doc_id, [])
        if not args.keep_precheck_issues and any(i["reason"] != "duplicate_ref" for i in d.issues):
            d.status, d.reason = "skipped", d.issues[0]["reason"]  # 预检没过的不录（实机只验录入这一步）
    log(f"drafts={len(drafts)} precheck={json.dumps(issues, ensure_ascii=False)}")
    holder = {}
    presence = Presence(WindowsSignals(), lambda: desk.last_input_tick,
                        stop_requested=lambda: holder["runner"].stop_requested(),
                        on_pause=lambda r: holder["runner"].presence_paused(r),
                        on_resume=lambda: holder["runner"].presence_resumed(),
                        background=args.background, foreground=lambda: WindowsDesk.foreground(desk))
    evidence = Evidence(out / "evidence", state.id)
    prune(out / "evidence")  # CC 启动时也会扫一次（接线在共用件那一步）
    engine = Engine(desk, recipe, book, presence=presence, evidence=evidence.save, log=log)
    original_run = engine.run

    def timed_run(draft):
        rec = original_run(draft)
        log(f"doc {draft.doc_id} {rec['outcome']} {rec.get('docnum') or rec.get('reason')} {rec.get('seconds')}s "
            f"timing={json.dumps(rec.get('timing', {}), ensure_ascii=True)} layers={json.dumps(rec.get('layers', {}))}")
        return rec

    engine.run = timed_run

    def probe(doc_type):
        result = probe_form(engine, doc_type)
        log(f"probe {doc_type} {json.dumps(result, ensure_ascii=False)}")
        return result

    if args.probe_only:
        session.ensure_open()
        for doc_type in sorted(types):
            probe(doc_type)
        return 0
    if args.keep_open:
        session.close = lambda: None
    runner = BT.Runner(state, store, engine, session, book, probe=probe,
                       housekeeping=lambda: prune(out / "evidence"), evidence_state=evidence.state,
                       pauses=lambda: presence.evidence,
                       on_change=lambda snap: (out / "snapshot.json").write_text(
                           json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8"))
    holder["runner"] = runner
    final = runner.run()
    fg_after = WindowsDesk.foreground(desk)
    log(f"pauses={json.dumps(presence.evidence, ensure_ascii=True)}")
    log(f"real mouse moved={desk.cursor() != cursor_before} foreground before={fg_before.get('exe')} after={fg_after.get('exe')} "
        f"messages={getattr(desk, 'sent', 0)}")
    if notepad is not None:
        notepad.terminate()
    log(f"status={final.status} reason={final.reason} "
        + " ".join(f"{d.doc_id}:{d.status}:{d.docnum or d.reason}" for d in final.docs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
