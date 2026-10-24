#!/usr/bin/env python3
"""换镜像之后必须做的事：升级站点库、清 Frappe 缓存、补运行配置。

为什么必须自动做（2026-09-24 在真实测试机上连着踩到三次）：

- 只换镜像不升级库 → 页面点按钮报 `Table 'tab<新 DocType>' doesn't exist`；
- 只换镜像不清缓存 → Frappe 把每个 App 的 hooks 缓存在 Redis 且**没有 TTL**，缓存里存的
  是旧的资源版本号（`app_include_js` 用文件内容哈希拼 `?v=`），页面于是继续加载旧 JS，
  表现就是"新功能看不见"；
- 缺 `pearnly_ocr_budget_redis` → LINE 发照片在**入队那一刻**就 `capacity_not_configured`，
  用户看到的是"识别队列繁忙，请稍后重发"。

范围：只写本栈自己的 sites 卷与 common_site_config；不动别的 app 的栈，也不碰线上。
升级失败必须让上层看到（非零退出），否则用户先撞到的是"表不存在"。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BENCH = Path("/home/frappe/frappe-bench")
SITES = BENCH / "sites"

#: OCR 是三个产品共用的队列；DMS 另有一条快照同步队列。
OCR_QUEUE_TIMEOUT = 600
DMS_SYNC_QUEUE_TIMEOUT = 420

#: 测试租户的站点按域名后缀识别；空白站点是 <app>.test.localhost。
TENANT_SUFFIXES = (".pearnly.com", ".test.localhost")


def log(message: str) -> None:
    print(message, flush=True)


COWORK_OCR = {"pearnly_cowork_ocr_environment": {"OCR_LLM_BACKEND": "vertex"},
              "pearnly_cowork_ocr_policy": {"mode": "economy"}}
# 测试机每租户 4 个识别名额（2026-09-29 压测：用户要测试这边加快、只核对结果）；生产 site-step.py 仍是 3。
OCR_BUDGET = {"concurrency": 8, "tenant_concurrency": 4, "rpm": 120}


def configure() -> None:
    """补齐换镜像后缺的运行配置。缺 OCR 容量会让识别在入队处直接失败。"""
    path = SITES / "common_site_config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    changed = []
    if not data.get("pearnly_ocr_budget_redis"):
        data["pearnly_ocr_budget_redis"] = "redis://redis-queue:6379/6"
        changed.append("pearnly_ocr_budget_redis")
    # 识别限额与线上开户一致（`infrastructure/production/site-step.py`）。缺了就用共用层默认 4 / 2 / 30，
    # 比线上严，压测时识别一直等名额（2026-09-29 压测窗口发现）。只补缺，手动调过的值不覆盖。
    budget_added = not data.get("pearnly_ocr_budget")
    if budget_added:
        data["pearnly_ocr_budget"] = dict(OCR_BUDGET)
        changed.append("pearnly_ocr_budget")
    # 与线上开户同一份 Cowork 发票识别配置（`infrastructure/production/site-step.py` 的 `COWORK_OCR`）。
    if os.environ.get("TESTENV_GROUP", "dms") == "cowork":
        for key, value in COWORK_OCR.items():
            if data.get(key) != value:
                data[key] = value
                changed.append(key)
    queues = {"ocr": OCR_QUEUE_TIMEOUT}
    if os.environ.get("TESTENV_GROUP", "dms") == "dms":
        queues["dms_sync"] = DMS_SYNC_QUEUE_TIMEOUT
    if os.environ.get("TESTENV_GROUP", "dms") in ("dms", "erp"):
        # 与线上 DMS / ERP bench 同一口径（`scripts/production/configure-{dms,erp}-runtime.py`，ADR-013）：DMS 白天增量、
        # ERP Express 主档同步推进与写账回读都按每分钟设计，调度器必须每 60 秒看一次，否则测试机上实际是 240 秒一轮。
        if data.get("scheduler_tick_interval") != 60:
            data["scheduler_tick_interval"] = 60
            changed.append("scheduler_tick_interval")
    workers = dict(data.get("workers") or {})
    for queue, timeout in queues.items():
        entry = dict(workers.get(queue) or {})
        if int(entry.get("timeout") or 0) != timeout:
            workers[queue] = {**entry, "timeout": timeout}
            changed.append(f"workers.{queue}.timeout")
    data["workers"] = workers
    if changed:
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        log("· 运行配置：" + "、".join(changed) + " 已写入")
    else:
        log("· 运行配置：已是当前值")
    if budget_added:
        # Redis 里记着第一次用到时的限额（`budget.permit` 按它比对），与新配置不一致就全部延后
        # （policy_mismatch）。直接写成新配置对应的记录，不要删：删掉后、重启前，还拿着旧配置的进程会按旧值
        # 抢先重建（2026-09-29 测试机同步后 5 分钟识别一份都没完成）。写法与 `budget.permit` 一致。
        import redis

        from pearnly_core.execution.budget import DEFAULTS, PREFIX

        policy = json.dumps({**DEFAULTS, **data["pearnly_ocr_budget"]}, sort_keys=True)
        redis.from_url(data["pearnly_ocr_budget_redis"]).set(PREFIX + "policy", policy)


def sites_to_finish() -> list:
    out = []
    for entry in sorted(SITES.iterdir()):
        if not entry.is_dir() or not (entry / "site_config.json").exists():
            continue
        if entry.name.endswith(TENANT_SUFFIXES):
            out.append(entry.name)
    return out


def bench(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bench", *args], cwd=str(BENCH), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def check_credentials(sites) -> None:
    """付费路径的凭据必须是**容器里这个用户**读得动的。

    实测踩过：从线上拷 `ocr.json` 过来时是 `root:root 600`，而应用以 uid 1000 跑，
    于是识别在换令牌那步就 `Permission denied`，表现成"任务一直重试/failed"。
    线上那份是 `1000:1000 600`。这里只读检查并提示，不改宿主文件。
    """
    for site in sites:
        config = json.loads((SITES / site / "site_config.json").read_text())
        path = str(config.get("pearnly_ocr_credentials") or "").strip()
        if not path:
            continue
        try:
            with open(path, "rb") as handle:
                handle.read(1)
            log(f"· {site} 识别凭据：可读（{path}）")
        except OSError as exc:
            log(f"· {site} 识别凭据：**读不了** {path}（{exc.strerror}）")
            log("  宿主上执行：chown 1000:1000 <文件> && chmod 600 <文件>（与线上一致）")


def finish() -> None:
    sites = sites_to_finish()
    if not sites:
        log("· 站点：本栈还没有站点（先跑 import 导入测试租户）")
        return
    check_credentials(sites)
    for site in sites:
        for name in ("migrate", "clear-cache"):
            done = bench("--site", site, name)
            log(f"· {site} {name}：{'ok' if done.returncode == 0 else '失败'}")
            if done.returncode:
                log("\n".join((done.stdout or "").strip().splitlines()[-12:]))
                if name == "migrate":
                    sys.exit(1)


if __name__ == "__main__":
    configure()
    finish()
    log("· 收尾完成：站点库已升级、缓存已清")
