"""登录后落在哪一屏：产品工作台，不是"桌面"。

用户 2026-09-18 的要求：**不要那个桌面（图标网格 + 再点一次才进工作台），
登录后直接进工作台，左边就是导航。**

Frappe 决定"登录后去哪"的地方是 `frappe/website/utils.py` 的 `_get_home_page()`，
顺序为：按角色的 home_page → Portal 设置 → hooks → Website Settings → `desk`，
而**最后一步 `User.default_workspace` 优先级最高** —— 设了它就直接返回
`/desk/<slug>`，跳过桌面那一屏。所以要的字段就是它。

比较过三种做法，选第三种：

- `Role.home_page`：一个设置覆盖全站，看起来最省事。但我们的工作区名是
  **每租户一个**（"开通验证租户 65956"），而框架的 `role_home_page` 是静态映射，
  对不上；临时的替代（改 `Desk User` 这个框架角色）又会被上游 migrate 影响。
- `Website Settings.home_page`：**会连带改掉访客的首页行为**，副作用超出这件事
  要解决的范围。
- `User.default_workspace`：Frappe 专为这个场景准备的字段，按用户生效、
  用户在界面上看得到也能自己改、清掉不影响别处。

两条路都要覆盖，缺一个就会有"有些人还是要先过桌面"：

1. 开通收尾时给**已存在的**账号设上（`apply_to_site_users`）；
2. 之后租户管理员在界面上**新增的员工账号**也要设
   （`apply_to_new_user`，挂在 User 的 after_insert 上）。
"""

import os

import frappe

# 产品代码 → 承载它的 App。工作区所属模块从该 App 自己的 `modules.txt` 读，
# 不在这里再抄一份模块名 —— 模块名的唯一权威是各产品 App。
APP_BY_PRODUCT = {
    "cowork": "pearnly_cowork",
    "erp": "pearnly_erp_portal",
    "dms": "pearnly_dms",
}


def is_tenant_site() -> bool:
    """业务站点 = 开通时写过 `pearnly_product` 的站点。控制面没有（ADR-004）。"""
    return bool(str(frappe.conf.get("pearnly_product") or "").strip())


def product_module() -> str:
    """本站点产品的模块名（例如 `Pearnly Cowork`）。取不到就返回空串。"""
    product = str(frappe.conf.get("pearnly_product") or "").strip()
    app = APP_BY_PRODUCT.get(product, "")
    if not app or app not in frappe.get_installed_apps():
        return ""
    modules_file = os.path.join(frappe.get_app_path(app), "modules.txt")
    try:
        with open(modules_file, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    return line.strip()
    except OSError:
        return ""
    return ""


def site_product_workspace() -> str:
    """本站点的产品工作区名字。没有就返回空串。

    按"模块属于本站点的产品 App"来找：Frappe 判断工作区可见性时会检查
    `workspace.module in user.allowed_modules`，所以产品工作区必然是产品模块下的，
    这样不会误取到框架自带的那几个工作区（Build / Users / Website…）。
    """
    module = product_module()
    if not module:
        return ""
    names = frappe.get_all(
        "Workspace",
        filters={"module": module, "public": 1},
        pluck="name",
        order_by="creation desc",
        limit=1,
    )
    return names[0] if names else ""


def _needs_default_workspace(user: str, current: str | None) -> bool:
    """要不要给他设。

    **已经有值的不动** —— 那可能是用户自己在界面上选的，重跑开通不能顶掉。
    只处理站点自己的业务账号：Administrator 与网站用户不属于产品使用者。
    """
    if not user or user in ("Administrator", "Guest"):
        return False
    if current:
        return False
    user_type = frappe.db.get_value("User", user, "user_type")
    return user_type == "System User"


def apply_to_site_users() -> dict:
    """给本站点所有还没有默认工作区的业务账号设上。幂等。"""
    if not is_tenant_site():
        return {"skipped": "控制面不受影响"}
    workspace = site_product_workspace()
    if not workspace:
        return {"skipped": "本站点还没有产品工作区"}

    changed = []
    users = frappe.get_all(
        "User",
        filters={"enabled": 1, "user_type": "System User"},
        fields=["name", "default_workspace"],
        limit_page_length=0,
    )
    for row in users:
        if not _needs_default_workspace(row.name, row.default_workspace):
            continue
        frappe.db.set_value(
            "User", row.name, "default_workspace", workspace, update_modified=False
        )
        changed.append(row.name)

    if changed:
        frappe.db.commit()
        # 默认首页是按用户缓存的（`frappe.cache.hget("home_page", 用户)`），
        # 不清的话改了也不生效 —— 表现是"数据改了，登录进去还是老样子"。
        frappe.clear_cache()
    return {"workspace": workspace, "users": changed}


def apply_to_new_user(doc, method=None) -> None:
    """新账号自动落在本租户的工作台（挂在 User 的 after_insert 上）。

    为什么需要：开通时设过一遍只覆盖**当时已存在**的账号；之后租户管理员在界面上
    新增员工，Frappe 不会去查任何默认值，新员工又会先落到桌面上。
    """
    if not is_tenant_site():
        return
    if not _needs_default_workspace(doc.name, doc.get("default_workspace")):
        return
    workspace = site_product_workspace()
    if not workspace:
        return
    # 用 db.set_value 而不是改 doc 再 save：after_insert 阶段再 save 会二次触发文档事件。
    frappe.db.set_value("User", doc.name, "default_workspace", workspace, update_modified=False)


# 这四个是"站点的门"，不是具体某一页：访客从任何一个进来，登录完都该落在
# 他自己的首页（也就是本租户的工作台），而不是回一个写死的地址。
ENTRY_PATHS = {"", "/", "/login", "/app", "/desk"}


def landing_url() -> str:
    """这个账号登录后该去哪。交给 Frappe 的首页规则算，不在这里自己拼。

    规则会先读 `User.default_workspace`（见本模块开头），所以这里拿到的通常就是
    `/desk/<工作区>`；没设的话退回 `desk` 这类短名，统一补上前导 `/`。

    为什么不直接写 `/desk`：那样等于绕开首页规则 —— 表现就是"默认工作区设了也没用，
    登录后还是先落桌面那一屏"（2026-09-18 实测）。
    """
    from frappe.website.utils import get_home_page

    try:
        target = str(get_home_page() or "")
    except Exception:  # 首页规则出问题时不要把人卡在登录页
        target = ""
    if not target:
        return "/desk"
    return target if target.startswith("/") else "/" + target


def resolve_after_login(redirect_to: str | None) -> str:
    """登录成功后最终跳哪里。

    访客本来要去的**具体页面**要尊重（以后深链接会用到）；但如果那只是站点的门
    （`/`、`/desk`、`/app`…），就换成他自己的首页 —— 否则每次登录都先落一屏桌面。
    """
    target = (redirect_to or "").strip()
    if target.rstrip("/") in {p.rstrip("/") for p in ENTRY_PATHS}:
        return landing_url()
    return target or landing_url()
