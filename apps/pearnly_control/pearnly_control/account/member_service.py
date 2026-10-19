"""ERP / Cowork member identity ownership and retry ledger, private Control service only.

Each product has its own identity realm (Cowork was split from ERP on 2026-09-26).
The product comes only from the scoped token header, never from the request body.
An account is created in the realm the site actually signs in to (the site's issuer;
an old tenant may differ from its product's default realm), and that realm is kept on
the identity record. Reset and teardown follow the recorded realm; a record without
one predates this and lives in its product's default realm.

The tenant row serializes member operations. Create/reset intent is durable before
IO; an ambiguous request cannot silently accept a different password. Keycloak's
server HMAC ownership proof safely recovers a created account after a lost reply.
No password or requesting user is stored.
"""
import hashlib
import hmac
import json
import re

import frappe

from pearnly_core.account import line_login_contract as line_contract

from pearnly_control.billing_auth import valid_token
from pearnly_control.account.member_provider import (MemberError, MemberProvider, PREFIX, config_prefix,
    pool_prefix, realm_prefix)

IDENTITY = "Pearnly Member Identity"
OPERATION = "Pearnly Member Operation"
# 开户时登记的租户管理员（员工台账只记团队里建的号，管理员由控制面发号）。
TENANT_IDENTITY = "Pearnly Tenant Identity"
# 每个站点（租户 × 产品）上各身份当前绑定的 LINE —— 同一账号池里还有没有站点绑着这个 LINE，
# 决定解绑时要不要删掉它的 LINE 专用身份。
LINE_LINK = "Pearnly LINE Login Link"
# ERP keys must stay byte-identical: ledger keys and owner stamps already exist.
PRODUCTS = ("erp", "cowork")
_CAPABILITY = object()


def authorize(tenant, products=PRODUCTS):
    product = frappe.get_request_header("X-Pearnly-Billing-Product")
    if (not frappe.conf.get("pearnly_billing_only")
            or not frappe.conf.get("pearnly_billing_scoped_tokens")
            or product not in products
            or not isinstance(tenant, str) or not tenant or len(tenant) > 140
            or not valid_token(frappe.conf.get("pearnly_billing_token") or "", tenant,
                product, frappe.get_request_header("X-Pearnly-Billing-Token") or "")):
        raise MemberError("not_permitted")
    _lock_tenant(tenant)
    return tenant, product


def _lock_tenant(tenant):
    if frappe.db.get_value("Pearnly Tenant", tenant, "status", for_update=True) != "active":
        raise MemberError("not_permitted")


def _digest(label, *values):
    key = str(frappe.conf.get(PREFIX + "_ownership_key") or "")
    if len(key) < 32:
        raise MemberError("unavailable")
    encoded = json.dumps([label, *values], ensure_ascii=True, separators=(",", ":")).encode()
    return hmac.new(key.encode(), encoded, hashlib.sha256).hexdigest()


def _member_key(tenant, email, product):
    return hashlib.sha256(json.dumps([tenant, product, email], separators=(",", ":")).encode()).hexdigest()


def _insert(values):
    doc = frappe.get_doc(values)
    doc.flags.member_identity_capability = _CAPABILITY
    return doc.insert(ignore_permissions=True, set_name=values["name"])


def _pool_of(row):
    """登记上的号在哪个池：记着池就按池；没记池的是改动前的老登记，当时按产品默认的池建。"""
    return realm_prefix(row.realm) if row.realm else config_prefix(row.product)


