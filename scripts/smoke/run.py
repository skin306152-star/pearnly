#!/usr/bin/env python3
"""按登记表跑自动冒烟（共用件连带回归闸门，docs/development/verification.md）。

    # 实验栈（测试机 testenv，候选源码已 live）：命中的共用件 → 全部 auto 入口
    python3 scripts/smoke/run.py --diff
    python3 scripts/smoke/run.py --part desk-scan-core --part desk-shell
    python3 scripts/smoke/run.py --entry erp.page.products --entry erp.products.new-dialog.scan
    # 上线后：线上测试租户只读子集（页面、扫码，不写单、不发 LINE）
    python3 scripts/smoke/run.py --target production --readonly --all
    # 本机回归实验室（tests/flows/README.md 起的栈，挂候选工作树；不经测试机）：两套数据的老站点
    PEARNLY_FLOWS_LAB=<栈名> REGRESSION_LAB_PORT=… python3 scripts/smoke/run.py --target local --diff

探针：page / scan 由本机无头 Chrome（scripts/smoke/drive.mjs，假摄像头放 make_barcode_video.py 的测试条码）打开测试租户跑；
line / express 在站点网页容器里跑（scripts/smoke/line_flow.py、express_flow.py，出站 LINE 一律截住、写 Express 只写测试机 C 盘 T 账套）。
会话：在网页容器里给测试账号建一次（session.py），跑完删掉；不碰 Keycloak 密码。
结果按「通过 / 不通过 / 没测到（原因）」出 Markdown（--out），贴进任务记录。标 manual 的入口列在「没测到（人工）」里交验收窗口。

环境变量（都有默认值，按测试机现状）：
  SMOKE_CHROME            本机 Chrome 路径
  SMOKE_LAB_HOST          测试机 SSH 别名（lenovo-office-lan）；SMOKE_LAB_TEMP Windows 临时目录；SMOKE_LAB_WSL 发行版名
  SMOKE_PROD_HOST         生产 SSH 别名（pearnly），只在 --target production 用
  SMOKE_USER_ERP / _COWORK / _DMS   各测试租户冒烟用的账号（默认 Administrator）
  SMOKE_USER_ERP_LIMITED 等          受限账号（只有某一岗位、看不到别人的单；入口 args 写 "as" = "limited" 的用它，没配记「没测到」）
  SMOKE_EXPRESS_USER      测试机 CC 上登录的 ERP 员工（Express 写入冒烟用；入口 args 里写了 user 时以它为准）
  SMOKE_COWORK_EXPRESS_USER  测试机 CC 上登录的 Cowork 录入人（Cowork 推送写 Express 冒烟用，同上）
  PEARNLY_FLOWS_LAB / REGRESSION_LAB_PORT[_COWORK|_DMS]  --target local 用的实验栈名与网页口（同 tests/flows/README.md）
  SMOKE_PAGE_LIMIT_MS     页面打开时限（默认 3000；入口 args 的 limit_ms 优先）：超过算不通过。大数据量要用 testerp 这种大账套跑，
                          ERP 用 --company 指定大账套那家公司（造数见 scripts/smoke/seed_stock_load.py）
"""
from __future__ import annotations

import argparse
import base64
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
reaper_spec = importlib.util.spec_from_file_location("chrome_reaper", HERE / "chrome_reaper.py")
reaper = importlib.util.module_from_spec(reaper_spec)
reaper_spec.loader.exec_module(reaper)

SITES = {"erp": "testerp.pearnly.com", "cowork": "testcowork.pearnly.com", "dms": "testdms.pearnly.com"}
# --target local：本机回归实验室的站点（两套数据里有存量的老站点；DMS 用实验室自带的站点）与各自的网页口、默认账号
LOCAL_SITES = {"erp": "erp-old.regression.localhost", "cowork": "cowork-old.regression.localhost", "dms": "dms.regression.localhost"}
LOCAL_PORTS = {"erp": "REGRESSION_LAB_PORT", "cowork": "REGRESSION_LAB_PORT_COWORK", "dms": "REGRESSION_LAB_PORT_DMS"}
LOCAL_DEFAULT_PORTS = {"erp": 18771, "cowork": 18772, "dms": 18773}
READONLY_PROBES = {"page", "scan", "find_product", "assistant", "doc_detail", "record_switch", "not_permitted"}
CONTAINER_PROBES = {"line": "line_flow.py", "express": "express_flow.py", "cowork_express": "cowork_express_flow.py",
                    "api_time": "api_time.py"}
# 写 Express 的探针要用测试机 CC 上登录过的员工（CC 只领他的单）：入口 args 里写了 user 时以它为准
EXPRESS_USERS = {"express": "SMOKE_EXPRESS_USER", "cowork_express": "SMOKE_COWORK_EXPRESS_USER"}


