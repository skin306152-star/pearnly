# -*- coding: utf-8 -*-
"""把一个租户**整个抹掉**：站点、数据库、身份账号、OIDC 客户端，以及控制面里关于它的所有记录。

用户 2026-09-19 定："删除就是清空所有，不备份，相当于格式化"。

## 为什么不是"在 Desk 上删一行"

Desk 上删 `Pearnly Tenant` 会被关联挡住（它还挂着发号单），而且就算删掉了，
**站点、数据库、身份服务里的账号都还在** —— 那是"看不见了"，不是"清空了"。

## 怎么做的（顺序不能换）

    点按钮 → 排一个"拆除请求"（步骤：每个部署 drop_site → purge_control）
      ① `drop_site`：宿主机上的执行器先删掉它在 Cowork 原件桶里的全部文件（`<站点>/` 前缀，含预览；
         隐私政策承诺删除，2026-10-05 补），再把站点与它的数据库**直接删掉**（`--no-backup`）。
         识别中间结果桶 7 天自动过期、异地备份按 14 天 / 8 周过期，不在这里删。
      ② `purge_control`：删身份服务里的账号与这个部署的 OIDC 客户端（**只有这一步知道它们是谁**）
      ③ 最后一步报完成时（见 `provisioning.api_report`）：控制面的记录整片删掉
         （部署 / 发号单 / 钱包与流水 / 订阅 / 充值单 / LINE 渠道 / 租户 / 拆除请求本身）

第 ③ 步放在"最后一步报完成"之后，是因为执行器还要往这条请求上写状态 ——
先把请求删了，它就无处可报。

## 安全边界

· 只有登录的操作员能调（控制面只在本机跑，见 ADR-004）；
· 删身份账号**只按记录里的 subject 删**，不按邮箱模糊匹配；
· 删之前先看"这个 subject 还有没有别的租户在用"——共用同一个登录邮箱的另一个租户
  不该被顺带清掉（宁可留下一个账号，也不能误删别人的）。
"""

from __future__ import annotations

import json
import logging

import frappe
from frappe import _

from pearnly_control.account import identity_pools

logger = logging.getLogger(__name__)

TENANT = "Pearnly Tenant"
ISSUE = "Pearnly Issue"
DEPLOYMENT = "Pearnly Deployment"
REQUEST = "Pearnly Provisioning Request"
IDENTITY = "Pearnly Identity"
WALLET = "Pearnly Wallet"
CREDIT = "Pearnly Credit Transaction"
SUBSCRIPTION = "Pearnly Subscription"
TOPUP = "Pearnly Topup Request"
LINE_CHANNEL = "Pearnly LINE Channel"


def _require_operator() -> str:
    from pearnly_control.issues import _require_operator as guard

    return guard()


# ── 这个租户到底有哪些东西会被抹掉（确认框与执行都用它） ────────────────────
def targets_of(tenant: str) -> dict:
    """列清楚"删掉这个租户等于删掉什么"。不做任何写操作。"""
    if not frappe.db.exists(TENANT, tenant):
        # 已经被清掉了：调用方按"没有可删的"处理（幂等）
        return {"tenant": tenant, "exists": False, "deployments": [], "issues": [],
                "sites": [], "accounts": [], "oidc_clients": [], "records": {}}

    deployments = frappe.get_all(
        DEPLOYMENT, filters={"tenant": tenant},
        fields=["name", "product", "site_name", "bench_group", "app", "port", "url", "oidc_client_id", "issue"],
    )
    issues = frappe.get_all(
        ISSUE, filters={"tenant": tenant},
        fields=["name", "account_email", "account_username", "identity_subject", "cowork_identity_subject", "target"],
    )
    counts = {}
    for doctype, field in (
        (WALLET, "tenant"), (CREDIT, "tenant"), (SUBSCRIPTION, "tenant"), (TOPUP, "tenant"),
    ):
        try:
            counts[doctype] = frappe.db.count(doctype, {field: tenant})
        except Exception:
            counts[doctype] = 0
    return {
        "tenant": tenant,
        "exists": True,
        "tenant_name": frappe.db.get_value(TENANT, tenant, "tenant_name"),
        "deployments": deployments,
        "issues": issues,
        "sites": [d["site_name"] for d in deployments],
        "accounts": [i["account_email"] for i in issues if i.get("account_email")],
        "oidc_clients": [d["oidc_client_id"] for d in deployments if d.get("oidc_client_id")],
        "records": counts,
    }