def create(tenant, email, username, password, product="erp", issuer=""):
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9]+", username):
        raise MemberError("invalid_request")
    # 站点实际签到哪个池就建在哪个池（没带签发方按产品）；不认识的池在写任何东西前拒绝。
    pool = pool_prefix(product, issuer)
    provider = MemberProvider(prefix=pool)
    key = _member_key(tenant, email, product)
    fingerprint = _digest("member-create-v1", key, username.lower(), password)
    existing = frappe.db.get_value(IDENTITY, key,
        ["tenant", "product", "email", "username", "subject", "create_request_hash", "realm"],
        as_dict=True, for_update=True)
    owner = _digest("member-owner-v1", tenant, product, email, username.lower())
    if existing:
        if (existing.tenant != tenant or existing.product != product or existing.email != email
                or existing.username.lower() != username.lower()
                or not hmac.compare_digest(existing.create_request_hash, fingerprint)
                or _pool_of(existing) != pool):
            raise MemberError("conflict")
        if existing.subject:
            provider.read_owned(existing.subject, email=email, username=existing.username, owner=owner)
            return {"subject": existing.subject, "email": email, "username": existing.username}
    else:
        _insert({"doctype": IDENTITY, "name": key, "tenant": tenant, "product": product,
            "email": email, "username": username, "subject": None, "create_request_hash": fingerprint,
            "realm": provider.realm})
    frappe.db.commit()  # Retain the original create request even if Keycloak times out.
    _lock_tenant(tenant)
    subject = frappe.db.get_value(IDENTITY, key, "subject", for_update=True)
    if subject:
        provider.read_owned(subject, email=email, username=username, owner=owner)
        return {"subject": subject, "email": email, "username": username}
    subject = provider.create(email=email, username=username, password=password, owner=owner)
    frappe.db.set_value(IDENTITY, key, "subject", subject)
    return {"subject": subject, "email": email, "username": username}


def reset_password(tenant, email, password, request_id, product="erp", issuer=""):
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise MemberError("invalid_request")
    key = _member_key(tenant, email, product)
    member = frappe.db.get_value(IDENTITY, key,
        ["tenant", "product", "email", "username", "subject", "realm"], as_dict=True, for_update=True)
    if (not member or not member.subject or member.tenant != tenant
            or member.product != product or member.email != email):
        raise MemberError("not_permitted")
    # 号在登记记着的池里改；站点带了签发方时，只能改它自己签到的那个池里的号（没带：旧客户端，照登记）。
    pool = _pool_of(member)
    if str(issuer or "").strip() and pool_prefix(product, issuer) != pool:
        raise MemberError("not_permitted")
    owner = _digest("member-owner-v1", tenant, product, email, member.username.lower())
    fingerprint = _digest("member-password-v1", key, password)
    operation_key = hashlib.sha256((key + ":" + request_id).encode()).hexdigest()
    existing = frappe.db.get_value(OPERATION, operation_key,
        ["request_hash", "status"], as_dict=True, for_update=True)
    if existing:
        if not hmac.compare_digest(existing.request_hash, fingerprint):
            raise MemberError("conflict")
        if existing.status == "done":
            return {"email": email}
    pending = frappe.db.get_value(OPERATION, {"member": key, "status": "pending"},
        ["name", "request_hash"], as_dict=True, for_update=True)
    if pending and not hmac.compare_digest(pending.request_hash, fingerprint):
        # Do not let a delayed old reset race a newer password. Same-password
        # retries may recover even when the browser lost its original request ID.
        raise MemberError("reset_pending")
    if not existing:
        _insert({"doctype": OPERATION, "name": operation_key, "member": key,
            "request_hash": fingerprint, "status": "pending"})
    frappe.db.commit()  # Keep the intent even if the external response is lost.
    _lock_tenant(tenant)
    # Re-read after the intent commit; another request may have finished this one.
    if frappe.db.get_value(OPERATION, operation_key, "status", for_update=True) == "done":
        return {"email": email}
    MemberProvider(prefix=pool).reset_password(member.subject, password,
        email=email, username=member.username, owner=owner)
    # All same-password recovery attempts refer to the same external effect.
    for name in frappe.get_all(OPERATION,
            filters={"member": key, "status": "pending", "request_hash": fingerprint}, pluck="name"):
        frappe.db.set_value(OPERATION, name, "status", "done", update_modified=True)
    return {"email": email}


