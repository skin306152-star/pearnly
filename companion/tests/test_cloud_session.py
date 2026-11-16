"""云端 Express 的 .rdp 文本与凭据目标（``companion.cloud_link.rdp`` / ``credentials``）。

守住：.rdp 逐行照 11-15 实测写法、UTF-16LE 带 BOM；参数不合规就拒（不让换行混进 .rdp）；读 ``Session.rdp`` 只取主机、端口、域，
密码一类的行不进结果；凭据目标名与用户名写法；非 Windows 不报错地回「没有」。"""
from __future__ import annotations

import sys

import pytest

from companion.cloud_link import credentials, rdp

EXPECTED = [
    "full address:s:198.51.100.7",
    "server port:i:3390",
    "username:s:CLOUDUSER-01",
    "domain:s:example",
    "drivestoredirect:s:X:\\;",
    "redirectclipboard:i:0",
    "redirectprinters:i:0",
    "redirectcomports:i:0",
    "redirectsmartcards:i:0",
    "redirectwebauthn:i:0",
    "devicestoredirect:s:",
    "camerastoredirect:s:",
    "audiomode:i:2",
    "audiocapturemode:i:0",
    "keyboardhook:i:1",
    "screen mode id:i:1",
    "desktopwidth:i:1280",
    "desktopheight:i:800",
    "session bpp:i:16",
    "smart sizing:i:0",
    "autoreconnection enabled:i:1",
    "prompt for credentials:i:0",
    "promptcredentialonce:i:1",
    "authentication level:i:0",
    "enablecredsspsupport:i:1",
    "displayconnectionbar:i:1",
]


def _build(**kw):
    args = dict(host_name="198.51.100.7", port_number=3390, user="CLOUDUSER-01", domain="example",
                tunnel_drive="X")
    args.update(kw)
    return args


def test_lines_match_probe_layout():
    assert rdp.lines(**_build()) == EXPECTED


def test_build_is_utf16le_with_bom_and_crlf():
    data = rdp.build(**_build())
    assert data[:2] == b"\xff\xfe"
    text = data[2:].decode("utf-16-le")
    assert text.endswith("\r\n")
    assert text.split("\r\n")[:-1] == EXPECTED


def test_no_password_or_shell_lines():
    text = "\n".join(rdp.lines(**_build())).lower()
    for word in ("password", "alternate shell", "shell working directory"):
        assert word not in text


def test_size_and_drive_forms():
    rows = rdp.lines(**_build(tunnel_drive="w:\\", width=1600, height=900))
    assert "drivestoredirect:s:W:\\;" in rows
    assert "desktopwidth:i:1600" in rows and "desktopheight:i:900" in rows


def test_empty_domain_allowed():
    assert "domain:s:" in rdp.lines(**_build(domain=""))


@pytest.mark.parametrize("bad", [
    dict(host_name="evil\r\nalternate shell:s:cmd"), dict(host_name=""), dict(host_name="a b"),
    dict(port_number=0), dict(port_number=70000), dict(port_number="x"),
    dict(user=""), dict(user="a\nb"), dict(user="dom\\user"), dict(domain="x;y"),
    dict(tunnel_drive="C"), dict(tunnel_drive="XY"), dict(tunnel_drive=""),
    dict(width=100), dict(height=99999),
])
def test_rejects_bad_values_without_echoing_them(bad):
    with pytest.raises(rdp.RdpValueError) as err:
        rdp.lines(**_build(**bad))
    for value in bad.values():
        if isinstance(value, str) and len(value) > 2:
            assert value not in str(err.value)


@pytest.mark.parametrize("account,default,expected", [
    ("CLOUDUSER-01", "example", ("CLOUDUSER-01", "example")),
    ("corp\\alice", "example", ("alice", "corp")),
    ("alice@corp", "", ("alice", "corp")),
    ("alice", "", ("alice", "")),
])
def test_split_account(account, default, expected):
    assert rdp.split_account(account, default) == expected


