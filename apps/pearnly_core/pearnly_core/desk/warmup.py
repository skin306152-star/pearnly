"""部署后一次性预热：`migrate` 会清空站点缓存，之后每个用户第一次打开 /app 都要现场冷算
启动信息（生产实测 4.6–6.6 秒，热的 0.3–0.7 秒），第一次打开某张表也要冷算表结构（例如
Cowork Bank Reconciliation 冷 1.27 秒、热 6 毫秒）。

官方没有部署后预热这一步，缓存都是第一个请求现场算、算完写进 redis。这里只是在上线时
**替用户先走一遍同样的官方路径**，结果和他们自己第一次打开时写进去的完全一样：

- 启动信息：按 `frappe.sessions.get` 的做法，用 `frappe.boot.get_bootinfo()` 算好写进
  `bootinfo` 这个哈希（以用户为键）。每个用户各开一次全新的站点上下文（等于一个独立请求），
  上一个用户的 `frappe.local` 不会带到下一个。会话里才有的 `ipinfo` 本来就不在里面——官方
  缓存的启动信息同样是第一次登录那次会话算的，后面别的会话直接复用。
- 表结构：本站已装的 pearnly 产品 App 里的 DocType，加上产品页面会读的 User / File，按
  `getdoctype` 用的 `frappe.desk.form.meta.get_meta` 写进缓存（全站共用，与用户无关）。

只给最近活跃过的用户算（各站点最近活跃的在前），所有站点合计有时长上限（`bench --site all`
在同一个进程里逐站调用）；任何一步失败只记日志、跳过，不影响上线
（部署脚本在切回正常服务**之后**才调它）。不是定时任务，只在激活时跑一次：

    bench --site all execute pearnly_core.desk.warmup.run
"""
import time

import frappe
from frappe.utils import add_days, now_datetime

RECENT_DAYS = 30
USER_LIMIT = 200
BUDGET_SECONDS = 180
SHARED_DOCTYPES = ("User", "File")
_started = None


def product_doctypes() -> list[str]:
    modules = frappe.get_all("Module Def", filters={"app_name": ("like", "pearnly\\_%")}, pluck="name")
    names = frappe.get_all("DocType", filters={"module": ("in", modules or [""]), "istable": 0},
                           pluck="name", order_by="name")
    return [*names, *(name for name in SHARED_DOCTYPES if frappe.db.exists("DocType", name))]


def recent_users() -> list[str]:
    return frappe.get_all("User", filters={
        "enabled": 1, "user_type": "System User", "name": ("not in", ["Administrator", "Guest"]),
        "last_active": (">", add_days(now_datetime(), -RECENT_DAYS))},
        pluck="name", order_by="last_active desc", limit=USER_LIMIT)


def warm_meta(doctypes) -> int:
    from frappe.desk.form.meta import get_meta

    done = 0
    for doctype in doctypes:
        try:
            get_meta(doctype)
            done += 1
        except Exception:
            frappe.log_error(title=f"pearnly desk warmup: meta {doctype}")
    frappe.db.commit()
    return done


def warm_boot(user) -> bool:
    """当前上下文必须是刚 init/connect 的全新站点上下文。已有缓存（用户这期间自己打开过）就不动。"""
    from frappe.boot import get_bootinfo

    if frappe.cache.hget("bootinfo", user):
        return False
    frappe.set_user(user)
    frappe.cache.hset("bootinfo", user, get_bootinfo())
    return True


def _fresh_context(site, sites_path):
    frappe.destroy()
    frappe.init(site, sites_path=sites_path)
    frappe.connect()


def run():
    """`bench execute` 在函数抛错时会把方法名再 eval 一遍重跑，所以这里绝不往外抛。"""
    global _started
    site, sites_path = frappe.local.site, frappe.local.sites_path
    started = time.monotonic()
    if _started is None:
        _started = started
    try:
        users = [] if frappe.conf.get("disable_session_cache") else recent_users()
        meta = warm_meta(product_doctypes())
    except Exception:
        frappe.log_error(title="pearnly desk warmup")
        frappe.db.commit()
        print(f"{site}: warmup skipped")
        return
    booted = skipped = failed = 0
    for user in users:
        if time.monotonic() - _started > BUDGET_SECONDS:
            break
        _fresh_context(site, sites_path)
        try:
            if warm_boot(user):
                booted += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
            frappe.log_error(title=f"pearnly desk warmup: boot {user}")
            frappe.db.commit()
    _fresh_context(site, sites_path)
    print(f"{site}: meta {meta}, boot {booted}/{len(users)} (already {skipped}, failed {failed}), "
          f"{time.monotonic() - started:.1f}s")
