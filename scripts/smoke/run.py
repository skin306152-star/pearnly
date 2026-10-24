#!/usr/bin/env python3
"""按登记表跑自动冒烟（共用件连带回归闸门，docs/development/verification.md）。

    # 实验栈（测试机 testenv，候选源码已 live）：命中的共用件 → 全部 auto 入口
    python3 scripts/smoke/run.py --diff
    python3 scripts/smoke/run.py --part desk-scan-core --part desk-shell
    python3 scripts/smoke/run.py --entry erp.page.products --entry erp.products.new-dialog.scan
    # 上线后：线上测试租户只读子集（页面、扫码，不写单、不发 LINE）
    python3 scripts/smoke/run.py --target production --readonly --all

探针：page / scan 由本机无头 Chrome（scripts/smoke/drive.mjs，假摄像头放 make_barcode_video.py 的测试条码）打开测试租户跑；
line / express 在站点网页容器里跑（scripts/smoke/line_flow.py、express_flow.py，出站 LINE 一律截住、写 Express 只写测试机 C 盘 T 账套）。
会话：在网页容器里给测试账号建一次（session.py），跑完删掉；不碰 Keycloak 密码。
结果按「通过 / 不通过 / 没测到（原因）」出 Markdown（--out），贴进任务记录。标 manual 的入口列在「没测到（人工）」里交验收窗口。

环境变量（都有默认值，按测试机现状）：
  SMOKE_CHROME            本机 Chrome 路径
  SMOKE_LAB_HOST          测试机 SSH 别名（lenovo-office-lan）；SMOKE_LAB_TEMP Windows 临时目录；SMOKE_LAB_WSL 发行版名
  SMOKE_PROD_HOST         生产 SSH 别名（pearnly），只在 --target production 用
  SMOKE_USER_ERP / _COWORK / _DMS   各测试租户冒烟用的账号（默认 Administrator）
  SMOKE_EXPRESS_USER      测试机 CC 上登录的 ERP 员工（Express 写入冒烟用；入口 args 里写了 user 时以它为准）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
spec = importlib.util.spec_from_file_location("check_impact", ROOT / "scripts" / "check-impact.py")
impact = importlib.util.module_from_spec(spec)
sys.modules["check_impact"] = impact
spec.loader.exec_module(impact)
video_spec = importlib.util.spec_from_file_location("make_barcode_video", HERE / "make_barcode_video.py")
barcode = importlib.util.module_from_spec(video_spec)
video_spec.loader.exec_module(barcode)

SITES = {"erp": "testerp.pearnly.com", "cowork": "testcowork.pearnly.com", "dms": "testdms.pearnly.com"}
READONLY_PROBES = {"page", "scan"}
CONTAINER_PROBES = {"line": "line_flow.py", "express": "express_flow.py"}


class Remote:
    """在站点网页容器里跑一个脚本：实验栈经测试机 WSL，线上经生产 SSH。"""

    def __init__(self, target: str):
        self.target = target
        self.lab_host = os.environ.get("SMOKE_LAB_HOST", "lenovo-office-lan")
        self.lab_temp = os.environ.get("SMOKE_LAB_TEMP", "C:/Users/codexops/AppData/Local/Temp")
        self.wsl = os.environ.get("SMOKE_LAB_WSL", "Ubuntu-24.04")
        self.prod_host = os.environ.get("SMOKE_PROD_HOST", "pearnly")

    def container(self, product: str) -> str:
        if self.target == "production":
            return f"pearnly-production-{product}-web-1"
        return f"pearnly-testenv-{product}-{product}-web-1"

    def run(self, product: str, script: Path, *args: str, timeout=600) -> str:
        name = f"smoke-{script.name}"
        container = self.container(product)
        quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
        inner = (f"docker cp /tmp/{name} {container}:/tmp/{name} >/dev/null && "
                 f"docker exec {container} bash -c \"cd /home/frappe/frappe-bench/sites && ../env/bin/python /tmp/{name} {quoted}\" </dev/null 2>&1; "
                 f"docker exec {container} rm -f /tmp/{name} </dev/null; rm -f /tmp/{name}")
        if self.target == "production":
            subprocess.run(["scp", "-q", str(script), f"{self.prod_host}:/tmp/{name}"], check=True, timeout=120)
            return subprocess.run(["ssh", self.prod_host, inner], capture_output=True, text=True, timeout=timeout).stdout
        # 测试机：脚本先放 Windows 临时目录，再由 WSL 里的 bash 包一层跑（PowerShell 的引号不经手）
        wrapper = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
        wrapper.write("#!/bin/bash\n" + f"cp /mnt/c/{self.lab_temp[3:]}/{name} /tmp/{name}\n" + inner + "\n")
        wrapper.close()
        try:
            subprocess.run(["scp", "-q", str(script), f"{self.lab_host}:{self.lab_temp}/{name}"], check=True, timeout=120)
            subprocess.run(["scp", "-q", wrapper.name, f"{self.lab_host}:{self.lab_temp}/smoke-run.sh"], check=True, timeout=120)
            win = f"/mnt/c/{self.lab_temp[3:]}/smoke-run.sh"
            return subprocess.run(["ssh", self.lab_host, f"wsl -d {self.wsl} -u root -- bash {win}"], capture_output=True, text=True,
                                  timeout=timeout).stdout
        finally:
            os.unlink(wrapper.name)


def select(args, parts, entries):
    if args.all:
        chosen = list(entries)
    else:
        chosen = []
        for part_id in args.part or []:
            chosen.extend(parts[part_id].entries)
        chosen.extend(args.entry or [])
        if args.diff:
            hit, touched = impact.impact(parts, entries, impact.changed(args.base), impact.tracked())
            for part in hit:
                chosen.extend(part.entries)
            chosen.extend(touched)
    return [entries[entry_id] for entry_id in dict.fromkeys(chosen)]


def drive(job: dict) -> dict:
    out = subprocess.run(["node", str(HERE / "drive.mjs"), json.dumps(job)], capture_output=True, text=True, timeout=180)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"result": None, "error": f"driver output: {out.stdout[-300:]} {out.stderr[-300:]}", "logs": []}


def verdict(raw: dict) -> tuple[str, str]:
    result = raw.get("result") or {}
    exceptions = [log["text"] for log in raw.get("logs", []) if log.get("kind") == "exception"]
    if raw.get("error"):
        return "不通过", str(raw["error"])[:200]
    if exceptions:
        return "不通过", "页面异常：" + exceptions[0][:200]
    if not result.get("ok"):
        return "不通过", json.dumps({k: v for k, v in result.items() if k != "ok"}, ensure_ascii=False)[:300]
    return "通过", ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("lab", "production"), default="lab")
    parser.add_argument("--readonly", action="store_true", help="只跑不写数据的探针（页面、扫码）；线上必须带")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--diff", action="store_true", help="按相对 merge-base 的改动选入口")
    parser.add_argument("--base", default="auto")
    parser.add_argument("--part", action="append")
    parser.add_argument("--entry", action="append")
    parser.add_argument("--out", help="结果 Markdown 写到这个文件")
    args = parser.parse_args(argv)
    if args.target == "production" and not args.readonly:
        parser.error("线上只跑只读子集：加 --readonly")
    parts, entries, errors = impact.load()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    chosen = select(args, parts, entries)
    remote = Remote(args.target)
    workdir = Path(tempfile.mkdtemp(prefix="smoke-"))
    video = workdir / "code.y4m"
    barcode.write(str(video))
    sessions, rows = {}, []
    try:
        for entry in chosen:
            if entry.check != "auto":
                rows.append((entry, "没测到", f"{entry.check}：交验收窗口按清单实测"))
                continue
            if args.readonly and entry.smoke not in READONLY_PROBES:
                rows.append((entry, "没测到", f"只读模式不跑 {entry.smoke}"))
                continue
            product = entry.site or entry.product.lower()
            site = SITES.get(product)
            if not site:
                rows.append((entry, "没测到", f"没有 {product} 的测试租户"))
                continue
            if entry.smoke in CONTAINER_PROBES:
                params = dict(entry.args)
                user = os.environ.get("SMOKE_EXPRESS_USER") if entry.smoke == "express" else os.environ.get(f"SMOKE_USER_{product.upper()}")
                params.setdefault("user", user or "Administrator")
                output = remote.run(product, HERE / CONTAINER_PROBES[entry.smoke], site, json.dumps(params, ensure_ascii=False))
                last = [line for line in output.splitlines() if line.startswith("SMOKE ")]
                result = json.loads(last[-1][6:]) if last else {"ok": False, "why": output[-300:]}
                rows.append((entry, "通过" if result.get("ok") else "不通过", "" if result.get("ok") else json.dumps(result, ensure_ascii=False)[:300]))
                continue
            if product not in sessions:
                user = os.environ.get(f"SMOKE_USER_{product.upper()}", "Administrator")
                output = remote.run(product, HERE / "session.py", site, user)
                sid = next((line.split()[1] for line in output.splitlines() if line.startswith("SID ")), "")
                if not sid:
                    print(f"建会话失败（{site} {user}）：{output[-300:]}", file=sys.stderr)
                    return 2
                sessions[product] = (user, sid)
            url = f"https://{site}{entry.url}"
            raw = drive({"url": url, "cookie_domain": site, "sid": sessions[product][1], "probe": entry.smoke,
                         "args": {"code": barcode.TEST_CODE, **entry.args}, "video": str(video) if entry.smoke == "scan" else "",
                         "wait_ms": 7000})
            state, why = verdict(raw)
            rows.append((entry, state, why))
            print(f"{state}  {entry.id}  {why}", file=sys.stderr)
    finally:
        for product, (user, sid) in sessions.items():
            remote.run(product, HERE / "session.py", SITES[product], user, "drop", sid)
    lines = ["| 入口 | 产品 | 方式 | 结果 | 说明 |", "|---|---|---|---|---|"]
    for entry, state, why in rows:
        lines.append(f"| `{entry.id}` | {entry.product} | {entry.check}{('：' + entry.smoke) if entry.smoke else ''} | {state} | {why.replace('|', '/')} |")
    passed = sum(1 for _, state, _ in rows if state == "通过")
    failed = sum(1 for _, state, _ in rows if state == "不通过")
    summary = f"自动冒烟（{args.target}{'，只读' if args.readonly else ''}）：通过 {passed}、不通过 {failed}、没测到 {len(rows) - passed - failed}"
    report = summary + "\n\n" + "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
