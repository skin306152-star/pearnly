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
from pearnly_control.account import identity_pools
from pearnly_control.onboarding.line_ids import clean_credential, half_filled
from pearnly_core import keycloak_admin as kc

REQUEST = "Pearnly Provisioning Request"
DEPLOYMENT = "Pearnly Deployment"
ISSUE = "Pearnly Issue"
LINE_CHANNEL = "Pearnly LINE Channel"


def _require_operator() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    frappe.only_for("System Manager")
    return user


# ── 计划 ────────────────────────────────────────────────────────────────────


def build_plan(issue: str, idempotency_key: str | None = None) -> dict:
    """把发号单拆成步骤。幂等：同一幂等键只会有一套请求与部署。"""
    _require_operator()
    issue = frappe.get_doc(ISSUE, issue)
    from pearnly_control.onboarding.targets import require_cloud_target

    require_cloud_target(issue.target, use_default=False)
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
        frappe.throw(_("Issue {0} is {1}; only an issued one can be provisioned").format(
            issue.name, issue.status))
    # LINE 凭据先全部核对完再建任何东西：建部署时 `_ensure_oidc_client` 会提交事务，
    # 之后再报错就留下一张没有步骤的请求，再点一次走 `_sync_steps`，LINE 被静默跳过（PR-2026-0296）。
    line_credentials = {row.name: _line_credentials_of(row) for row in issue.apps}

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
        # 必须在 `_steps_for` **之前**：那一步会把渠道的当前状态算进 `set_line` 的载荷。
        _ensure_line_channel(issue, row, dep, line_credentials[row.name])
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
    late_channels = []
    for row in issue.apps:
        dep = frappe.db.get_value(DEPLOYMENT, {"site_name": (row.domain or "").lower()}, "name")
        if not dep:
            continue
        dep_doc = frappe.get_doc(DEPLOYMENT, dep)
        # 首次规划半路失败时渠道还没建：开户单上带了凭据就在这里补上（已有的渠道不动，
        # 那可能是之后在「LINE 渠道」里改过的）。
        if not frappe.db.exists(LINE_CHANNEL, {"deployment": dep}):
            channel = _ensure_line_channel(issue, row, dep_doc, _line_credentials_of(row))
            if channel:
                late_channels.append((dep, channel))
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
    for dep, channel in late_channels:
        # 待执行 / 失败的 set_line 上面已按新渠道刷新；已跑过（跳过或完成）的得重排，站点才拿得到凭据。
        if have.get(f"{dep}:set_line") is not None and have[f"{dep}:set_line"].status in ("done", "skipped"):
            queue_cloud_line_config(frappe.get_doc(LINE_CHANNEL, channel))
            frappe.db.commit()
            changed += 1
    return changed


def _ensure_deployment(issue, row):
    """一个产品一条部署。同一个站点已经存在就复用（幂等）。"""
    site_name = (row.domain or "").strip().lower()
    if not site_name:
        frappe.throw(_("Row {0} on the issue has no domain; cannot build the site").format(row.app))

    name = frappe.db.get_value(DEPLOYMENT, {"site_name": site_name}, "name")
    if name:
        existing = frappe.get_doc(DEPLOYMENT, name)
        if existing.issue != issue.name or existing.tenant != issue.tenant:
            frappe.throw(_("Site name {0} is already taken by deployment {1}").format(site_name, name))
        return existing

    other = frappe.db.get_value(DEPLOYMENT, {"site_name": site_name, "issue": ("!=", issue.name)}, "name")
    if other:
        frappe.throw(_("Site name {0} is already taken by deployment {1}").format(site_name, other))

    dep = frappe.get_doc({
        "doctype": DEPLOYMENT,
        "status": "pending",
        "product": row.app,
        "tenant": issue.tenant,
        "site_name": site_name,
        "bench_group": infra.group_of(row.app),
        "app": infra.app_of(row.app),
        "port": infra.port_of(row.app),
        "url": infra.target_site_url(issue.target, site_name, row.app),
        "issue": issue.name,
        "features": _feature_rows(issue, row.app),
    })
    dep.insert()
    _ensure_oidc_client(dep, issue.target)
    return dep


def _line_credentials_of(row) -> tuple[str, str]:
    """开户单产品行上的 LINE 密钥与访问令牌（明文）。只填了一个就拒绝。"""
    secret = row.get_password("line_channel_secret", raise_exception=False) if row.line_channel_secret else ""
    token = row.get_password("line_channel_access_token", raise_exception=False) if row.line_channel_access_token else ""
    secret, token = clean_credential(secret), clean_credential(token)
    if half_filled(secret, token):
        frappe.throw(_("Row {0}: a LINE channel needs both the channel secret and the access token").format(row.app))
    return secret, token


