"""拆池（2026-09-26）前开的 Cowork 租户换账号池：站点从业务账号池搬到 Cowork 自己的账号池，或搬回（回退）。

只在本机管理后台由运维运行（不是界面功能）。先只读看一遍，再搬：

    bench --site <管理站点> execute pearnly_control.account.repool.plan --kwargs "{'deployment': 'DEP-…'}"
    bench --site <管理站点> execute pearnly_control.account.repool.move \\
        --kwargs "{'deployment': 'DEP-…', 'pool': 'cowork', 'confirm': '<站点域名>'}"

`move` 做的事（每一步可重复执行，中途失败照原命令重跑）：

只搬拆池前开在业务池的站点；回退只给本工具搬过的站点（set_oidc 载荷记着 `moved_from`）——开户就在 Cowork 池的站点
不能「回退」，它在 Cowork 池的号绝不会被删。

1. 开户管理员：搬去 Cowork 池时在那里建号（同一用户名、邮箱），身份记在发号单 `cowork_identity_subject`；
   密码按 `owner_password`：`initial` 用开户单上的初始密码（与发号时一样，不经过任何人），`none` 不设密码
   （用户自己在登录页「忘记密码」用邮箱设）。目标池里已有同一邮箱的号而发号单没记着它 → 停下，绝不按邮箱认领。
   回退（`pool='business'`）用业务池原来那个号；排好站点步骤后删掉 Cowork 池里发号单记着的那个号（只按记着的 subject 删）。
2. 目标池里建 / 更新本站的 OIDC 客户端（同一个 client_id = 站点域名），密钥记到部署上。
3. 计费服务：团队成员放进目标池（`move_members`：已有本服务建的号就沿用，否则建一个不带密码的，老板在团队管理里
   重设密码），fatcc.com 入口登记与管理员身份改成目标池（`provision_scope`，同开户的 register）。
4. 开户计划上重排站点步骤：set_oidc（新签发方）→ bind_user（管理员的新身份）→ 每个成员一步 bind_user（只绑不建）。
   本机执行器照常逐步跑。站点签发方变了，LINE 绑定改回待同步，每小时对账在新池重挂。不重启产品
   （同组其他租户不受影响），站点按配置缓存最多一两分钟后用新签发方。

旧池里的号、OIDC 客户端与站点上旧签发方的身份绑定都不删：回退靠它们。
"""
import hashlib
import json

import frappe

from pearnly_control import billing_remote, infra, provisioning
from pearnly_control.account import identity_pools

# 池名 → 代表产品（`identity_pools.REPRESENTATIVE`、`keycloak_admin.client_for_target` 用它选池）。
POOLS = {"business": "erp", "cowork": "cowork"}
OWNER_PASSWORDS = ("initial", "none")


class RepoolError(ValueError):
    """运维看的拒绝原因（不是界面文案）。"""


def _context(deployment):
    provisioning._require_operator()
    dep = frappe.get_doc(provisioning.DEPLOYMENT, deployment)
    issue = frappe.get_doc(provisioning.ISSUE, dep.issue) if dep.get("issue") else None
    if not issue or dep.product != "cowork" or not infra.is_cloud(issue.target):
        raise RepoolError("Only a cloud Cowork deployment has a second account pool")
    if dep.get("status") != "active" or not dep.get("site_name"):
        raise RepoolError("The deployment is not active")
    if frappe.db.get_value("Pearnly Tenant", issue.tenant, "status") != "active":
        raise RepoolError("The tenant is not active")
    request = frappe.db.get_value(provisioning.REQUEST, {"idempotency_key": dep.issue}, "name")
    if not request:
        raise RepoolError("The deployment has no provisioning plan")
    return dep, issue, request


def current_pool(issue) -> str:
    """控制面记录上这个 Cowork 站点现在按哪个池登录（`identity_pools.site_pool`）。"""
    return "cowork" if identity_pools.site_pool(issue, "cowork") == "cowork" else "business"


def _owner_in(client, issue):
    """目标池里开户管理员邮箱已有的号（没有就 None）。"""
    return client.find_user_id_by_email(issue.account_email)


def _issuer(issue, pool):
    return provisioning._issuer_url(issue.target, POOLS[pool])


