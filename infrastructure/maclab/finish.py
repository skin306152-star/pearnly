#!/usr/bin/env python3
"""本机 Mac 实验栈（maclab）起栈 / 换代码之后的收尾，在本栈 <产品>-bench 容器里跑（scripts/maclab.sh 调用）。

做的事与测试机的 testenv/finish-start.py 一样（那份文件头写了为什么必须自动做）：补运行配置（识别容量、队列超时、
调度器每 60 秒一拍、请求体上限——直接调它的 ``configure()``，不另写一份）、升级站点库、清 Frappe 缓存。
差别只在认哪些站点：testenv 认 ``*.pearnly.com`` / ``*.test.localhost``，这里认本栈的 ``*.maclab.localhost``。

    MACLAB_SKIP_MIGRATE=1   刚由 site-init.py 建好并迁移过的站点只清缓存（省一次迁移）
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

BENCH = Path("/home/frappe/frappe-bench")
SITES = BENCH / "sites"
SUFFIX = ".maclab.localhost"
TESTENV_FINISH = Path("/workspace/testenv/finish-start.py")


def log(message: str) -> None:
    print(message, flush=True)


def maclab_sites() -> list:
    return sorted(entry.name for entry in SITES.iterdir()
                  if entry.is_dir() and entry.name.endswith(SUFFIX) and (entry / "site_config.json").exists())


def main() -> int:
    spec = importlib.util.spec_from_file_location("testenv_finish", TESTENV_FINISH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    os.chdir(SITES)
    module.configure()
    sites = maclab_sites()
    if not sites:
        log("· 站点：本栈还没有站点（先跑 maclab.sh init）")
        return 0
    steps = ("clear-cache",) if os.environ.get("MACLAB_SKIP_MIGRATE") == "1" else ("migrate", "clear-cache")
    for site in sites:
        for step in steps:
            done = subprocess.run(["bench", "--site", site, step], cwd=str(BENCH), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            log(f"· {site} {step}：{'ok' if done.returncode == 0 else '失败'}")
            if done.returncode:
                log("\n".join((done.stdout or "").strip().splitlines()[-12:]))
                return 1
    log("· 收尾完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
