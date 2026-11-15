"""实验栈复现并核对：切换当前公司的缓存竞态（主会话 11-15「设为当前点 3 次只第一次生效」）。

只在任务自己的回归实验室里跑（``PEARNLY_REGRESSION_LAB=1``），真 Frappe、真 Redis、两条数据库连接（两个线程各一条）：

1. 竞态：线程 A 切当前公司（写库、未提交）→ 线程 B（另一个请求，如页面刷新 / 启动数据）读这个用户的默认值、回填缓存
   → A 提交。修好后：提交后缓存里是新公司；``active_company`` 是新公司。``--without-fix`` 把钩子换成空的，复现原来的
   旧值卡在缓存里（这一步应「不通过」，证明测试真能复现）。
2. 卡住之后再点：缓存里已是旧值、库里已是新值，再「设为当前」同一家（Frappe 判没变、不清缓存）→ 修好后缓存里是新公司。

    docker compose -p <栈名> -f infrastructure/compose/regression-lab.yml exec -T bench \\
        env/bin/python /workspace/scripts/erp-active-company-race-lab-verify.py [--without-fix]

只改实验栈这个用户的当前公司，跑完还原；任何一项不过退出码非 0。
"""
import argparse
import os
import sys
import threading

SITE = "erp.regression.localhost"
USER = "admin@erp.regression"


def connect():
    import frappe

    frappe.init(site=SITE, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    return frappe


def in_thread(fn):
    out = {}

    def run():
        frappe = connect()
        try:
            out["value"] = fn(frappe)
        except Exception as error:  # noqa: BLE001
            out["error"] = repr(error)
        finally:
            frappe.destroy()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def cached(frappe):
    """下一个请求照 Frappe 的读法（`get_defaults_for`：先看缓存、没有才读库）读到的当前公司。"""
    return frappe.defaults.get_defaults_for(USER).get("company")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--without-fix", action="store_true")
    args = parser.parse_args()
    if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
        sys.exit("只在回归实验室里跑（PEARNLY_REGRESSION_LAB=1）")
    from pearnly_core.account import user_defaults

    if args.without_fix:
        user_defaults.after_write = lambda doc, method=None: None
        user_defaults.refresh = lambda parent: None

    frappe = connect()
    from pearnly_erp_portal.masters import company

    allowed = company.allowed_companies(USER)
    if len(allowed) < 2:
        sys.exit(f"{USER} 至少要能看两家公司（现在 {len(allowed)} 家）")
    original = company.active_company(USER)
    old, new = original, next(name for name in allowed if name != original)
    failures = []

    def check(ok, label):
        print(("ok  " if ok else "FAIL") + " " + label)
        if not ok:
            failures.append(label)

    wrote, read_done = threading.Event(), threading.Event()

    def writer(f):
        from pearnly_erp_portal.masters import company as c

        c.set_active_company(new, USER)       # 写库、清缓存（Frappe 在提交前清）
        wrote.set()
        read_done.wait(20)                    # 等另一个请求读完、回填
        f.db.commit()                         # 提交（修好后：提交后再清一次）
        return True

    def reader(f):
        wrote.wait(20)
        value = f.defaults.get_defaults_for(USER).get("company")   # 另一个请求：读到已提交的旧值并回填缓存
        read_done.set()
        return value

    try:
        frappe.defaults.set_user_default("company", old, USER)
        frappe.db.commit()
        a, a_out = in_thread(writer)
        b, b_out = in_thread(reader)
        a.join(30), b.join(30)
        print("reader saw", b_out, "writer", a_out)
        check(b_out.get("value") == old, "the concurrent read saw the old company (the race happened)")
        after = connect()
        check(cached(after) == new, f"after commit the next read gets the new company ({cached(after)})")
        check(company.active_company(USER) == new, "active_company is the new company")

        # 卡住之后再点同一家：库里已是新值（Frappe 判没变、不清），缓存里塞旧值
        after.client_cache.set_value(f"defaults::{USER}", {**after.defaults.get_defaults_for(USER), "company": old})
        check(cached(after) == old, "a stuck old value is in the cache before setting again")
        after.db.commit()
        again, _ = in_thread(lambda f: (company.set_active_company(new, USER), f.db.commit()))
        again.join(30)
        check(cached(connect()) == new, f"setting the same company again clears a stuck old value ({cached(connect())})")
    finally:
        restore = connect()
        restore.defaults.set_user_default("company", original, USER)
        restore.db.commit()
        restore.client_cache.delete_value(f"defaults::{USER}")
    print("FAILED" if failures else "ALL OK", len(failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
