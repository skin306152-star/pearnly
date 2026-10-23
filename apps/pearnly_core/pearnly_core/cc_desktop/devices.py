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
from pearnly_core.cc_desktop.wire import route_contracts

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
    documents.new(DEVICE, name, {
        "device_label": label, "device_digest": digest, "token_digest": contracts.token_digest(token),
        "token_last4": token[-4:], "issued_by": user, "issued_at": moment, "signed_in_user": user,
        "signed_in_users": contracts.with_user(None, user), "full_report_due": 1})
    return {"status": "created", "device": name, "token": token}


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
    """电脑自己断开（CC 桌面版删掉这个成员）：只作废这把令牌，电脑行与登录记录留着。"""
    doc = frappe.get_doc(DEVICE, context.device, for_update=True)
    doc.update({"token_digest": None, "last_seen_at": None})
    documents.persist(doc)
    return {"status": "left"}


def record_report(context: Context, *, version=None, payload_version=None, offline=False, full=False):
    """心跳报到：版本、最高载荷版本、最近报到时间（报离线就清掉，马上算离线）；收到整份名单就不再要求整份重报。"""
    doc = frappe.get_doc(DEVICE, context.device, for_update=True)
    values = {"last_seen_at": None if offline else now()}
    if isinstance(version, str) and version.strip() and len(version.strip()) <= 20:
        values["companion_version"] = version.strip()
    if type(payload_version) is int and payload_version >= 1:
        values["max_payload_version"] = payload_version
    if full:
        values["full_report_due"] = 0
    doc.update(values)
    documents.persist(doc)
    return bool(doc.full_report_due)


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
                          fields=["name", "signed_in_users", "last_seen_at", "max_payload_version", "token_digest"])
    return {row.name: route_contracts.Peer(users=frozenset(contracts.users(row.signed_in_users)),
                                           online=bool(row.token_digest) and contracts.online(row.last_seen_at, moment),
                                           payload_version=row.max_payload_version or None)
            for row in rows}


def version_of(device) -> str:
    return frappe.db.get_value(DEVICE, device, "companion_version") or ""


def label(device) -> str:
    return frappe.db.get_value(DEVICE, device, "device_label") or device


def of_user(user) -> list:
    """归 ``user`` 的电脑（他登录过 CC 的；一个登录记录都没有的电脑过渡期谁的都算，`route_contracts.serves`）。"""
    return [name for name, peer in peers().items() if route_contracts.serves(peer.users, user)]
