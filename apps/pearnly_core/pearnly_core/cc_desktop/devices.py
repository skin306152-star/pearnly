"""CC 桌面版共用机器通道：电脑（多产品共用，ADR-025）。

一台电脑在一个站点上一行（``Pearnly CC Device``），只存设备密钥的摘要与令牌的摘要：

- **登记**：人在 CC 窗口里登录站点，对话页带本机设备密钥调 :func:`register`（Frappe 会话）。新电脑发令牌（``created``）；已登记
  的只把这个人记进「在这台电脑上登录过 CC 的人」（``signed_in_users``，写账归属按它，ADR-021 第 50 条），不换令牌
  （``exists``）；本机丢了令牌（重装、换了配对）带 ``rekey`` 换一把；令牌的签发人已失权（离职、没了权限）时这次登录的人自动
  换成新签发人。被移除过的电脑自动登记只回 ``removed``，人在设置里亲手点「连接」（``rejoin``）才作为新电脑回来。
- **认证**：机器请求带 ``pcc_`` 令牌（`http_auth`），按摘要找到电脑，核没移除、签发人此刻仍能用（在职、产品声明允许）。
- **报到**：心跳记版本与最近报到时间；电脑自己断开（``leave``）只作废这把令牌。

与 Cowork 的电脑表（`pearnly_cowork.companion.devices`）互不相干：Cowork 站点仍用它自己的那一套（ADR-025 登记的债）。
"""
from dataclasses import dataclass
from datetime import datetime, timezone

import frappe
from frappe import _

from pearnly_core.cc_desktop import contracts, documents, products
from pearnly_core.cc_desktop.wire import link_contracts, route_contracts

DEVICE = documents.DEVICE


@dataclass(frozen=True)
class Context:
    site: str
    device: str
    issuer: str


