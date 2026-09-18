# -*- coding: utf-8 -*-
"""地基自检：外部连接（凭据）+ 推送日志（租约/状态）。

由 `scripts/sample-verify-foundation.sh` 拷进容器、用 `bench console` 跑。
**自己造数据、无论成败都清干净**，不往站点上留假凭据。

为什么要有这个：这两张表是所有外部调用的地基，而它们的故障都很安静 ——
比如"租约一个都抢不到"（`frappe.db.sql` 对 UPDATE 返回的是空元组而不是影响行数，
拿它判成败永远为假）不会报错，只表现为"没有任务可推"；又比如列表接口
不小心把密码带出去。所以必须有断言盯着。

⚠️ **写法约束（被 `bench console` 逼出来的）**：这个文件是
`exec(open(...).read())` 执行的，而 console 的 exec 命名空间与函数解析 globals 的
命名空间**不是同一个** —— 模块级的名字（不管是 import 进来的模块，还是自己定义的
函数和列表）在函数体里都看不见，会报 `NameError`。所以：

  · 所有 import 放在 `main()` 里；
  · 所有状态（结果列表、清理名单）与辅助函数都放在 `main()` 里（闭包可见）；
  · 模块级只剩"调用 main + 打印结论"这种直白语句。
"""


def main():
    import frappe

    from pearnly_core import endpoints, push_log

    results = []
    # 造出来的东西都登记下来，**无论中间哪一步炸了都要清干净**。
    # 早先没有这一步，脚本中途报错就把测试连接留在站点上了（实际留了 3 条）。
    created_logs = []
    endpoint_name = None

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    real = frappe.db.get_value("DMS ID Card Intake", {}, "name")
    if not real:
        print("SKIP 站点上还没有 DMS 身份证记录，无法验证推送日志（先造一条再来）")
        return results

    try:
        # ── 1. 连接：建、不泄密、两组凭据各取各的 ──────────────────────────
        ep = frappe.get_doc({
            "doctype": "Pearnly ERP Endpoint",
            "label": "自检连接（临时）",
            "adapter": "mrerp_dms",
            "system_url": "https://example.invalid/dms",
            "username": "check-user",
            "password": "check-pass",
            "admin_username": "check-admin",
            "admin_password": "check-admin-pass",
        })
        ep.insert(ignore_permissions=True)
        frappe.db.commit()
        endpoint_name = ep.name

        rows = endpoints.list_for("mrerp_dms")
        row = next((r for r in rows if r["name"] == ep.name), None)
        check("连接建得出来", bool(row))
        check("列表**不**返回密码字段", row is not None and "password" not in row)
        check("列表能看出『配过凭据』", row is not None and row["has_credential"] is True)
        check("列表能看出『可用』", row is not None and row["usable"] is True)

        user_c = endpoints.credentials(ep.name)
        admin_c = endpoints.credentials(ep.name, as_admin=True)
        check("普通凭据取的是操作员那组", user_c["username"] == "check-user", user_c["username"])
        check("管理凭据取的是有修改权限那组", admin_c["username"] == "check-admin",
              admin_c["username"])
        check("管理凭据上 as_admin 标记为真", admin_c["as_admin"] is True)

        # 停用后不该再能用（"同批不误投"的闸）
        frappe.db.set_value("Pearnly ERP Endpoint", ep.name, "enabled", 0)
        frappe.db.commit()
        blocked = False
        try:
            endpoints.credentials(ep.name)
        except Exception:
            blocked = True
        check("停用的连接取不到凭据", blocked)
        frappe.db.set_value("Pearnly ERP Endpoint", ep.name, "enabled", 1)
        frappe.db.commit()

        # 测试连接必须是**诚实**的：不谎报"连上了"
        probe = endpoints.api_test_connection(ep.name)
        check(
            "测试连接说明了它只检查配置、没有真连",
            probe.get("checked") == "config" and "没有真的连" in (probe.get("note") or ""),
        )

        # ── 2. 推送日志：租约保证"同一条只被推一次" ────────────────────────
        n1 = push_log.record("pending", endpoint=ep.name, reference_doctype="DMS ID Card Intake",
                             reference_name=real, business_key="CHECK-1")
        n2 = push_log.record("pending", endpoint=ep.name, reference_doctype="DMS ID Card Intake",
                             reference_name=real, business_key="CHECK-2")
        created_logs.extend([n1, n2])

        c1 = push_log.claim_for_push(ep.name, owner="check-worker-1")
        c2 = push_log.claim_for_push(ep.name, owner="check-worker-2")
        check("worker-1 抢到了待推送的行", sorted(c1) == sorted([n1, n2]), c1)
        check("worker-2 抢不到（租约挡住了）", c2 == [], c2)

        push_log.finish(n1, "success", http_status=200, response_body="ok")
        check("收口后租约被释放",
              (frappe.db.get_value("Pearnly Push Log", n1, "lease_owner") or "") == "")
        check("收口后状态是 success",
              frappe.db.get_value("Pearnly Push Log", n1, "status") == "success")

        # 按单据查结论：以"最新一条"为准
        st = push_log.status_of("DMS ID Card Intake", real)
        check("按单据能查到推送历史", st["count"] >= 2, st["count"])
        check("最新那条还是 pending，所以结论是『没推成功』", st["pushed"] is False,
              st["latest_status"])
        push_log.finish(n2, "success", http_status=200, response_body="ok")
        st = push_log.status_of("DMS ID Card Intake", real)
        check("两条都成功后，结论才是『已推送』", st["pushed"] is True, st["latest_status"])

        # 重试用尽 → 终态 failed，不无限重试
        n3 = push_log.record("failed", endpoint=ep.name, reference_doctype="DMS ID Card Intake",
                             reference_name=real)
        created_logs.append(n3)
        last = None
        for _ in range(4):
            last = push_log.schedule_retry(n3, delay_minutes=1)
        check("重试到上限后收成终态 failed", last and last.get("exhausted") is True, last)

    finally:
        for name in created_logs:
            if frappe.db.exists("Pearnly Push Log", name):
                frappe.delete_doc("Pearnly Push Log", name, force=True, ignore_permissions=True)
        if endpoint_name and frappe.db.exists("Pearnly ERP Endpoint", endpoint_name):
            frappe.delete_doc("Pearnly ERP Endpoint", endpoint_name, force=True,
                              ignore_permissions=True)
        frappe.db.commit()

    return results


try:
    _results = main()
except Exception:
    import traceback

    traceback.print_exc()
    # 异常**必须**计入失败。以前这里只打印堆栈，于是后面的判定因为结果为空而输出
    # SKIP —— 一次真实报错被我读成了"条件不足"（踩过）。
    _results = [("自检脚本自身跑通（没有抛异常）", False, "见上面的 traceback")]

_failed = [r for r in _results if not r[1]]
print("")
if not _results:
    print("RESULT: SKIP")
elif _failed:
    print(f"RESULT: FAIL ({len(_failed)}/{len(_results)})")
    for _label, _ok, _detail in _failed:
        print("  - " + _label + ((" — " + str(_detail)) if _detail else ""))
else:
    print(f"RESULT: ALL PASS ({len(_results)})")
