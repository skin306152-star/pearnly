# -*- coding: utf-8 -*-
"""删掉跑测试留下的发号单（含它建的租户、空部署、以及它在身份服务里建的账号）。

由 `scripts/sample-clean-issues.sh` 送进容器、用 `bench console` 跑。

为什么要有：`sample-issue-form.sh` 这类验证每跑一次就真建一张发号单
（还在身份服务里真建一个账号），跑十次控制面的「开通单」列表就全是噪声。

安全边界（都是刻意的）：

- **只删点名给的单号**。不按"名字像测试"去猜，避免误伤。
- **有 active 部署的单子拒绝删**（除非 `force=1`）—— 那背后是已建好的站点，
  删单会把站点变成孤儿。要删先单独处理站点。
- **身份服务里只删发号单自己记录的那个 subject**（`identity_subject`），
  绝不按邮箱模糊匹配 —— 邮箱可能是别人的。

两种输入：

- `ISSUES=ISS-...,ISS-...`：正常用法，连单子带租户带身份账号一起删；
- `SUBJECTS=<uuid>,<uuid>`：只删身份服务里的账号（给"单子已经删了、账号还在"
  这种残局收尾用）。

管理凭据从 `Pearnly Target` 取（见 `keycloak_admin.client_for_target`），
不从站点配置取 —— 控制面站点的 `frappe.conf` 里没有那对凭据。
"""


def main():
    import os

    import frappe
    import requests

    raw = (os.environ.get("ISSUES") or "").strip()
    raw_subjects = (os.environ.get("SUBJECTS") or "").strip()
    dry = (os.environ.get("DRY") or "1") != "0"
    force = (os.environ.get("FORCE") or "0") == "1"

    names = [n.strip() for n in raw.split(",") if n.strip()]
    subjects_only = [s.strip() for s in raw_subjects.split(",") if s.strip()]
    if not names and not subjects_only:
        print("FAIL 没给 ISSUES 或 SUBJECTS")
        return

    from pearnly_core import keycloak_admin as kc

    deleted_users, kept, removed = [], [], []

    # 管理客户端按目标环境取；取不到就只在删账号时报错，删单子不受影响。
    admin = None
    if names or subjects_only:
        try:
            admin, target_name = kc.client_for_target(None)
            print("  身份服务目标：%s（%s / %s）" % (target_name, admin.base, admin.realm))
        except Exception as exc:  # noqa: BLE001
            print("  ! 拿不到管理客户端，身份账号将跳过：%s" % exc)

    def _delete_identity(subject: str) -> bool:
        if not admin:
            return False
        try:
            resp = requests.delete(
                admin._url("/users/%s" % subject),
                headers=admin._headers(admin.token()),
                timeout=15,
            )
            if resp.status_code in (204, 404):
                deleted_users.append(subject)
                return True
            print("    ! 身份账号 %s 没删掉：HTTP %s" % (subject[:8], resp.status_code))
        except Exception as exc:  # noqa: BLE001
            print("    ! 身份账号 %s 没删掉：%s" % (subject[:8], exc))
        return False

    for subject in subjects_only:
        print("  孤儿身份账号 %s" % subject)
        if not dry:
            _delete_identity(subject)

    for name in names:
        if not frappe.db.exists("Pearnly Issue", name):
            kept.append((name, "没有这张单"))
            continue

        issue = frappe.get_doc("Pearnly Issue", name)
        active = frappe.get_all(
            "Pearnly Deployment",
            filters={"issue": name, "status": "active"},
            pluck="name",
        )
        if active and not force:
            kept.append((name, "有 active 部署 %s（要先处理站点）" % ", ".join(active)))
            continue

        print(
            "  %s → 公司=%s 邮箱=%s 用户名=%s 部署=%s 身份=%s"
            % (
                name,
                issue.tenant_name,
                issue.account_email,
                issue.account_username,
                ",".join(active) or "-",
                issue.identity_subject or "-",
            )
        )
        if dry:
            removed.append(name)
            continue

        # 1) 身份服务里的账号（只删这张单自己记的那个 subject）
        subject = (issue.identity_subject or "").strip()
        if subject and not dry:
            _delete_identity(subject)

        # 2) 部署（有 active 的已被上面的守卫挡掉，这里只剩 pending/failed/skipped）
        for dep in frappe.get_all("Pearnly Deployment", filters={"issue": name}, pluck="name"):
            frappe.delete_doc("Pearnly Deployment", dep, force=1, ignore_permissions=True)

        # 3) 单子与租户
        tenant = issue.tenant
        frappe.delete_doc("Pearnly Issue", name, force=1, ignore_permissions=True)
        if tenant and frappe.db.exists("Pearnly Tenant", tenant):
            still = frappe.get_all("Pearnly Issue", filters={"tenant": tenant}, pluck="name")
            if not still:
                frappe.delete_doc("Pearnly Tenant", tenant, force=1, ignore_permissions=True)
        removed.append(name)

    frappe.db.commit()

    if dry:
        print("DRY 可删 %d 张：%s" % (len(removed), ", ".join(removed)))
    else:
        print("已删 %d 张：%s" % (len(removed), ", ".join(removed)))
    print("身份账号删除 %d 个" % len(deleted_users))
    for name, why in kept:
        print("  跳过 %s：%s" % (name, why))
