# -*- coding: utf-8 -*-
"""桥把云端安装文件放进隧道盘 cc\\（cloud_link/cloud_package.py）与 ``cloud_pair_begin`` 回 drive / command。全部离线：
发布清单与下载用假客户端，隧道根、缓存都在临时目录。"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import types

import pytest

from companion.cloud_link import cloud_package as pkg

HOST = "198.51.100.7"
ZIP = b"PK\x03\x04" + b"cloud-package" * 100
SHA = hashlib.sha256(ZIP).hexdigest()
TOKEN = "7K3M-Q9TB-ABCD-EFGH-JKMN-PQRS-TVXX"  # scan: fake


def _entry(version="1.20.0", body=ZIP, url="/downloads/companion/releases/1.20.0/" + "a" * 40 + "/CC-Cloud.zip"):
    return {"version": version, "url": url, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}


class _Response:
    def __init__(self, body=b"", data=None, status=200):
        self.body, self.data, self.status = body, data, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError("HTTP %d" % self.status)

    def json(self):
        return self.data

    def iter_bytes(self, chunk_size=0):
        for i in range(0, len(self.body), 100):
            yield self.body[i:i + 100]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Client:
    """假 net.client：记下请求；``latest`` 是 latest.json 的内容，``body`` 是下载回来的字节。"""

    def __init__(self, latest=None, body=ZIP, fail=False):
        self.latest, self.body, self.fail, self.calls = latest, body, fail, []

    def __call__(self, timeout):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if self.fail:
            raise ConnectionError("offline")
        return _Response(data=self.latest)

    def stream(self, method, url):
        self.calls.append(("STREAM", url))
        if self.fail:
            raise ConnectionError("offline")
        return _Response(body=self.body)


def _script(tmp_path):
    path = tmp_path / "cloud_install.ps1"
    path.write_text("param([string]$Token)\n", encoding="utf-8")
    return path


def _prepare(tmp_path, client, **kw):
    return pkg.prepare(tmp_path / "tunnel", kw.pop("host", HOST), site="https://example.com", cache=tmp_path / "cache",
                       client=client, script=_script(tmp_path), **kw)


# ── 清单 ────────────────────────────────────────────────────────────────────

def test_the_manifest_entry_is_validated():
    assert pkg.parse_entry({"cloud": _entry()}) == _entry()
    assert pkg.parse_entry({"version": "1.20.0"}) is None
    for bad in ({"version": "x"}, {"sha256": "0" * 63}, {"size": 0}, {"size": True}, {"url": "ftp://example.com/x"}):
        assert pkg.parse_entry({"cloud": {**_entry(), **bad}}) is None


def test_the_entry_is_read_from_the_same_latest_json_as_updates(tmp_path):
    from companion.updater import LATEST_PATH

    client = _Client(latest={"version": "1.20.0", "cloud": _entry()})
    assert pkg.fetch_entry("https://example.com", client=client) == _entry()
    assert client.calls == [("GET", "https://example.com" + LATEST_PATH)]
    assert pkg.fetch_entry("https://example.com", client=_Client(fail=True)) is None


# ── 缓存与下载 ──────────────────────────────────────────────────────────────

def test_a_cache_miss_downloads_checks_and_keeps_the_package(tmp_path):
    client = _Client(latest={"cloud": _entry()})
    result = _prepare(tmp_path, client)
    assert ("STREAM", "https://example.com" + _entry()["url"]) in client.calls
    cached = tmp_path / "cache" / "1.20.0"
    assert (cached / "CC-Cloud.zip").read_bytes() == ZIP
    assert json.loads((cached / "cloud.json").read_text()) == {"version": "1.20.0", "sha256": SHA, "size": len(ZIP)}
    assert not (cached / "CC-Cloud.zip.part").exists()
    assert result == {"version": "1.20.0", "written": ["cloud_install.ps1", "CC-Cloud.zip", "cloud.json"]}


def test_a_cache_hit_does_not_download(tmp_path):
    _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    client = _Client(latest={"cloud": _entry()})
    pkg.prepare(tmp_path / "tunnel2", HOST, site="https://example.com", cache=tmp_path / "cache", client=client,
                script=_script(tmp_path))
    assert [call for call in client.calls if call[0] == "STREAM"] == []
    assert (tmp_path / "tunnel2" / "cc" / "CC-Cloud.zip").read_bytes() == ZIP


def test_a_damaged_cache_is_downloaded_again(tmp_path):
    _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    (tmp_path / "cache" / "1.20.0" / "CC-Cloud.zip").write_bytes(b"PK broken")
    client = _Client(latest={"cloud": _entry()})
    _prepare(tmp_path, client)
    assert any(call[0] == "STREAM" for call in client.calls)
    assert (tmp_path / "cache" / "1.20.0" / "CC-Cloud.zip").read_bytes() == ZIP


def test_a_wrong_checksum_is_deleted_and_reported(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    client = _Client(latest={"cloud": _entry()}, body=ZIP[:-1] + b"X")
    with pytest.raises(pkg.PackageError) as raised:
        _prepare(tmp_path, client)
    assert raised.value.code == pkg.CHECKSUM
    cached = tmp_path / "cache" / "1.20.0"
    assert not (cached / "CC-Cloud.zip").exists() and not (cached / "CC-Cloud.zip.part").exists()
    assert not (tmp_path / "tunnel" / "cc" / "cloud.json").exists()


def test_a_too_long_download_is_a_checksum_error(tmp_path):
    with pytest.raises(pkg.PackageError) as raised:
        _prepare(tmp_path, _Client(latest={"cloud": _entry()}, body=ZIP + b"more"))
    assert raised.value.code == pkg.CHECKSUM


def test_offline_uses_the_newest_valid_cache_and_without_one_reports_no_package(tmp_path):
    with pytest.raises(pkg.PackageError) as raised:
        _prepare(tmp_path, _Client(fail=True))
    assert raised.value.code == pkg.NO_PACKAGE
    _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    result = pkg.prepare(tmp_path / "t3", HOST, site="https://example.com", cache=tmp_path / "cache",
                         client=_Client(fail=True), script=_script(tmp_path))
    assert result["version"] == "1.20.0"


def test_a_failed_download_is_a_download_code(tmp_path):
    entry = _entry()
    with pytest.raises(pkg.PackageError) as raised:
        pkg.prepare(tmp_path / "tunnel", HOST, site="https://example.com", cache=tmp_path / "cache",
                    client=_Client(fail=True), script=_script(tmp_path), fetch=lambda: entry)
    assert raised.value.code == pkg.DOWNLOAD_FAILED
    assert not (tmp_path / "cache" / "1.20.0" / "CC-Cloud.zip.part").exists()


def test_an_unwritable_tunnel_is_a_write_code(tmp_path):
    (tmp_path / "tunnel").mkdir()
    (tmp_path / "tunnel" / "cc").write_text("a file where the folder should be")
    with pytest.raises(pkg.PackageError) as raised:
        _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    assert raised.value.code == pkg.WRITE_FAILED


# ── 写 cc\ ──────────────────────────────────────────────────────────────────

def test_the_host_is_written_normalised_like_the_pairing_uses_it(tmp_path):
    from companion.cloud_link import cloud_side

    _prepare(tmp_path, _Client(latest={"cloud": _entry()}), host="  Cloud-Host.Example.  ")
    manifest = json.loads((tmp_path / "tunnel" / "cc" / "cloud.json").read_text())
    assert manifest == {"version": "1.20.0", "sha256": SHA, "size": len(ZIP), "host": "cloud-host.example"}
    assert cloud_side.read_host(tmp_path / "tunnel") == "cloud-host.example"


def test_existing_files_with_the_same_sha256_are_skipped(tmp_path, monkeypatch):
    _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    replaced = []
    real = pkg.os.replace
    monkeypatch.setattr(pkg.os, "replace", lambda a, b: (replaced.append(str(b)), real(a, b)))
    result = _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    assert result["written"] == [] and replaced == []


def test_each_file_goes_through_a_part_file_then_a_rename(tmp_path, monkeypatch):
    renames = []
    real = pkg.os.replace
    monkeypatch.setattr(pkg.os, "replace", lambda a, b: (renames.append((str(a), str(b))), real(a, b)))
    _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    cc = tmp_path / "tunnel" / "cc"
    tunnel_renames = [(a, b) for a, b in renames if b.startswith(str(cc))]
    assert tunnel_renames == [(str(cc / name) + ".part", str(cc / name))
                              for name in ("cloud_install.ps1", "CC-Cloud.zip", "cloud.json")]
    assert sorted(p.name for p in cc.iterdir()) == ["CC-Cloud.zip", "cloud.json", "cloud_install.ps1"]


def test_a_new_version_replaces_the_package(tmp_path):
    _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    body = ZIP + b"v2"
    _prepare(tmp_path, _Client(latest={"cloud": _entry("1.21.0", body)}, body=body))
    cc = tmp_path / "tunnel" / "cc"
    assert (cc / "CC-Cloud.zip").read_bytes() == body
    assert json.loads((cc / "cloud.json").read_text())["version"] == "1.21.0"


def test_the_install_script_ships_with_the_package_and_is_found_from_source():
    from pathlib import Path

    assert pkg.script_path() == Path(pkg.__file__).resolve().parents[3] / "packaging" / "cloud_install.ps1"
    spec = (Path(pkg.__file__).resolve().parents[3] / "packaging" / "companion.spec").read_text(encoding="utf-8")
    assert '("cloud_install.ps1", "src/companion/cloud_link")' in spec


# ── 只在需要时 ──────────────────────────────────────────────────────────────

def test_refresh_does_nothing_and_stays_offline_when_cc_is_empty(tmp_path):
    client = _Client(latest={"cloud": _entry()})
    assert pkg.refresh(tmp_path / "tunnel", HOST, site="https://example.com", client=client) is None
    assert client.calls == []


def test_refresh_only_rewrites_when_the_version_changed(tmp_path):
    _prepare(tmp_path, _Client(latest={"cloud": _entry()}))
    same = _Client(latest={"cloud": _entry()})
    assert pkg.refresh(tmp_path / "tunnel", HOST, site="https://example.com", client=same,
                       cache=tmp_path / "cache", script=_script(tmp_path)) is None
    assert [call[0] for call in same.calls] == ["GET"]
    body = ZIP + b"v2"
    newer = _Client(latest={"cloud": _entry("1.21.0", body)}, body=body)
    result = pkg.refresh(tmp_path / "tunnel", HOST, site="https://example.com", client=newer,
                         cache=tmp_path / "cache", script=_script(tmp_path))
    assert result["version"] == "1.21.0"


# ── 测试构建随包带的云端包（11-28 真机：线上清单的云端包会顶掉测试包） ───────────────

def _bundle(tmp_path, version="1.12.25", body=ZIP + b"test-build"):
    folder = tmp_path / "app" / pkg.BUNDLE_DIR
    folder.mkdir(parents=True)
    (folder / pkg.PACKAGE).write_bytes(body)
    (folder / pkg.MANIFEST).write_text(json.dumps({"version": version, "sha256": hashlib.sha256(body).hexdigest(),
                                                   "size": len(body), "source_sha": "b" * 40}), encoding="utf-8")
    return tmp_path / "app"


def test_a_bundled_cloud_package_is_found_only_when_it_checks_out(tmp_path):
    base = _bundle(tmp_path)
    folder, meta = pkg.bundled(base)
    assert folder == base / pkg.BUNDLE_DIR and meta["version"] == "1.12.25"
    (folder / pkg.PACKAGE).write_bytes(b"tampered")
    assert pkg.bundled(base) is None
    assert pkg.bundled(tmp_path / "nothing-here") is None


def test_with_a_bundled_package_prepare_places_it_and_never_reads_the_release_manifest(tmp_path):
    found = pkg.bundled(_bundle(tmp_path))
    client = _Client(latest={"cloud": _entry("1.12.25")})   # 线上同号不同包：不能用它
    result = _prepare(tmp_path, client, bundle=lambda: found)
    assert client.calls == [] and result["version"] == "1.12.25"
    placed = tmp_path / "tunnel" / pkg.CC_DIR
    assert (placed / pkg.PACKAGE).read_bytes() == ZIP + b"test-build"
    assert json.loads((placed / pkg.MANIFEST).read_text(encoding="utf-8"))["sha256"] == found[1]["sha256"]


def test_with_a_bundled_package_refresh_follows_only_the_bundle(tmp_path):
    found = pkg.bundled(_bundle(tmp_path))
    _prepare(tmp_path, _Client(), bundle=lambda: found)
    online = _Client(latest={"cloud": _entry("1.30.0")})
    # 隧道盘里就是随包那版：线上更新也不重放
    assert pkg.refresh(tmp_path / "tunnel", HOST, site="https://example.com", client=online, cache=tmp_path / "cache",
                       script=_script(tmp_path), bundle=lambda: found) is None
    assert online.calls == []
    # 隧道盘里的版本被改过（如自升级测试）：重启桥时放回随包那版，仍不看线上
    manifest = tmp_path / "tunnel" / pkg.CC_DIR / pkg.MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps({**data, "version": "1.12.26"}), encoding="utf-8")
    result = pkg.refresh(tmp_path / "tunnel", HOST, site="https://example.com", client=online, cache=tmp_path / "cache",
                         script=_script(tmp_path), bundle=lambda: found)
    assert result["version"] == "1.12.25" and online.calls == []


def test_the_command_has_the_drive_and_the_token():
    assert pkg.install_command("y:", TOKEN) == (
        "powershell -NoProfile -ExecutionPolicy Bypass -File \\\\tsclient\\Y\\cc\\cloud_install.ps1 -Token " + TOKEN)


# ── 桥动作 ──────────────────────────────────────────────────────────────────

def _bridge(tmp_path, prepare, refresh=None):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from companion.tomo_bridge import TomoBridge

    QApplication.instance() or QApplication([])
    settings = types.SimpleNamespace(snapshot=lambda: {"members": [], "device": {"name": "OFFICE-PC"}})
    bridge = TomoBridge(settings, allowed=lambda: True, window=lambda: None, set_lang=lambda lang: None,
                        cloud_links=lambda: None, session_rdp=lambda: None, cloud_prepare=prepare,
                        cloud_refresh=refresh or (lambda root, host: None))
    host = types.SimpleNamespace(running=True, root=tmp_path / "tunnel", host=HOST, drive="Y", session=None,
                                 begin_pairing=lambda lang: types.SimpleNamespace(token=TOKEN, expires=123),
                                 pairing_status=lambda: {"state": "waiting", "code": "", "expires": 123},
                                 stop=lambda: None)
    bridge._cloud_host = host
    return bridge, host


def _wait(bridge, reply):
    from PySide6.QtWidgets import QApplication

    first = json.loads(reply)
    if "job" not in first:
        return first
    done = {}
    bridge.done.connect(lambda job, result: done.__setitem__(job, json.loads(result)))
    deadline = time.monotonic() + 5
    while first["job"] not in done and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return done[first["job"]]


def test_pair_begin_prepares_cc_first_and_returns_drive_and_command(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    bridge, host = _bridge(tmp_path, lambda root, name: calls.append((root, name)))
    result = _wait(bridge, bridge.call("cloud_pair_begin", "{}"))
    assert calls == [(tmp_path / "tunnel", HOST)]
    assert result == {"ok": True, "token": TOKEN, "expires": 123, "drive": "Y",
                      "command": pkg.install_command("Y", TOKEN)}
    assert TOKEN not in caplog.text
    bridge.shutdown()


def test_pair_begin_returns_the_failure_code_without_a_token(tmp_path):
    def fail(root, name):
        raise pkg.PackageError(pkg.CHECKSUM)

    bridge, host = _bridge(tmp_path, fail)
    begun = []
    host.begin_pairing = lambda lang: begun.append(lang)
    assert _wait(bridge, bridge.call("cloud_pair_begin", "{}")) == {"ok": False, "code": "checksum"}
    assert begun == []
    assert bridge._cloud_settings("cloud_status", {}, "zh")["package"] == "checksum"
    bridge.shutdown()


def test_pair_begin_without_a_running_host_prepares_nothing(tmp_path):
    calls = []
    bridge, host = _bridge(tmp_path, lambda root, name: calls.append(root))
    host.running = False
    assert json.loads(bridge.call("cloud_pair_begin", "{}")) == {"ok": False, "code": "not_running"}
    assert calls == []
    bridge.shutdown()


def test_connecting_refreshes_only_when_cc_was_placed_before(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    refreshed = []
    bridge, host = _bridge(tmp_path, lambda root, name: None, refresh=lambda root, name: refreshed.append(root))
    bridge._refresh_cloud_package(host)               # cc\ 是空的：不起任何后台事
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.005)
    assert refreshed == []
    (tmp_path / "tunnel" / "cc").mkdir(parents=True)
    (tmp_path / "tunnel" / "cc" / "cloud.json").write_text("{}")
    bridge._refresh_cloud_package(host)
    deadline = time.monotonic() + 5
    while not refreshed and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert refreshed == [tmp_path / "tunnel"]
    bridge.shutdown()


def test_an_idle_bridge_never_prepares(tmp_path):
    calls = []
    bridge, host = _bridge(tmp_path, lambda root, name: calls.append(root), refresh=lambda root, name: calls.append(root))
    bridge.state()
    bridge.call("cloud_status", "{}")
    bridge.call("cloud_pair_status", "{}")
    assert calls == []
    bridge.shutdown()
