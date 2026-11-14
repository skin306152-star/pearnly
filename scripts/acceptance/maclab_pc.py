"""测试机接本机 Mac 实验栈（maclab）的编排（本机这一侧；风格同 cc_switch.py）。主会话 11-14 定：只用测试机2，它的 ERP、Cowork
两种连接各接一套 Mac 栈；启用期间同种连接的线上档案暂停，用完原样还回，并核对 companion.json 的哈希等于启用前。

    python3 scripts/acceptance/maclab_pc.py prepare --host testpc2 [--address 192.168.0.93]
    python3 scripts/acceptance/maclab_pc.py attach  --host testpc2 --app erp|cowork [--name maclab-erp]
    python3 scripts/acceptance/maclab_pc.py release --host testpc2 [--expect C216460D]
    python3 scripts/acceptance/maclab_pc.py status  --host testpc2

- prepare：① hosts 加一行「<Mac 局域网地址> erp.maclab.localhost cowork.maclab.localhost」（管理员 ssh，`win/hosts_add.ps1`；改前整份
  备份；已有同一行不动；同名字不同地址停下不改）② 配对要的第三方包（httpx 一族，版本照 companion/packaging/requirements-build.txt）
  在本机按 win_amd64 / 3.11 装好、拷到测试机 `C:\\PearnlyAcceptance\\maclab\\site`（不动测试机现有 Python），本工作树的
  `companion/src/companion` 刷新到 `C:\\PearnlyAcceptance\\ccsrc\\companion` ③ 以桌面用户看两个站点解析到哪、ping 一次（没起的标「没起」）。
- attach：第一次启用前在测试机记下 companion.json 的哈希与逐字节备份（`maclab\\before\\`，已有不覆盖）→ 本机 `maclab.sh mint` 取配对码
  （只在内存和 600 的临时文件里）→ 拷到测试机一个只用一次的文件 → 以桌面用户 `pair_profile.py add`（读完即删）→ 回读确认那个文件已不在。
- release：每份 maclab 档案 `remove`（能连上 Mac 栈就作废密钥）→ 核哈希：等于启用前（与 `--expect`）就过；不等但语义相同 → 停 CC、
  逐字节拷回、启动、再核；语义也不同 → 停下列出差异键路径（不带值），不硬拷 → 重启 CC 等一次对线上的心跳 → `before\\` 改名留档。
- status：`pair_profile.py show` + 当前哈希 + 启用前哈希。

桌面用户的脚本都经 `targets.Windows(host).python`（一次性计划任务；参数文件只有路径和名字，配对码不进参数 / 输出 / 日志）。
连不上、超时抛 Infra；前置不满足（Mac 栈不肯签码、hosts 有冲突行）抛 Unmet；核对没过抛 Failed。
"""
from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WIN_DIR = ROOT / "infrastructure" / "maclab" / "win"
COMPANION = ROOT / "companion" / "src" / "companion"
REQUIREMENTS = ROOT / "companion" / "packaging" / "requirements-build.txt"
NAMES = ("erp.maclab.localhost", "cowork.maclab.localhost")
DEPS = ("httpx", "httpcore", "h11", "anyio", "sniffio", "idna", "certifi", "typing_extensions")
ADDRESS = os.environ.get("PEARNLY_MACLAB_LAN_IP", "192.168.0.93")
PORTS = {"erp": int(os.environ.get("MACLAB_ERP_PORT", "18991")), "cowork": int(os.environ.get("MACLAB_COWORK_PORT", "18992"))}
WORK = r"C:\PearnlyAcceptance"
MACLAB = WORK + r"\maclab"
SITE = MACLAB + r"\site"
CCSRC = WORK + r"\ccsrc"
STATE = MACLAB
BEFORE = MACLAB + r"\before"
CODES = MACLAB + r"\codes"
ROOT_DIR = os.environ.get("PEARNLY_ACC_ROOT_DIR", r"C:\PearnlyAcceptance\root")
LEDGER = os.environ.get("PEARNLY_ACC_LEDGER", r"\\accserver\d$\ACCOUNT\69EXP\TEST2")
HEARTBEAT_WAIT = 150


def _lib(name: str):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


Infra = _lib("acc_errors").Infra
Unmet = _lib("acc_errors").Unmet


class Failed(RuntimeError):
    """核对没过（还原后配置不对、心跳不干净、配对码文件删不掉）：停下，现场留给人看，不自动重跑。"""


# ── 纯函数（tests/test_maclab_pc.py 离线测）────────────────────────────────────────