class Remote:
    """在站点网页容器里跑一个脚本：实验栈经测试机 WSL，线上经生产 SSH。"""

    def __init__(self, target: str):
        self.target = target
        self.lab_host = os.environ.get("SMOKE_LAB_HOST", "lenovo-office-lan")
        self.lab_temp = os.environ.get("SMOKE_LAB_TEMP", "C:/Users/codexops/AppData/Local/Temp")
        self.wsl = os.environ.get("SMOKE_LAB_WSL", "Ubuntu-24.04")
        self.prod_host = os.environ.get("SMOKE_PROD_HOST", "pearnly")

    def container(self, product: str) -> str:
        if self.target == "local":
            return os.environ.get("PEARNLY_FLOWS_LAB", "pearnly-regression-lab") + "-bench-1"
        if self.target == "production":
            return f"pearnly-production-{product}-web-1"
        return f"pearnly-testenv-{product}-{product}-web-1"

    def run(self, product: str, script: Path, *args: str, timeout=600) -> str:
        name = f"smoke-{script.name}"
        container = self.container(product)
        # 参数一律 base64（JSON 里的引号、泰文不经 shell 转义）；容器里的脚本见到 b64: 前缀就解开
        quoted = " ".join("b64:" + base64.b64encode(a.encode("utf-8")).decode() for a in args)
        inner = (f"docker cp /tmp/{name} {container}:/tmp/{name} >/dev/null && "
                 f"docker exec {container} bash -c \"cd /home/frappe/frappe-bench/sites && ../env/bin/python /tmp/{name} {quoted}\" </dev/null 2>&1; "
                 f"docker exec {container} rm -f /tmp/{name} </dev/null; rm -f /tmp/{name}")
        if self.target == "local":
            subprocess.run(["docker", "cp", str(script), f"{container}:/tmp/{name}"], check=True, capture_output=True, timeout=120)
            try:
                return subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", container, "../env/bin/python",
                                       f"/tmp/{name}", *quoted.split()], capture_output=True, text=True, timeout=timeout,
                                      stdin=subprocess.DEVNULL).stdout
            finally:
                subprocess.run(["docker", "exec", "-u", "root", container, "rm", "-f", f"/tmp/{name}"], capture_output=True)
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
    # 驱动放进单独的进程组：超时、出错、被打断都连它开的 Chrome 一起收掉（chrome_reaper；以前超时直接杀 node，Chrome 成了孤儿）
    try:
        _code, stdout, stderr = reaper.run_driver(["node", str(HERE / "drive.mjs"), json.dumps(job)], timeout=180)
    except subprocess.TimeoutExpired:
        return {"result": None, "error": "超时：页面 3 分钟内没跑完探针", "logs": []}
    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"result": None, "error": f"driver output: {stdout[-300:]} {stderr[-300:]}", "logs": []}


PAGE_LIMIT_MS = int(os.environ.get("SMOKE_PAGE_LIMIT_MS", "3000"))


