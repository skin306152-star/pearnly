"""云端那份 CC 管自己的安装（段 ③ 不依赖 V1 的两件，``cloud_link/cloud_self.py``）：自升级、断开连带删。

只离线：Windows 上真起 PowerShell 小进程、改快捷方式、删目录要在云端真机验（夜间步骤单）。这里守：
- 「我们装的」判法：路径写死在 ``%LOCALAPPDATA%\\Pearnly\\CC-Cloud``、本进程在它下面一层的版本目录、标记文件内容对，缺一不删；
- 自升级：只在比自己新时备好新版本（拷包、核大小与 sha256、解压），失败的版本不反复试，备好了起小进程、请本进程退出；
- 断开连带删：桥经 ``ctl`` 加密请求 → 云端校验 → 回执同一编号 → 桥等回执（超时照常断开）；
- 小进程的命令只删那一个目录、只动指向它的快捷方式。
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from companion.cloud_link import cloud_self, cloud_side, ctl_files
from companion.cloud_link import tunnel_protocol as protocol
from test_cloud_host import HOST, _FakeHost, _bridge, _host, _saved, _wait, vault  # noqa: F401 - vault 是夹具

RUNNING = "1.12.26"


def _layout(tmp_path, version=RUNNING, *, marker=True):
    """假的 %LOCALAPPDATA%：<local>\\Pearnly\\CC-Cloud\\<版本>\\companion.exe。"""
    local = tmp_path / "local"
    base = cloud_self.install_base({"LOCALAPPDATA": str(local)})
    exe = base / version / "companion.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"old")
    if marker:
        (base / cloud_self.MARKER).write_text(cloud_self.MARKER_TEXT, encoding="utf-8")
    return base, exe


def _package(root: Path, version: str, *, files=None, corrupt=False) -> dict:
    """隧道盘 cc\\ 里放一份云端包与 cloud.json（同 cloud_package.place 的样子）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in (files if files is not None else {"companion.exe": b"new", "_internal/x.pyd": b"x"}).items():
            archive.writestr(name, data)
    data = buffer.getvalue()
    cc = root / "cc"
    cc.mkdir(parents=True, exist_ok=True)
    (cc / "CC-Cloud.zip").write_bytes(data)
    sha = hashlib.sha256(b"other" if corrupt else data).hexdigest()
    manifest = {"version": version, "sha256": sha, "size": len(data), "host": HOST}
    (cc / "cloud.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _link(tmp_path):
    root = tmp_path / "tunnel"
    for sub in ("in", "out", "ctl"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return cloud_side.CloudLink(root, protocol.new_session(), take=lambda r: None)


# ── 我们的目录 ────────────────────────────────────────────────────────────────

def test_the_install_folder_is_fixed_under_local_appdata():
    assert cloud_self.install_base({"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}) == Path(
        r"C:\Users\u\AppData\Local") / "Pearnly" / "CC-Cloud"
    assert cloud_self.install_base({}) is None
    assert cloud_self.install_base({"LOCALAPPDATA": "  "}) is None


def test_only_the_copy_installed_in_a_version_folder_counts_as_ours(tmp_path):
    base, exe = _layout(tmp_path)
    assert cloud_self.installed_dir(base, exe) == exe.parent
    assert cloud_self.ours(base, exe) is True
    # 开发机 / 别处手动跑的：不在 <base>\<版本> 下
    assert cloud_self.installed_dir(base, tmp_path / "elsewhere" / RUNNING / "companion.exe") is None
    assert cloud_self.installed_dir(base, base / "not-a-version" / "companion.exe") is None
    assert cloud_self.installed_dir(base, base / RUNNING / "python.exe") is None
    assert cloud_self.installed_dir(None, exe) is None
    assert cloud_self.ours(base, tmp_path / "elsewhere" / RUNNING / "companion.exe") is False


def test_without_our_marker_nothing_is_ours(tmp_path):
    base, exe = _layout(tmp_path, marker=False)
    assert cloud_self.ours(base, exe) is False
    (base / cloud_self.MARKER).write_text("someone else", encoding="utf-8")
    assert cloud_self.ours(base, exe) is False
    # PowerShell 写的带 BOM / 换行也认
    (base / cloud_self.MARKER).write_bytes(b"\xef\xbb\xbf" + cloud_self.MARKER_TEXT.strip().encode() + b"\r\n")
    assert cloud_self.ours(base, exe) is True


def test_the_installed_copy_adds_the_marker_on_start(tmp_path):
    base, exe = _layout(tmp_path, marker=False)
    assert cloud_self.ensure_marker(base, exe) is True
    assert (base / cloud_self.MARKER).read_text(encoding="utf-8") == cloud_self.MARKER_TEXT
    # 不是我们装的那份：不写
    other = tmp_path / "other"
    assert cloud_self.ensure_marker(other, tmp_path / "x" / RUNNING / "companion.exe") is False
    assert not other.exists()


def test_the_install_script_writes_the_same_marker():
    script = (Path(__file__).resolve().parents[1] / "packaging" / "cloud_install.ps1").read_text(encoding="utf-8-sig")
    assert "'" + cloud_self.MARKER + "'" in script
    assert '"' + cloud_self.MARKER_TEXT.strip() + "`n" + '"' in script


# ── 自升级 ────────────────────────────────────────────────────────────────────

def test_the_placed_manifest_must_be_well_formed(tmp_path):
    root = tmp_path / "t"
    assert cloud_self.placed_manifest(root) is None
    manifest = _package(root, "1.12.27")
    assert cloud_self.placed_manifest(root) == {k: manifest[k] for k in ("version", "sha256", "size")}
    for bad in ({"version": "x", "sha256": "a" * 64, "size": 1}, {"version": "1.2.3", "sha256": "z", "size": 1},
                {"version": "1.2.3", "sha256": "a" * 64, "size": 0}, {"version": "1.2.3", "sha256": "a" * 64, "size": True}, []):
        (root / "cc" / "cloud.json").write_text(json.dumps(bad), encoding="utf-8")
        assert cloud_self.placed_manifest(root) is None


def test_no_upgrade_when_not_installed_or_not_newer(tmp_path):
    base, exe = _layout(tmp_path)
    link = _link(tmp_path)
    spawned = []
    run = dict(running=RUNNING, base=base, exe=exe, spawn=spawned.append, pid=4321)
    assert cloud_self.upgrade(link, **run) == cloud_self.NONE                      # 没放包
    _package(link.root, RUNNING)
    assert cloud_self.upgrade(link, **run) == cloud_self.NONE                      # 同版本
    _package(link.root, "1.12.9")
    assert cloud_self.upgrade(link, **run) == cloud_self.NONE                      # 按数字比：1.12.9 < 1.12.26
    _package(link.root, "1.12.27")
    assert cloud_self.upgrade(link, **{**run, "exe": tmp_path / "dev" / RUNNING / "companion.exe"}) == \
        cloud_self.NOT_INSTALLED
    assert spawned == [] and not link.quit.is_set()


def test_a_newer_package_is_staged_then_the_helper_swaps_and_we_quit(tmp_path):
    base, exe = _layout(tmp_path)
    link = _link(tmp_path)
    _package(link.root, "1.12.27")
    spawned = []
    generation = link.generation
    code = cloud_self.upgrade(link, running=RUNNING, base=base, exe=exe, spawn=spawned.append, pid=4321)
    assert code == cloud_self.STAGED
    new_exe = base / "1.12.27" / "companion.exe"
    assert new_exe.read_bytes() == b"new" and (base / "1.12.27" / "_internal" / "x.pyd").exists()
    assert exe.read_bytes() == b"old"                                   # 旧版本目录留着
    assert sorted(p.name for p in base.iterdir()) == [cloud_self.MARKER, "1.12.26", "1.12.27"]   # 临时文件收干净
    assert link.quit.is_set() and link.generation == generation + 1
    [command] = spawned
    assert command[:6] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command"]
    script = command[6]
    assert script.startswith("Wait-Process -Id 4321 -ErrorAction SilentlyContinue; ") and "-Timeout" not in script
    assert f"$l.TargetPath = '{new_exe}'" in script
    assert f".StartsWith('{base}\\'" in script                                # 只改指向我们目录的快捷方式
    assert script.rstrip().endswith(f"Start-Process -FilePath '{new_exe}' -ArgumentList '--cloud' -WorkingDirectory "
                                    f"'{new_exe.parent}'")


@pytest.mark.parametrize("kind", ["checksum", "size", "no_exe", "bad_zip"])
def test_a_bad_package_is_not_installed_and_not_retried(tmp_path, kind):
    base, exe = _layout(tmp_path)
    link = _link(tmp_path)
    manifest = _package(link.root, "1.12.27", corrupt=kind == "checksum",
                        files={"readme.txt": b"x"} if kind == "no_exe" else None)
    if kind == "size":
        manifest["size"] += 1
        (link.root / "cc" / "cloud.json").write_text(json.dumps(manifest), encoding="utf-8")
    if kind == "bad_zip":
        data = b"not a zip"
        (link.root / "cc" / "CC-Cloud.zip").write_bytes(data)
        manifest.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        (link.root / "cc" / "cloud.json").write_text(json.dumps(manifest), encoding="utf-8")
    spawned = []
    run = dict(running=RUNNING, base=base, exe=exe, spawn=spawned.append, pid=1)
    expected = cloud_self.CHECKSUM if kind in ("checksum", "size") else cloud_self.BAD_PACKAGE
    assert cloud_self.upgrade(link, **run) == expected
    assert cloud_self.upgrade(link, **run) == cloud_self.NONE        # 同一版本不反复试
    assert spawned == [] and not link.quit.is_set()
    assert sorted(p.name for p in base.iterdir()) == [cloud_self.MARKER, "1.12.26"]


def test_a_copy_already_running_from_the_placed_version_does_not_restart_itself(tmp_path):
    # 包里程序自报 1.12.26、清单写 1.12.27：从 1.12.27 目录起来的那份不再对着同一份包「升级」
    base, exe = _layout(tmp_path, "1.12.27")
    link = _link(tmp_path)
    _package(link.root, "1.12.27")
    spawned = []
    assert cloud_self.upgrade(link, running=RUNNING, base=base, exe=exe, spawn=spawned.append, pid=1) == cloud_self.NONE
    assert spawned == [] and not link.quit.is_set()


def test_any_unpacking_error_is_recorded_once(tmp_path, monkeypatch):
    base, exe = _layout(tmp_path)
    link = _link(tmp_path)
    _package(link.root, "1.12.27")

    def unsupported(*_a, **_k):
        raise NotImplementedError("compression type 99")

    monkeypatch.setattr(cloud_self.zipfile.ZipFile, "extractall", unsupported)
    run = dict(running=RUNNING, base=base, exe=exe, spawn=lambda c: pytest.fail("spawned"), pid=1)
    assert cloud_self.upgrade(link, **run) == cloud_self.FAILED
    assert cloud_self.upgrade(link, **run) == cloud_self.NONE
    assert sorted(p.name for p in base.iterdir()) == [cloud_self.MARKER, "1.12.26"]


def test_the_helper_runs_outside_the_install_folder(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cloud_self.subprocess, "Popen", lambda command, **kw: seen.update(kw, command=command))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    cloud_self.spawn_detached(["powershell.exe"])
    assert seen["cwd"] == str(tmp_path / "local" / "Pearnly")
    cloud_self.spawn_detached(["powershell.exe"], cwd=tmp_path)
    assert seen["cwd"] == str(tmp_path)


def test_an_already_unpacked_version_is_reused(tmp_path):
    base, exe = _layout(tmp_path)
    link = _link(tmp_path)
    _package(link.root, "1.12.27")
    (base / "1.12.27").mkdir()
    (base / "1.12.27" / "companion.exe").write_bytes(b"kept")
    spawned = []
    assert cloud_self.upgrade(link, running=RUNNING, base=base, exe=exe, spawn=spawned.append, pid=1) == cloud_self.STAGED
    assert (base / "1.12.27" / "companion.exe").read_bytes() == b"kept" and len(spawned) == 1


# ── 断开连带删：云端 ──────────────────────────────────────────────────────────

def test_uninstall_refuses_anything_that_is_not_ours(tmp_path):
    base, exe = _layout(tmp_path, marker=False)
    spawned = []
    got = cloud_self.uninstall(base=base, exe=exe, spawn=spawned.append, pid=7)
    assert got == {"code": cloud_self.NOT_OURS, "dir": True, "link": None} and spawned == []
    (base / cloud_self.MARKER).write_text(cloud_self.MARKER_TEXT, encoding="utf-8")
    got = cloud_self.uninstall(base=base, exe=tmp_path / "dev" / RUNNING / "companion.exe", spawn=spawned.append, pid=7)
    assert got["code"] == cloud_self.NOT_OURS and spawned == []
    assert cloud_self.uninstall(base=None, exe=exe, spawn=spawned.append)["code"] == cloud_self.NOT_OURS


def test_uninstall_schedules_a_helper_that_only_removes_our_folder(tmp_path):
    base, exe = _layout(tmp_path)
    spawned = []
    got = cloud_self.uninstall(base=base, exe=exe, spawn=spawned.append, pid=7,
                               read_link_target=lambda: str(base / RUNNING / "companion.exe"))
    assert got == {"code": cloud_self.SCHEDULED, "dir": True, "link": True}
    assert base.exists()                                                # 删要等本进程退出后由小进程做
    script = spawned[0][6]
    # 小进程先离开要删的目录，再一直等本进程退出（不设超时：没退出就删会留下半个安装）
    assert script.startswith(f"Set-Location -LiteralPath '{base.parent}'; Wait-Process -Id 7 -ErrorAction SilentlyContinue; ")
    assert "-Timeout" not in script
    removes = [part for part in script.split("; ") if "Remove-Item" in part]
    assert removes == [
        f"  if ([string]$l.TargetPath -and ([string]$l.TargetPath).StartsWith('{base}\\', "
        "[System.StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $p -Force }",
        f"if ($ok) {{ Remove-Item -LiteralPath '{base}' -Recurse -Force -ErrorAction SilentlyContinue }}",
    ]
    assert f"$m = Join-Path '{base}' '{cloud_self.MARKER}'" in script
    assert f"-eq '{cloud_self.MARKER_TEXT.strip()}'" in script          # 删前小进程再核一次标记
    assert f"Add-Content -LiteralPath '{base.parent / cloud_self.TRACE}'" in script   # 删后回读写在被删目录外
    assert "$before = Test-Path" in script and "$after = Test-Path" in script


def test_quotes_in_paths_are_escaped_for_powershell(tmp_path):
    base = tmp_path / "O'Brien" / "Pearnly" / "CC-Cloud"
    script = cloud_self.uninstall_script(1, base)
    assert "O''Brien" in script and "O'Brien" not in script.replace("O''Brien", "")


def test_the_cloud_handles_the_sealed_request_and_answers_with_the_same_id(tmp_path):
    link = _link(tmp_path)
    item_id = ctl_files.new_id()
    (link.ctl_dir / ctl_files.UNINSTALL_FILE).write_bytes(ctl_files.seal(link.session, "uninstall", {}, item_id=item_id))
    ran = []

    def run():
        ran.append(1)
        return {"code": cloud_self.SCHEDULED, "dir": True, "link": True}

    assert cloud_self.handle_uninstall(link, run=run) == cloud_self.SCHEDULED
    assert not (link.ctl_dir / ctl_files.UNINSTALL_FILE).exists() and link.quit.is_set()
    got = ctl_files.open_((link.ctl_dir / ctl_files.UNINSTALLED_FILE).read_bytes(), link.session, "uninstalled")
    assert got == (item_id, {"code": "scheduled", "dir": True, "link": True})
    # 同一编号再来（重放）：不再做
    (link.ctl_dir / ctl_files.UNINSTALL_FILE).write_bytes(ctl_files.seal(link.session, "uninstall", {}, item_id=item_id))
    assert cloud_self.handle_uninstall(link, run=run) is None and ran == [1]
    assert cloud_self.handle_uninstall(link, run=run) is None            # 没有文件


def test_a_request_from_another_session_or_not_ours_does_not_quit(tmp_path):
    link = _link(tmp_path)
    (link.ctl_dir / ctl_files.UNINSTALL_FILE).write_bytes(ctl_files.seal(protocol.new_session(), "uninstall", {}))
    assert cloud_self.handle_uninstall(link, run=lambda: pytest.fail("ran")) == ctl_files.WRONG_EPOCH
    (link.ctl_dir / ctl_files.UNINSTALL_FILE).write_bytes(ctl_files.seal(link.session, "uninstall", {}))
    assert cloud_self.handle_uninstall(link, run=lambda: {"code": cloud_self.NOT_OURS, "dir": True, "link": None}) == \
        cloud_self.NOT_OURS
    assert not link.quit.is_set()


def test_check_ctl_runs_uninstall_and_upgrade_and_stops_after_quit(tmp_path):
    link = _link(tmp_path)
    calls = []
    common = dict(members=lambda l: None, commands=lambda l: [], status=lambda l, force=False: False)

    def uninstall(l):
        calls.append("uninstall")
        return None

    def upgrade(l):
        calls.append("upgrade")
        l.quit.set()
        return cloud_self.STAGED

    cloud_side.check_ctl(link, uninstall=uninstall, upgrade=upgrade, **common)
    assert calls == ["uninstall", "upgrade"]
    cloud_side.check_ctl(link, uninstall=uninstall, upgrade=upgrade, **common)
    assert calls == ["uninstall", "upgrade"]                            # 要退出了：不再处理别的

    link2 = _link(tmp_path / "2")

    def removed(l):
        l.quit.set()
        return cloud_self.SCHEDULED

    cloud_side.check_ctl(link2, uninstall=removed, upgrade=lambda l: pytest.fail("upgrade after uninstall"), **common)


# ── 断开连带删：桥 ────────────────────────────────────────────────────────────

def test_the_bridge_waits_for_the_receipt_of_its_own_request(tmp_path):
    _saved(tmp_path)
    host, *_ = _host(tmp_path)
    assert host.uninstall_cloud() == "not_running"
    host.start()
    session = host._session

    def cloud_answers(_seconds):
        data = (host.ctl_dir / ctl_files.UNINSTALL_FILE).read_bytes()
        item_id, _payload = ctl_files.open_(data, session, "uninstall")
        (host.ctl_dir / ctl_files.UNINSTALLED_FILE).write_bytes(
            ctl_files.seal(session, "uninstalled", {"code": "not_ours", "dir": True, "link": None}, item_id="f" * 32))
        (host.ctl_dir / ctl_files.UNINSTALLED_FILE).write_bytes(   # 后写的是同一编号的（前一份别的编号不认）
            ctl_files.seal(session, "uninstalled", {"code": "scheduled", "dir": True, "link": True}, item_id=item_id))

    assert host.uninstall_cloud(sleep=cloud_answers) == "scheduled"
    assert not (host.ctl_dir / ctl_files.UNINSTALLED_FILE).exists()
    host.stop()
    assert not (host.ctl_dir / ctl_files.UNINSTALL_FILE).exists()


def test_the_bridge_gives_up_after_the_timeout_and_clears_its_request(tmp_path):
    _saved(tmp_path)
    host, *_ = _host(tmp_path)
    host.start()
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    assert host.uninstall_cloud(timeout=3, poll=1, sleep=sleep, clock=lambda: now[0]) == "timeout"
    assert now[0] == 3 and not (host.ctl_dir / ctl_files.UNINSTALL_FILE).exists()
    host.stop()


def test_disconnect_asks_the_cloud_to_uninstall_before_stopping(tmp_path, vault, monkeypatch):
    log, holder = [], {}
    monkeypatch.setattr(_FakeHost, "running", True, raising=False)
    monkeypatch.setattr(_FakeHost, "uninstall_cloud", lambda self: log.append(("uninstall", self.host)) or "scheduled",
                        raising=False)
    bridge = _bridge(tmp_path, holder, log)
    args = json.dumps({"host": HOST, "port": 3390, "user": "CLOUDUSER-01"})
    _wait(bridge, bridge.call("cloud_connect", args))
    assert _wait(bridge, bridge.call("cloud_disconnect", "{}")) == {"ok": True}
    assert log[-2:] == [("uninstall", HOST), ("stop", HOST)]
    bridge.shutdown()


def test_a_connect_made_while_waiting_for_the_receipt_is_kept(tmp_path, vault, monkeypatch):
    log, holder = [], {}
    args = json.dumps({"host": HOST, "port": 3390, "user": "CLOUDUSER-01"})
    bridge = _bridge(tmp_path, holder, log)

    def reconnect_meanwhile(self):
        log.append(("uninstall", self.host))
        bridge._cloud_host = _FakeHost(HOST, None, log)   # 等回执时人又点了「连接」（换了一个宿主）
        return "timeout"

    monkeypatch.setattr(_FakeHost, "running", True, raising=False)
    monkeypatch.setattr(_FakeHost, "uninstall_cloud", reconnect_meanwhile, raising=False)
    _wait(bridge, bridge.call("cloud_connect", args))
    first = bridge._cloud_host
    assert _wait(bridge, bridge.call("cloud_disconnect", "{}")) == {"ok": True}
    assert bridge._cloud_host is not first and log.count(("stop", HOST)) == 0   # 新起的没被停
    assert [link.host for link in holder["store"].links] == [HOST]              # 连接没被删
    bridge.shutdown()


def test_the_new_ctl_kinds_are_swept_with_the_other_state_files():
    assert ctl_files.UNINSTALL_FILE in ctl_files.FILES and ctl_files.UNINSTALLED_FILE in ctl_files.FILES
