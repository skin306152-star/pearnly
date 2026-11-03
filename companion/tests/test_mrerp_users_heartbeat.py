"""心跳带「MR.ERP 连接号 → 用户名」（ADR-026 第 8 条，第 3 步账号租约）：CC 一侧。

- 只取这个站点授权过的连接（按凭据备注认，同 ``mrerp_connections``），用户名来自凭据枚举里的明文用户名，不读密码；
- 别的站点、第 2 步代录自己存的（备注不是站点）、Express 的凭据、没有用户名的都不报；
- 心跳报文：给了才带 ``mrerp_users`` 键，空对象照发；读不到时 worker 不报（None）。
"""
import json

from companion import queue_client, worker
from companion.express_operator import credentials

PASSWORD = "S3cret-pass!"
SITE = "testcowork.pearnly.com"
MRERP = "https://www.mrerp4sme.com"


class Vault:
    """凭据管理器替身：目标名 → (用户名, 密码, 备注)。``entries`` 只给枚举时的三项明文（不含密码）。"""

    def __init__(self):
        self.items = {}
        self.read_passwords = 0

    def store(self, target, user, password, comment="Pearnly CC"):
        self.items[target] = (user, password, comment)

    def entries(self, prefix):
        return [(name, item[2], item[0]) for name, item in self.items.items() if name.startswith(prefix)]


def _vault():
    vault = Vault()
    mine = credentials.site_comment(SITE)
    vault.store(credentials.mrerp_target(MRERP, "CWEC-2"), "acc02", PASSWORD, mine)
    vault.store(credentials.mrerp_target(MRERP, "CWEC-1"), "test01", PASSWORD, mine)
    vault.store(credentials.mrerp_target(MRERP, "CWEC-3"), "", PASSWORD, mine)  # 没有用户名：不报
    vault.store(credentials.mrerp_target(MRERP, "ERPC-1"), "erpuser", PASSWORD, credentials.site_comment("testerp.pearnly.com"))
    vault.store(credentials.mrerp_target(MRERP, "OPR-1"), "op", PASSWORD)  # 第 2 步代录自己存的：不算哪个站点
    vault.store(credentials.target("CWEC-9"), "expuser", PASSWORD)
    return vault


def test_only_this_sites_connections_with_their_usernames_and_never_a_password():
    vault = _vault()
    found = credentials.mrerp_users(SITE, listing=vault.entries)
    assert found == {"CWEC-1": "test01", "CWEC-2": "acc02"}
    assert PASSWORD not in json.dumps(found)
    assert credentials.mrerp_users("", listing=vault.entries) == {}
    assert credentials.mrerp_users("testerp.pearnly.com", listing=vault.entries) == {"ERPC-1": "erpuser"}


def test_the_same_connections_as_the_login_list():
    vault = _vault()
    listing = lambda prefix: [(name, note) for name, note, _user in vault.entries(prefix)]  # noqa: E731
    with_user = set(credentials.mrerp_users(SITE, listing=vault.entries))
    assert with_user <= set(credentials.mrerp_connections(SITE, listing=listing))


def test_heartbeat_carries_the_usernames_only_when_given():
    import httpx

    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"ok": True}})

    client = queue_client.QueueClient(queue_client.QueueClientConfig("https://" + SITE, "exp_CWEC-1_secret", "c1"))
    client._client = httpx.Client(base_url="https://" + SITE, transport=httpx.MockTransport(handler))
    client.health(mrerp_users={"CWEC-1": "test01"})
    client.health(mrerp_users={})
    client.health()
    assert bodies[0]["mrerp_users"] == {"CWEC-1": "test01"}
    assert bodies[1]["mrerp_users"] == {}
    assert "mrerp_users" not in bodies[2]


class _Cfg:
    backend_url = "https://" + SITE


def test_worker_reads_this_member_sites_usernames_and_reports_nothing_when_reading_fails(monkeypatch):
    seen = []
    monkeypatch.setattr(credentials, "mrerp_users", lambda site: seen.append(site) or {"CWEC-1": "test01"})
    assert worker._mrerp_users(_Cfg()) == {"CWEC-1": "test01"}
    assert seen == [SITE]

    def broken(site):
        raise OSError("vault")

    monkeypatch.setattr(credentials, "mrerp_users", broken)
    assert worker._mrerp_users(_Cfg()) is None