# ── 排队拆除 ────────────────────────────────────────────────────────────────
def _steps_for(targets: dict, request_name: str) -> list[dict]:
    """先拆站点，最后外部清理。每个动作都幂等：已经不在就算做完。"""
    steps = []
    for dep in targets["deployments"]:
        steps.append({
            "step_key": f"{request_name}:drop:{dep['name']}",
            "deployment": dep["name"],
            "action": "drop_site",
            "status": "pending",
            "payload": json.dumps(
                {"site": dep["site_name"], "group": dep["bench_group"]}, ensure_ascii=False
            ),
        })
    steps.append({
        "step_key": f"{request_name}:purge_control",
        "action": "purge_control",
        "status": "pending",
        "payload": json.dumps({"tenant": targets["tenant"], "request": request_name},
                              ensure_ascii=False),
    })
    return steps


@frappe.whitelist(methods=["POST"])
def api_plan_teardown(tenant: str) -> dict:
    """排一个拆除请求（幂等：这个租户已经有未完成的拆除请求就复用它）。"""
    _require_operator()
    if not tenant or not frappe.db.exists(TENANT, tenant):
        frappe.throw(_("Tenant {0} does not exist").format(tenant or "?"))

    targets = targets_of(tenant)
    from pearnly_control.onboarding.teardown_members import prepare
    prepare(tenant, targets["issues"])
    # 发号单是请求的必填字段：拿这个租户名下的单子；一张都没有就现造一张占位单，
    # 让"只有租户、没有单子"这种残局也能删干净。
    issue = targets["issues"][0]["name"] if targets["issues"] else None
    if not issue:
        issue = _placeholder_issue(tenant)

    existing = frappe.db.get_value(
        REQUEST,
        {"idempotency_key": f"teardown:{tenant}"},
        "name",
        order_by="creation desc",
    )
    if existing:
        doc = frappe.get_doc(REQUEST, existing)
        # 失败的步骤退回 pending：用户再点一次就该继续，不是永远卡在失败上
        reset = False
        for step in doc.steps:
            if step.status == "failed":
                step.status = "pending"
                step.output = ""
                reset = True
        if reset:
            doc.status = "pending"
            doc.save(ignore_permissions=True)
            frappe.db.commit()
        _mark_tenant(tenant, "deleting")
        return _state(doc)

    doc = frappe.get_doc({"doctype": REQUEST, "issue": issue, "status": "pending",
                          "idempotency_key": f"teardown:{tenant}"})
    for step in _steps_for(targets, "pending"):
        doc.append("steps", step)
    doc.insert(ignore_permissions=True)
    # 请求名要进**步骤名和 payload 两处**：执行器拿 payload 里的 request 回来清理，
    # 只改步骤名的话它会拿着占位值去调接口 —— 那次调用"成功"了、什么都没删（真踩过）。
    for step in doc.steps:
        step.step_key = step.step_key.replace("pending:", f"{doc.name}:")
        if step.payload and '"pending"' in step.payload:
            step.payload = step.payload.replace('"request": "pending"',
                                                f'"request": "{doc.name}"')
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    _mark_tenant(tenant, "deleting")
    logger.warning("[teardown] 已排入拆除：tenant=%s request=%s sites=%s",
                   tenant, doc.name, targets["sites"])
    return _state(doc)


def _placeholder_issue(tenant: str) -> str:
    """只剩租户、没有发号单时的占位单：把能对的上的信息尽量填上，别留空壳。

    **故意跳过发号单的业务校验**（`ignore_validate`）：这张单子是拆除流程的锚点
    （`Pearnly Provisioning Request.issue` 是必填），不是一次开户。发号单的校验里
    有一条"必须选产品"，而这里恰恰是"产品早就不在了"的残局 —— 不跳过就建不出来，
    表现是**删除租户点下去只弹一句「请选择一个产品」，而界面上根本没有产品可选**
    （2026-09-19 实测，就是用户卡住的那一步）。
    """
    tenant_name = frappe.db.get_value(TENANT, tenant, "tenant_name") or tenant
    doc = frappe.get_doc({
        "doctype": ISSUE,
        "tenant_name": tenant_name,
        "account_email": f"{tenant}@teardown.invalid",
        "account_username": tenant,
        "account_password": frappe.generate_hash(length=16),
        "tenant": tenant,
        "status": "draft",
        "target": frappe.db.get_value("Pearnly Target", {"is_default": 1}, "name"),
        "notes": "Auto-created placeholder for teardown",
    })
    # Frappe 跳过校验的方式是打 flag（`insert(ignore_validate=…)` 没有这个参数，实测 TypeError）。
    # 两个都要：`ignore_validate` 跳过 DocType 自己的规则（"必须选产品"），
    # `ignore_mandatory` 跳过"apps 是必填"—— 这张单子本来就没有、也不该有产品。
    doc.flags.ignore_validate = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _mark_tenant(tenant: str, status: str) -> None:
    try:
        frappe.db.set_value(TENANT, tenant, "status", status, update_modified=True)
        frappe.db.commit()
    except Exception:
        logger.warning("[teardown] 标记租户状态失败 tenant=%s", tenant, exc_info=True)


