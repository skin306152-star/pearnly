"""文件隧道（``companion.cloud_link``，评估 docs/work/2026-11-11-express-cloud-assessment.md 第 4.3 节）：本机假桥——同一进程里
云端一侧（``TunnelTransport``）与桥一侧（``TunnelRelay`` + 假代发函数）共用一个临时隧道文件夹，不连任何站点。

守住：请求原样到站、站点回复原样回（包括 5xx / 真 401）；桥不在、桥不回、隧道拒绝一律是 httpx 的网络错误，从不伪造 4xx；
超大请求不写文件；白名单外路径、重放、坏签名被拒且不代发；云端带的 Authorization 换成桥的令牌；最多 8 个在路上；旧文件清理；
日志里没有正文和头。"""
from __future__ import annotations

import json
import os
import threading
import time

import httpx
import pytest

from companion import channel
from companion.cloud_link import tunnel_protocol as protocol
from companion.cloud_link.tunnel_relay import Incoming, SiteTimeout, SiteUnreachable, TunnelRelay
from companion.cloud_link.tunnel_transport import TunnelTooLarge, TunnelTransport

SITE = "https://erp.test"
TOKEN = "pcc_real_token_on_the_bridge"  # scan: fake
HEARTBEAT = "/api/method/pearnly_core.cc_desktop.machine.heartbeat"
LEASE = "/api/method/pearnly_cowork.companion.queue_api.lease"
COMPLETE = "/api/method/pearnly_core.cc_desktop.machine.complete_request"


class FakeSite:
    """假代发函数：记下桥交来的请求，按 ``answer`` 回。"""

    def __init__(self, answer=None, delay: float = 0.0):
        self.seen = []
        self.answer = answer or (lambda out: Incoming(200, {"content-type": "application/json"},
                                                      json.dumps({"message": {"ok": True}}).encode()))
        self.delay = delay
        self._lock = threading.Lock()

    def __call__(self, out):
        with self._lock:
            self.seen.append(out)
        if self.delay:
            time.sleep(self.delay)
        return self.answer(out)


class Bridge:
    """后台线程一直轮询（真桥将来是事件驱动，处理逻辑同一个 ``process``）。"""

    def __init__(self, relay: TunnelRelay, interval: float = 0.005):
        self.relay, self.interval = relay, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.relay.poll_once()
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(5)


@pytest.fixture
def tunnel(tmp_path):
    session = protocol.new_session()
    site = FakeSite()
    relay = TunnelRelay(tmp_path, session, SITE, TOKEN, site)
    relay.ensure_dirs()
    transport = TunnelTransport(tmp_path, session, poll_first=0.005, poll_most=0.02, grace=0.2)
    client = httpx.Client(base_url="https://cloud-side-host-is-ignored.example", transport=transport, timeout=5.0,
                          headers={"Authorization": "Bearer cloud_has_no_token"})
    yield session, site, relay, transport, client, tmp_path
    client.close()


def _files(folder):
    return sorted(p.name for p in folder.iterdir())


# ── 成功 ──

def test_post_json_reaches_the_paired_site_and_the_reply_comes_back(tunnel):
    session, site, relay, transport, client, root = tunnel
    with Bridge(relay):
        response = client.post(HEARTBEAT, json={"agent_id": "c1", "sets": ["ก", "A"]})
    assert response.status_code == 200
    assert response.json() == {"message": {"ok": True}}
    out = site.seen[-1]
    assert out.method == "POST"
    assert out.url == SITE + HEARTBEAT               # 站点固定：客户端的主机名不用
    assert json.loads(out.body) == {"agent_id": "c1", "sets": ["ก", "A"]}
    assert out.headers["content-type"] == "application/json"
    assert out.headers["authorization"] == f"Bearer {TOKEN}"   # 云端带的那个丢掉、换成桥的
    assert set(out.headers) <= {"accept", "content-type", "authorization"}
    assert _files(root / "in") == [] and _files(root / "out") == []   # 读完都删了