def register_owner(tenant, subject):
    """管理通道（开户 `register`）登记租户管理员身份。幂等；只增不改。"""
    if not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", subject):
        raise MemberError("invalid_request")
    name = hashlib.sha256(json.dumps([tenant, subject], separators=(",", ":")).encode()).hexdigest()
    if not frappe.db.exists(TENANT_IDENTITY, name):
        _insert({"doctype": TENANT_IDENTITY, "name": name, "tenant": tenant, "subject": subject, "role": "owner"})


def _registered(tenant, subject):
    """这个身份是不是本租户的：开户登记的管理员，或团队里建的员工。"""
    if not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", subject):
        return False
    return bool(frappe.db.exists(TENANT_IDENTITY, {"tenant": tenant, "subject": subject})
                or frappe.db.exists(IDENTITY, {"tenant": tenant, "subject": subject}))


def _alias(login_channel):
    try:
        return line_contract.idp_alias(login_channel)
    except ValueError:
        raise MemberError("invalid_request") from None


def _link_key(tenant, product, subject, alias):
    return hashlib.sha256(json.dumps([tenant, product, subject, alias], separators=(",", ":")).encode()).hexdigest()


def _person_owner(provider, alias, line_user_id):
    """LINE 专用身份的归属证明：只有本服务算得出来（服务器密钥），身份服务里管理员可见。"""
    return _digest("line-person-v1", provider.realm, alias, line_user_id)


def _person_lock(realm, alias, line_user_id):
    """同一池同一 LINE 的建 / 删串行（各租户锁不住彼此）；连接结束或提交后释放。"""
    name = "pearnly-line-person:" + hashlib.sha256(
        json.dumps([realm, alias, line_user_id], separators=(",", ":")).encode()).hexdigest()[:40]
    if (frappe.db.sql("select get_lock(%s, 20)", (name,)) or [[0]])[0][0] != 1:
        raise MemberError("unavailable")


def _old_direct_link(holder):
    """改造前 LINE 直接挂在租户账号上；这些账号都由本平台登记过，迁到专用身份时可以摘。"""
    return bool(frappe.db.exists(TENANT_IDENTITY, {"subject": holder})
                or frappe.db.exists(IDENTITY, {"subject": holder}))


def _release(realm, alias, line_user_id):
    """这个 LINE 在该池里已没有任何站点绑着：删掉它的专用身份（连同身份服务会话）。"""
    if frappe.db.exists(LINE_LINK, {"realm": realm, "alias": alias, "line_user_id": line_user_id}):
        return
    provider = MemberProvider(prefix=realm_prefix(realm))
    provider.release_line_person(_person_owner(provider, alias, line_user_id))


def link_line(tenant, product, subject, line_user_id, login_channel, issuer=""):
    """站点上一条生效的 LINE 绑定 → 该 LINE 在站点所在账号池的专用身份挂上这个 LINE。

    入口按 LINE Login 频道共用（全平台只配一次）。登录时身份服务认出的是专用身份，站点按
    自己的有效绑定认人，所以同一个 LINE 绑在多个租户的账号上互不冲突（2026-10-04 用户在
    testerp 与 testcowork 各有账号、同一个 LINE，后绑的那边一直登录不了）。
    站点只能替**本租户已登记**的身份登记，并按（租户, 产品, 身份）记账。
    """
    if not line_contract.valid_line_user_id(line_user_id) or not _registered(tenant, subject):
        raise MemberError("not_permitted")
    alias = _alias(login_channel)
    provider = MemberProvider(product, issuer=issuer)
    key = _link_key(tenant, product, subject, alias)
    previous = frappe.db.get_value(LINE_LINK, key, ["line_user_id", "realm"], as_dict=True)
    _person_lock(provider.realm, alias, line_user_id)
    provider.ensure_line_person(alias, line_user_id, _person_owner(provider, alias, line_user_id),
                                _old_direct_link)
    # 租户账号自己不再挂 LINE（改造前可能挂着别的 LINE；没有就是 404）。
    provider.unlink_line(subject, alias)
    values = {"line_user_id": line_user_id, "realm": provider.realm}
    if previous:
        frappe.db.set_value(LINE_LINK, key, values)
    else:
        _insert({"doctype": LINE_LINK, "name": key, "tenant": tenant, "product": product,
                 "subject": subject, "alias": alias, **values})
    if previous and (previous.line_user_id, previous.realm or provider.realm) != (line_user_id, provider.realm):
        realm = previous.realm or provider.realm
        _person_lock(realm, alias, previous.line_user_id)
        _release(realm, alias, previous.line_user_id)
    return {"subject": subject}