def hosts_plan(text: str, address: str, names=NAMES) -> dict:
    """测试机 hosts 原文 → 要不要加那一行：``present``（已有同地址、带全两个名字的行，不动）/ ``add`` / ``conflict``（有行把这两个名字
    指向别的地址，或同地址只带了其中一个：停下不改）。注释（# 之后）不算。"""
    seen: dict = {}
    for line in text.splitlines():
        tokens = line.split("#", 1)[0].split()
        if len(tokens) < 2:
            continue
        for name in tokens[1:]:
            if name.lower() in names:
                seen.setdefault(name.lower(), []).append((tokens[0], line.strip()))
    lines = sorted({line for found in seen.values() for _, line in found})
    if not seen:
        return {"action": "add", "line": f"{address} {' '.join(names)}"}
    if all(name in seen for name in names) and all(addr == address for found in seen.values() for addr, _ in found):
        return {"action": "present", "lines": lines}
    return {"action": "conflict", "lines": lines}


def pinned(text: str, names=DEPS) -> dict:
    """requirements-build.txt → ``{包: 版本}``（只要 ``names`` 里的，名字按 PEP 503 规范化比）；少一个就抛 ValueError。"""
    norm = lambda value: re.sub(r"[-_.]+", "-", value).lower()  # noqa: E731
    found = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([^\s#;]+)", line)
        if match:
            found[norm(match.group(1))] = match.group(2)
    missing = [name for name in names if norm(name) not in found]
    if missing:
        raise ValueError(f"requirements 里没有钉 {missing}")
    return {name: found[norm(name)] for name in names}


def dist_versions(entries) -> dict:
    """装好的目录里的 ``<包>-<版本>.dist-info`` → ``{包: 版本}``（打清单用）。"""
    out = {}
    for entry in entries:
        match = re.match(r"^(.+?)-([^-]+)\.dist-info$", str(entry))
        if match:
            out[match.group(1).lower().replace("-", "_")] = match.group(2)
    return dict(sorted(out.items()))


def pairing_code(stdout: str) -> str:
    """`maclab.sh mint` 的标准输出 → 配对码；签不出（MINT_REFUSED、没有那一行）抛 Unmet，消息里不带任何密钥。"""
    refused = [line for line in stdout.splitlines() if line.startswith("MINT_REFUSED ")]
    if refused:
        raise Unmet("Mac 栈不肯签配对码：" + refused[-1][len("MINT_REFUSED "):][:300])
    codes = [line[len("PAIRING_CODE "):].strip() for line in stdout.splitlines() if line.startswith("PAIRING_CODE ")]
    if len(codes) != 1 or not re.fullmatch(r"(pcc|exp)_[A-Za-z0-9_\-]+@[a-z0-9.\-]+\.maclab\.localhost:\d+", codes[0]):
        raise Unmet(f"maclab.sh mint 没给出一个形状对的配对码（{len(codes)} 行）")
    return codes[0]


def last_json(text: str) -> dict:
    lines = [line for line in (text or "").splitlines() if line.strip().startswith("{")]
    if not lines:
        raise ValueError("没有结果行")
    return json.loads(lines[-1])


def release_decision(check: dict, expect: str = "") -> str:
    """cfg_guard check 的结果 → ``ok``（字节等于启用前，给了 expect 也等于它）/ ``restore``（语义相同、字节不同：逐字节拷回）/
    ``stop``（语义不同：列差异停下；或启用前记的哈希就不等于 expect——那份备份本身不是要的状态）。"""
    if expect and check.get("before_hash") != expect.upper():
        return "stop"
    if check.get("bytes_equal") and (not expect or check.get("hash") == expect.upper()):
        return "ok"
    return "restore" if check.get("semantic_equal") else "stop"


def maclab_names(show: dict) -> list:
    """pair_profile show 的结果里本脚本登记过的 maclab 档案名。"""
    return sorted((show.get("maclab") or {}).keys())


# ── 本机与测试机上的动作 ──────────────────────────────────────────────────────────


class Pc:
    def __init__(self, host: str):
        self.win = _lib("targets").Windows(host)
        self.host = host

    def ssh(self, command: str, timeout: int = 120):
        """管理员 ssh（不走桌面用户通道）。"""
        return self.win._run(["ssh", "-o", "BatchMode=yes", self.win.host, command], timeout)

    def mkdir(self, path: str) -> None:
        done = self.ssh(f'cmd /c if not exist "{path}" mkdir "{path}"')
        if done.returncode:
            raise Infra(f"{self.host}：建不了 {path}（{(done.stderr or '').strip()[-200:]}）")

    def exists(self, path: str) -> bool:
        done = self.ssh(f'cmd /c if exist "{path}" (echo PRESENT) else (echo GONE)')
        return "PRESENT" in (done.stdout or "")

    def ps1(self, script: Path, *args: str, timeout: int = 300) -> str:
        remote = self.win.put(script)
        done = self.ssh(f'powershell -NoProfile -ExecutionPolicy Bypass -File "{remote.replace("/", chr(92))}" ' + " ".join(args), timeout)
        return (done.stdout or "") + (done.stderr or "")

    def desk(self, script: str, *args: str, timeout: int = 600) -> dict:
        """以桌面用户跑 infrastructure/maclab/win/ 下的脚本，取末行 JSON。"""
        code, out = self.win.python(WIN_DIR / script, *args, timeout=timeout)
        try:
            return last_json(out)
        except ValueError:
            raise Infra(f"{self.host}：{script} {args[:1]} 没回结果（退出码 {code}）：{out[-400:]}") from None


def prepare_hosts(pc: Pc, address: str) -> dict:
    with tempfile.TemporaryDirectory() as folder:
        local = Path(folder) / "hosts"
        got = pc.win._run(["scp", "-q", f"{pc.win.host}:C:/Windows/System32/drivers/etc/hosts", str(local)], 120)
        if got.returncode:
            raise Infra(f"{pc.host}：读不回 hosts（{(got.stderr or '').strip()[-200:]}）")
        plan = hosts_plan(local.read_bytes().decode("utf-8", "replace"), address)
    if plan["action"] == "conflict":
        raise Unmet(f"{pc.host} 的 hosts 里已有指向别处的同名行，没改：{plan['lines']}")
    if plan["action"] == "present":
        return plan
    out = pc.ps1(WIN_DIR / "hosts_add.ps1", "-Address", address, "-BackupDir", f'"{MACLAB}"')
    if "HOSTS_OK" not in out:
        raise Unmet(f"{pc.host}：hosts 没改成：{out[-300:]}")
    return {"action": "added", "backup": next((l[7:] for l in out.splitlines() if l.startswith("BACKUP ")), ""),
            "lines": [l[5:] for l in out.splitlines() if l.startswith("LINE ")]}


def _zip(folder: Path, target: Path) -> int:
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                archive.write(path, path.relative_to(folder).as_posix())
                count += 1
    return count


def deploy(pc: Pc, zip_path: Path, dest: str) -> str:
    pc.mkdir(MACLAB)
    remote = pc.win.put(zip_path, f"maclab/{zip_path.name}")
    out = pc.ps1(WIN_DIR / "deploy_zip.ps1", "-Zip", f'"{remote.replace("/", chr(92))}"', "-Dest", f'"{dest}"')
    if "DEPLOY_OK" not in out:
        raise Unmet(f"{pc.host}：展开到 {dest} 没成：{out[-300:]}")
    return out.strip().splitlines()[-1]


def prepare_deps(pc: Pc) -> dict:
    versions = pinned(REQUIREMENTS.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as folder:
        site = Path(folder) / "site"
        pip = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", str(site), "--no-compile",
               "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.11", "--implementation", "cp",
               "--no-deps", *[f"{name}=={version}" for name, version in versions.items()]]
        done = subprocess.run(pip, capture_output=True, text=True, timeout=600)
        if done.returncode:
            raise Infra(f"本机 pip 装不了 Windows 包：{(done.stderr or done.stdout)[-400:]}")
        installed = dist_versions(entry.name for entry in site.iterdir())
        shutil.rmtree(site / "bin", ignore_errors=True)   # 包带的命令行入口（Mac 上生成的），测试机用不到
        site_zip = Path(folder) / "site.zip"
        _zip(site, site_zip)
        src_zip = Path(folder) / "ccsrc.zip"
        files = _zip(COMPANION, src_zip)
        deployed = {"site": deploy(pc, site_zip, SITE), "ccsrc": deploy(pc, src_zip, CCSRC + r"\companion")}
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", "companion/src"]).returncode != 0
    return {"packages": installed, "pinned": versions, "ccsrc_files": files, "ccsrc_from": head + (" + 未提交改动" if dirty else ""),
            "deployed": deployed}


def prepare(pc: Pc, address: str) -> dict:
    ipaddress.IPv4Address(address)
    out = {"hosts": prepare_hosts(pc, address), "deps": prepare_deps(pc)}
    out["reach"] = pc.desk("maclab_reach.py", str(PORTS["erp"]), str(PORTS["cowork"]), timeout=180).get("reach")
    bad = [item for item in out["reach"] or [] if item.get("state") not in ("pong", "down")]
    out["ok"] = not bad
    return out


def guard(pc: Pc, *args: str, timeout: int = 300) -> dict:
    return pc.desk("cfg_guard.py", *args, timeout=timeout)


def profile(pc: Pc, *args: str, timeout: int = 600) -> dict:
    return pc.desk("pair_profile.py", "--site", SITE, "--ccsrc", CCSRC, "--state", STATE, *args, timeout=timeout)


def attach(pc: Pc, app: str, name: str) -> dict:
    out = {"before": guard(pc, "snapshot", BEFORE)}
    label = f"{name}-{pc.host}"
    minted = subprocess.run([str(ROOT / "scripts" / "maclab.sh"), "mint", app, label], capture_output=True, text=True, timeout=600)
    code = pairing_code(minted.stdout)
    pc.mkdir(CODES)
    file_name = f"{secrets.token_hex(8)}.code"
    remote = CODES + "\\" + file_name
    with tempfile.TemporaryDirectory() as folder:
        local = Path(folder) / file_name
        handle = os.open(local, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(code)
        pc.win.put(local, f"maclab/codes/{file_name}")
    try:
        result = profile(pc, "add", name, remote, ROOT_DIR, LEDGER)
        if code.split("@")[0] in json.dumps(result):
            raise Failed("pair_profile 的输出里出现了密钥")   # 不该发生：守住「配对码不进输出」
    finally:
        out["code_file_left_by_script"] = pc.exists(remote)
        if out["code_file_left_by_script"]:
            pc.ssh(f'cmd /c del /f /q "{remote}"')
        out["code_file_gone"] = not pc.exists(remote)
        del code
    out["add"] = {key: result.get(key) for key in ("added", "site", "channel", "account_sets", "undo")}
    out["config"] = result.get("config")
    if not out["code_file_gone"]:
        raise Failed(f"{pc.host}：配对码文件 {remote} 删不掉，手工删")
    return out


def release(pc: Pc, expect: str = "") -> dict:
    out = {"removed": []}
    show = profile(pc, "show")
    for name in maclab_names(show):
        result = profile(pc, "remove", name)
        out["removed"].append({"name": name, "leave_status": result.get("leave_status")})
    check = guard(pc, "check", BEFORE, *([expect] if expect else []))
    out["check"] = {key: check.get(key) for key in ("hash", "before_hash", "bytes_equal", "semantic_equal", "diff", "expect")}
    decision = release_decision(check, expect)
    out["decision"] = decision
    if decision == "stop":
        raise Failed("还原后的配置与启用前语义不同（或启用前的备份不是期望的那份），没硬拷回："
                    + json.dumps(out["check"], ensure_ascii=False))
    if decision == "restore":
        out["restore"] = guard(pc, "restore", BEFORE)
        again = guard(pc, "check", BEFORE, *([expect] if expect else []))
        out["check_after_restore"] = {key: again.get(key) for key in ("hash", "bytes_equal")}
        if release_decision(again, expect) != "ok":
            raise Failed("逐字节拷回后哈希仍不等于启用前：" + json.dumps(out["check_after_restore"]))
    beat = guard(pc, "heartbeat", str(HEARTBEAT_WAIT), timeout=HEARTBEAT_WAIT + 120)
    out["heartbeat"] = {key: beat.get(key) for key in ("since", "state", "first", "lines", "hash_after")}
    if beat.get("state") != "ok":
        raise Failed(f"{pc.host}：重启后 {HEARTBEAT_WAIT} 秒内没等到干净的线上心跳：{out['heartbeat']}")
    if beat.get("hash_after") != check.get("before_hash"):
        raise Failed(f"{pc.host}：CC 重启后又改写了 companion.json（{beat.get('hash_after')} ≠ {check.get('before_hash')}）")
    out["kept"] = guard(pc, "finish", BEFORE).get("kept")
    out["ok"] = True
    return out


def status(pc: Pc) -> dict:
    show = profile(pc, "show")
    return {"maclab": show.get("maclab"), "config": show.get("config"), "cc_running": show.get("cc_running"),
            "hash": guard(pc, "hash", BEFORE)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="测试机接本机 Mac 实验栈：准备、接上、还原、看状态")
    parser.add_argument("action", choices=("prepare", "attach", "release", "status"))
    parser.add_argument("--host", default="testpc2")
    parser.add_argument("--address", default=ADDRESS, help="本机 Mac 的局域网地址（写进测试机 hosts）")
    parser.add_argument("--app", choices=("erp", "cowork"))
    parser.add_argument("--name", default="")
    parser.add_argument("--expect", default="", help="启用前 companion.json 的 SHA-256 前 8 位（给了就也要等于它）")
    args = parser.parse_args(argv)
    pc = Pc(args.host)
    if args.action == "prepare":
        result = prepare(pc, args.address)
    elif args.action == "attach":
        if not args.app:
            parser.error("attach 要 --app erp|cowork")
        result = attach(pc, args.app, args.name or f"maclab-{args.app}")
    elif args.action == "release":
        result = release(pc, args.expect)
    else:
        result = status(pc)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
