#!/usr/bin/env python3
"""把本栈账本容器（billing-web）的 bench 配置补到能用的状态。

为什么需要它：账本站点是**整站导入**进一个新卷的，而 sites 卷上的
`common_site_config.json` 与 `apps.txt` 不属于站点数据——它们没人写过，
于是 `bench migrate` 直接报 "Service redis_cache is not running"
（实际是配置里根本没有 redis），计费请求也就一直 404。

只补**缺失**的键，不改已有的值；不碰数据库里的业务数据。
"""
from __future__ import annotations

import json
from pathlib import Path

SITES = Path("/home/frappe/frappe-bench/sites")
APPS_DIR = Path("/home/frappe/frappe-bench/apps")

#: 与产品栈一致的内网服务名；账本不需要 OCR 容量键，也不需要产品 worker 超时。
COMMON = {
    "db_host": "mariadb",
    "db_port": 3306,
    "redis_cache": "redis://redis-cache:6379",
    "redis_queue": "redis://redis-queue:6379/1",
    "redis_socketio": "redis://redis-queue:6379/1",
    "socketio_port": 9000,
    "serve_default_site": False,
}

#: 镜像里装了、且账本要用的 App。frappe 必装，控制面提供计费 API。
REQUIRED_APPS = ("frappe", "pearnly_control")


def log(message: str) -> None:
    print(message, flush=True)


def main() -> None:
    common = SITES / "common_site_config.json"
    values = json.loads(common.read_text()) if common.exists() else {}
    added = [key for key, value in COMMON.items() if key not in values]
    for key in added:
        values[key] = COMMON[key]
    if added:
        # Frappe 认为带尾随换行的 JSON 也算合法；与产品栈保持同一种写法。
        common.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
        log("· 公共配置：" + "、".join(added) + " 已写入")
    else:
        log("· 公共配置：已是当前值")

    apps_file = SITES / "apps.txt"
    existing = [line.strip() for line in apps_file.read_text().splitlines() if line.strip()] if apps_file.exists() else []
    wanted = list(existing)
    for app in REQUIRED_APPS:
        if app not in wanted and (APPS_DIR / app).is_dir():
            wanted.append(app)
    # 账本站点只装控制面；把产品 App 混进来会让 migrate 去加载用不到的模块。
    if wanted != existing:
        apps_file.write_text("\n".join(wanted) + "\n")
        log("· 应用清单：" + ", ".join(wanted))
    else:
        log("· 应用清单：" + ", ".join(existing))


if __name__ == "__main__":
    main()