def unlink_line(tenant, product, subject, login_channel, issuer=""):
    if not _registered(tenant, subject):
        raise MemberError("not_permitted")
    alias = _alias(login_channel)
    provider = MemberProvider(product, issuer=issuer)
    filters = {"tenant": tenant, "product": product, "subject": subject, "alias": alias}
    rows = frappe.get_all(LINE_LINK, filters=filters, fields=["line_user_id", "realm"])
    frappe.db.delete(LINE_LINK, filters)
    # 改造前直接挂在这个账号上的关联（没有就是 404，不算错）。
    provider.unlink_line(subject, alias)
    for row in rows:
        realm = row.realm or provider.realm
        _person_lock(realm, alias, row.line_user_id)
        _release(realm, alias, row.line_user_id)
    return {"subject": subject}


def execute(tenant, action, email, password, username="", request_id="", subject="", line_user_id="",
            login_channel="", issuer=""):
    if action in ("link-line", "unlink-line"):
        tenant, product = authorize(tenant, line_contract.PRODUCTS)
        if action == "link-line":
            return link_line(tenant, product, subject, line_user_id, login_channel, issuer)
        return unlink_line(tenant, product, subject, login_channel, issuer)
    tenant, product = authorize(tenant)
    if (not isinstance(email, str) or "@" not in email or len(email) > 140
            or not isinstance(password, str) or not password):
        raise MemberError("invalid_request")
    email = email.strip().lower()
    if action == "create":
        return create(tenant, email, username, password, product, issuer)
    if action == "reset-password":
        return reset_password(tenant, email, password, request_id, product, issuer)
    raise MemberError("invalid_request")


def move_members(tenant, product, issuer, *, dry_run=False):
    """管理通道：站点换了账号池（拆池前开的 Cowork 租户搬到 Cowork 池，或回退搬回原来的池）。

    本租户本产品团队里建的号都放进站点新签到的池：新池里已有本服务建的同一个号（归属证明一致，回退时就是原来那个）
    就沿用，否则建一个**不带密码**的（老板在团队管理里重设密码，密码不经过本服务以外的任何人）。登记改记新池与新身份；
    原来那个池里的号不删，回退时按归属证明找回。原来那个池里本租户本产品的 LINE 关联记录删掉（那个池里没别的站点用这个
    LINE 了就释放它的专用身份），站点对账时在新池重新挂。`dry_run` 只读：列出每个号会沿用还是新建，不写任何东西。
    """
    if product not in PRODUCTS or not str(issuer or "").strip():
        raise MemberError("not_permitted")
    _lock_tenant(tenant)
    pool = pool_prefix(product, issuer)
    provider = MemberProvider(prefix=pool)
    rows = frappe.get_all(IDENTITY, filters={"tenant": tenant, "product": product},
        fields=["name", "email", "username", "subject", "realm", "product"], order_by="name asc")
    members = []
    for row in rows:
        owner = _digest("member-owner-v1", tenant, product, row.email, row.username.lower())
        if not row.subject:
            # 建号请求登记了、号没建出来（超时后没再重试）：站点上也没有这个人，不搬。
            members.append({"email": row.email, "username": row.username, "subject": None, "action": "incomplete"})
            continue
        if _pool_of(row) == pool:
            subject = provider.read_owned(row.subject, email=row.email, username=row.username, owner=owner)
            members.append({"email": row.email, "username": row.username, "subject": subject, "action": "kept"})
            continue
        found = provider.lookup_owned(None, email=row.email, username=row.username, owner=owner)
        if dry_run:
            members.append({"email": row.email, "username": row.username, "subject": found,
                            "action": "adopt" if found else "create"})
            continue
        subject = found or provider.create(email=row.email, username=row.username, password=None, owner=owner)
        frappe.db.set_value(IDENTITY, row.name, {"subject": subject, "realm": provider.realm})
        members.append({"email": row.email, "username": row.username, "subject": subject,
                        "action": "adopted" if found else "created"})
    stale = [link for link in frappe.get_all(LINE_LINK, filters={"tenant": tenant, "product": product},
                                             fields=["name", "realm", "alias", "line_user_id"], order_by="name asc")
             if link.realm and link.realm != provider.realm]
    if not dry_run:
        for link in stale:
            frappe.db.delete(LINE_LINK, {"name": link.name})
            _person_lock(link.realm, link.alias, link.line_user_id)
            _release(link.realm, link.alias, link.line_user_id)
    return {"realm": provider.realm, "members": members, "line_links_released": len(stale)}


