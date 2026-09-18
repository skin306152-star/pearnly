# -*- coding: utf-8 -*-
"""开通：把发号单变成真的站点。

分工（重要）：

- **控制面（本文件）**负责"要做什么、做到哪了"：把发号单拆成有序步骤、落库、
  逐步收状态；它**不执行**任何建站命令。
- **受限开通代理（`scripts/sample-provision.sh`）**负责"真的去做"：它跑在宿主机上，
  按控制面给的步骤，用 `bench new-site` / `install-app` / `set-config` 落到对应运行组。

为什么不干脆让控制面容器自己建站：控制面跑在容器里，容器里没有 docker，
也**不该**给它 docker socket（那等于给容器 root）。P1.05 要求"部署代理独立、
运行进程不持 root"，这里就是那条约束在样板环境的落地。

步骤动作（执行器认识这些名字）：
  new_site      {site, group, admin_password}
  install_app   {site, group, app}
  set_config    {site, group, key, value}
  restart_web   {group}
  bind_user     {site, group, email, full_name, subject, issuer}
  mark_active   {deployment}

幂等：请求按幂等键唯一；每个步骤带稳定的 step_key，重复执行同一个 key 不会重复建资源
（执行器侧对"已存在"视为成功，见脚本里的处理）。
"""

import json

import frappe
from frappe import _
from frappe.utils import now

from pearnly_control import features as features_mod
from pearnly_control import infra
from pearnly_core import keycloak_admin as kc

REQUEST = "Pearnly Provisioning Request"
DEPLOYMENT = "Pearnly Deployment"
ISSUE = "Pearnly Issue"
LINE_CHANNEL = "Pearnly LINE Channel"


def _require_operator() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("未登录"), frappe.AuthenticationError)
    return user


# ── 计划 ────────────────────────────────────────────────────────────────────


def build_plan(issue: str, idempotency_key: str | None = None) -> dict:
    """把发号单拆成步骤。幂等：同一幂等键只会有一套请求与部署。"""
    _require_operator()
    issue = frappe.get_doc(ISSUE, issue)
    key = (idempotency_key or issue.name).strip()

    existing = frappe.db.get_value(REQUEST, {"idempotency_key": key}, "name")
    if existing:
        # 已经规划过：不重建资源，但**同步步骤定义**
        # （流程演进时，已经开了一半的单子能靠重跑 plan 跟上，不用从头来）
        req = frappe.get_doc(REQUEST, existing)
        _sync_steps(req)
        # 每次都重算：补过步骤的单子状态会停在 done，不重算就永远没人执行新步骤
        _refresh_request_status(req)
        return state(existing, allow_pending_create=False)

    license_ok = issue.status == "issued"
    if not license_ok:
        frappe.throw(_("发号单 {0} 状态是 {1}，只有 issued 的发号单才能开通").format(
            issue.name, issue.status))

    request = frappe.get_doc({
        "doctype": REQUEST,
        "idempotency_key": key,
        "issue": issue.name,
        "status": "pending",
        "started_at": now(),
    })
    request.insert()

    deployments = []
    steps = []
    for row in issue.apps:
        dep = _ensure_deployment(issue, row)
        deployments.append(dep.name)
        steps.extend(_steps_for(issue, dep, row))

    for step in steps:
        request.append("steps", step)
    request.save(ignore_permissions=True)
    frappe.db.commit()

    result = state(request.name, allow_pending_create=False)
    result["deployments"] = deployments
    return result