def _state(doc) -> dict:
    counts = {}
    for step in doc.steps:
        counts[step.status] = counts.get(step.status, 0) + 1
    return {
        "request": doc.name,
        "status": doc.status,
        "counts": {"total": len(doc.steps), **counts},
        "steps": [{"step_key": s.step_key, "status": s.status, "action": s.action} for s in doc.steps],
    }


# ── 执行器在最后一步调用：删身份账号与 OIDC 客户端 ─────────────────────────
@frappe.whitelist(methods=["POST"])
def api_purge_control(request: str) -> dict:
    """拆掉**外部**那些东西：身份账号、这个部署的 OIDC 客户端、它在共用 LINE Login 频道下的 LIFF。

    为什么单独一步、不让执行器直接删：只有控制面知道这些是谁（subject 记在发号单上）。
    """
    _require_operator()
    if not frappe.db.exists(REQUEST, request):
        # **不当作成功**：曾经这里写成"请求不在 → 幂等成功"，结果执行器拿着一个错的
        # 请求名调过来时，这一步被记成完成、账号一个都没删（真踩过）。
        # 执行器只会对自己刚领到的步骤调这个接口，请求不该不存在。
        frappe.throw(_("Teardown request {0} does not exist").format(request))

    doc = frappe.get_doc(REQUEST, request)
    issue_names = {row.issue for row in doc.steps if getattr(row, "issue", None)}
    issue_names.add(doc.issue)
    tenant = None
    for step in doc.steps:
        if step.action == "purge_control" and step.payload:
            try:
                tenant = json.loads(step.payload).get("tenant")
            except ValueError:
                tenant = None
    targets = targets_of(tenant) if tenant else {"deployments": [], "issues": []}
    if tenant:
        from pearnly_control.onboarding.teardown_members import prepare
        prepare(tenant, targets["issues"])

    from pearnly_core import keycloak_admin as kc

    removed_users, kept_users, removed_clients = [], [], []
    # 每张单在各账号池里建的身份（Cowork 与 ERP / DMS 分池，见 identity_pools）：按池删。
    subjects = []
    for name in _issue_names_for(targets, doc):
        issue = frappe.db.get_value(ISSUE, name, list(identity_pools.SUBJECT_FIELDS.values()), as_dict=True) or {}
        for product, subject in identity_pools.subjects_of(issue):
            subjects.append((name, product, subject))

    # 有东西要删就必须拿到管理客户端：拿不到就**如实失败**。
    # 曾经这里写过 `except: client = None` 悄悄跳过 —— 结果这一步"成功"了、
    # 账号和 OIDC 客户端一个都没删（真踩过，验收脚本当场抓到）。
    for issue_name, product, subject in subjects:
        client, _target = kc.client_for_target(frappe.db.get_value(ISSUE, issue_name, "target"), product)
        if _subject_used_elsewhere(subject, product, keep_issues={name for name, _p, _s in subjects} | {doc.issue}):
            # 同一个登录邮箱还挂在别的租户上：**不删**，如实记下来
            kept_users.append(subject)
            continue
        if client and client.delete_user(subject):
            removed_users.append(subject)
        frappe.db.delete(IDENTITY, {"subject": subject})

    for dep in targets["deployments"]:
        client_id = dep.get("oidc_client_id")
        if not client_id:
            continue
        client, _target = kc.client_for_target(frappe.db.get_value(ISSUE, dep["issue"], "target"), dep.get("product"))
        if client and client.delete_oidc_client(client_id):
            removed_clients.append(client_id)

    # 每个部署的 LIFF 建在全平台共用的 LINE Login 频道下（LINE 上限 30 个）：拆租户就腾出名额。
    from pearnly_control.provisioning import remove_liff
    removed_liffs = []
    for dep in targets["deployments"]:
        liff_id = frappe.db.get_value(LINE_CHANNEL, {"deployment": dep["name"]}, "liff_id")
        if liff_id and remove_liff(frappe.db.get_value(ISSUE, dep["issue"], "target"), liff_id):
            removed_liffs.append(liff_id)

    from pearnly_control import infra, billing_remote
    if any(infra.is_cloud(row.get("target")) for row in targets["issues"]):
        billing_remote.call("purge", tenant=tenant)

    frappe.db.commit()
    # 复核：该删的都真删了才算这一步成功（"报了完成但其实还在"是最难查的一种）
    left_users = [s for _n, _p, s in subjects if s not in removed_users and s not in kept_users]
    if left_users:
        frappe.throw(_("These accounts could not be deleted: {0}").format(", ".join(left_users)))
    return {
        "purged": True,
        "removed_users": removed_users,
        "kept_users": kept_users,
        "removed_oidc_clients": removed_clients,
        "removed_liffs": removed_liffs,
    }