def test_get_with_query_keeps_the_query_string(tunnel):
    session, site, relay, transport, client, root = tunnel
    with Bridge(relay):
        response = client.get(LEASE, params={"max": "1", "q": "ก ข&c"})
    assert response.status_code == 200
    out = site.seen[-1]
    assert out.method == "GET" and out.body == b""
    assert httpx.URL(out.url).params == httpx.QueryParams({"max": "1", "q": "ก ข&c"})
    assert httpx.URL(out.url).host == "erp.test"


@pytest.mark.parametrize("size", [protocol.MAX_BODY // 4, 4 * 1024 * 1024 * 4 // 3 + 1024])
def test_chunk_sized_bodies_go_both_ways_intact(tunnel, size):
    """按需请求按 4 MB 分块交回（base64 后约 5.4 MB）；回复同样大小。"""
    session, site, relay, transport, client, root = tunnel
    site.answer = lambda out: Incoming(200, {"content-type": "application/octet-stream"}, out.body[::-1])
    body = os.urandom(size)
    with Bridge(relay):
        response = client.post(COMPLETE, content=body, headers={"content-type": "application/octet-stream"})
    assert response.status_code == 200
    assert response.content == body[::-1]
    assert site.seen[-1].body == body


def test_the_size_limit_is_exact():
    session = protocol.new_session()
    protocol.encode_request(session, seq=1, request_id=protocol.new_id(), method="POST", path=HEARTBEAT,
                            body=b"x" * protocol.MAX_BODY)
    with pytest.raises(protocol.TunnelError) as caught:
        protocol.encode_request(session, seq=2, request_id=protocol.new_id(), method="POST", path=HEARTBEAT,
                                body=b"x" * (protocol.MAX_BODY + 1))
    assert caught.value.code == protocol.TOO_LARGE
    with pytest.raises(protocol.TunnelError) as caught:
        protocol.encode_reply(session, protocol.new_id(), 200, {}, b"x" * (protocol.MAX_BODY + 1))
    assert caught.value.code == protocol.RESPONSE_TOO_LARGE


# ── 站点的回复原样回 ──

@pytest.mark.parametrize("status", [500, 502, 503, 401, 403, 404])
def test_site_status_codes_come_back_as_they_are(tunnel, status):
    """站点自己回的 5xx 与真的 401 / 403 / 404 原样回（隧道自己出的问题才变网络错误）。"""
    session, site, relay, transport, client, root = tunnel
    site.answer = lambda out: Incoming(status, {"content-type": "text/html", "set-cookie": "sid=1"}, b"oops")
    with Bridge(relay):
        response = client.post(HEARTBEAT, json={})
    assert response.status_code == status and response.content == b"oops"
    assert "set-cookie" not in response.headers
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()


@pytest.mark.parametrize("error, expected", [(SiteUnreachable("down"), httpx.ConnectError),
                                             (httpx.ConnectError("down"), httpx.ConnectError),
                                             (SiteTimeout("slow"), httpx.ReadTimeout),
                                             (httpx.ReadTimeout("slow"), httpx.ReadTimeout)])
def test_site_unreachable_or_slow_is_a_network_error_not_a_status(tunnel, error, expected):
    session, site, relay, transport, client, root = tunnel

    def answer(out):
        raise error

    site.answer = answer
    with Bridge(relay), pytest.raises(expected):
        client.post(HEARTBEAT, json={})


# ── 桥不在 / 不回 ──

def test_no_bridge_times_out_as_a_connect_error_and_withdraws_the_request(tunnel):
    session, site, relay, transport, client, root = tunnel
    started = time.monotonic()
    with pytest.raises(httpx.ConnectError):
        client.post(HEARTBEAT, json={}, timeout=0.3)
    assert 0.4 <= time.monotonic() - started < 3   # 原超时 + 宽限（测试里 0.2 秒）
    assert _files(root / "in") == []               # 撤掉了：桥过后回来也不会代发
    assert site.seen == []


def test_bridge_took_it_but_never_answered_is_a_read_timeout(tunnel):
    session, site, relay, transport, client, root = tunnel
    stop = threading.Event()

    def claim_and_sit():
        while not stop.is_set():
            for path in relay.pending():
                os.replace(path, path.with_suffix(".work"))
            stop.wait(0.005)

    thread = threading.Thread(target=claim_and_sit, daemon=True)
    thread.start()
    try:
        with pytest.raises(httpx.ReadTimeout):
            client.post(HEARTBEAT, json={}, timeout=0.3)
    finally:
        stop.set()
        thread.join(5)


def test_oversized_request_fails_on_the_cloud_side_without_writing(tunnel):
    session, site, relay, transport, client, root = tunnel
    with pytest.raises(TunnelTooLarge) as caught:
        client.post(COMPLETE, content=b"x" * (protocol.MAX_BODY + 1))
    assert isinstance(caught.value, httpx.TransportError)
    assert _files(root / "in") == []


# ── 拒绝：变成网络错误，不代发 ──

@pytest.mark.parametrize("path", ["/api/method/frappe.client.get_list", "/api/method/pearnly_cowork.companion.api.register",
                                  "/api/method/pearnly_cowork.folder_intake.machine.sync", "/app/user",
                                  HEARTBEAT + "/../../frappe.auth.get_logged_user", HEARTBEAT.upper()])
def test_paths_outside_the_list_are_refused(tunnel, path):
    session, site, relay, transport, client, root = tunnel
    with Bridge(relay), pytest.raises(httpx.ConnectError, match="path_not_allowed"):
        client.post(path, json={})
    assert site.seen == []


def test_methods_outside_get_post_are_refused(tunnel):
    session, site, relay, transport, client, root = tunnel
    with Bridge(relay), pytest.raises(httpx.ConnectError, match="method_not_allowed"):
        client.delete(HEARTBEAT)
    assert site.seen == []


def test_the_allowed_paths_are_exactly_what_cc_calls():
    """白名单 = ``channel.PATHS`` 里两种通道的全部机器方法（加一条方法要两边一起改，这条测试提醒）；都在两个前缀下。"""
    called = {path for paths in channel.PATHS.values() for path in paths.values()}
    assert protocol.ALLOWED_PATHS == called
    assert all(path.startswith(protocol.PREFIXES) for path in protocol.ALLOWED_PATHS)


def _drop(root, data: bytes, seq: int, request_id: str):
    path = root / "in" / protocol.request_name(seq, request_id)
    path.write_bytes(data)
    return path


def _reply(root, session, request_id):
    path = root / "out" / protocol.reply_name(request_id)
    return protocol.decode_reply(path.read_bytes(), session, request_id) if path.exists() else None


def test_replays_are_refused(tmp_path):
    session = protocol.new_session()
    site = FakeSite()
    relay = TunnelRelay(tmp_path, session, SITE, TOKEN, site)
    relay.ensure_dirs()
    first = protocol.new_id()
    data = protocol.encode_request(session, seq=300, request_id=first, method="POST", path=HEARTBEAT, body=b"{}")
    _drop(tmp_path, data, 300, first)
    assert relay.poll_once() == 1 and len(site.seen) == 1
    assert _reply(tmp_path, session, first).status == 200
    (tmp_path / "out" / protocol.reply_name(first)).unlink()

    # 同一个文件再放一次（同 id、同 seq）
    _drop(tmp_path, data, 300, first)
    # 同 id、新 seq（签名是对的）
    again = protocol.encode_request(session, seq=301, request_id=first, method="POST", path=HEARTBEAT, body=b"{}")
    _drop(tmp_path, again, 301, first)
    # 新 id、窗口外的旧 seq
    old = protocol.new_id()
    _drop(tmp_path, protocol.encode_request(session, seq=300 - protocol.SEQ_WINDOW, request_id=old, method="POST",
                                            path=HEARTBEAT, body=b"{}"), 300 - protocol.SEQ_WINDOW, old)
    relay.poll_once()
    assert len(site.seen) == 1
    assert _reply(tmp_path, session, first) is None      # 重放不写回复，盖不掉真回复
    assert _reply(tmp_path, session, old) is None

    # 窗口内乱序到达的照收
    late = protocol.new_id()
    _drop(tmp_path, protocol.encode_request(session, seq=299, request_id=late, method="POST", path=HEARTBEAT,
                                            body=b"{}"), 299, late)
    relay.poll_once()
    assert len(site.seen) == 2

    # 别的会话（旧 epoch 的云端）：拒绝回复按本会话签，旧会话的云端核不过 → 也是网络错误
    other = protocol.new_session()
    stale = protocol.new_id()
    _drop(tmp_path, protocol.encode_request(other, seq=400, request_id=stale, method="POST", path=HEARTBEAT,
                                            body=b"{}"), 400, stale)
    relay.poll_once()
    assert len(site.seen) == 2
    assert _reply(tmp_path, session, stale).refused == protocol.STALE_EPOCH


def test_bad_mac_and_tampered_fields_are_refused(tmp_path):
    session = protocol.new_session()
    site = FakeSite()
    relay = TunnelRelay(tmp_path, session, SITE, TOKEN, site)
    relay.ensure_dirs()
    forged = protocol.Session(epoch=session.epoch, key=os.urandom(32))   # 会话号对、密钥不对
    cases = {}
    rid = protocol.new_id()
    cases[rid] = protocol.encode_request(forged, seq=1, request_id=rid, method="POST", path=HEARTBEAT, body=b"{}")
    # 签名之后改查询串 / 改正文 / 改路径
    for seq, mutate in ((2, lambda h, b: (dict(h, query="max=99"), b)), (3, lambda h, b: (h, b + b"x")),
                        (4, lambda h, b: (dict(h, path=LEASE), b))):
        rid = protocol.new_id()
        good = protocol.encode_request(session, seq=seq, request_id=rid, method="POST", path=HEARTBEAT, body=b"{}")
        line, body = good.split(b"\n", 1)
        head, body = mutate(json.loads(line), body)
        cases[rid] = json.dumps(head).encode() + b"\n" + body
    # 头一行不是 JSON
    rid = protocol.new_id()
    cases[rid] = b"not json\n{}"
    for seq, (rid, data) in enumerate(cases.items(), start=1):
        _drop(tmp_path, data, seq, rid)
    relay.poll_once()
    assert site.seen == []
    codes = [_reply(tmp_path, session, rid).refused for rid in cases]
    assert codes == [protocol.BAD_MAC, protocol.BAD_MAC, protocol.BAD_BODY, protocol.BAD_MAC, protocol.BAD_FORMAT]


def test_authorization_written_into_the_request_file_is_dropped(tmp_path):
    """就算请求文件里带着 Authorization（签名也对），桥照样丢掉、换上自己的令牌；Host、Cookie 同样不传。"""
    session = protocol.new_session()
    site = FakeSite()
    relay = TunnelRelay(tmp_path, session, SITE, lambda: TOKEN, site)
    relay.ensure_dirs()
    rid = protocol.new_id()
    headers = {"authorization": "Bearer stolen", "host": "evil.example", "cookie": "sid=1",
               "content-type": "application/json"}
    body = b"{}"
    head = {"v": 1, "id": rid, "epoch": session.epoch, "seq": 1, "method": "POST", "path": HEARTBEAT, "query": "",
            "headers": headers, "body_sha256": protocol.sha256_hex(body), "timeout": 5.0}
    head["mac"] = protocol._request_mac(session.key, session.epoch, 1, rid, "POST", HEARTBEAT, head["body_sha256"],
                                        "", headers)
    _drop(tmp_path, json.dumps(head).encode() + b"\n" + body, 1, rid)
    relay.poll_once()
    out = site.seen[-1]
    assert out.headers == {"content-type": "application/json", "authorization": f"Bearer {TOKEN}"}
    assert out.url == SITE + HEARTBEAT


def test_forged_reply_is_a_network_error_not_a_401(tunnel):
    """往 out\\ 塞一个没签名的假 401：云端核不过，变成网络错误（伪造的 401 会让 CC 以为密钥被吊销）。"""
    session, site, relay, transport, client, root = tunnel
    stop = threading.Event()
    other = protocol.new_session()

    def forge():
        while not stop.is_set():
            for path in relay.pending():
                rid = protocol.id_of_request_name(path.name)
                path.unlink()
                (root / "out" / protocol.reply_name(rid)).write_bytes(protocol.encode_reply(other, rid, 401, {}, b""))
            stop.wait(0.005)

    thread = threading.Thread(target=forge, daemon=True)
    thread.start()
    try:
        with pytest.raises(httpx.ConnectError, match="bad_reply"):
            client.post(HEARTBEAT, json={})
    finally:
        stop.set()
        thread.join(5)


def test_the_site_must_be_https_host_only(tmp_path):
    session = protocol.new_session()
    for bad in ("http://erp.test", "https://erp.test/api", "https://u:p@erp.test", "erp.test", "https://erp.test?x=1"):
        with pytest.raises(ValueError):
            TunnelRelay(tmp_path, session, bad, TOKEN, FakeSite())
    assert TunnelRelay(tmp_path, session, "https://ERP.test/", TOKEN, FakeSite()).site == "https://erp.test"


# ── 并发 ──

def test_at_most_eight_in_flight_and_replies_match_their_requests(tunnel):
    session, site, relay, transport, client, root = tunnel
    site.answer = lambda out: Incoming(200, {"content-type": "application/json"}, out.body)
    results, errors = {}, []

    def call(n):
        try:
            results[n] = client.post(HEARTBEAT, json={"n": n}, timeout=10.0).json()
        except Exception as error:   # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=call, args=(n,)) for n in range(12)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while len(list((root / "in").glob("*.req"))) < 8 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)
    assert len(list((root / "in").glob("*.req"))) == 8   # 第 9 个起在云端排队，不写进隧道
    with Bridge(relay):
        for thread in threads:
            thread.join(10)
    assert errors == []
    assert results == {n: {"n": n} for n in range(12)}


# ── 清理 ──

def test_cleanup_removes_files_nobody_took_for_ten_minutes(tmp_path):
    session = protocol.new_session()
    now = 1_000_000.0
    relay = TunnelRelay(tmp_path, session, SITE, TOKEN, FakeSite(), clock=lambda: now)
    relay.ensure_dirs()
    old = [tmp_path / "in" / "000000000001-aa.req", tmp_path / "in" / "x.part", tmp_path / "out" / "bb.resp",
           tmp_path / "out" / "cc.part"]
    fresh = [tmp_path / "in" / "000000000002-dd.req", tmp_path / "out" / "ee.resp"]
    other = tmp_path / "out" / "keep.txt"
    for path in old + fresh + [other]:
        path.write_bytes(b"x")
    for path in old + [other]:
        os.utime(path, (now - 601, now - 601))
    for path in fresh:
        os.utime(path, (now - 599, now - 599))
    assert relay.cleanup() == len(old)
    assert all(not path.exists() for path in old)
    assert all(path.exists() for path in fresh) and other.exists()


# ── 日志 ──

def test_logs_carry_method_path_status_sizes_but_no_body_or_headers(tunnel, caplog):
    session, site, relay, transport, client, root = tunnel
    caplog.set_level("INFO", logger="pearnly_companion")
    site.answer = lambda out: Incoming(200, {"content-type": "application/json", "x-secret": "reply-header-secret"},
                                       b'{"message": "reply-body-secret"}')
    with Bridge(relay):
        client.post(HEARTBEAT, json={"password": "request-body-secret"},
                    headers={"Authorization": "Bearer cloud-header-secret"})
        with pytest.raises(httpx.ConnectError):
            client.post("/api/method/frappe.secret_path_in_log", json={"password": "request-body-secret"})
    text = caplog.text
    assert "POST" in text and HEARTBEAT in text and "200" in text
    for secret in ("request-body-secret", "reply-body-secret", "reply-header-secret", "cloud-header-secret", TOKEN,
                   "application/json", "secret_path_in_log"):
        assert secret not in text
