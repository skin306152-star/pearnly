# -*- coding: utf-8 -*-
"""看/清 DMS 会话锁。开发期会用到：进程被 OOM 杀掉时锁不会自己释放。

由 `scripts/dms-lock.sh` 送进容器用独立 python 跑。

为什么需要它：DMS 的测试账号 `dmstest` **只允许一个活跃登录**，所以同一账号
同时只能有一个会话（用 Redis 锁保证，见 `pearnly_core/session_lock.py`）。
进程正常退出会释放锁；被 OOM 杀掉则要等 TTL（默认 10 分钟）自动过期。
在这中间你看到的报错是"拿不到锁，还剩 xxx 秒"—— 那时候需要判断：

· 还剩很多 + 你知道没人正在跑 → **是死锁**，清掉；
· 还剩很多 + 可能有别的会话 → **别清**，等它，或者先确认那边结束了。

所以这个工具默认只**看**，要清必须显式说 `clear`。
"""

import sys

import frappe

ACTION = sys.argv[1] if len(sys.argv) > 1 else "status"
SITE = sys.argv[2] if len(sys.argv) > 2 else "qd65956.localhost"

frappe.init(site=SITE, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()

c = frappe.cache()
keys = c.keys("*sessionlock*")

print("站点: %s" % SITE)
print("当前锁数量: %d" % len(keys))
for k in keys:
    key = k.decode() if isinstance(k, bytes) else str(k)
    print("  %s  剩余 %s 秒" % (key, c.ttl(key)))

if ACTION == "clear":
    for k in keys:
        key = k.decode() if isinstance(k, bytes) else str(k)
        c.delete(key)
        print("  已清除: %s" % key)
    print("清完，剩 %d 个" % len(c.keys("*sessionlock*")))
elif ACTION != "status":
    print("用法: dms-lock.py status|clear [站点]")

frappe.destroy()
