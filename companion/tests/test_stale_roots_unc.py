"""网络路径（UNC）上的数据根「确实没了才去掉」（主会话 10-26：两个成员同扫服务器不在 accserver 上实测，用用例覆盖）。

express_machine.root_gone 在网络路径上的四种情况，用不碰网络的替身模拟一台服务器的「在不在」与它的共享 / 文件夹：
- 服务器不在线或连不上（445 不通）、探测超时：位置与授权根一律保留；
- 服务器在线、共享访问得到、文件夹没了：去掉；共享本身访问不到的不算没了（保留）；
- 两个成员同时探测同一台服务器：结论一致（scan_pace.HostGate 让后来的那个等前一个的结果），不会一个保留一个去掉；
- 配对 / 换配对码、断开后重连、心跳里的定时重扫三条路径都照这个判。
"""
import ntpath
import threading
import time

import pytest

from src.companion import express_machine, pairing_flow, scan_pace, worker
from test_stale_roots import _fake_pairing, _load, _settings, _store, roots  # noqa: F401

LIVE = r"\\acc\ACC\70EXP"       # 服务器在、文件夹在
GONE = r"\\acc\ACC\71EXP"       # 服务器在、共享在、文件夹没了 → 去掉
NOSHARE = r"\\acc\OLD\67EXP"    # 服务器在、共享访问不到 → 不算没了
DOWN = r"\\down\ACC\69EXP"      # 服务器不在线（445 不通）→ 保留
SLOW = r"\\slow\ACC\68EXP"      # 连得上但探测超时 → 保留


class FakeNet:
    """几台服务器：``state`` 是 up / down / slow；``dirs`` 是访问得到的共享与文件夹（小写、不带结尾斜杠）。"""

    def __init__(self):
        self.state = {"acc": "up", "down": "down", "slow": "slow"}
        self.dirs = {r"\\acc\acc", r"\\acc\acc\70exp"}
        self.probes = []
        self.delay = 0.0

    @staticmethod
    def host(path):
        return str(path).lstrip("\\").split("\\")[0].lower()

    def probe(self, path):
        host = self.host(path)
        self.probes.append(host)
        if self.state.get(host) == "down":
            return False
        if self.state.get(host) == "slow":
            time.sleep(1.0)  # 比探测时限长：按卡住算
            return True
        time.sleep(self.delay)
        return True

    def isdir(self, path):
        text = str(path).replace("/", "\\")
        if not text.startswith("\\\\"):
            return _REAL_ISDIR(path)
        if self.state.get(self.host(text)) != "up":
            return False  # 同真的 os.path.isdir：访问不到回 False，不抛错
        return text.rstrip("\\").lower() in self.dirs


_REAL_ISDIR = express_machine.os.path.isdir


@pytest.fixture
def net(monkeypatch, roots):  # noqa: F811 — roots 来自 test_stale_roots（本机数据根、状态目录）
    fake = FakeNet()
    monkeypatch.setattr(scan_pace, "GATE", scan_pace.HostGate(fake.probe, probe_seconds=0.2))
    monkeypatch.setattr(express_machine.os.path, "isdir", fake.isdir)
    return fake


def test_on_a_network_path_only_a_missing_folder_on_a_reachable_share_counts_as_gone(net):
    assert express_machine.root_gone(GONE) is True        # 服务器在、共享在、文件夹没了
    assert express_machine.root_gone(LIVE) is False
    assert express_machine.root_gone(NOSHARE) is False     # 共享访问不到：不是「没了」
    assert express_machine.root_gone(DOWN) is False        # 445 不通
    assert express_machine.root_gone(SLOW) is False        # 探测超时
    assert ntpath.splitdrive(GONE)[0].lower() == r"\\acc\acc"


def test_a_server_that_drops_offline_keeps_every_root_on_it(net):
    net.state["acc"] = "down"
    assert express_machine.live_roots([LIVE, GONE, NOSHARE]) == [LIVE, GONE, NOSHARE]


def test_one_search_asks_each_share_once(net):
    asked = {}
    gone = express_machine.gone_test(asked)
    assert [gone(root) for root in (GONE, LIVE, NOSHARE, DOWN, DOWN + r"\..\66EXP")] == [True, False, False, False, False]
    assert net.probes.count("acc") == 2 and net.probes.count("down") == 1  # \\acc\ACC、\\acc\OLD 各一次


