# -*- coding: utf-8 -*-
"""租户拆除验收的夹具与复核（在**控制面**的 bench console 里跑）。

为什么需要一个专门的样板：拆除是**不可逆**的（站点、数据库、身份账号一起没），
不能拿真租户试。所以要造一个"确实存在、确实该删"的一次性租户：

  · 控制面：租户 + 发号单 + 部署（都是真行）
  · 身份服务：一个真账号（验证"账号被删掉"）
  · Keycloak：这个部署自己的 OIDC 客户端（验证"客户端被删掉"）
  · 站点与数据库由宿主脚本用 `bench new-site` 建（控制面容器里没有 docker）

四种模式（宿主的 `sample-verify-tenant-teardown.sh` 依次调用）：

  · `prepare`  —— 造上面这些；打印 `FIXTURE {json}`
  · `plan`     —— 调**按钮调的那个接口**（api_plan_teardown）
  · `inspect`  —— 复核控制面与身份服务里都没了；打印 `STATE {json}`
  · `cleanup`  —— 强制清掉本轮夹具（拆除失败时的兜底，别把垃圾留在库里）
"""


def main():
    import json
    import os

    import frappe

    from pearnly_control import teardown
    from pearnly_core import keycloak_admin as kc

    mode = (os.environ.get("MODE") or "prepare").strip()
    tenant = (os.environ.get("TENANT") or "").strip()
    issue = (os.environ.get("ISSUE") or "").strip()
    deployment = (os.environ.get("DEPLOYMENT") or "").strip()
    stamp = (os.environ.get("STAMP") or "zzdel").strip()
    site = "%s.localhost" % stamp
    email = "%s@teardown.sample.local" % stamp

    if mode == "prepare":
        client, target = kc.client_for_target(None)
        subject = client.create_user(email, first_name=stamp, password="Tear-Down-2026")
        client.create_oidc_client(site, ["http://%s:8103/*" % site], ["http://%s:8103" % site])

        t = frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": "拆除验收 %s" % stamp,
                            "status": "active"})
        t.insert(ignore_permissions=True)
        i = frappe.get_doc({
            "doctype": "Pearnly Issue",
            "tenant_name": "拆除验收 %s" % stamp,
            "account_email": email,
            "account_username": stamp,
            "account_password": "Tear-Down-2026",
            "target": target,
            "tenant": t.name,
            "status": "issued",
            "identity_subject": subject,
            "issued_at": frappe.utils.now(),
            "apps": [{"app": "dms", "domain_prefix": stamp}],
        })
        i.insert(ignore_permissions=True)
        d = frappe.get_doc({
            "doctype": "Pearnly Deployment",
            "status": "active",
            "product": "dms",
            "tenant": t.name,
            "site_name": site,
            "bench_group": "dms",
            "app": "pearnly_dms",
            "port": 8103,
            "url": "http://%s:8103" % site,
            "issue": i.name,
            "oidc_client_id": site,
            "oidc_client_secret": "not-a-real-secret",
        })
        d.insert(ignore_permissions=True)
        frappe.db.commit()
        print("FIXTURE " + json.dumps({
            "tenant": t.name, "issue": i.name, "deployment": d.name,
            "site": site, "email": email, "subject": subject, "stamp": stamp,
        }, ensure_ascii=False))
        return

    if mode == "plan":
        state = teardown.api_plan_teardown(tenant)
        print("PLAN " + json.dumps(state, ensure_ascii=False))
        return

    if mode == "inspect":
        client = None
        try:
            client, _t = kc.client_for_target(None)
        except Exception:
            client = None
        print("STATE " + json.dumps({
            "tenant_exists": bool(frappe.db.exists("Pearnly Tenant", tenant)),
            "issue_exists": bool(frappe.db.exists("Pearnly Issue", issue)),
            "deployment_exists": bool(frappe.db.exists("Pearnly Deployment", deployment)),
            "requests_left": frappe.db.count(
                "Pearnly Provisioning Request",
                {"idempotency_key": ("like", "teardown:%")}),
            "user_left": bool(client and client.find_user_id_by_email(email)),
            "client_left": bool(client and client.find_client_uuid(site)),
            "wallet_left": frappe.db.count("Pearnly Wallet", {"tenant": tenant}),
            "identity_left": frappe.db.count("Pearnly Identity", {"deployment_id": deployment}),
        }, ensure_ascii=False))
        return

    if mode == "cleanup":
        # 兜底：拆除中途失败时把控制面的残留强行清掉（站点由宿主脚本负责）
        from pearnly_control import teardown as td
        req = frappe.db.get_value(
            "Pearnly Provisioning Request", {"issue": issue}, "name", order_by="creation desc")
        if req:
            td.purge_records(req)
        for doctype, filters in (
            ("Pearnly Credit Transaction", {"tenant": tenant}),
            ("Pearnly Topup Request", {"tenant": tenant}),
            ("Pearnly Subscription", {"tenant": tenant}),
            ("Pearnly Wallet", {"tenant": tenant}),
            ("Pearnly Line Channel", {"deployment": deployment}),
            ("Pearnly Identity", {"deployment_id": deployment}),
        ):
            try:
                frappe.db.delete(doctype, filters)
            except Exception:
                pass
        for doctype, name in (("Pearnly Deployment", deployment), ("Pearnly Issue", issue),
                              ("Pearnly Tenant", tenant)):
            if name and frappe.db.exists(doctype, name):
                frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
        frappe.db.commit()
        try:
            client, _t = kc.client_for_target(None)
            left = client.find_user_id_by_email(email)
            if left:
                client.delete_user(left)
            client.delete_oidc_client(site)
        except Exception as exc:
            print("（身份服务清理没做完：%s）" % str(exc)[:120])
        print("CLEANED")
        return

    raise SystemExit("unknown MODE: %s" % mode)


if __name__ == "__main__":
    main()
