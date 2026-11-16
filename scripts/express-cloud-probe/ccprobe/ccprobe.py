# -*- coding: utf-8 -*-
"""ccprobe —— 云端 Express 实测 T6 的常驻只读探针。

用 CC 桌面版同一套 PyInstaller（版本照 companion/packaging/requirements-build.txt）打成没签名的 onedir 程序，在云端常驻：
启动时跑一次 CC 的 `express_locate.candidate_roots()` + `detect_express_root()`（同云端 CC 找账套的真实做法），
之后每分钟只读一次 `<root>/secure/SCCOMP.DBF` 的公司数，结果写 `<out>/cc-<n>-<UTC>.json`，跑满 --minutes 自己退出。
只读：dbf 以 READ_ONLY 打开；配置、日志目录由 APPDATA 指到探针目录（express_locate 导入 config 时会建 Pearnly\\logs）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write(dirs: list[Path], name: str, obj: dict) -> dict:
    """先本地后隧道；隧道写不进去不算失败（断线时本地仍有记录）。"""
    text = json.dumps(obj, ensure_ascii=False)
    ok = {}
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / (name + ".part")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, d / name)
            ok[str(d)] = True
        except OSError as exc:
            ok[str(d)] = f"{type(exc).__name__}"
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Express 数据根（含 secure\\SCCOMP.DBF）")
    ap.add_argument("--out", required=True, help="结果目录（隧道 out）")
    ap.add_argument("--local", default="", help="本地副本目录")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--no-locate", action="store_true", help="不跑全盘定位（本机自检用）")
    args = ap.parse_args(argv)

    home = os.environ.get("APPDATA") or ""
    if not home:  # 没被探针重定向时也别写进真用户目录
        os.environ["APPDATA"] = str(Path(args.local or ".") / "ccprobe-home")

    from src.companion import express_locate  # noqa: E402  导入即建 APPDATA\\Pearnly\\logs

    dirs = [Path(args.out)] + ([Path(args.local)] if args.local else [])
    start = time.monotonic()
    head = {"utc": _utc(), "pid": os.getpid(), "exe": sys.executable, "frozen": bool(getattr(sys, "frozen", False)),
            "python": sys.version.split()[0], "root": args.root, "minutes": args.minutes}
    if not args.no_locate:
        t0 = time.monotonic()
        try:
            cands = express_locate.candidate_roots()
            best = express_locate.detect_express_root(cands)
            head["locate"] = {"seconds": round(time.monotonic() - t0, 1), "candidates": len(cands),
                              "best_is_root": bool(best) and best.lower().rstrip("\\/") == args.root.lower().rstrip("\\/"),
                              "best_count": express_locate.sccomp_company_count(best) if best else 0}
        except Exception as exc:  # 探针只记，不中断常驻
            head["locate"] = {"error": f"{type(exc).__name__}: {exc}"}
    _write(dirs, f"cc-start-{_utc()}.json", head)

    n = 0
    deadline = start + args.minutes * 60
    while True:
        n += 1
        t0 = time.monotonic()
        count = express_locate.sccomp_company_count(args.root)
        rec = {"n": n, "utc": _utc(), "count": count, "ms": round((time.monotonic() - t0) * 1000)}
        rec["written"] = _write(dirs, f"cc-{n:03d}-{rec['utc']}.json", rec)
        nxt = start + n * args.interval
        if nxt >= deadline:
            break
        time.sleep(max(0.0, nxt - time.monotonic()))
    _write(dirs, f"cc-done-{_utc()}.json", {"utc": _utc(), "ticks": n, "minutes": round((time.monotonic() - start) / 60, 1)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