def _ensure_line_channel(issue, row, dep, credentials):
    """开户单上填了 LINE 就顺手把渠道建好 —— 这样**同一次开通**就把渠道下发到站点。

    为什么要有它（用户 2026-09-19 问的）：渠道必须挂在部署上，而部署是点「发号并开通」
    时才建的，所以原来的顺序只能是"先开户、等建好再去配渠道"——那一步必然记 `skipped`。
    把凭据收在开户单的**产品行**上，规划时就落成渠道记录，`_line_payload` 这一轮就能读到
    `configured: True`，`set_line` 在同一次开通里落地。

    只在产品行**带了凭据**时才动渠道（幂等）：没填就完全保持现状——离线开户、之后再配。
    反过来，如果渠道已经存在而开户单上又填了新的，以开户单为准（那是运维的显式修改）。
    """
    secret, token = credentials
    if not (secret and token):
        return None

    existing = frappe.db.get_value(LINE_CHANNEL, {"deployment": dep.name}, "name")
    channel = frappe.get_doc(LINE_CHANNEL, existing) if existing else frappe.new_doc(LINE_CHANNEL)
    channel.update({
        "deployment": dep.name,
        "status": "active",
        "channel_id": row.line_channel_id or "",
        "liff_id": row.line_liff_id or "",
        "channel_secret": secret,
        "channel_access_token": token,
    })
    # 开户单上的值直接落库：打标记让 `on_update` 跳过排队 —— 这一步发生在开通步骤
    # 还没生成的时候，`set_line` 的载荷由下面的规划自己算。
    channel.flags.pearnly_from_issue = True
    channel.save(ignore_permissions=True)
    return channel.name


def _ensure_oidc_client(dep, target: str | None = None) -> None:
    """给这个部署建一个自己的 OIDC 客户端（幂等），并记在部署上。

    回调地址按站点域名与端口算 —— 这正是"共用客户端不行"的原因。
    """
    # 客户端建在该产品的账号池里（Cowork 在自己的池，ERP / DMS 在业务池）。
    client, _target_name = kc.client_for_target(target, dep.product)
    base = infra.deployment_base_url(dep)
    urls = [f"{base}/api/method/pearnly_core.identity.callback"]
    origins = [base]
    attributes = {}
    if infra.is_cloud(target):
        attributes = {
            "post.logout.redirect.uris": base + "/",
            "backchannel.logout.url": base + "/api/method/pearnly_core.identity.backchannel_logout",
            "backchannel.logout.session.required": "true",
        }
    result = client.create_oidc_client(dep.site_name, urls, origins, attributes=attributes)
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


def target_credentials(target_name=None, product=None) -> dict:
    """开户目标上的**realm 级**凭据，开通时要一起写进租户站点。

    为什么必须下发，而不是让站点自己想办法：改密功能要用这两个客户端 ——

    - `pearnly-admin`：以 client_credentials 换管理员令牌，去身份服务改密码；
    - `pearnly-password-check`：只开密码授权，用来校验用户填的"当前密码"对不对。

    它们是 **realm 级**的（同一 realm 下所有租户共用一套），所以存在开户目标上最合适；
    Cowork 有自己的账号池，取目标上 Cowork 那一组（`keycloak_admin.pool_fields`）。

    踩过的坑：这两个凭据以前只在样板站上手工写过，**开通流程从来没下发** ——
    结果是"改密在样板站验过、在真租户站上一直是坏的"（报"取管理令牌失败"，
    因为 client_id/secret 读出来是 None）。样板站能过不代表租户站能用。
    """
    from frappe.utils.password import get_decrypted_password

    if infra.is_cloud(target_name):
        return {"native_account": True}
    _client, target = kc.client_for_target(target_name, product)
    fields = kc.pool_fields(product)
    row = frappe.db.get_value(
        "Pearnly Target",
        {"target_name": target},
        [fields["admin_client_id"], fields["password_check_client_id"]],
        as_dict=True,
    ) or {}
    return {
        "admin_client_id": row.get(fields["admin_client_id"]) or "",
        "admin_client_secret": get_decrypted_password(
            "Pearnly Target", target, fields["admin_client_secret"], raise_exception=False
        ) or "",
        "password_check_client_id": row.get(fields["password_check_client_id"]) or "",
        "password_check_client_secret": get_decrypted_password(
            "Pearnly Target", target, fields["password_check_client_secret"], raise_exception=False
        ) or "",
    }


