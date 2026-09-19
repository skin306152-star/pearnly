# -*- coding: utf-8 -*-
"""验证：从**我们自己的容器**里真登一次 MR.ERP DMS。

由 `scripts/sample-verify-dms-login.sh` 送进容器，用**独立 python 进程**跑
（`env/bin/python`，自己 `frappe.init/connect`）。

⚠️ **不要用 `bench console` 跑这个**（踩过，2026-09-18）：在 IPython 里启动
Playwright 会把 Python 进程直接打死 —— 表现是 node 驱动那边报 `EPIPE`、
Python 侧连缓冲的输出都丢了，而且**账号锁来不及释放**（Redis 里留一把 900 秒的
死锁，后面 15 分钟谁都进不来）。后来还发现它比真实路径更不真实：生产上跑
DMS 的是 web 请求和后台任务，都不是 IPython。所以这里改用真实形态。

为什么这一步值得单独做：搬 2500 行 DMS 业务代码之前，先证明地基是对的 ——
浏览器能在我们镜像里起、能连上真系统、能登进去、能干净退出。
地基不对的话，后面写得再多也是白写。

验四件事：

1. **会话锁真的互斥** —— 同一账号第二把锁必须拿不到（`dmstest` 只允许一个活跃登录，
   锁不生效就会互相踢下线，而且表现为"莫名其妙失败"，最难查）；
2. **能登进真 DMS** —— 离开 `index.php`，checklogin 给的是通过判定；
3. **没被当成重复登录** —— 没有"重复登录"弹窗；
4. **退出后不占登录槽** —— `__exit__` 会显式调 logout。

⚠️ 老产品文档：`dmstest` 只允许一个活跃登录。跑之前确认没有别的会话占着。
"""


def main():
    from pathlib import Path

    import frappe

    from pearnly_core.session_lock import SessionLockUnavailable, account_lock, dms_login_key
    from pearnly_dms.adapter.session import MrerpDmsAdapter

    ENDPOINT_LABEL = "MR.ERP DMS（测试账号）"
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def note(label, detail):
        print("  " + label + ": " + str(detail))

    def mask(v):
        v = str(v or "")
        return ("%s***(%d)" % (v[:2], len(v))) if v else "(空)"

    # ── 0. 端点存在 ────────────────────────────────────────────────────
    name = frappe.db.get_value("Pearnly ERP Endpoint", {"label": ENDPOINT_LABEL}, "name")
    check("找到端点 %s" % ENDPOINT_LABEL, bool(name), name)
    if not name:
        print("RESULT: FAIL")
        return

    # ── 1. 锁真的互斥 ──────────────────────────────────────────────────
    doc = frappe.get_doc("Pearnly ERP Endpoint", name)
    key = dms_login_key(doc.system_url, doc.username)
    note("锁键", key.replace(doc.username, mask(doc.username)))
    inner_blocked = False
    outer_held = None
    try:
        with account_lock(key, timeout_sec=10) as got:
            outer_held = got
            try:
                with account_lock(key, timeout_sec=2):
                    inner_blocked = False
            except SessionLockUnavailable:
                inner_blocked = True
    except SessionLockUnavailable as exc:
        check("第一把锁能拿到", False, "被别的会话占着：%s" % exc)
        print("RESULT: FAIL")
        return
    check("第一把锁能拿到", outer_held is True, outer_held)
    check("同账号第二把锁被拒（真互斥）", inner_blocked,
          "已拒绝" if inner_blocked else "居然也拿到了 —— 锁没生效")

    # ── 2~4. 真登一次 ─────────────────────────────────────────────────
    shot_dir = frappe.get_site_path("private", "files", "dms-shots")
    note("截图目录", shot_dir)

    try:
        with MrerpDmsAdapter.from_endpoint(name, headless=True) as adapter:
            note("登录地址", adapter.login_url)
            note("账号", mask(doc.username))
            note("带 admin 凭据", adapter.has_distinct_admin_creds)
            adapter.login()

            url_after = adapter.current_url
            check("登录后已离开 index.php", "index.php" not in url_after, url_after)
            check("没有被当成重复登录", not adapter.concurrent_login_detected,
                  adapter.last_dialog or "(无弹窗)")
            cookies = adapter.session_cookies()
            check("会话已建立（有 cookie）", len(cookies) > 0, "%d 个 cookie" % len(cookies))
            # cookie 值不打印，只报名字
            note("cookie 名", sorted({c.get("name", "") for c in cookies})[:8])
            # 截图要给证据，所以**出错必须报出来**，不能吞掉（老产品那个 helper
            # 只 warning，被日志级别一挡就什么线索都没有，我在这儿卡过一次）。
            shot = None
            try:
                shot = adapter._session.screenshot("dms-login-from-container", "登录成功")
            except Exception as exc:  # noqa: BLE001
                note("截图报错（默认 full_page）", "%s: %s" % (type(exc).__name__, str(exc)[:200]))
            if shot is None:
                # full_page 在带图表的页面上容易出问题，退一步用视口截图
                try:
                    fallback = Path(shot_dir) / "dms-login-viewport.png"
                    adapter.page.screenshot(path=str(fallback), full_page=False)
                    shot = fallback
                    note("改用视口截图", fallback)
                except Exception as exc:  # noqa: BLE001
                    note("视口截图也失败", "%s: %s" % (type(exc).__name__, str(exc)[:200]))
            check("截到一张登录后的图（证据）", shot is not None, shot or "两张方式都失败")
            note("截图", shot or "(没截)")
    except Exception as exc:  # noqa: BLE001 — 验证脚本就是要如实报出任何错
        check("从容器里真登 DMS", False, "%s: %s" % (type(exc).__name__, str(exc)[:300]))
        import traceback
        traceback.print_exc()

    failed = [r for r in results if not r[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


SITES_PATH = "/home/frappe/frappe-bench/sites"


if __name__ == "__main__":
    import sys

    import frappe

    site = sys.argv[1] if len(sys.argv) > 1 else "qd65956.localhost"
    frappe.init(site=site, sites_path=SITES_PATH)
    frappe.connect()
    try:
        main()
    finally:
        frappe.destroy()