def _sync_steps(req) -> int:
    """让请求里的步骤与当前流程定义一致：缺的补上，**待执行的刷新参数**。

    为什么要刷新参数而不只是补步骤：步骤的 payload 是规划时算好存进库的。
    如果只补新步骤，那"改了生成逻辑"（比如工作区要带产品模块名）对已经存在的
    步骤永远不生效 —— 表现为改了代码、重跑开通，结果还是旧的（踩过）。

    只刷新 `pending` / `failed` 的步骤：`done` 的是既成事实，改了就是骗人；
    `skipped` 是"本单不需要"，也不该被翻回。
    """
    issue = frappe.get_doc(ISSUE, req.issue)
    have = {s.step_key: s for s in req.steps}
    changed = 0
    for row in issue.apps:
        dep = frappe.db.get_value(DEPLOYMENT, {"site_name": (row.domain or "").lower()}, "name")
        if not dep:
            continue
        dep_doc = frappe.get_doc(DEPLOYMENT, dep)
        for step in _steps_for(issue, dep_doc, row):
            existing = have.get(step["step_key"])
            if existing is None:
                req.append("steps", step)
                changed += 1
            elif existing.status in ("pending", "failed") and existing.payload != step["payload"]:
                existing.payload = step["payload"]
                changed += 1
    if changed:
        req.save(ignore_permissions=True)
        frappe.db.commit()
        # 关键：补了步骤就要重算请求状态，否则它还停在 done，
        # 而 next_steps 只挑 pending/running/partial，新步骤永远没人执行
        _refresh_request_status(req)
    return changed


def _ensure_deployment(issue, row):
    """一个产品一条部署。同一个站点已经存在就复用（幂等）。"""
    site_name = (row.domain or "").strip().lower()
    if not site_name:
        frappe.throw(_("发号单里 {0} 这一行没有域名，无法建站").format(row.app))

    name = frappe.db.get_value(DEPLOYMENT, {"site_name": site_name}, "name")
    if name:
        return frappe.get_doc(DEPLOYMENT, name)

    other = frappe.db.get_value(DEPLOYMENT, {"site_name": site_name, "issue": ("!=", issue.name)}, "name")
    if other:
        frappe.throw(_("站点名 {0} 已被部署 {1} 占用").format(site_name, other))

    dep = frappe.get_doc({
        "doctype": DEPLOYMENT,
        "status": "pending",
        "product": row.app,
        "tenant": issue.tenant,
        "site_name": site_name,
        "bench_group": infra.group_of(row.app),
        "app": infra.app_of(row.app),
        "port": infra.port_of(row.app),
        "url": infra.local_url(site_name, row.app),
        "issue": issue.name,
        "features": _feature_rows(issue, row.app),
    })
    dep.insert()
    _ensure_oidc_client(dep, issue.target)
    return dep


def _ensure_oidc_client(dep, target: str | None = None) -> None:
    """给这个部署建一个自己的 OIDC 客户端（幂等），并记在部署上。

    回调地址按站点域名与端口算 —— 这正是"共用客户端不行"的原因。
    """
    client, _target_name = kc.client_for_target(target)
    urls = [f"http://{dep.site_name}:{dep.port}/*"]
    origins = [f"http://{dep.site_name}:{dep.port}"]
    result = client.create_oidc_client(dep.site_name, urls, origins)
    dep.oidc_client_id = result["client_id"]
    dep.oidc_client_secret = result["client_secret"]
    dep.save(ignore_permissions=True)
    frappe.db.commit()


def oidc_secret_of(deployment: str) -> str:
    """取该部署客户端密钥（明文）。只用于写进目标站点配置，不返回给前端。"""
    from frappe.utils.password import get_decrypted_password

    return get_decrypted_password(
        DEPLOYMENT, deployment, "oidc_client_secret", raise_exception=False
    ) or ""