def _purge_preflight(tenant, *, frozen=False):
    """Only management dispatch calls lifecycle methods; no product RPC action."""
    status = frappe.db.get_value("Pearnly Tenant", tenant, "status", for_update=True)
    if status not in (("deleting",) if frozen else ("active", "suspended", "deleting")):
        raise MemberError("not_permitted")
    rows = frappe.get_all(IDENTITY, filters={"tenant": tenant},
        fields=["name", "tenant", "product", "email", "username", "subject", "realm"], order_by="name asc")
    # One provider per recorded realm (old rows: the product's realm); an unknown
    # product or a realm that is no longer configured fails closed.
    providers = {}
    verified = []
    for row in rows:
        if row.tenant != tenant or row.product not in PRODUCTS:
            raise MemberError("conflict")
        pool = _pool_of(row)
        provider = providers.get(pool) or providers.setdefault(pool, MemberProvider(prefix=pool))
        owner = _digest("member-owner-v1", tenant, row.product, row.email, row.username.lower())
        subject = provider.lookup_owned(row.subject,
            email=row.email, username=row.username, owner=owner)
        verified.append((row, subject, owner, provider))
    return verified


def prepare_purge(tenant):
    """Before scheduling teardown: verify all ownership, then freeze new RPC writes."""
    members = _purge_preflight(tenant)
    frappe.db.set_value("Pearnly Tenant", tenant, "status", "deleting")
    return {"members": len(members), "subjects": [subject for _row, subject, _owner, _provider in members if subject]}


def purge_members(tenant):
    """Management-only teardown; exact verified subjects, no email-based deletion.

    A remote failure leaves the local rows intact. Retry treats a previously
    deleted subject as absent and safely finishes the remaining verified accounts.
    The calling management transaction owns the final local commit.
    """
    members = _purge_preflight(tenant, frozen=True)
    for row, subject, owner, provider in members:
        if subject:
            provider.delete_owned(subject, email=row.email, username=row.username, owner=owner)
    for row, _subject, _owner, _provider in members:
        frappe.db.delete(OPERATION, {"member": row.name})
        frappe.db.delete(IDENTITY, {"name": row.name, "tenant": tenant})
    frappe.db.delete(TENANT_IDENTITY, {"tenant": tenant})
    lines = {(row.realm, row.alias, row.line_user_id) for row in frappe.get_all(
        LINE_LINK, filters={"tenant": tenant}, fields=["realm", "alias", "line_user_id"]) if row.realm}
    frappe.db.delete(LINE_LINK, {"tenant": tenant})
    # 别的租户不再绑着的 LINE，专用身份一并删掉。
    for realm, alias, line_user_id in sorted(lines):
        _person_lock(realm, alias, line_user_id)
        _release(realm, alias, line_user_id)
    return {"members": len(members)}