def _feature_rows(issue, product) -> list[dict]:
    """该部署开通的功能 = 那个产品的**全部**功能。

    以前从开单的三张功能模版表里抄；现在开单不分功能模版（用户 2026-09-18 定），
    所以由目录直接给全集。部署上的 `features` 仍然保留 —— 它是"这个租户当时
    开到了什么"的档案，以后目录变了也不会追溯改掉历史部署。
    """
    return [
        {"feature": features_mod.feature_name(product, code)}
        for code in features_mod.codes_of(product)
    ]


def _issuer_url(target_name=None, product=None) -> str:
    """统一身份签发方。必须浏览器与容器都能访问到同一个字符串。

    取目标环境配的地址（本机样板是 http://keycloak.localhost:8105/realms/pearnly-business），
    与各样板站点用的完全一致，避免出现两套 issuer。按产品取账号池：Cowork 是它自己的 realm。
    """
    _client, target = kc.client_for_target(target_name, product)
    realm_field = kc.pool_fields(product)["realm"]
    row = frappe.db.get_value(
        "Pearnly Target", {"target_name": target}, ["base_url", realm_field], as_dict=True
    )
    if not row:
        frappe.throw(_("Cannot read the provisioning target, so the identity service URL is unknown"))
    return f"{row.base_url.rstrip('/')}/realms/{row.get(realm_field)}"


def _line_payload(dep, site: str, group: str) -> dict:
    """该部署的 LINE 渠道配置。没有配渠道就给出空凭据，执行器会把这步标成 skipped。

    为什么要"没有也走一步"：状态机里显式记 skipped（而不是不生成步骤），
    看进度的人就能一眼分清"这个租户还没配 LINE"和"这一步被漏了"。
    """
    channel = frappe.db.get_value(
        LINE_CHANNEL, {"deployment": dep.name},
        ["name", "status", "channel_id", "channel_secret", "channel_access_token", "liff_id"],
        as_dict=True,
    )
    payload = {
        "site": site,
        "group": group,
        "product": dep.product,
        "webhook_url": line_webhook_url(dep),
    }
    if not channel:
        payload["configured"] = False
        return payload
    cloud = infra.is_cloud(frappe.db.get_value(ISSUE, dep.issue, "target"))
    if channel.status == "suspended" or (cloud and channel.status != "active"):
        payload.update(configured=False, clear_credentials=cloud)
        return payload

    from frappe.utils.password import get_decrypted_password

    def _secret(field: str) -> str:
        # 用查出来的**单据名**，不按命名规则拼：拼出来的是"猜的"，改名或换命名规则后
        # 会静默取到空串（表现是"渠道配好了，站点却发不出消息"）。
        return get_decrypted_password(
            LINE_CHANNEL, channel.name, field, raise_exception=False
        ) or ""

    payload.update({
        "configured": True,
        "channel_id": channel.channel_id or "",
        "liff_id": channel.liff_id or "",
        "channel_secret": _secret("channel_secret"),
        "channel_access_token": _secret("channel_access_token"),
        # 有它才在身份服务里开 LINE 登录入口（见 scripts/production/configure-line-login.py）。
        "login_channel_secret": line_login_secret(frappe.db.get_value(ISSUE, dep.issue, "target"),
                                                  dep.product, channel.liff_id),
    })
    return payload


# 接 LINE 登录的产品（用户 2026-09-26：DMS 不接，已有 LIFF 票据门户；Cowork 2026-09-30 接入）。
LINE_LOGIN_PRODUCTS = ("erp", "cowork")


def line_login_secret(target, product, liff_id) -> str:
    """开户目标上全平台一份的 LINE Login 频道密钥；该渠道 LIFF 属于这个频道、产品已接入才给。"""
    from frappe.utils.password import get_decrypted_password
    from pearnly_core.account.line_login_contract import login_channel_of

    if product not in LINE_LOGIN_PRODUCTS or not target:
        return ""
    channel = frappe.db.get_value("Pearnly Target", target, "line_login_channel_id")
    if not channel or login_channel_of(liff_id) != channel:
        return ""
    return get_decrypted_password("Pearnly Target", target, "line_login_channel_secret",
                                  raise_exception=False) or ""


