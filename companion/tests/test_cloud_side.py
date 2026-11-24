"""云端那份 CC 自己的接线（``cloud_link.cloud_side``），离线：假隧道目录 + 同进程假桥（``TunnelRelay`` + 假代发函数）。

守住：隧道盘只认 ``\\\\tsclient`` 下恰好一个带 in / out / ctl 的单字母盘；``--pair`` 成功 / 失败的退出码、令牌不进日志；
会话文件读后删除；没有会话时退避等待（最多 15 分钟一次）、不退出；transport 装上后 ``net.client`` 建的客户端走隧道；
桥重连换了会话后不重启就能继续；回复超时不重发；``ctl`` 里的撤销交 ``handle_forget``；代录凭据先看本机、没有才向桥要；
本地模式 net 不装 factory、代录取凭据不变。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from functools import partial
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("cryptography")

from src.companion import main, net, runtime_mode  # noqa: E402
from src.companion.cloud_link import cloud_side  # noqa: E402
from src.companion.cloud_link import cred_handoff as handoff  # noqa: E402
from src.companion.cloud_link import device_pairing as pairing  # noqa: E402
from src.companion.cloud_link import dir_watch  # noqa: E402
from src.companion.cloud_link import tunnel_protocol as protocol  # noqa: E402
from src.companion.cloud_link.tunnel_relay import Incoming, TunnelRelay  # noqa: E402
from src.companion.express_operator import credentials  # noqa: E402

HOST = "cloud.example.test"
SITE = "https://erp.example.test"
TOKEN = "pcc_bridge_side_token"  # scan: fake
HEARTBEAT = "/api/method/pearnly_core.cc_desktop.machine.heartbeat"
DEVICE_KEY = "dk_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2H1g"
FAST = dict(poll_first=0.005, poll_most=0.02, grace=0.3)


@pytest.fixture(autouse=True)
def _clean_factory():
    net.set_transport_factory(None)
    cloud_side._current = None
    yield
    net.set_transport_factory(None)
    cloud_side._current = None


def _tunnel(base, letter="Z"):
    root = base / letter
    for name in ("in", "out", "ctl"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _write_session(root, session):
    (root / "ctl" / protocol.SESSION_FILE).write_bytes(protocol.encode_session(session))


class Site:
    def __init__(self):
        self.seen = []

    def __call__(self, out):
        self.seen.append(out)
        return Incoming(200, {"content-type": "application/json"}, json.dumps({"message": {"n": len(self.seen)}}).encode())


class Bridge:
    """假桥：后台线程轮询 ``in``；``rotate`` 照真桥换会话（写新的 session.json、换上新密钥的中转）。"""

    def __init__(self, root, site):
        self.root, self.site = root, site
        self.relay = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.rotate()

    def rotate(self):
        session = protocol.new_session()
        self.relay = TunnelRelay(self.root, session, SITE, TOKEN, self.site)
        _write_session(self.root, session)
        return session

    def _run(self):
        while not self._stop.is_set():
            self.relay.poll_once()
            self._stop.wait(0.005)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(5)


# ── 隧道盘 ────────────────────────────────────────────────────────────────────

def test_the_one_mapped_drive_with_in_out_ctl_is_the_tunnel(tmp_path):
    root = _tunnel(tmp_path, "Z")
    (tmp_path / "C").mkdir()                       # 别的盘：没有 in / out / ctl
    (tmp_path / "AB" / "in").mkdir(parents=True)   # 不是单个字母
    assert cloud_side.find_tunnel_root(base=str(tmp_path)) == root


@pytest.mark.parametrize("layout", ["none", "two", "partial", "empty"])
def test_no_or_several_candidates_return_none(tmp_path, layout):
    if layout == "two":
        _tunnel(tmp_path, "Y")
        _tunnel(tmp_path, "Z")
    elif layout == "partial":
        (tmp_path / "Z" / "in").mkdir(parents=True)
        (tmp_path / "Z" / "out").mkdir()
    elif layout == "none":
        (tmp_path / "C").mkdir()
    assert cloud_side.find_tunnel_root(base=str(tmp_path)) is None


def test_listing_the_share_is_injectable_and_errors_are_none():
    def boom(_path):
        raise OSError("no session")

    assert cloud_side.find_tunnel_root(listdir=boom) is None
    seen = []
    dirs = {os.path.join("\\\\tsclient", "Z", sub) for sub in ("in", "out", "ctl")}
    found = cloud_side.find_tunnel_root(listdir=lambda path: seen.append(path) or ["Z", "C", "share"],
                                        is_dir=lambda path: path in dirs)
    # Windows 上 Path("\\\\tsclient\\Z") 是 UNC 根，str 会带结尾的反斜杠
    assert seen == ["\\\\tsclient"] and found is not None and str(found).rstrip("\\/").endswith("Z")


def test_the_host_comes_from_the_bridge_manifest(tmp_path):
    root = _tunnel(tmp_path)
    assert cloud_side.read_host(root) is None
    (root / "cc").mkdir()
    (root / "cc" / "cloud.json").write_text(json.dumps({"version": "1.0.0", "sha256": "0" * 64}), encoding="ascii")
    assert cloud_side.read_host(root) is None
    (root / "cc" / "cloud.json").write_text(json.dumps({"host": " Cloud.Example.Test. "}), encoding="utf-8")
    assert cloud_side.read_host(root) == HOST      # 同 rdp.host：去空白、去末尾点、小写


def test_the_paired_host_is_read_from_the_credential_name_only():
    prefix = pairing.DEVICE_PREFIX
    assert cloud_side.paired_host(entries=lambda p: [(prefix + HOST, "Pearnly CC", HOST)]) == HOST
    assert cloud_side.paired_host(entries=lambda p: []) == ""
    two = [(prefix + HOST, "", ""), (prefix + "other.example.test", "", "")]
    assert cloud_side.paired_host(entries=lambda p: two) == ""


# ── 配对 ──────────────────────────────────────────────────────────────────────

class Vault:
    def __init__(self):
        self.items = {}

    def store(self, host, key):
        self.items[pairing.device_target(host)] = (host, key)

    def read_back(self, host):
        found = self.items.get(pairing.device_target(host))
        return found[1] if found else ""


def _manifest(root, host=HOST):
    (root / "cc").mkdir(exist_ok=True)
    (root / "cc" / "cloud.json").write_text(json.dumps({"host": host}), encoding="ascii")


def test_pair_succeeds_against_a_bridge_and_never_logs_the_token(tmp_path, caplog):
    root = _tunnel(tmp_path)
    _manifest(root)
    bridge = pairing.BridgePairing(HOST, lambda: DEVICE_KEY)
    ticket = bridge.begin()
    vault = Vault()
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            bridge.pump(root / "ctl")
            stop.wait(0.005)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    caplog.set_level(logging.DEBUG)
    try:
        code = cloud_side.run_pair(ticket.token, find=lambda: root,
                                   pair=partial(pairing.pair, poll=0.005, store=vault.store, read_back=vault.read_back),
                                   timeout=5)
        deadline = time.monotonic() + 5
        while bridge.state != pairing.PAIRED and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
        thread.join(5)
    assert code == cloud_side.EXIT_OK
    assert bridge.state == pairing.PAIRED
    assert vault.items[pairing.device_target(HOST)] == (HOST, DEVICE_KEY)
    text = caplog.text
    plain = ticket.token.replace("-", "")
    assert ticket.token not in text and plain not in text and DEVICE_KEY not in text
    assert not list((root / "ctl").glob(protocol.SESSION_FILE))   # 配对不碰会话文件


@pytest.mark.parametrize("code,exit_code", [
    (pairing.OK, 0), (pairing.CHECKSUM, 5), (pairing.MALFORMED, 5), (pairing.TIMEOUT, 6),
    (pairing.TAMPERED, 7), (pairing.STORE_FAILED, 7), (pairing.EXPIRED, 7),
])
def test_pair_exit_codes(tmp_path, code, exit_code, caplog):
    root = _tunnel(tmp_path)
    _manifest(root)
    seen = []
    caplog.set_level(logging.DEBUG)
    got = cloud_side.run_pair("SECRET-TOKEN-TEXT", find=lambda: root,
                              pair=lambda ctl, token, host, timeout: seen.append((ctl, host)) or code)
    assert got == exit_code
    assert seen == [(root / "ctl", HOST)]
    assert "SECRET-TOKEN-TEXT" not in caplog.text and code in caplog.text


def test_pair_without_a_tunnel_or_host_fails_without_calling_pair(tmp_path):
    def never(*_a, **_k):
        pytest.fail("pair called")

    assert cloud_side.run_pair("T", find=lambda: None, pair=never) == cloud_side.EXIT_NO_TUNNEL
    root = _tunnel(tmp_path)
    assert cloud_side.run_pair("T", find=lambda: root, pair=never) == cloud_side.EXIT_NO_HOST


def test_pair_errors_are_a_nonzero_exit_without_the_token(tmp_path, caplog):
    root = _tunnel(tmp_path)
    _manifest(root)

    def boom(*_a, **_k):
        raise OSError("SECRET-TOKEN-TEXT")

    caplog.set_level(logging.DEBUG)
    assert cloud_side.run_pair("SECRET-TOKEN-TEXT", find=lambda: root, pair=boom) == cloud_side.EXIT_REFUSED
    assert "SECRET-TOKEN-TEXT" not in caplog.text


def test_main_pair_flag_exits_with_the_pairing_code_and_skips_the_instance_lock(monkeypatch):
    monkeypatch.delenv(runtime_mode.CLOUD_ENV, raising=False)
    monkeypatch.setattr(main, "setup_logging", lambda *a: None)
    seen = []
    monkeypatch.setattr(cloud_side, "run_pair", lambda token: seen.append(token) or 6)
    from src.companion import single_instance

    monkeypatch.setattr(single_instance, "acquire", lambda *a: pytest.fail("pairing must not take the lock"))
    monkeypatch.setattr("sys.argv", ["companion", "--cloud", "--pair", "ABCD-EFGH"])
    try:
        assert main.main() == 6
    finally:
        os.environ.pop(runtime_mode.CLOUD_ENV, None)
    assert seen == ["ABCD-EFGH"]
    monkeypatch.setattr("sys.argv", ["companion", "--pair", "ABCD-EFGH"])   # 本地模式不认
    assert main.main() == 2 and seen == ["ABCD-EFGH"]


# ── 会话 ──────────────────────────────────────────────────────────────────────

def test_the_session_file_is_deleted_after_reading(tmp_path):
    root = _tunnel(tmp_path)
    session = protocol.new_session()
    _write_session(root, session)
    assert cloud_side.take_session(root) == session
    assert not (root / "ctl" / protocol.SESSION_FILE).exists()
    assert cloud_side.take_session(root) is None
    (root / "ctl" / protocol.SESSION_FILE).write_bytes(b"{not json")
    assert cloud_side.take_session(root) is None
    assert not (root / "ctl" / protocol.SESSION_FILE).exists()


def test_without_a_session_it_waits_with_backoff_up_to_fifteen_minutes(tmp_path):
    root = _tunnel(tmp_path)
    slept, rounds = [], {"n": 0}

    def find():
        rounds["n"] += 1
        if rounds["n"] == 14:
            _write_session(root, protocol.new_session())
        return root

    link = cloud_side.connect(lambda: False, find=find, sleep=slept.append)
    assert link is not None and link.root == root
    # 13 轮等待：1, 2, 4 … 512 秒，之后封顶 900 秒（15 分钟）；每轮按 1 秒一片睡，好随时停
    assert sum(slept) == sum(min(900, 2 ** k) for k in range(13)) == 1023 + 3 * 900
    assert max(slept) <= 1.0


def test_waiting_stops_when_asked(tmp_path):
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 3

    assert cloud_side.connect(stop, find=lambda: None, sleep=lambda _s: None) is None


# ── transport ────────────────────────────────────────────────────────────────

def _client():
    return net.client(base_url="https://ignored.example.test", timeout=3.0,
                      headers={"Authorization": "Bearer cloud_has_no_token"})


def test_net_clients_go_through_the_tunnel_once_installed(tmp_path):
    root = _tunnel(tmp_path)
    site = Site()
    with Bridge(root, site):
        link = cloud_side.connect(lambda: False, find=lambda: root, sleep=lambda _s: None)
        running = cloud_side.install(link, watch=False, **FAST)
        try:
            with _client() as client:
                reply = client.post(HEARTBEAT, json={"x": 1})
            assert reply.status_code == 200 and reply.json() == {"message": {"n": 1}}
            assert site.seen[0].url == SITE + HEARTBEAT
            assert site.seen[0].headers.get("authorization") == "Bearer " + TOKEN
            assert cloud_side.current() is link
        finally:
            running.stop()
    assert cloud_side.current() is None
    with _client() as client:   # 卸下后回到普通 transport
        assert not isinstance(client._transport, cloud_side.ReloadingTransport)


def test_a_new_session_after_the_bridge_reconnects_keeps_working_without_restart(tmp_path):
    root = _tunnel(tmp_path)
    site = Site()
    with Bridge(root, site) as bridge:
        link = cloud_side.connect(lambda: False, find=lambda: root, sleep=lambda _s: None)
        running = cloud_side.install(link, watch=False, **FAST)
        try:
            with _client() as client:
                assert client.post(HEARTBEAT, json={}).status_code == 200
                new = bridge.rotate()   # 桥重连：旧会话的请求被拒（核不过签名），云端换上新会话后重发
                reply = client.post(HEARTBEAT, json={})
            assert reply.status_code == 200 and link.session == new
            assert not (root / "ctl" / protocol.SESSION_FILE).exists()
            assert len(site.seen) == 2
        finally:
            running.stop()


def test_a_read_timeout_takes_the_new_session_but_is_not_sent_twice(tmp_path):
    root = _tunnel(tmp_path)
    old = protocol.new_session()
    link = cloud_side.CloudLink(root, old)
    transport = cloud_side.ReloadingTransport(link, **FAST)
    calls = []

    def claim_and_rotate(request):
        calls.append(request)
        _write_session(root, protocol.new_session())
        raise httpx.ReadTimeout("tunnel: no reply", request=request)

    transport.inner.handle_request = claim_and_rotate
    with httpx.Client(transport=transport, base_url="https://x.example.test") as client:
        with pytest.raises(httpx.ReadTimeout):
            client.post(HEARTBEAT, json={})
    assert len(calls) == 1 and link.session != old and transport.inner.session == link.session


def test_no_bridge_and_no_new_session_is_still_a_connect_error(tmp_path):
    root = _tunnel(tmp_path)
    link = cloud_side.CloudLink(root, protocol.new_session())
    cloud_side.install(link, watch=False, poll_first=0.005, poll_most=0.01, grace=0.05)
    with _client() as client:
        with pytest.raises(httpx.ConnectError):
            client.post(HEARTBEAT, json={}, timeout=0.05)
    assert not list((root / "in").iterdir())


# ── ctl 变化：新会话与撤销 ───────────────────────────────────────────────────

def test_ctl_changes_take_a_new_session_and_answer_a_forget(tmp_path):
    root = _tunnel(tmp_path)
    link = cloud_side.CloudLink(root, protocol.new_session())
    assert cloud_side.check_ctl(link, host=lambda: HOST, handle_forget=lambda *a, **k: pytest.fail("no forget")) is None
    fresh = protocol.new_session()
    _write_session(root, fresh)
    (root / "ctl" / handoff.FORGET_FILE).write_bytes(b"{}")
    seen = []

    def forget(ctl, *, epoch, seen_ids, host):
        seen.append((ctl, epoch, host, seen_ids is link.forget_seen))
        return "ok"

    assert cloud_side.check_ctl(link, host=lambda: HOST, handle_forget=forget) == "ok"
    assert link.session == fresh and seen == [(root / "ctl", fresh.epoch, HOST, True)]


def test_the_ctl_watch_runs_a_check_on_each_change_and_stops(tmp_path):
    root = _tunnel(tmp_path)
    link = cloud_side.CloudLink(root, protocol.new_session())
    watcher = dir_watch.ManualWatcher()
    checks = []
    watch = cloud_side.CtlWatch(link, watch_factory=lambda path: watcher, fallback=30, check=checks.append)
    watch.start()
    watcher.notify()
    deadline = time.monotonic() + 3
    while not checks and time.monotonic() < deadline:
        time.sleep(0.01)
    watch.stop()
    assert checks == [link]


# ── 代录凭据 ──────────────────────────────────────────────────────────────────

def test_a_stored_login_is_used_without_asking_the_bridge(tmp_path):
    asked = []
    code = cloud_side.ensure_express_login(link=cloud_side.CloudLink(tmp_path, protocol.new_session()),
                                           exists=lambda target: target == credentials.target("default"),
                                           fetch=lambda *a, **k: asked.append(1), host=lambda: HOST)
    assert code == "ok" and asked == []


def test_a_missing_login_is_fetched_from_the_bridge_over_ctl(tmp_path):
    root = _tunnel(tmp_path)
    session = protocol.new_session()
    seen = []

    def fetch(ctl, *, epoch, connection, host):
        seen.append((ctl, epoch, connection, host))
        return "ok"

    code = cloud_side.ensure_express_login(link=cloud_side.CloudLink(root, session), exists=lambda _t: False,
                                           fetch=fetch, host=lambda: HOST)
    assert code == "ok" and seen == [(root / "ctl", session.epoch, "default", HOST)]


def test_no_tunnel_or_no_pairing_does_not_ask(tmp_path):
    never = lambda *a, **k: pytest.fail("asked the bridge")  # noqa: E731
    assert cloud_side.ensure_express_login(exists=lambda _t: False, fetch=never) == cloud_side.NO_TUNNEL
    link = cloud_side.CloudLink(tmp_path, protocol.new_session())
    assert cloud_side.ensure_express_login(link=link, exists=lambda _t: False, fetch=never,
                                           host=lambda: "") == cloud_side.UNPAIRED


def test_a_failed_fetch_is_a_code_not_an_exception(tmp_path):
    def boom(*_a, **_k):
        raise OSError("tunnel drive gone")

    link = cloud_side.CloudLink(tmp_path, protocol.new_session())
    assert cloud_side.ensure_express_login(link=link, exists=lambda _t: False, fetch=boom, host=lambda: HOST) == "error"


def test_the_operator_session_asks_first_only_when_given_the_cloud_hook():
    from src.companion.express_operator.engine import NeedHuman
    from src.companion.express_operator.session import Session

    recipe = SimpleNamespace(login={"verified": True, "title_contains": "LogIn", "user_label": "u",
                                    "password_label": "p"}, data={})
    asked = []
    s = Session(object(), recipe, express_exe="", account_dir="x", company_name="c",
                credential_target=credentials.target("default"), ensure_login=lambda: asked.append(1))
    with pytest.raises(NeedHuman):
        s._start_express()          # 不是 Windows：凭据管理器里没有 → 照旧交人，但先问过一次
    with pytest.raises(NeedHuman):
        s._login(recipe.login)
    assert asked == [1, 1]
    local = Session(object(), recipe, express_exe="", account_dir="x", company_name="c",
                    credential_target=credentials.target("default"))
    assert local.ensure_login is None


def test_local_mode_installs_nothing_and_the_operator_hook_is_none(monkeypatch):
    from src.companion.express_operator import host as operator_host

    monkeypatch.delenv(runtime_mode.CLOUD_ENV, raising=False)
    assert operator_host.cloud_login() is None
    with net.client() as client:
        assert not isinstance(client._transport, cloud_side.ReloadingTransport)
    monkeypatch.setenv(runtime_mode.CLOUD_ENV, "1")
    assert operator_host.cloud_login() is cloud_side.ensure_express_login


# ── 安装脚本（Mac 上没有 PowerShell，只核对文本里的边界） ────────────────────

def test_the_install_script_keeps_the_token_out_of_files_and_the_shortcut():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "packaging" / "cloud_install.ps1").read_text(encoding="utf-8-sig")
    code = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    body = "\n".join(code)
    assert "$link.Arguments = '--cloud'" in body
    assert "Set-ExecutionPolicy" not in body and "Register-ScheduledTask" not in body and "HKLM:" not in body
    assert "RunAs" not in body
    for line in code:
        if "Token" in line:   # 令牌只出现在参数、校验、交给配对子进程与清空这几处
            assert not any(word in line for word in ("Out-File", "Set-Content", "Add-Content", "Write-Host",
                                                     "Arguments", "Start-Transcript")), line
    assert "-ArgumentList @('--cloud', '--pair', $cleanToken)" in body
    assert "Get-FileHash" in body and "$manifest.sha256" in body


def _install_script_lines():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "packaging" / "cloud_install.ps1").read_text(encoding="utf-8-sig")
    return [line for line in script.splitlines() if not line.lstrip().startswith("#")]


def _first(lines, needle):
    return next(i for i, line in enumerate(lines) if needle in line)


def test_a_checksum_mismatch_exits_before_unpacking_or_starting_anything():
    lines = _install_script_lines()
    mismatch = _first(lines, "Stop-With 'checksum' 11")
    assert _first(lines, "if ($actual -ne $expected)") < mismatch
    for later in ("ExtractToDirectory", "Expand-Archive", "Start-Process", "CreateShortcut"):
        found = [i for i, line in enumerate(lines) if later in line]
        assert all(i > mismatch for i in found), later
    assert any("ExtractToDirectory" in line for line in lines) and any("Start-Process" in line for line in lines)


def test_the_startup_shortcut_is_created_only_after_pairing_succeeded():
    lines = _install_script_lines()
    failed = _first(lines, "if ($code -ne 0) {")
    pairing = _first(lines, "'--pair'")
    saves = [i for i, line in enumerate(lines) if "$link.Save()" in line]
    assert saves and all(i > failed > pairing for i in saves)
    failure_exit = next(i for i in range(failed, len(lines)) if "Stop-With $line $code" in lines[i])
    assert all(i > failure_exit for i in saves)


def test_a_failed_pairing_removes_the_shortcut_this_install_made():
    lines = _install_script_lines()
    failed = _first(lines, "if ($code -ne 0) {")
    failure_exit = next(i for i in range(failed, len(lines)) if "Stop-With $line $code" in lines[i])
    branch = "\n".join(lines[failed:failure_exit])
    assert "Remove-Item -LiteralPath $linkPath" in branch
    assert "StartsWith($base" in branch   # 只删指向本安装目录的那个
    timeout = _first(lines, "$code = 6")
    assert timeout < failed               # 配对超时也走同一个失败分支


def test_every_result_line_of_the_install_script_has_four_languages():
    """云端主机上打命令的人看到的结果行按云端主机界面语言出（泰 / 中 / 日，其余英文）：每个结果都有四语。"""
    import re

    lines = _install_script_lines()
    body = "\n".join(lines)
    table = body[body.index("$messages = @{"):body.index("$lang = ")]
    entries = dict(re.findall(r"^\s*(\w+)\s*=\s*@\{(.*)\}\s*$", table, re.M))
    for key, row in entries.items():
        assert sorted(re.findall(r"\b(zh|th|en|ja) = '[^']+'", row)) == ["en", "ja", "th", "zh"], key
    used = set(re.findall(r"Stop-With '(\w+)'", body))
    exits = set(re.findall(r"^\s*\d+ = '(\w+)'", body, re.M)) | set(re.findall(r"else \{ '(\w+)' \}", body))
    assert used and exits and (used | exits) <= set(entries)
    assert "(Get-UICulture).TwoLetterISOLanguageName" in body