@pytest.mark.parametrize("root, expected", [(GONE, True), (DOWN, False), (SLOW, False)])
def test_two_members_probing_the_same_server_at_once_agree(net, root, expected):
    """两个成员（各自一次查找、各自的 ``asked``）同时问同一台服务器：后来的等前一个的结果，结论一致。"""
    net.delay = 0.05
    start = threading.Barrier(2)
    results = []

    def scan():
        start.wait()
        results.append(express_machine.root_gone(root, {}))

    threads = [threading.Thread(target=scan) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert results == [expected, expected]


def test_a_server_that_flaps_between_two_members_still_gives_one_answer(net):
    """同一次定时重扫里两个成员共用一份 ``asked``：第一个问到在线，之后服务器掉线，第二个照第一次的结论判，不会一个去掉一个保留。"""
    asked = {}
    first = express_machine.live_roots([LIVE, GONE], asked)
    net.state["acc"] = "down"
    net.dirs.discard(r"\\acc\acc")
    second = express_machine.live_roots([LIVE, GONE, NOSHARE], asked)
    assert first == [LIVE] and second == [LIVE, NOSHARE]


# ── 三条路径 ──

def test_pairing_on_network_roots_drops_only_the_missing_folder(tmp_path, monkeypatch, roots, net):  # noqa: F811
    store, _ids = _store(tmp_path, roots, ("exp_endpoint-1_secret", LIVE, [LIVE, GONE, NOSHARE, DOWN, SLOW]))
    seen = _fake_pairing(monkeypatch, roots)

    assert _settings(store).pair("exp_endpoint-2_secret@testcowork.pearnly.com", "zh")["ok"] is True
    assert seen["express_root"] == LIVE
    assert seen["authorized_roots"] == [LIVE, DOWN, SLOW, NOSHARE]  # 授权年度新的在前


def test_changing_the_key_on_a_network_primary_that_is_gone_moves_to_the_next(tmp_path, monkeypatch, roots, net):  # noqa: F811
    store, (member,) = _store(tmp_path, roots, ("exp_endpoint-1_secret", GONE, [GONE, LIVE, DOWN]))
    seen = _fake_pairing(monkeypatch, roots)

    pairing_flow.run(store, "exp_endpoint-1_new@testcowork.pearnly.com", roots=[], profile_id=member, lang="zh")
    assert (seen["express_root"], seen["authorized_roots"]) == (LIVE, [LIVE, DOWN])


def test_reconnecting_on_network_roots_keeps_the_offline_server(tmp_path, monkeypatch, roots, net):  # noqa: F811
    store, (member,) = _store(tmp_path, roots, ("exp_endpoint-1_secret", LIVE, [LIVE, GONE, DOWN, SLOW]))
    store.set_places([r"\\acc\ACC", r"\\down\ACC", r"\\slow\ACC"])  # 重连只认在「位置」里的（断开前授权过的）
    store = _load(store.path)
    monkeypatch.setattr(express_machine, "places_roots", lambda places: [LIVE])
    settings = _settings(store)
    assert settings.disconnect(member)["ok"] is True
    seen = _fake_pairing(monkeypatch, roots)

    assert settings.pair("exp_endpoint-1_secret@testcowork.pearnly.com", "zh")["ok"] is True
    assert seen["express_root"] == LIVE and seen["authorized_roots"] == [LIVE, DOWN, SLOW]


def test_a_scheduled_rescan_treats_every_member_on_the_same_server_alike(tmp_path, monkeypatch, roots, net):  # noqa: F811
    store, (first, second) = _store(tmp_path, roots,
                                    ("exp_endpoint-1_secret", LIVE, [LIVE, GONE, DOWN]),
                                    ("exp_endpoint-2_secret", LIVE, [LIVE, DOWN, GONE, SLOW]))

    worker._drop_gone_roots(store.runtime_config(first))

    saved = _load(store.path)
    assert {r.profile_id: list(r.authorized_roots) for r in saved.profiles} == {first: [LIVE, DOWN],
                                                                               second: [LIVE, DOWN, SLOW]}


def test_a_scheduled_rescan_while_the_server_is_down_changes_nobody(tmp_path, monkeypatch, roots, net):  # noqa: F811
    net.state["acc"] = "down"
    store, (first, second) = _store(tmp_path, roots,
                                    ("exp_endpoint-1_secret", LIVE, [LIVE, GONE, DOWN]),
                                    ("exp_endpoint-2_secret", LIVE, [LIVE, GONE]))

    worker._drop_gone_roots(store.runtime_config(first))

    saved = _load(store.path)
    assert {r.profile_id: list(r.authorized_roots) for r in saved.profiles} == {first: [LIVE, GONE, DOWN],
                                                                               second: [LIVE, GONE]}