def plan(deployment, pool="cowork") -> dict:
    """只读：现在签在哪个池、要搬去哪、管理员与成员在目标池的情况、会排哪几步。"""
    from pearnly_core import keycloak_admin as kc

    if pool not in POOLS:
        raise RepoolError("pool must be cowork or business")
    dep, issue, request = _context(deployment)
    current = current_pool(issue)
    client, _target = kc.client_for_target(issue.target, POOLS[pool])
    owner_existing = _owner_in(client, issue)
    recorded = issue.get(identity_pools.SUBJECT_FIELDS["cowork_"])
    members = billing_remote.move_members(issue.tenant, dep.product, _issuer(issue, pool), dry_run=True)
    moved_from = _moved_from(request, dep)
    return {
        "deployment": dep.name, "site": dep.site_name, "tenant": issue.tenant,
        "current_pool": current, "target_pool": pool, "already_there": current == pool,
        "moved_from": moved_from,
        "allowed": (current == "business" or moved_from == "business") if pool == "cowork" else moved_from == "business",
        "issuer": _issuer(issue, pool),
        "owner": {"email": issue.account_email, "username": issue.account_username,
                  "business_subject": issue.get("identity_subject"), "cowork_subject": recorded,
                  "in_target_pool": owner_existing,
                  # 目标池里已有同邮箱的号、发号单却没记着它：move 会停下
                  "blocked": bool(pool == "cowork" and owner_existing and owner_existing != recorded),
                  "initial_password_kept": bool(issue.get_password("account_password", raise_exception=False))},
        "members": members,
        "steps": ["set_oidc", "bind_user"] + [f"bind_member:{m['email']}" for m in (members.get("members") or [])
                                              if m.get("action") != "incomplete"],
    }


def _owner_subject(issue, pool, owner_password):
    """开户管理员在目标池里的身份（搬去 Cowork 池时按需建号）。"""
    from pearnly_core import keycloak_admin as kc

    if pool == "business":
        subject = issue.get("identity_subject")
        if not subject:
            raise RepoolError("The issue has no business-pool identity to return to")
        return subject
    recorded = issue.get(identity_pools.SUBJECT_FIELDS["cowork_"])
    client, _target = kc.client_for_target(issue.target, POOLS[pool])
    existing = _owner_in(client, issue)
    if recorded:
        if existing != recorded:
            raise RepoolError("The Cowork-pool account recorded on the issue is missing or differs")
        return recorded
    if existing:
        raise RepoolError("The Cowork pool already has an account with this email; it is never adopted by email")
    if owner_password not in OWNER_PASSWORDS:
        raise RepoolError("owner_password must be initial or none")
    password = issue.get_password("account_password", raise_exception=False) if owner_password == "initial" else None
    if owner_password == "initial" and not password:
        raise RepoolError("The issue has no initial password; use owner_password='none'")
    subject = client.create_user(issue.account_email, username=issue.account_username,
                                 first_name=(issue.account_username or "")[:140], password=password)
    frappe.db.set_value(provisioning.ISSUE, issue.name, identity_pools.SUBJECT_FIELDS["cowork_"], subject)
    frappe.db.commit()  # 号建出来就记住：重跑时认得它，不会再建第二个
    issue.set(identity_pools.SUBJECT_FIELDS["cowork_"], subject)
    return subject


def _moved_from(request, dep):
    """本工具搬过的站点在 set_oidc 载荷里记着从哪个池搬来的（开户时的载荷没有这一项）。"""
    step = next((s for s in frappe.get_doc(provisioning.REQUEST, request).steps
                 if s.step_key == f"{dep.name}:set_oidc"), None)
    return (json.loads(step.payload or "{}").get("moved_from") if step else None) or None


def _member_step_key(dep, email):
    return f"{dep.name}:bind_member:{hashlib.sha256(email.encode()).hexdigest()[:16]}"


def _queue(request, dep, steps):
    """把这些步骤排进开户计划：已有同名步骤就换载荷并改回待办，没有就追加在末尾（执行器按顺序逐步跑）。"""
    req = frappe.get_doc(provisioning.REQUEST, request, for_update=True)
    for step_key, action, payload in steps:
        step = next((s for s in req.steps if s.step_key == step_key), None)
        if step and step.status == "running":
            raise RepoolError(f"Step {step_key} is running; retry after it finishes")
        if step:
            step.payload = json.dumps(payload, ensure_ascii=False)
            step.status, step.output, step.finished_at = "pending", "", None
        else:
            req.append("steps", {"step_key": step_key, "deployment": dep.name, "action": action,
                                 "status": "pending", "payload": json.dumps(payload, ensure_ascii=False)})
    req.status, req.error, req.finished_at = "pending", "", None
    req.save(ignore_permissions=True)
    frappe.db.commit()


