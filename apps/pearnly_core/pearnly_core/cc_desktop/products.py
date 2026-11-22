"""产品怎么接共用机器通道（多产品共用，ADR-025）：产品在自己的 ``hooks.py`` 里声明 ``pearnly_cc_desktop``，共用层只读它。

    pearnly_cc_desktop = {
        "allowed": "<点路径>",        # (user) -> bool：谁能把自己的电脑连上这个站点（在 CC 窗口里登录站点时登记）
        "job_changed": "<点路径>",    # (job: dict) -> None：一件写账有了新状态（同一事务里叫，产品把单号 / 原因写回自己的单据）
        "kinds": ["express_masters"], # 这个站点会请电脑做的按需请求种类
        "books_changed": "<点路径>",  # (device) -> None：可选；一台电脑报来的账套名单有了变化（同一事务里叫，产品排后台任务）
        "ledger_changed": "<点路径>", # (device, {book_key: {类别}}) -> None：可选；CC 桌面版报某几本账套变了哪几类
                                      # （文件变更通知，`pearnly_core.ledger_sync`），同一事务里叫，产品只排读取、不在这里读
        "watch_books": "<点路径>",    # (device) -> [book_key]：可选；要这台电脑盯着哪几本（已接公司的现用账套），心跳回复带给它
        "still_wanted": "<点路径>",   # (job: dict) -> bool：可选；领单前问一次这件还要不要写（来源单据已作废回 False，撤掉不交电脑）
        "stand_in": "<点路径>",       # (user, job: dict) -> bool：可选；``user``（不是提交人）此刻能不能替这件写（用户 11-17 定稿：
                                      # 公司里有一台开着、够得到、登录人有权限的电脑，任何成员的单都能写）；没声明 = 只写本人的
        # MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7 条，可选；两项都声明才算支持，没声明回 unsupported）：
        "mrerp_login_scope": "<点路径>",    # (user, connection) -> "site" | "local" | ""：管理员 site（写站点 + 存本机）、
                                            # 使用成员 local（只存本机）、别人空串（主会话 11-02 定）
        "mrerp_login_store": "<点路径>",    # (connection, username, password, *, user, device_label, scope) -> dict：
                                            # site 写进连接的 Password 字段（此刻不是管理员必须拒写）、local 不写；回
                                            # {connection, system_url, username, device, at, written}（不含密码）
    }

没声明的站点（Cowork、DMS）：登记一律拒绝，机器方法一律 403，什么都不做。一个站点只装一个产品，取最后声明的那一份。
"""
import frappe

HOOK = "pearnly_cc_desktop"


def _declared(key):
    found = frappe.get_hooks(HOOK) or {}
    values = found.get(key) if isinstance(found, dict) else None
    if isinstance(values, list):
        return values
    return [] if values in (None, "") else [values]


def _path(key):
    values = [value for value in _declared(key) if isinstance(value, str) and value]
    return values[-1] if values else ""


def enabled() -> bool:
    return bool(_path("allowed"))


def allowed(user) -> bool:
    """``user`` 此刻能不能用共用通道（登记电脑、电脑以他的身份领单）。没声明的站点一律不能。"""
    path = _path("allowed")
    return bool(path and user and user not in ("Guest", "Administrator") and frappe.get_attr(path)(user))


def job_changed(view: dict) -> None:
    path = _path("job_changed")
    if path:
        frappe.get_attr(path)(view)


def still_wanted(view: dict) -> bool:
    """领单前兜底：产品说这件不要了（如来源单据已作废）回 False；没声明一律要。"""
    path = _path("still_wanted")
    return bool(frappe.get_attr(path)(view)) if path else True


def stand_in_enabled() -> bool:
    return bool(_path("stand_in"))


def stand_in(user, view: dict) -> bool:
    """``user``（这台电脑上登录过 CC、不是提交人）此刻能不能替这件写：产品判（ERP：签发有效、对这家公司有权限）；没声明一律不能。"""
    path = _path("stand_in")
    return bool(path and user and user not in ("Guest", "Administrator") and frappe.get_attr(path)(user, view))


def books_changed(device: str) -> None:
    path = _path("books_changed")
    if path:
        frappe.get_attr(path)(device)


def ledger_changed(device: str, changes: dict) -> None:
    path = _path("ledger_changed")
    if path and changes:
        frappe.get_attr(path)(device, changes)


def watch_books(device: str) -> list:
    """产品要这台电脑盯着的账套键；没声明回空（电脑什么都不盯，靠标记检查与每小时整读）。"""
    path = _path("watch_books")
    if not path:
        return []
    found = frappe.get_attr(path)(device) or []
    return [str(key) for key in found if key]


def kinds() -> list:
    return [value for value in dict.fromkeys(_declared("kinds")) if isinstance(value, str)]


def mrerp_login_supported() -> bool:
    """这个站点的产品接了「MR.ERP 登录在 CC 里授权」（ERP 在第 6 步接 MR.ERP 连接之前没有）。"""
    return bool(_path("mrerp_login_scope") and _path("mrerp_login_store"))


def mrerp_login_scope(user, connection) -> str:
    """``user`` 此刻对这条连接的登录能做什么：``site`` / ``local``；不能是空串。"""
    path = _path("mrerp_login_scope")
    if not path or not user or user in ("Guest", "Administrator"):
        return ""
    scope = frappe.get_attr(path)(user, connection)
    return scope if scope in ("site", "local") else ""


def mrerp_login_store(connection, username, password, *, user, device_label, scope) -> dict:
    return frappe.get_attr(_path("mrerp_login_store"))(connection, username, password, user=user,
                                                         device_label=device_label, scope=scope)