def target_credentials() -> dict:
    """开户目标上的**realm 级**凭据，开通时要一起写进租户站点。

    为什么必须下发，而不是让站点自己想办法：改密功能要用这两个客户端 ——

    - `pearnly-admin`：以 client_credentials 换管理员令牌，去身份服务改密码；
    - `pearnly-password-check`：只开密码授权，用来校验用户填的"当前密码"对不对。

    它们是 **realm 级**的（同一 realm 下所有租户共用一套），所以存在开户目标上最合适。

    踩过的坑：这两个凭据以前只在样板站上手工写过，**开通流程从来没下发** ——
    结果是"改密在样板站验过、在真租户站上一直是坏的"（报"取管理令牌失败"，
    因为 client_id/secret 读出来是 None）。样板站能过不代表租户站能用。
    """
    from frappe.utils.password import get_decrypted_password

    _client, target = kc.client_for_target(None)
    row = frappe.db.get_value(
        "Pearnly Target",
        {"target_name": target},
        ["admin_client_id", "password_check_client_id"],
        as_dict=True,
    ) or {}
    return {
        "admin_client_id": row.get("admin_client_id") or "",
        "admin_client_secret": get_decrypted_password(
            "Pearnly Target", target, "admin_client_secret", raise_exception=False
        ) or "",
        "password_check_client_id": row.get("password_check_client_id") or "",
        "password_check_client_secret": get_decrypted_password(
            "Pearnly Target", target, "password_check_client_secret", raise_exception=False
        ) or "",
    }


def _feature_rows(issue, product) -> list[dict]:
    return [
        {"feature": row.feature}
        for row in (issue.get(f"{product}_features") or [])
    ]


def _issuer_url() -> str:
    """统一身份签发方。必须浏览器与容器都能访问到同一个字符串。

    取目标环境配的地址（本机样板是 http://keycloak.localhost:8105/realms/pearnly-business），
    与各样板站点用的完全一致，避免出现两套 issuer。
    """
    _client, target = kc.client_for_target(None)
    row = frappe.db.get_value(
        "Pearnly Target", {"target_name": target}, ["base_url", "realm"], as_dict=True
    )
    if not row:
        frappe.throw(_("取不到开户目标，无法确定身份服务地址"))
    return f"{row.base_url.rstrip('/')}/realms/{row.realm}"


def _line_payload(dep, site: str, group: str) -> dict:
    """该部署的 LINE 渠道配置。没有配渠道就给出空凭据，执行器会把这步标成 skipped。

    为什么要"没有也走一步"：状态机里显式记 skipped（而不是不生成步骤），
    看进度的人就能一眼分清"这个租户还没配 LINE"和"这一步被漏了"。
    """
    channel = frappe.db.get_value(
        LINE_CHANNEL, {"deployment": dep.name, "status": ("!=", "suspended")},
        ["channel_id", "channel_secret", "channel_access_token", "liff_id"],
        as_dict=True,
    )
    payload = {
        "site": site,
        "group": group,
        "product": dep.product,
        "webhook_url": f"http://{site}:{dep.port}/api/method/pearnly_core.line.webhook",
    }
    if not channel:
        payload["configured"] = False
        return payload

    from frappe.utils.password import get_decrypted_password

    def _secret(field: str) -> str:
        return get_decrypted_password(
            LINE_CHANNEL, f"LINE-{dep.name}", field, raise_exception=False
        ) or ""

    payload.update({
        "configured": True,
        "channel_id": channel.channel_id or "",
        "liff_id": channel.liff_id or "",
        "channel_secret": _secret("channel_secret"),
        "channel_access_token": _secret("channel_access_token"),
    })
    return payload


def _roles_for(issue, dep) -> dict:
    """该部署要建的角色与权限 = 开单上**勾了**的功能。

    这是"未勾选的功能不可访问"的执行入口：没勾的功能不会出现在这里，
    于是租户站点上就不会有对应角色，那个 DocType 也就访问不了。
    """
    codes = [row.feature.split("-", 1)[-1] for row in (dep.features or [])]
    spec = features_mod.roles_payload(dep.product, codes)
    spec.update({
        "site": dep.site_name,
        "group": dep.bench_group,
        "admin_user": issue.account_email,
    })
    return spec


