"""ERP / Cowork member identity ownership and retry ledger, private Control service only.

Each product has its own identity realm (Cowork was split from ERP on 2026-09-26).
The product comes only from the scoped token header, never from the request body.

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

from pearnly_control.billing_auth import valid_token
from pearnly_control.account.member_provider import MemberError, MemberProvider, PREFIX

IDENTITY = "Pearnly Member Identity"
OPERATION = "Pearnly Member Operation"
# ERP keys must stay byte-identical: ledger keys and owner stamps already exist.
PRODUCTS = ("erp", "cowork")
_CAPABILITY = object()


def authorize(tenant):
    product = frappe.get_request_header("X-Pearnly-Billing-Product")
    if (not frappe.conf.get("pearnly_billing_only")
            or not frappe.conf.get("pearnly_billing_scoped_tokens")
            or product not in PRODUCTS
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


def create(tenant, email, username, password, product="erp"):
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9]+", username):
        raise MemberError("invalid_request")
    key = _member_key(tenant, email, product)
    fingerprint = _digest("member-create-v1", key, username.lower(), password)
    existing = frappe.db.get_value(IDENTITY, key,
        ["tenant", "product", "email", "username", "subject", "create_request_hash"],
        as_dict=True, for_update=True)
    owner = _digest("member-owner-v1", tenant, product, email, username.lower())
    provider = MemberProvider(product)
    if existing:
        if (existing.tenant != tenant or existing.product != product or existing.email != email
                or existing.username.lower() != username.lower()
                or not hmac.compare_digest(existing.create_request_hash, fingerprint)):
            raise MemberError("conflict")
        if existing.subject:
            provider.read_owned(existing.subject, email=email, username=existing.username, owner=owner)
            return {"subject": existing.subject, "email": email, "username": existing.username}
    else:
        _insert({"doctype": IDENTITY, "name": key, "tenant": tenant, "product": product,
            "email": email, "username": username, "subject": None, "create_request_hash": fingerprint})
    frappe.db.commit()  # Retain the original create request even if Keycloak times out.
    _lock_tenant(tenant)
    subject = frappe.db.get_value(IDENTITY, key, "subject", for_update=True)
    if subject:
        provider.read_owned(subject, email=email, username=username, owner=owner)
        return {"subject": subject, "email": email, "username": username}
    subject = provider.create(email=email, username=username, password=password, owner=owner)
    frappe.db.set_value(IDENTITY, key, "subject", subject)
    return {"subject": subject, "email": email, "username": username}


def reset_password(tenant, email, password, request_id, product="erp"):
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise MemberError("invalid_request")
    key = _member_key(tenant, email, product)
    member = frappe.db.get_value(IDENTITY, key,
        ["tenant", "product", "email", "username", "subject"], as_dict=True, for_update=True)
    if (not member or not member.subject or member.tenant != tenant
            or member.product != product or member.email != email):
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
    MemberProvider(product).reset_password(member.subject, password,
        email=email, username=member.username, owner=owner)
    # All same-password recovery attempts refer to the same external effect.
    for name in frappe.get_all(OPERATION,
            filters={"member": key, "status": "pending", "request_hash": fingerprint}, pluck="name"):
        frappe.db.set_value(OPERATION, name, "status", "done", update_modified=True)
    return {"email": email}


def execute(tenant, action, email, password, username="", request_id=""):
    tenant, product = authorize(tenant)
    if (not isinstance(email, str) or "@" not in email or len(email) > 140
            or not isinstance(password, str) or not password):
        raise MemberError("invalid_request")
    email = email.strip().lower()
    if action == "create":
        return create(tenant, email, username, password, product)
    if action == "reset-password":
        return reset_password(tenant, email, password, request_id, product)
    raise MemberError("invalid_request")


def _purge_preflight(tenant, *, frozen=False):
    """Only management dispatch calls lifecycle methods; no product RPC action."""
    status = frappe.db.get_value("Pearnly Tenant", tenant, "status", for_update=True)
    if status not in (("deleting",) if frozen else ("active", "suspended", "deleting")):
        raise MemberError("not_permitted")
    rows = frappe.get_all(IDENTITY, filters={"tenant": tenant},
        fields=["name", "tenant", "product", "email", "username", "subject"], order_by="name asc")
    if not rows:
        return [], {}
    # One provider per product realm; an unknown product fails closed.
    providers = {}
    verified = []
    for row in rows:
        if row.tenant != tenant or row.product not in PRODUCTS:
            raise MemberError("conflict")
        provider = providers.get(row.product) or providers.setdefault(row.product, MemberProvider(row.product))
        owner = _digest("member-owner-v1", tenant, row.product, row.email, row.username.lower())
        subject = provider.lookup_owned(row.subject,
            email=row.email, username=row.username, owner=owner)
        verified.append((row, subject, owner))
    return verified, providers


def prepare_purge(tenant):
    """Before scheduling teardown: verify all ownership, then freeze new RPC writes."""
    members, _providers = _purge_preflight(tenant)
    frappe.db.set_value("Pearnly Tenant", tenant, "status", "deleting")
    return {"members": len(members), "subjects": [subject for _row, subject, _owner in members if subject]}


def purge_members(tenant):
    """Management-only teardown; exact verified subjects, no email-based deletion.

    A remote failure leaves the local rows intact. Retry treats a previously
    deleted subject as absent and safely finishes the remaining verified accounts.
    The calling management transaction owns the final local commit.
    """
    members, providers = _purge_preflight(tenant, frozen=True)
    for row, subject, owner in members:
        if subject:
            providers[row.product].delete_owned(subject, email=row.email, username=row.username, owner=owner)
    for row, _subject, _owner in members:
        frappe.db.delete(OPERATION, {"member": row.name})
        frappe.db.delete(IDENTITY, {"name": row.name, "tenant": tenant})
    return {"members": len(members)}