def _issue_names_for(targets: dict, request_doc) -> list[str]:
    names = [row["name"] for row in targets.get("issues") or []]
    if request_doc.issue and request_doc.issue not in names:
        names.append(request_doc.issue)
    return names


def _subject_used_elsewhere(subject: str, product=None, *, keep_issues: set) -> bool:
    """这个 subject 还有没有**别的**发号单在用（别的租户共用同一个登录邮箱；只看同一个账号池）。"""
    rows = frappe.get_all(
        ISSUE, filters={identity_pools.subject_field(product): subject}, fields=["name"], limit=50
    )
    return any(row["name"] not in keep_issues for row in rows)


# ── 最后一步报完成之后：把控制面的记录整片删掉 ──────────────────────────────
def purge_records(request: str) -> dict:
    """删掉这个租户在控制面的所有记录。**由 `provisioning.api_report` 在拆除完成时调用。**

    顺序：先删引用别人的，再删被引用的，最后删请求自己（带子步骤）。
    """
    if not frappe.db.exists(REQUEST, request):
        return {"purged": False}
    doc = frappe.get_doc(REQUEST, request)
    tenant = None
    for step in doc.steps:
        if step.action == "purge_control" and step.payload:
            try:
                tenant = json.loads(step.payload).get("tenant")
            except ValueError:
                tenant = None
    issue_names = _issue_names_for(targets_of(tenant) if tenant else {"issues": []}, doc)
    deployment_names = [
        row["name"] for row in frappe.get_all(
            DEPLOYMENT, filters={"tenant": tenant} if tenant else {"issue": doc.issue},
            fields=["name"],
        )
    ]

    removed = {}
    # 拆除流程就是"要删站点"的那条合法路径：它已经先让执行器把站点与库删掉了
    # （drop_site 那一步），这里才轮到清记录。所以整段打上放行标记，绕开
    # `PearnlyDeployment.on_trash` 里"别单删部署"的保护。
    from pearnly_control.pearnly_control.doctype.pearnly_deployment.pearnly_deployment import (
        TEARDOWN_FLAG,
    )

    frappe.flags[TEARDOWN_FLAG] = True
    for doctype, filters in (
        (CREDIT, {"tenant": tenant} if tenant else None),
        (TOPUP, {"tenant": tenant} if tenant else None),
        (SUBSCRIPTION, {"tenant": tenant} if tenant else None),
        (WALLET, {"tenant": tenant} if tenant else None),
        (LINE_CHANNEL, {"deployment": ("in", deployment_names)} if deployment_names else None),
        (IDENTITY, {"deployment_id": ("in", deployment_names)} if deployment_names else None),
    ):
        if not filters:
            continue
        names = frappe.get_all(doctype, filters=filters, pluck="name")
        removed[doctype] = len(names)
        for name in names:
            frappe.delete_doc(doctype, name, ignore_permissions=True, force=True, delete_permanently=True)

    request_names = frappe.get_all(REQUEST, filters={"issue": ("in", issue_names)}, pluck="name")
    for name in request_names:
        if name != request:
            frappe.delete_doc(REQUEST, name, ignore_permissions=True, force=True, delete_permanently=True)
    for name in deployment_names:
        frappe.delete_doc(DEPLOYMENT, name, ignore_permissions=True, force=True, delete_permanently=True)
    for name in issue_names:
        if name and frappe.db.exists(ISSUE, name):
            frappe.delete_doc(ISSUE, name, ignore_permissions=True, force=True, delete_permanently=True)
    if tenant and frappe.db.exists(TENANT, tenant):
        frappe.delete_doc(TENANT, tenant, ignore_permissions=True, force=True, delete_permanently=True)
    # 请求（带子步骤）最后删：执行器已经报完最后一步，没有谁还要往上写了
    frappe.delete_doc(REQUEST, request, ignore_permissions=True, force=True, delete_permanently=True)
    # 放行标记只在这一次拆除里有效：留在进程上会让**之后**的误删也不再被拦住。
    frappe.flags.pop(TEARDOWN_FLAG, None)
    frappe.db.commit()
    logger.warning("[teardown] 控制面记录已清空：request=%s tenant=%s removed=%s",
                   request, tenant, removed)
    return {"purged": True, "removed": removed}


@frappe.whitelist()
def api_targets(tenant: str) -> dict:
    """确认框要显示的内容：删掉这个租户等于删掉什么。"""
    _require_operator()
    return targets_of(tenant)


__all__ = ["api_plan_teardown", "api_purge_control", "api_targets", "purge_records", "targets_of"]
