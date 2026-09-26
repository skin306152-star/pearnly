"""Fixed Keycloak member operations. No user-supplied URL, subject or admin credential."""
from urllib.parse import quote

import frappe
import requests

OWNER_ATTRIBUTE = "pearnly_member_owner"
PREFIX = "pearnly_member_identity"
TIMEOUT = 15


class MemberError(Exception):
    """Only these fixed codes can cross the account RPC boundary."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def config_prefix(product="erp"):
    """每个产品一个账号池（realm）。ERP 沿用原来那组键（`pearnly_member_identity_*`），
    Cowork 用自己的一组（`pearnly_member_identity_cowork_*`，用户 2026-09-26：Cowork 与 ERP 分池）。"""
    # DMS 与 ERP 同在业务 realm；DMS 只用 LINE 关联，不在这里建号（见 member_service）。
    if product in ("erp", "dms"):
        return PREFIX
    if product == "cowork":
        return PREFIX + "_cowork"
    raise MemberError("not_permitted")


class MemberProvider:
    def __init__(self, product="erp"):
        prefix = config_prefix(product)
        issuer = str(frappe.conf.get(prefix + "_issuer") or "").rstrip("/")
        self.client_id = frappe.conf.get(prefix + "_admin_client_id")
        self.secret = frappe.conf.get(prefix + "_admin_client_secret")
        if "/realms/" not in issuer or not self.client_id or not self.secret:
            raise MemberError("unavailable")
        base, realm = issuer.rsplit("/realms/", 1)
        if not base or not realm or "/" in realm:
            raise MemberError("unavailable")
        self.token_url = issuer + "/protocol/openid-connect/token"
        self.admin_url = base + "/admin/realms/" + quote(realm, safe="")
        self._token = None

    def _request(self, method, path, **kwargs):
        try:
            if self._token is None:
                token = requests.post(self.token_url, data={
                    "grant_type": "client_credentials", "client_id": self.client_id,
                    "client_secret": self.secret,
                }, timeout=TIMEOUT, allow_redirects=False)
                if token.status_code != 200:
                    raise MemberError("unavailable")
                self._token = token.json()["access_token"]
            return requests.request(method, self.admin_url + path,
                headers={"Authorization": "Bearer " + self._token},
                timeout=TIMEOUT, allow_redirects=False, **kwargs)
        except (requests.RequestException, ValueError, KeyError):
            # Never include response bodies, request objects, passwords or tokens.
            raise MemberError("unavailable") from None

    def find(self, field, value):
        response = self._request("GET", "/users", params={field: value, "exact": "true"})
        if response.status_code != 200:
            raise MemberError("unavailable")
        rows = response.json()
        if not isinstance(rows, list) or len(rows) > 1:
            raise MemberError("conflict")
        return rows[0] if rows else None

    @staticmethod
    def verify(row, *, email, username, owner, subject=None):
        if (not isinstance(row, dict) or not row.get("id")
                or (subject and row["id"] != subject)
                or str(row.get("email") or "").lower() != email
                or str(row.get("username") or "").lower() != username.lower()
                or (row.get("attributes") or {}).get(OWNER_ATTRIBUTE) != [owner]):
            raise MemberError("conflict")
        return row["id"]

    def read_owned(self, subject, *, email, username, owner):
        found = self.lookup_owned(subject, email=email, username=username, owner=owner)
        if not found:
            raise MemberError("conflict")
        return found

    def lookup_owned(self, subject, *, email, username, owner):
        """Private lifecycle preflight; absent is safe, another owner is never safe."""
        if not subject:
            found = [self.find(field, value) for field, value in (("email", email), ("username", username))]
            subjects = {self.verify(row, email=email, username=username, owner=owner) for row in found if row}
            if len(subjects) > 1:
                raise MemberError("conflict")
            return next(iter(subjects), None)
        response = self._request("GET", "/users/" + quote(subject, safe=""))
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise MemberError("unavailable")
        return self.verify(response.json(), email=email, username=username, owner=owner, subject=subject)

    def delete_owned(self, subject, *, email, username, owner):
        subject = self.lookup_owned(subject, email=email, username=username, owner=owner)
        if not subject:
            return
        response = self._request("DELETE", "/users/" + quote(subject, safe=""))
        if response.status_code not in (204, 404):
            raise MemberError("unavailable")

    def create(self, *, email, username, password, owner):
        # An ordinary existing identity (including a tenant owner) is never adopted.
        for field, value in (("email", email), ("username", username)):
            found = self.find(field, value)
            if found:
                return self.verify(found, email=email, username=username, owner=owner)
        response = self._request("POST", "/users", json={
            "email": email, "username": username, "firstName": username,
            "enabled": True, "emailVerified": True, "requiredActions": [],
            "attributes": {OWNER_ATTRIBUTE: [owner]},
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        })
        if response.status_code not in (201, 204, 409):
            raise MemberError("unavailable")
        # Do not trust Location alone. A conflict or a retry after a lost response
        # succeeds only if the immutable, admin-only ownership proof matches.
        found = self.find("email", email)
        return self.verify(found, email=email, username=username, owner=owner)

    def reset_password(self, subject, password, *, email, username, owner):
        self.read_owned(subject, email=email, username=username, owner=owner)
        response = self._request("PUT", "/users/" + quote(subject, safe="") + "/reset-password",
            json={"type": "password", "value": password, "temporary": False})
        if response.status_code not in (200, 204):
            raise MemberError("unavailable")

    def link_line(self, subject, alias, line_user_id):
        """幂等：已是同一个 LINE 就不动；是别的 LINE 就换成这个。"""
        path = "/users/" + quote(subject, safe="") + "/federated-identity"
        response = self._request("GET", path)
        if response.status_code == 404:
            raise MemberError("not_permitted")
        if response.status_code != 200:
            raise MemberError("unavailable")
        current = [row for row in response.json() or [] if row.get("identityProvider") == alias]
        if current and current[0].get("userId") == line_user_id:
            return
        if current:
            self.unlink_line(subject, alias)
        response = self._request("POST", path + "/" + quote(alias, safe=""), json={
            "identityProvider": alias, "userId": line_user_id, "userName": line_user_id})
        if response.status_code == 409:
            # 这个 LINE 已经关联在另一个账号上：不抢。
            raise MemberError("conflict")
        if response.status_code not in (200, 201, 204):
            raise MemberError("unavailable")

    def line_holder(self, alias, line_user_id):
        """这个 LINE 在该入口下关联在谁身上（没有就 None）。"""
        response = self._request("GET", "/users", params={
            "idpAlias": alias, "idpUserId": line_user_id, "briefRepresentation": "true"})
        if response.status_code != 200:
            raise MemberError("unavailable")
        rows = response.json()
        if not isinstance(rows, list) or len(rows) > 1:
            raise MemberError("conflict")
        return rows[0].get("id") if rows else None

    def unlink_line(self, subject, alias):
        response = self._request("DELETE", "/users/" + quote(subject, safe="")
                                 + "/federated-identity/" + quote(alias, safe=""))
        if response.status_code not in (200, 204, 404):
            raise MemberError("unavailable")