def move(deployment, pool="cowork", confirm="", owner_password="initial") -> dict:
    """把这个 Cowork 站点搬到 `pool`（cowork：Cowork 自己的池；business：回退到业务池）。`confirm` 必须是站点域名。"""
    if pool not in POOLS:
        raise RepoolError("pool must be cowork or business")
    dep, issue, request = _context(deployment)
    if confirm != dep.site_name:
        raise RepoolError("confirm must be the site domain")
    if any(s.status == "running" for s in frappe.get_doc(provisioning.REQUEST, request).steps
           if s.deployment == dep.name):
        raise RepoolError("A provisioning step of this site is running; retry after it finishes")
    # 只搬拆池前开在业务池的站点；回退只给本工具搬过的站点（开户就在 Cowork 池的站点，它的号绝不能被回退删掉）
    moved_from = _moved_from(request, dep)
    if pool == "cowork" and current_pool(issue) != "business" and moved_from != "business":
        raise RepoolError("This site was opened on the Cowork pool; there is nothing to move")
    if pool == "business" and moved_from != "business":
        raise RepoolError("Only a site moved here from the business pool can go back to it")
    issuer = _issuer(issue, pool)
    # 1. 开户管理员在目标池的身份（回退只读；搬去 Cowork 池时按需建号并记在发号单上，重跑认得它）
    owner = _owner_subject(issue, pool, owner_password)
    # 2. 目标池里本站的 OIDC 客户端（建 / 更新，读回密钥记到部署上）
    provisioning._ensure_oidc_client(dep, issue.target, POOLS[pool])
    dep.reload()
    # 3. 计费服务：成员进目标池；入口登记与管理员身份改成目标池
    moved = billing_remote.move_members(issue.tenant, dep.product, issuer)
    if moved.get("error"):
        raise RepoolError(f"Billing could not move the members: {moved['error']}")
    billing_remote.provision_scope(issue.tenant, dep.product, issue.tenant_name, owner_subject=owner,
                                   site=dep.site_name, issuer=issuer, owner_username=issue.account_username,
                                   owner_email=issue.account_email)
    # 4. 站点步骤
    site, group = dep.site_name, dep.bench_group
    steps = [
        (f"{dep.name}:set_oidc", "set_oidc", {
            "site": site, "group": group, "product": dep.product, "issuer": issuer,
            "client_id": dep.oidc_client_id, "client_secret": provisioning.oidc_secret_of(dep.name),
            "redirect_uri": f"{infra.deployment_base_url(dep)}/api/method/pearnly_core.identity.callback",
            "moved_from": "business",  # 站点步骤不看它；回退只认记着它的站点（`_moved_from`）
            **provisioning.target_credentials(issue.target, POOLS[pool])}),
        (f"{dep.name}:bind_user", "bind_user", {
            "site": site, "group": group, "email": issue.account_email,
            "full_name": issue.get("account_username") or issue.tenant_name, "subject": owner,
            "deployment": dep.name}),
    ]
    members = [m for m in moved.get("members") or [] if m.get("subject")]
    steps += [(_member_step_key(dep, m["email"]), "bind_user", {
        "site": site, "group": group, "email": m["email"], "full_name": m["username"], "subject": m["subject"],
        "deployment": dep.name, "member": True}) for m in members]
    _queue(request, dep, steps)
    removed = None
    if pool == "business":
        # 回退：Cowork 池里本工具建的那个管理员号删掉（只按发号单记着的 subject），发号单不再记它
        from pearnly_core import keycloak_admin as kc

        field = identity_pools.SUBJECT_FIELDS["cowork_"]
        removed = issue.get(field)
        if removed:
            kc.client_for_target(issue.target, POOLS["cowork"])[0].delete_user(removed)
            frappe.db.set_value(provisioning.ISSUE, issue.name, field, None)
            frappe.db.commit()
    return {"deployment": dep.name, "site": site, "pool": pool, "issuer": issuer, "owner_subject": owner,
            "members": moved.get("members") or [], "line_links_released": moved.get("line_links_released", 0),
            "queued": [key for key, _action, _payload in steps], "removed_cowork_owner": removed}