def requeue_line_login(target) -> int:
    """开户目标的 LINE 登录配置变了：已开通的可接入渠道全部重排 `set_line`。"""
    count = 0
    for name in frappe.get_all(LINE_CHANNEL, filters={"status": "active", "product": ("in", LINE_LOGIN_PRODUCTS)},
                               pluck="name"):
        channel = frappe.get_doc(LINE_CHANNEL, name)
        dep_issue = frappe.db.get_value(DEPLOYMENT, channel.deployment, "issue")
        if dep_issue and frappe.db.get_value(ISSUE, dep_issue, "target") == target:
            queue_cloud_line_config(channel)
            count += 1
    return count


def line_webhook_url(dep) -> str:
    """租户 LINE OA 的 Webhook 地址。开户和开通后重排 set_line 都用这一个（09-30 重排漏了它，站点步骤 KeyError）。"""
    return f"{infra.deployment_base_url(dep)}/api/method/pearnly_core.line.webhook"


def queue_cloud_line_config(channel, *, enabled: bool | None = None):
    """把"这条渠道现在长什么样"重排成云端的 set_line 步骤。**开通之后再配渠道就走这里。**

    为什么不是"开通时配好就完事"：真实的顺序是**先开通租户、后配渠道**（用户 2026-09-19
    问的就是这件事）。开通时渠道还不存在，那一步记 `skipped`；等这里补上凭据，
    必须能把那一步翻回 `pending` 并且**带上现在的凭据**，否则站点永远拿不到密钥，
    而看进度的人以为这件事处理过了。

    `enabled` 默认按渠道自己的状态算；删除渠道时显式传 False（那时单据还是 active）。
    """
    if enabled is None:
        enabled = channel.status == "active"
    if not channel.deployment or not frappe.db.exists(DEPLOYMENT, channel.deployment):
        return  # 部署/租户已经拆掉了，没有站点可下发
    dep = frappe.get_doc(DEPLOYMENT, channel.deployment)
    target = frappe.db.get_value(ISSUE, dep.issue, "target")
    if not infra.is_cloud(target):
        return
    tenant = frappe.db.get_value(ISSUE, dep.issue, "tenant")
    if tenant and frappe.db.get_value("Pearnly Tenant", tenant, "status") == "deleting":
        return  # 正在拆除：站点马上要被删，不要再往它上面写配置
    parent = frappe.db.get_value(REQUEST, {"idempotency_key": dep.issue}, "name")
    if not parent:
        return  # The initial provisioning plan will pick up these credentials.
    req = frappe.get_doc(REQUEST, parent, for_update=True)
    step = next((s for s in req.steps if s.deployment == dep.name and s.action == "set_line"), None)
    if not step:
        return
    if step.status == "running":
        frappe.throw(_("LINE configuration is being applied. Try again shortly."))
    secret = channel.get_password("channel_secret", raise_exception=False) if enabled else ""
    token = channel.get_password("channel_access_token", raise_exception=False) if enabled else ""
    login_secret = line_login_secret(target, dep.product, channel.liff_id) if enabled else ""
    if enabled and not (secret and token):
        frappe.throw(_("LINE credentials are required before activation."))
    # `restart`: 这是**改一个正在服务的站点**，站点会继续按进程内的旧副本应答最长一分钟
    # （Frappe 进程内缓存 `site_config.json`）。开/停渠道是运维动作，容忍一次该产品组的
    # 重启，换"立刻生效"。开通流程本身不带这个键 —— 新站点没有旧副本，不需要重启，
    # 也就不该把该产品下的其他租户一起踢下线（见 provision-agent 里的说明）。
    payload = {"site": dep.site_name, "group": dep.bench_group, "product": dep.product,
               "configured": enabled, "clear_credentials": not enabled, "restart": True,
               "channel_id": channel.channel_id or "", "liff_id": channel.liff_id or "",
               "channel_secret": secret, "channel_access_token": token,
               "login_channel_secret": login_secret, "webhook_url": line_webhook_url(dep)}
    step.payload = json.dumps(payload)
    step.status, step.output, step.finished_at = "pending", "", None
    req.status, req.error, req.finished_at = "pending", "", None
    req.save(ignore_permissions=True)
    # Commit together with the channel save. The runner sees only committed state.


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


