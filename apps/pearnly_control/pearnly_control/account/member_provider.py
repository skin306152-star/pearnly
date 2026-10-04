"""Fixed Keycloak member operations. No user-supplied URL, subject or admin credential."""
from urllib.parse import quote

import frappe
import requests

OWNER_ATTRIBUTE = "pearnly_member_owner"
PREFIX = "pearnly_member_identity"
# 两个账号池的配置键前缀：业务池（ERP / DMS）、Cowork 池。
POOL_PREFIXES = (PREFIX, PREFIX + "_cowork")
TIMEOUT = 15
# LINE 专用身份：用户名 = 前缀 + 归属证明的前 32 位；邮箱是不可投递的占位（账号池要求有邮箱，
# 否则登录时会弹"补全资料"）。不设密码，只能经 LINE 入口登录。
LINE_PERSON_PREFIX = "pearnly-line-"
LINE_PERSON_DOMAIN = "line.invalid"


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


def _issuer_of(prefix):
    return str(frappe.conf.get(prefix + "_issuer") or "").rstrip("/")


def _realm_of(issuer):
    issuer = str(issuer or "").rstrip("/")
    return issuer.rsplit("/realms/", 1)[1] if "/realms/" in issuer else ""


def pool_prefix(product="erp", issuer=""):
    """站点实际签到哪个账号池就用哪个；没给签发方时按产品。

    老租户可能与产品默认的池不一致（testcowork 是 Cowork 产品却签到业务池，2026-10-04 查实），
    按产品猜会把关联写进它根本不存在的池。只比账号池名：站点给的是公开地址
    （`https://auth…/realms/x`），控制面配的是内部地址（`http://keycloak:8080/realms/x`）。
    池只能是已配置的两个之一，否则拒绝。
    """
    if not str(issuer or "").strip():
        return config_prefix(product)
    realm = _realm_of(issuer)
    for prefix in POOL_PREFIXES:
        if realm and _realm_of(_issuer_of(prefix)) == realm:
            return prefix
    raise MemberError("not_permitted")


def realm_prefix(realm):
    """账号池名 → 配置键前缀（解绑时按关联记录上的池找回去）。"""
    for prefix in POOL_PREFIXES:
        if realm and _realm_of(_issuer_of(prefix)) == realm:
            return prefix
    raise MemberError("unavailable")


class MemberProvider:
    def __init__(self, product="erp", *, issuer="", prefix=None):
        prefix = prefix or pool_prefix(product, issuer)
        issuer = _issuer_of(prefix)
        self.client_id = frappe.conf.get(prefix + "_admin_client_id")
        self.secret = frappe.conf.get(prefix + "_admin_client_secret")
        if "/realms/" not in issuer or not self.client_id or not self.secret:
            raise MemberError("unavailable")
        base, realm = issuer.rsplit("/realms/", 1)
        if not base or not realm or "/" in realm:
            raise MemberError("unavailable")
        self.realm = realm
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

    @staticmethod
    def _line_person(owner):
        username = LINE_PERSON_PREFIX + owner[:32]
        return username, f"{username}@{LINE_PERSON_DOMAIN}"

    def find_line_person(self, owner):
        username, email = self._line_person(owner)
        row = self.find("username", username)
        return self.verify(row, email=email, username=username, owner=owner) if row else None

    def ensure_line_person(self, alias, line_user_id, owner, removable):
        """这个 LINE 在本池的专用身份，并让它独占该入口下的这条 LINE 关联（幂等）。

        专用身份不属于任何租户：同一个 LINE 可以绑多个租户的账号，各站点按自己的有效绑定认人。
        用户名由归属证明派生，所以同一 LINE 同一入口只会有一个；并发重复建会撞名后按归属证明认领。
        原来直接挂在别的身份上的关联：`removable(holder)` 认可的（本平台登记过的租户账号）才摘，
        否则报 conflict，不抢。
        """
        username, email = self._line_person(owner)
        subject = self.find_line_person(owner)
        if not subject:
            response = self._request("POST", "/users", json={
                "username": username, "email": email, "emailVerified": True, "firstName": "LINE",
                "enabled": True, "requiredActions": [], "attributes": {OWNER_ATTRIBUTE: [owner]},
            })
            if response.status_code not in (201, 204, 409):
                raise MemberError("unavailable")
            subject = self.find_line_person(owner)
            if not subject:
                raise MemberError("unavailable")
        for holder in self.line_holders(alias, line_user_id):
            if holder != subject:
                if not removable(holder):
                    raise MemberError("conflict")
                self.unlink_line(holder, alias)
        self._link(subject, alias, line_user_id)
        return subject

    def release_line_person(self, owner):
        """删掉 LINE 专用身份；它在身份服务里的会话随之结束。已经没有就算完成。"""
        subject = self.find_line_person(owner)
        if not subject:
            return
        response = self._request("DELETE", "/users/" + quote(subject, safe=""))
        if response.status_code not in (204, 404):
            raise MemberError("unavailable")

    def _linked(self, subject, alias):
        path = "/users/" + quote(subject, safe="") + "/federated-identity"
        response = self._request("GET", path)
        if response.status_code == 404:
            raise MemberError("not_permitted")
        if response.status_code != 200:
            raise MemberError("unavailable")
        current = [row for row in response.json() or [] if row.get("identityProvider") == alias]
        return current[0].get("userId") if current else None

    def _link(self, subject, alias, line_user_id):
        """幂等：已是同一个 LINE 就不动；是别的 LINE 就换成这个。"""
        current = self._linked(subject, alias)
        if current == line_user_id:
            return
        if current:
            self.unlink_line(subject, alias)
        response = self._request("POST", "/users/" + quote(subject, safe="") + "/federated-identity/"
                                 + quote(alias, safe=""), json={
            "identityProvider": alias, "userId": line_user_id, "userName": line_user_id})
        if response.status_code in (200, 201, 204):
            return
        # 并发里别的请求先挂上了：回读是同一个 LINE 就算成功。
        if response.status_code == 409 and self._linked(subject, alias) == line_user_id:
            return
        raise MemberError("conflict" if response.status_code == 409 else "unavailable")

    def line_holders(self, alias, line_user_id):
        """这个 LINE 在该入口下关联在哪些身份上。"""
        response = self._request("GET", "/users", params={
            "idpAlias": alias, "idpUserId": line_user_id, "briefRepresentation": "true", "max": 20})
        if response.status_code != 200:
            raise MemberError("unavailable")
        rows = response.json()
        if not isinstance(rows, list):
            raise MemberError("unavailable")
        return [row.get("id") for row in rows if row.get("id")]

    def unlink_line(self, subject, alias):
        response = self._request("DELETE", "/users/" + quote(subject, safe="")
                                 + "/federated-identity/" + quote(alias, safe=""))
        if response.status_code not in (200, 204, 404):
            raise MemberError("unavailable")