SESSION_RDP = "\r\n".join([
    "screen mode id:i:2",
    "full address:s:198.51.100.7",
    "server port:i:3390",
    "username:s:CLOUDUSER-01",
    "domain:s:example",
    "password 51:b:01000000D08C9DDF0115D1118C7A00C04FC297EB",
    "credential:s:SECRETVALUE",
    "authentication level:i:0",
]) + "\r\n"


def test_parse_session_rdp_takes_only_three_fields():
    target = rdp.parse_session_rdp(b"\xff\xfe" + SESSION_RDP.encode("utf-16-le"))
    assert target == rdp.CloudTarget(host="198.51.100.7", port=3390, domain="example")
    assert set(vars(target)) == {"host", "port", "domain"}
    assert "SECRET" not in repr(target) and "D08C9DDF" not in repr(target)


def test_parse_session_rdp_encodings_and_embedded_port():
    utf8 = "full address:s:Cloud.Example.com:5000\ndomain:s:corp\n".encode()
    assert rdp.parse_session_rdp(utf8) == rdp.CloudTarget("cloud.example.com", 5000, "corp")
    assert rdp.parse_session_rdp(b"\xef\xbb\xbf" + utf8).port == 5000
    no_port = "full address:s:host1\n".encode("utf-16-le")
    assert rdp.parse_session_rdp(no_port) == rdp.CloudTarget("host1", rdp.DEFAULT_PORT, "")


def test_parse_session_rdp_server_port_wins_and_first_value_kept():
    data = "full address:s:h1:5000\nserver port:i:3390\nfull address:s:h2\n".encode()
    assert rdp.parse_session_rdp(data) == rdp.CloudTarget("h1", 3390, "")


def test_parse_session_rdp_missing_or_bad_host():
    with pytest.raises(rdp.RdpValueError):
        rdp.parse_session_rdp(b"domain:s:corp\n")
    with pytest.raises(rdp.RdpValueError):
        rdp.parse_session_rdp(b"full address:i:host\n")


def test_find_and_read_session_rdp(tmp_path):
    assert rdp.find_session_rdp(tmp_path) is None
    folder = tmp_path / "RDP6"
    folder.mkdir()
    path = folder / "Session.rdp"
    path.write_bytes(b"\xff\xfe" + SESSION_RDP.encode("utf-16-le"))
    assert rdp.find_session_rdp(tmp_path) == path
    assert rdp.read_session_rdp(path).host == "198.51.100.7"


def test_read_session_rdp_refuses_large_file(tmp_path):
    path = tmp_path / "Session.rdp"
    path.write_bytes(b"x" * (rdp.MAX_SESSION_RDP + 1))
    with pytest.raises(rdp.RdpValueError):
        rdp.read_session_rdp(path)


def test_credential_target_and_user():
    assert credentials.target("Cloud.Example.com.") == "TERMSRV/cloud.example.com"
    assert credentials.credential_user("CLOUDUSER-01", "example") == "example\\CLOUDUSER-01"
    assert credentials.credential_user("alice", "") == "alice"
    with pytest.raises(rdp.RdpValueError):
        credentials.target("bad host")
    with pytest.raises(rdp.RdpValueError):
        credentials.credential_user("a\nb", "corp")


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_non_windows_no_credentials():
    with pytest.raises(credentials.NotWindows):
        credentials.store("host1", "alice", "corp", "pw")
    assert credentials.user_name("host1") == "" and not credentials.exists("host1")
    assert credentials.forget("host1") is False


def test_store_error_never_contains_password(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ValueError) as err:
        credentials.store("host1", "alice", "corp", "")
    assert str(err.value) == "password"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows credential manager")
def test_windows_store_read_forget_roundtrip():
    host = "pearnly-cc-test.invalid"
    credentials.forget(host)
    try:
        credentials.store(host, "alice", "corp", "not-a-real-password")
        assert credentials.exists(host)
        assert credentials.user_name(host) == "corp\\alice"
    finally:
        assert credentials.forget(host)
    assert not credentials.exists(host)
    assert credentials.forget(host) is False