def _billing_payload(issue, dep) -> dict:
    """把计费底座的地址、令牌、租户身份写给这个站点。

    为什么站点要知道这三样：余额与台账在**控制面**（三套产品共用一份），
    产品站点自己算不了钱，只能带着"我是谁"去问。

    地址用容器名 `control-web:8000` 而不是 `control.localhost:8104` ——
    站点跑在容器里，走的是 compose 内网；`Host` 头由执行器那边带上，
    Frappe 靠它认站点。
    """
    from pearnly_control import billing_api

    from pearnly_control import billing_remote
    if infra.is_cloud(issue.target):
        scope = billing_remote.provision_scope(issue.tenant, dep.product, issue.tenant_name,
                                               owner_subject=identity_pools.subject_of(issue, dep.product))
        return {
            "site": dep.site_name, "group": dep.bench_group, "tenant": issue.tenant,
            "api": "http://billing-web:8000", "host_header": "billing.internal", "token": scope["token"],
        }

    return {
        "site": dep.site_name,
        "group": dep.bench_group,
        "tenant": issue.tenant,
        "api": "http://control-web:8000",
        "host_header": "control.localhost",
        "token": billing_api.service_token(),
    }


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
             "issuer": _issuer_url(issue.target, dep.product),
             "client_id": dep.oidc_client_id,
             "client_secret": oidc_secret_of(dep.name),
             "redirect_uri": f"{infra.deployment_base_url(dep)}/api/method/pearnly_core.identity.callback",
             # realm 级的两个客户端凭据也要一起写：改密功能依赖它们
             # （只写本部署的 client 会导致"能登录但改不了密码"）
             **target_credentials(issue.target, dep.product),
         }, ensure_ascii=False)},
        {"step_key": f"{dep.name}:restart_web", "deployment": dep.name,
         "action": "restart_web", "status": "pending",
         "payload": json.dumps({"group": group}, ensure_ascii=False)},
        # 计费底座：余额与台账在控制面，站点要拿到地址 + 令牌 + 自己是谁
        {"step_key": f"{dep.name}:set_billing", "deployment": dep.name,
         "action": "set_billing", "status": "pending",
         "payload": json.dumps(_billing_payload(issue, dep), ensure_ascii=False)},
        # OCR 是三个产品**共用的底座**（用户 2026-09-18 定：它不是可选项）。
        # 所以不进发号单让谁勾，直接每个站点都配 —— 漏了这一步的后果是
        # "站点开出来好好的，就是识别不了"（老样板站是手工补的）。
        {"step_key": f"{dep.name}:set_ocr", "deployment": dep.name,
         "action": "set_ocr", "status": "pending",
         "payload": json.dumps({"site": site, "group": group}, ensure_ascii=False)},
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
             # 字段是 `account_username`（"用户名"，用户 2026-09-19 从"使用人姓名"改过来）。
             # 这里曾经写的是旧名 `account_name`：旧列还在库里、DocType 元数据里已经没有了，
             # 于是 `bench migrate` 同步一次字段就会让这行抛 AttributeError、把整条开通链卡死
             # （2026-09-19 实踩）。取不到就退回公司名。
             "full_name": issue.get("account_username") or issue.tenant_name,
             # 绑该产品账号池里为本单建的那个身份（Cowork 与 ERP / DMS 分池）。
             "subject": identity_pools.subject_of(issue, dep.product),
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
    if infra.is_cloud(issue.target):
        steps.insert(-1, {
            "step_key": f"{dep.name}:publish_site", "deployment": dep.name,
            "action": "publish_site", "status": "pending",
            "payload": json.dumps({"site": site, "group": group}),
        })
    return steps


# ── 执行器接口 ──────────────────────────────────────────────────────────────