def verdict(raw: dict, limit_ms: int | None = None) -> tuple[str, str]:
    """limit_ms：页面打开时限（大数据量页面卡顿算不通过；驱动量到的 ready_ms = 导航开始到主区内容稳定）。"""
    result = raw.get("result") or {}
    exceptions = [log["text"] for log in raw.get("logs", []) if log.get("kind") == "exception"]
    if raw.get("error"):
        return "不通过", str(raw["error"])[:200]
    if exceptions:
        return "不通过", "页面异常：" + exceptions[0][:200]
    if result.get("skip"):
        return "没测到", str(result.get("why") or "")[:200]  # 详情页探针：测试租户里没有这种状态的单
    if not result.get("ok"):
        return "不通过", json.dumps({k: v for k, v in result.items() if k != "ok"}, ensure_ascii=False)[:300]
    if limit_ms:
        ready = raw.get("ready_ms")
        if ready is None:
            return "不通过", f"页面主区 {limit_ms / 1000:g} 秒时限内外都没稳定下来"
        if ready > limit_ms:
            return "不通过", f"打开用了 {ready / 1000:.1f} 秒，超过 {limit_ms / 1000:g} 秒时限"
        return "通过", f"{ready / 1000:.1f} 秒"
    return "通过", ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("lab", "local", "production"), default="lab")
    parser.add_argument("--readonly", action="store_true", help="只跑不写数据的探针（页面、扫码）；线上必须带")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--diff", action="store_true", help="按相对 merge-base 的改动选入口")
    parser.add_argument("--base", default="auto")
    parser.add_argument("--part", action="append")
    parser.add_argument("--entry", action="append")
    parser.add_argument("--site", choices=sorted(SITES), help="只跑这个测试租户的入口（实验栈一次只起一个产品）")
    parser.add_argument("--company", help="ERP 页面冒烟先把冒烟账号的当前公司切到这家（大账套跑打开时限），收尾还原")
    parser.add_argument("--out", help="结果 Markdown 写到这个文件")
    parser.add_argument("--shots", help="浏览器探针跑完后各存一张截图到这个目录（留证）")
    args = parser.parse_args(argv)
    if args.shots:
        Path(args.shots).mkdir(parents=True, exist_ok=True)
    if args.target == "production" and not args.readonly:
        parser.error("线上只跑只读子集：加 --readonly")
    parts, entries, errors = impact.load()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    chosen = select(args, parts, entries)
    if args.site:
        chosen = [entry for entry in chosen if (entry.site or entry.product.lower()) == args.site]
    remote = Remote(args.target)
    sites = LOCAL_SITES if args.target == "local" else SITES
    reaper.exit_on_signals()
    swept = reaper.reap_orphans()   # 以前被打断留下的孤儿 Chrome 与旧临时 profile（只认冒烟自己这一类）
    if swept["killed"] or swept["removed"]:
        print(f"清掉以前冒烟留下的 Chrome {len(swept['killed'])} 个、临时目录 {len(swept['removed'])} 个", file=sys.stderr)
    workdir = reaper.claim(Path(tempfile.mkdtemp(prefix=reaper.PROFILE_PREFIX)))
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
            site = sites.get(product)
            if not site:
                rows.append((entry, "没测到", f"没有 {product} 的测试租户"))
                continue
            if entry.smoke in CONTAINER_PROBES:
                params = dict(entry.args)
                user = os.environ.get(EXPRESS_USERS.get(entry.smoke) or f"SMOKE_USER_{product.upper()}")
                params.setdefault("user", user or (f"admin@{product}.regression" if args.target == "local" else "Administrator"))
                if entry.smoke == "api_time" and args.company and product == "erp":
                    params.setdefault("company", args.company)  # 大账套那家公司（--company）
                output = remote.run(product, HERE / CONTAINER_PROBES[entry.smoke], site, json.dumps(params, ensure_ascii=False))
                last = [line for line in output.splitlines() if line.startswith("SMOKE ")]
                result = json.loads(last[-1][6:]) if last else {"ok": False, "why": output[-300:]}
                rows.append((entry, "通过" if result.get("ok") else "不通过",
                             str(result.get("summary") or "")[:200] if result.get("ok") else json.dumps(result, ensure_ascii=False)[:300]))
                continue
            # 受限账号（`"as" = "limited"`）：只有某一岗位、看不到别人的单的账号；没配就不测
            limited = entry.args.get("as") == "limited"
            fallback = (f"admin@{product}.regression" if args.target == "local" else "Administrator")
            user = os.environ.get(f"SMOKE_USER_{product.upper()}{'_LIMITED' if limited else ''}", "" if limited else fallback)
            if not user:
                rows.append((entry, "没测到", f"没配受限账号 SMOKE_USER_{product.upper()}_LIMITED"))
                continue
            probe_args = {"code": barcode.TEST_CODE, **entry.args}
            if entry.smoke == "not_permitted":
                output = remote.run(product, HERE / "pick_doc.py", site, entry.args["doctype"], user)
                name = next((line[5:].strip() for line in output.splitlines() if line.startswith("NAME ")), "")
                if not name:
                    rows.append((entry, "没测到", f"没有别人建的 {entry.args['doctype']}"))
                    continue
                probe_args["name"] = name
            key = (product, user)
            if key not in sessions:
                extra = ("company", args.company) if args.company and product == "erp" and not limited else ()
                output = remote.run(product, HERE / "session.py", site, user, *extra)
                sid = next((line.split()[1] for line in output.splitlines() if line.startswith("SID ")), "")
                previous = next((line.split()[1] for line in output.splitlines() if line.startswith("PREV ")), None)
                if not sid:
                    print(f"建会话失败（{site} {user}）：{output[-300:]}", file=sys.stderr)
                    return 2
                sessions[key] = (user, sid, previous)
            url = (f"http://{site}:{os.environ.get(LOCAL_PORTS[product], LOCAL_DEFAULT_PORTS[product])}{entry.url}"
                   if args.target == "local" else f"https://{site}{entry.url}")
            raw = drive({"url": url, "cookie_domain": site, "sid": sessions[key][1], "probe": entry.smoke,
                         "args": probe_args, "video": str(video) if entry.smoke == "scan" else "",
                         "wait_ms": 7000, "wait_for": "none" if entry.smoke == "assistant" else "frappe",
                         **({"screenshot": str(Path(args.shots).resolve() / f"{entry.id}.png")} if args.shots else {})})
            limit = int(entry.args.get("limit_ms", PAGE_LIMIT_MS)) if entry.smoke == "page" else None
            state, why = verdict(raw, limit)
            rows.append((entry, state, why))
            print(f"{state}  {entry.id}  {why}", file=sys.stderr)
    finally:
        for (product, _user), (user, sid, previous) in sessions.items():
            remote.run(product, HERE / "session.py", sites[product], user, "drop", sid,
                       *((previous,) if previous is not None else ()))
        __import__("shutil").rmtree(workdir, ignore_errors=True)   # 测试条码视频的临时目录
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