def _workspace_for(issue, dep) -> dict:
    """该租户的工作区 + 它能看到的角色（本租户自己的角色，不跨租户）。"""
    codes = [row.feature.split("-", 1)[-1] for row in (dep.features or [])]
    spec = features_mod.workspace_payload(dep.product, codes, title=issue.tenant_name)
    roles = features_mod.roles_payload(dep.product, codes)
    spec["site"] = dep.site_name
    spec["group"] = dep.bench_group
    # 把**买到的完整功能码**一起带过去：站点侧要拿它渲染菜单
    # （`pearnly_core.shell.describe`）。注意这里不是 roles —— roles 只含"有可挡对象"
    # 的功能，而菜单要显示的是买到的全部（例如"首页（订阅与计费）"就没有 DocType）。
    spec["features"] = codes
    spec["roles"] = (
        list(roles.get("base_roles") or [])
        + ([roles["admin_role"]] if roles.get("admin_role") else [])
        + [r["role"] for r in roles.get("roles") or []]
    )
    return spec


def _steps_for(issue, dep, row) -> list[dict]:
    """一个部署的步骤序列。顺序有依赖：先有站点才能装 App，装完才能配、再绑人。"""
    site, group = dep.site_name, dep.bench_group
    steps = [
        {"step_key": f"{dep.name}:new_site", "deployment": dep.name,
         "action": "new_site", "status": "pending",
         "payload": json.dumps({"site": site, "group": group}, ensure_ascii=False)},
        {"step_key": f"{dep.name}:install_core", "deployment": dep.name,
         "action": "install_app", "status": "pending",
         "payload": json.dumps({"site": site, "group": group, "app": infra.CORE_APP},
                               ensure_ascii=False)},
        {"step_key": f"{dep.name}:install_product", "deployment": dep.name,
         "action": "install_app", "status": "pending",
         "payload": json.dumps({"site": site, "group": group, "app": dep.app},
                               ensure_ascii=False)},
        {"step_key": f"{dep.name}:set_oidc", "deployment": dep.name,
         "action": "set_oidc", "status": "pending",
         "payload": json.dumps({
             "site": site, "group": group,
             "product": dep.product,
             "issuer": _issuer_url(),
             "client_id": dep.oidc_client_id,
             "client_secret": oidc_secret_of(dep.name),
             "redirect_uri": f"http://{site}:{dep.port}/api/method/pearnly_core.identity.callback",
             # realm 级的两个客户端凭据也要一起写：改密功能依赖它们
             # （只写本部署的 client 会导致"能登录但改不了密码"）
             **target_credentials(),
         }, ensure_ascii=False)},
        {"step_key": f"{dep.name}:restart_web", "deployment": dep.name,
         "action": "restart_web", "status": "pending",
         "payload": json.dumps({"group": group}, ensure_ascii=False)},
        {"step_key": f"{dep.name}:finalize_site", "deployment": dep.name,
         "action": "finalize_site", "status": "pending",
         "payload": json.dumps({"site": site, "group": group}, ensure_ascii=False)},
        {"step_key": f"{dep.name}:set_line", "deployment": dep.name,
         "action": "set_line", "status": "pending",
         "payload": json.dumps(_line_payload(dep, site, group), ensure_ascii=False)},
        {"step_key": f"{dep.name}:bind_user", "deployment": dep.name,
         "action": "bind_user", "status": "pending",
         "payload": json.dumps({
             "site": site, "group": group,
             "email": issue.account_email,
             "full_name": issue.account_name or issue.tenant_name,
             "subject": issue.identity_subject,
             "deployment": dep.name,
         }, ensure_ascii=False)},
        {"step_key": f"{dep.name}:set_roles", "deployment": dep.name,
         "action": "set_roles", "status": "pending",
         "payload": json.dumps(_roles_for(issue, dep), ensure_ascii=False)},
        {"step_key": f"{dep.name}:set_workspace", "deployment": dep.name,
         "action": "set_workspace", "status": "pending",
         "payload": json.dumps(_workspace_for(issue, dep), ensure_ascii=False)},
        {"step_key": f"{dep.name}:mark_active", "deployment": dep.name,
         "action": "mark_active", "status": "pending",
         "payload": json.dumps({"deployment": dep.name}, ensure_ascii=False)},
    ]
    return steps