def now():
    """表里的时间一律存 UTC（不带时区）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _site():
    return frappe.local.site


def _error(error):
    frappe.throw(_(error.code))


def issuer_valid(user) -> bool:
    return bool(user) and bool(frappe.db.get_value("User", user, "enabled")) and products.allowed(user)


def _issue(doc, user, moment):
    token = contracts.new_token(doc.name)
    doc.update({"token_digest": contracts.token_digest(token), "token_last4": token[-4:], "issued_by": user,
                "issued_at": moment, "full_report_due": 1})
    return token


def register(request_key=None, device_key=None, device_name=None, rekey=False, rejoin=False):
    """对话页登记这台电脑（调用方已核过 Frappe 会话与 CSRF）。回 ``{status, device, token?}``，令牌只在 ``created`` 时给一次。"""
    user = frappe.session.user
    if not products.allowed(user):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    try:
        if request_key not in (None, ""):
            contracts.text(request_key, maximum=200)
        key = contracts.device_key(device_key)
        label = contracts.text(device_name, maximum=140, optional=True)
        rekey, rejoin = contracts.flag(rekey), contracts.flag(rejoin)
    except contracts.ChannelError as error:
        _error(error)
    digest = contracts.device_digest(_site(), key)
    moment = now()
    found = frappe.db.sql(f"SELECT name FROM `tab{DEVICE}` WHERE device_digest = %s FOR UPDATE", (digest,))
    doc = frappe.get_doc(DEVICE, found[0][0]) if found else None
    if doc is not None and doc.retired_at:
        if not rejoin:
            return {"status": "removed", "device": doc.name}
        # 被移除过、人亲手点了「连接」：旧行交出摘要留作记录，这台作为新电脑回来
        doc.update({"device_digest": None, "token_digest": None})
        documents.persist(doc)
        doc = None
    if doc is not None:
        doc.update({"signed_in_users": contracts.with_user(doc.signed_in_users, user), "signed_in_user": user})
        if label:
            doc.device_label = label
        if doc.token_digest and not rekey and issuer_valid(doc.issued_by):
            documents.persist(doc)
            return {"status": "exists", "device": doc.name}
        token = _issue(doc, user, moment)
        documents.persist(doc)
        return {"status": "created", "device": doc.name, "token": token}
    name = contracts.new_device_name()
    token = contracts.new_token(name)
    retire_dead(moment)   # 本来就写库的登记里顺带退役死记录（第二阶段 4.10：不加定时任务）
    documents.new(DEVICE, name, {
        "device_label": label, "device_digest": digest, "token_digest": contracts.token_digest(token),
        "token_last4": token[-4:], "issued_by": user, "issued_at": moment, "signed_in_user": user,
        "signed_in_users": contracts.with_user(None, user), "full_report_due": 1})
    return {"status": "created", "device": name, "token": token}


DEAD_SCAN_MAX = 500


def dead_devices(moment) -> list:
    """站点上没退役、从没报过账本、不在线满 7 天（``contracts.dead``，从没报到过的按签发时刻算）的电脑。只读。"""
    rows = frappe.get_all(DEVICE, filters={"retired_at": ["is", "not set"]},
                          fields=["name", "last_seen_at", "issued_at"], limit_page_length=DEAD_SCAN_MAX)
    if not rows:
        return []
    reported = set(frappe.get_all(documents.BOOK, filters={"device": ["in", [row.name for row in rows]]}, pluck="device",
                                  limit_page_length=contracts.MAX_BOOKS * len(rows)))
    return [row.name for row in rows
            if contracts.dead(row.last_seen_at or row.issued_at, moment, row.name in reported)]


def retire(name, actor, moment) -> bool:
    """让一台电脑退役：令牌作废、记退役时刻与谁（``auto`` = 死记录自动退役）。已退役的不动，回 False。它若回来，走
    ``register`` 的 ``rejoin``（人亲手点「连接」）作为新电脑。"""
    doc = frappe.get_doc(DEVICE, name)
    if doc.retired_at:
        return False
    doc.update({"retired_at": moment, "retired_by": actor, "token_digest": None})
    documents.persist(doc)
    return True


def retire_dead(moment) -> list:
    """死记录一并退役；回退役了哪几台。失败不拦登记。"""
    done = []
    try:
        for name in dead_devices(moment):
            if retire(name, "auto", moment):  # loop-query: 死记录逐台退役（DEAD_SCAN_MAX 内、只在登记新电脑时）
                done.append(name)
    except Exception:  # noqa: BLE001 - 清理死记录不能挡住新电脑登记
        frappe.log_error(title="cc_desktop retire dead devices")
    return done


def authenticate(token) -> Context:
    """机器令牌 → 电脑与签发人；不认就抛 ``AuthenticationError``（不说为什么）。"""
    parsed = contracts.parse_token(token)
    if parsed is None:
        raise frappe.AuthenticationError
    name, digest = parsed
    row = frappe.db.get_value(DEVICE, name, ["name", "token_digest", "issued_by", "retired_at"], as_dict=True)
    if (not row or row.retired_at or not contracts.digest_matches(row.token_digest, digest)
            or not issuer_valid(row.issued_by)):
        raise frappe.AuthenticationError
    return Context(_site(), row.name, row.issued_by)


def revalidate(context: Context) -> Context:
    """写之前按当前读再核一次（锁住电脑行）：令牌被换了、电脑被移除、签发人失权都不再认。"""
    row = frappe.db.sql(f"""SELECT name, token_digest, issued_by, retired_at FROM `tab{DEVICE}` WHERE name = %s
        FOR UPDATE""", (context.device,), as_dict=True)
    if not row or row[0].retired_at or row[0].issued_by != context.issuer or not row[0].token_digest \
            or not issuer_valid(context.issuer):
        raise frappe.AuthenticationError
    return context


def leave(context: Context) -> dict:
    """电脑自己断开（CC 桌面版删掉这个成员）：作废这把令牌、删掉它报的账套名单（再连上时心跳整份重报），电脑行与登录记录留着。
    名单留着的话产品还会按它挑账套、建公司（10-23：断开的电脑名单里的账套被挑中，读主档一直「电脑离线」）。"""
    from pearnly_core.cc_desktop import books

    doc = frappe.get_doc(DEVICE, context.device, for_update=True)
    doc.update({"token_digest": None, "last_seen_at": None})
    documents.persist(doc)
    books.drop_device(context.device)
    return {"status": "left"}


def record_report(context: Context, *, version=None, payload_version=None, offline=False, full=False, runner=None,
                  targets=None, logins=None, users=None, link=None):
    """心跳报到：版本、最高载荷版本、索引重建程序在不在 / 能不能跑、能代录到哪些目标（ADR-026；旧版不报，保留上次）、
    这台电脑上已授权了哪些 MR.ERP 连接（只有连接号，ADR-026 第 7 条；旧版不报，保留上次）、
    最近报到时间（报离线就清掉，马上算离线）；收到整份名单就不再要求整份重报；经哪台电脑连上来（云端 Express 的来源标记，
    `wire.link_contracts`；只给界面显示，旧版不报保留上次，认不出的不改）。"""
    doc = frappe.get_doc(DEVICE, context.device, for_update=True)
    values = {"last_seen_at": None if offline else now()}
    if isinstance(version, str) and version.strip() and len(version.strip()) <= 20:
        values["companion_version"] = version.strip()
    if type(payload_version) is int and payload_version >= 1:
        values["max_payload_version"] = payload_version
    if runner in contracts.RUNNER_STATES:
        values["reindex_runner"] = runner
    stored = route_contracts.stored_targets(targets)
    if stored:
        values["operator_targets"] = stored
    logged_in = route_contracts.stored_logins(logins)
    if logged_in is not None and not offline:
        values["mrerp_logins"] = logged_in
    # 本机授权的「连接号 → MR.ERP 用户名」（ADR-026 第 8 条，账号租约用；不报密码）：旧版不报，保留上次
    named = route_contracts.stored_users(users)
    if named is not None and not offline:
        values["mrerp_users"] = named
    linked = link_contracts.stored(link)
    if linked is not None and not offline:
        values.update(linked)
    if full:
        values["full_report_due"] = 0
    doc.update(values)
    documents.persist(doc)
    return bool(doc.full_report_due)


def by_device_key(device_key) -> str:
    """对话页交来的本机设备密钥 → 这台电脑的编号（没移除、令牌有效）；认不出回空串。密钥形状不对抛 ``ChannelError``。
    MR.ERP 登录授权发票时认电脑用（`api.mrerp_login_ticket`）。只读。"""
    digest = contracts.device_digest(_site(), contracts.device_key(device_key))
    found = frappe.db.get_value(DEVICE, {"device_digest": digest}, ["name", "retired_at", "token_digest"], as_dict=True)
    return found.name if found and not found.retired_at and found.token_digest else ""


def note_mrerp_login(context: Context, connection: str):
    """站点收下这台电脑上的 MR.ERP 授权（`machine.mrerp_login`）：把连接号记进它已授权的清单，不等下一次心跳。"""
    doc = frappe.get_doc(DEVICE, context.device, for_update=True)
    value = route_contracts.with_login(doc.get("mrerp_logins"), connection)
    if value != (doc.get("mrerp_logins") or ""):
        doc.mrerp_logins = value
        documents.persist(doc)


def touch(context: Context):
    """领单 / 领请求也算报到（不经文档、不升 modified）：只在超过一半在线窗口时才写一次，3 秒一次的领单不必次次写。"""
    seen = frappe.db.get_value(DEVICE, context.device, "last_seen_at")
    moment = now()
    if not seen or (moment - seen).total_seconds() > contracts.ONLINE_SECONDS / 2:
        frappe.db.set_value(DEVICE, context.device, "last_seen_at", moment, update_modified=False)


def signed_in(device) -> frozenset:
    return frozenset(contracts.users(frappe.db.get_value(DEVICE, device, "signed_in_users")))


def peers() -> dict:
    """``{电脑: route_contracts.Peer}``：站点上没移除的电脑（写账分派的纯规则按它判）。"""
    moment = now()
    rows = frappe.get_all(DEVICE, filters={"retired_at": ["is", "not set"]},
                          fields=["name", "signed_in_users", "last_seen_at", "max_payload_version", "token_digest",
                                  "operator_targets"])
    return {row.name: route_contracts.Peer(users=frozenset(contracts.users(row.signed_in_users)),
                                           online=bool(row.token_digest) and contracts.online(row.last_seen_at, moment),
                                           payload_version=row.max_payload_version or None,
                                           targets=route_contracts.targets_of(row.operator_targets))
            for row in rows}


def version_of(device) -> str:
    return frappe.db.get_value(DEVICE, device, "companion_version") or ""


def label(device) -> str:
    return frappe.db.get_value(DEVICE, device, "device_label") or device


def of_user(user) -> list:
    """归 ``user`` 的电脑（他登录过 CC 的；一个登录记录都没有的电脑过渡期谁的都算，`route_contracts.serves`）。"""
    return [name for name, peer in peers().items() if route_contracts.serves(peer.users, user)]