def next_steps(limit: int = 20, target: str | None = None) -> list[dict]:
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
        tenant = frappe.db.get_value(ISSUE, req.issue, "tenant")
        if (tenant and frappe.db.get_value("Pearnly Tenant", tenant, "status") == "deleting"
                and not str(req.idempotency_key).startswith("teardown:")):
            continue
        issue_target = frappe.db.get_value(ISSUE, req.issue, "target")
        # A retired host's historical jobs, including teardown, must stay inert.
        if infra.target_settings(issue_target).get("executor") == "retired":
            continue
        # Historical local provisioning requests must never resume or be retargeted.
        # Explicit teardown remains a separate, previously authorized lifecycle.
        if not str(req.idempotency_key).startswith("teardown:") and not infra.is_cloud(issue_target):
            continue
        # Legacy runner has no target argument. It must never consume cloud jobs.
        if target and issue_target != target:
            continue
        if not target and infra.is_cloud(issue_target):
            continue
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
            payload = json.loads(step.payload or "{}")
            if target and step.deployment:
                dep = frappe.get_doc(DEPLOYMENT, step.deployment)
                payload.update(site=dep.site_name, group=dep.bench_group,
                               deployment=dep.name, tenant=dep.tenant)
            out.append({
                "request": req.name,
                "step_key": step.step_key,
                "action": step.action,
                "payload": payload,
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
        frappe.throw(_("Unknown step status: {0}").format(status))

    found = frappe.db.get_value(
        "Pearnly Provisioning Step", {"step_key": step_key}, ["name", "parent"],
        as_dict=True,
    )
    if not found:
        frappe.throw(_("No such step: {0}").format(step_key))

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
    if not frappe.db.exists(REQUEST, req.name):
        # 拆除请求在最后一步报完成时把自己连同租户记录一起删掉了（见
        # `_refresh_request_status`）。这时候**不能再回读** —— 请求已经不存在，
        # 回读会抛 DoesNotExist，让执行器收到一个莫名其妙的 500。
        return {"request": req.name, "status": "done", "counts": {}, "steps": [],
                "purged": True}
    return state(req.name, allow_pending_create=False)


def _refresh_request_status(req) -> None:
    req.reload()
    statuses = [s.status for s in req.steps]
    is_teardown = any(s.action in ("drop_site", "purge_control") for s in req.steps)
    if all(s in ("done", "skipped") for s in statuses):
        req.status = "done"
        req.finished_at = now()
        req.save(ignore_permissions=True)
        frappe.db.commit()
        if is_teardown:
            # 拆除的最后一步报完成 → 控制面的记录整片删掉（含这条请求自己）。
            # 放在这里、而不是执行器里：执行器刚刚还在往这条请求上写状态，
            # 先删了它就无处可报（顺序错了会表现成"最后一步永远失败"）。
            from pearnly_control import teardown

            teardown.purge_records(req.name)
        return
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


def retry_failed_steps(request: str, include_skipped: bool = False) -> dict:
    """把失败/卡住的步骤重置为 pending，让执行器能再跑一遍。

    为什么需要：执行器遇到失败会**停住不往下跳**（后面的步骤依赖它），
    所以修好原因之后必须能重试；否则一次失败就把整张单卡死，只能重开单子。
    幂等的步骤（建站、装 App、写配置、发角色）重跑是安全的 —— 这正是幂等
    要求的价值所在。

    `include_skipped=True` 连 `skipped` 一起翻回 pending。为什么需要这个开关：
    "跳过"有两种，含义完全不同 ——

    - 「这一步本单不需要」（例如没买看板，就不建看板）→ 永远不该重跑；
    - 「**现在还没到配的时候**」（例如 LINE 渠道：先开通、后配渠道）
      → 配好之后必须能补跑，否则那一步就永远停在 skipped，
      站点上永远拿不到渠道密钥，而看进度的人会以为"这件事处理过了"。

    LINE 属于后者。所以补跑时带上这个开关。（默认不带：把"不需要"也重跑一遍
    没有意义，还会让人误以为流程会自愈。）
    """
    _require_operator()
    req = frappe.get_doc(REQUEST, request)
    reset = []
    for step in req.steps:
        retryable = ("failed", "running") + (("skipped",) if include_skipped else ())
        if step.status in retryable:
            step.status = "pending"
            step.output = ""
            step.finished_at = None
            reset.append(step.step_key)
    if reset:
        req.error = ""
        req.save(ignore_permissions=True)
        frappe.db.commit()
        # 翻回「待执行」之后**必须按现在的情况重算参数**：步骤的 payload 是规划时
        # 算好存进库的字符串，而「先开通、后配渠道」这类步骤恰恰是**在 skipped 期间**
        # 才把外部条件配好的。不重算就会拿建单时那份旧参数去执行 —— 实测：配好 LINE
        # 渠道再补跑，执行器读到的是旧的 `configured: false`，又记一次 skipped，
        # 站点上永远拿不到密钥，而看进度的人以为这件事处理过了。
        if not str(req.idempotency_key).startswith("teardown:"):
            _sync_steps(req)
        _refresh_request_status(req)
    return {"request": req.name, "reset": reset, "status": req.status}


@frappe.whitelist(methods=["POST"])
def api_retry_failed(request, include_skipped: int | bool = 0):
    return retry_failed_steps(request, include_skipped=bool(int(include_skipped or 0)))


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