# ── 执行器接口 ──────────────────────────────────────────────────────────────


def next_steps(limit: int = 20) -> list[dict]:
    """给执行器取待办步骤。只返回**所有依赖都已完成**的那些，不跳步。"""
    _require_operator()
    out = []
    # 按**步骤**驱动，不按请求状态驱动。
    # 坑：曾经按 `status in (pending, running, partial)` 过滤请求，结果一张单
    # 的步骤被重置回 pending 之后，只要请求状态还停在 done，就永远没人执行它
    # （表现是"没有待办步骤了"，而实际上有）。步骤是事实，请求状态是它的投影。
    requests = frappe.get_all(
        "Pearnly Provisioning Step",
        filters={"status": ("in", ["pending", "running"])},
        pluck="parent", distinct=True, limit_page_length=0,
    )
    for req_name in requests:
        req = frappe.get_doc(REQUEST, req_name)
        for step in req.steps:
            if len(out) >= limit:
                break
            if step.status in ("done", "skipped"):
                continue
            if step.status == "failed":
                # 失败就停在这个部署上，不往下跳（后面的步骤依赖它）
                break
            if _blocked_by_previous(req, step):
                break
            out.append({
                "request": req.name,
                "step_key": step.step_key,
                "action": step.action,
                "payload": json.loads(step.payload or "{}"),
            })
            break
    return out


def _blocked_by_previous(req, step) -> bool:
    """同一步骤所属部署里，它前面还有没做完的步骤就不能做它。"""
    for other in req.steps:
        if other.name == step.name:
            return False
        if other.deployment and other.deployment != step.deployment:
            continue
        if other.status not in ("done", "skipped"):
            return True
    return False


def report_step(step_key: str, status: str, output: str = "") -> dict:
    """执行器回报一步的结果。全部做完才把请求标成 done（不虚报）。"""
    _require_operator()
    if status not in ("running", "done", "failed", "skipped"):
        frappe.throw(_("未知步骤状态：{0}").format(status))

    found = frappe.db.get_value(
        "Pearnly Provisioning Step", {"step_key": step_key}, ["name", "parent"],
        as_dict=True,
    )
    if not found:
        frappe.throw(_("找不到步骤 {0}").format(step_key))

    req = frappe.get_doc(REQUEST, found.parent)
    step = next(s for s in req.steps if s.name == found.name)
    step.status = status
    if status == "running" and not step.started_at:
        step.started_at = now()
    if status in ("done", "failed", "skipped"):
        step.finished_at = now()
    if output:
        step.output = (output or "")[:60000]
    if status == "failed":
        req.error = f"{step_key}: {output[:500]}"
    req.status = "running"
    req.save(ignore_permissions=True)
    frappe.db.commit()

    _refresh_request_status(req)
    return state(req.name, allow_pending_create=False)


def _refresh_request_status(req) -> None:
    req.reload()
    statuses = [s.status for s in req.steps]
    if all(s in ("done", "skipped") for s in statuses):
        req.status = "done"
        req.finished_at = now()
    elif any(s == "failed" for s in statuses):
        # 有失败就是 partial/failed，绝不写成 done
        req.status = "failed" if not any(s == "done" for s in statuses) else "partial"
    else:
        req.status = "running"
    req.save(ignore_permissions=True)
    frappe.db.commit()


def mark_deployment_active(deployment: str) -> str:
    _require_operator()
    dep = frappe.get_doc(DEPLOYMENT, deployment)
    dep.status = "active"
    dep.save(ignore_permissions=True)
    frappe.db.commit()
    return dep.status


def retry_failed_steps(request: str) -> dict:
    """把失败/卡住的步骤重置为 pending，让执行器能再跑一遍。

    为什么需要：执行器遇到失败会**停住不往下跳**（后面的步骤依赖它），
    所以修好原因之后必须能重试；否则一次失败就把整张单卡死，只能重开单子。
    幂等的步骤（建站、装 App、写配置、发角色）重跑是安全的 —— 这正是幂等
    要求的价值所在。
    """
    _require_operator()
    req = frappe.get_doc(REQUEST, request)
    reset = []
    for step in req.steps:
        if step.status in ("failed", "running"):
            step.status = "pending"
            step.output = ""
            step.finished_at = None
            reset.append(step.step_key)
    if reset:
        req.error = ""
        req.save(ignore_permissions=True)
        frappe.db.commit()
        _refresh_request_status(req)
    return {"request": req.name, "reset": reset, "status": req.status}


@frappe.whitelist(methods=["POST"])
def api_retry_failed(request):
    return retry_failed_steps(request)


@frappe.whitelist()
def api_request_of_issue(issue):
    """按发号单找开通请求名（执行器脚本用）。"""
    _require_operator()
    return frappe.db.get_value(REQUEST, {"issue": issue}, "name", order_by="creation desc")


def state(request_name: str, allow_pending_create: bool = True) -> dict:
    _require_operator()
    req = frappe.get_doc(REQUEST, request_name)
    steps = [
        {"step_key": s.step_key, "status": s.status, "action": s.action,
         "deployment": s.deployment}
        for s in req.steps
    ]
    return {
        "request": req.name,
        "issue": req.issue,
        "status": req.status,
        "error": req.error,
        "steps": steps,
        "counts": {
            "total": len(steps),
            "done": sum(1 for s in steps if s["status"] == "done"),
            "failed": sum(1 for s in steps if s["status"] == "failed"),
        },
        "deployments": frappe.get_all(
            DEPLOYMENT, filters={"issue": req.issue},
            fields=["name", "product", "site_name", "status", "url"],
            order_by="product asc", limit_page_length=0,
        ),
    }


# ── 接口层 ──────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_plan(**kwargs):
    kwargs.pop("cmd", None)
    return build_plan(**kwargs)


@frappe.whitelist()
def api_state(request):
    return state(request)


@frappe.whitelist()
def api_next_steps(limit=20):
    return next_steps(limit=int(limit))


@frappe.whitelist(methods=["POST"])
def api_report(step_key, status, output=""):
    return report_step(step_key, status, output)


@frappe.whitelist(methods=["POST"])
def api_mark_active(deployment):
    return {"deployment": deployment, "status": mark_deployment_active(deployment)}


@frappe.whitelist()
def api_state_for_issue(issue):
    """按发号单查开通进度。没有请求就返回 None（不是错误）。"""
    _require_operator()
    name = frappe.db.get_value(REQUEST, {"issue": issue}, "name", order_by="creation desc")
    return state(name) if name else None


@frappe.whitelist()
def api_reapply_payloads(deployment):
    """按**当前**的功能目录，重算某个部署的「角色」与「工作区」载荷。

    为什么需要单独一个接口：开通步骤的 payload 是**建单时**就存进库的字符串，
    之后目录改了不会跟着动（实测：老单子的 `set_oidc` payload 里就没有后来新增的
    凭据字段）。所以"目录改了，已经开通的站点也要跟上"这件事，只能按现在这份
    目录重算一份再打过去。

    这里**只算不写**：往哪台机器上写由本机的开通代理决定（控制面没有 docker）。
    """
    _require_operator()
    dep = frappe.get_doc(DEPLOYMENT, deployment)
    issue = frappe.get_doc(ISSUE, dep.issue)
    return {
        "deployment": dep.name,
        "site": dep.site_name,
        "group": dep.bench_group,
        "product": dep.product,
        "roles": _roles_for(issue, dep),
        "workspace": _workspace_for(issue, dep),
    }
