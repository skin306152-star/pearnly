"""测试机上给 CC 桌面版加一份连本机 Mac 实验栈（maclab）的**单独档案**，不经 CC 窗口（以已登录桌面的那个 Windows 用户跑）。

    python pair_profile.py show
    python pair_profile.py add <名字> <配对码文件> <验收数据根> <验收账套完整路径>
    python pair_profile.py pause <名字>        Mac 栈不在时：这份档案暂停，被它让出来的东西还回去
    python pair_profile.py resume <名字>       Mac 栈回来了：再接上
    python pair_profile.py remove <名字>       断开（能连上就告诉服务端作废这把密钥）并删掉这份档案

**怎么跑**：必须是桌面用户本人的会话——密钥用 Windows DPAPI（当前用户）加密存进 ``%APPDATA%\\Pearnly\\companion.json``，
ssh 的公钥登录会话解不开 / 加不了密；也读不到账套服务器。照验收体系的做法经一次性计划任务跑
（``scripts/acceptance/win/acc-run.ps1``）。Python 用 ``C:\\PearnlyPython311\\python.exe``，CC 源码副本 ``C:\\PearnlyAcceptance\\ccsrc``
（``--ccsrc`` 可改），除已有的 ``dbf``、``aenum`` 外还要 ``httpx``（连带 ``httpcore``、``h11``、``anyio``、``sniffio``、``idna``、
``certifi``、``typing_extensions``，版本照 ``companion/packaging/requirements-build.txt``）：由本机
``scripts/acceptance/maclab_pc.py prepare`` 装进 ``C:\\PearnlyAcceptance\\maclab\\site``（``--site`` 可改），这里只把它加到
``sys.path`` 最前面，不动测试机现有的 Python 环境。整条导入链（``pairing`` / ``profile_store`` / ``account_probe`` / ``channel``，
含函数内的导入）用到的第三方包只有 dbf、httpx 一族，和不写开机自启时用不到的 winrt。

**配对**：直接调 CC 自己的 ``pairing.pair()``（同配对窗那一步：校验配对码、上报账套名单、把所选账套锁进档案），写进一份**新的**
档案（``ProfileStore.new_runtime_config``），已有档案的密钥、账套一概不动；不写开机自启。配对码从文件读、读完就删
（不放命令行、不打印）。名字已用过就拒绝。

**为什么要让出东西（影响已有档案，见 README「已有档案的上报范围」）**：CC 的「Express 位置」（``express_places``）和每个位置给哪种
成员读（``place_channels``）是**整台电脑一份、按通道（ERP = core / Cowork = cowork）分**，不按档案分；数据根也会在同一台电脑的
成员之间互相补（``worker._adopt_machine_roots``）。所以同一通道上两份档案分不开：新档案一启用，它会读到这条通道的全部位置，
原来那份同通道档案也会读到验收数据根。启用这份档案时只好：

1. 验收数据根加进位置清单、给上这条通道（ERP、Cowork 两份 maclab 档案共用同一个验收数据根时两种都给；最后一份拿走时去掉）；
2. 别的位置上去掉这条通道（另一条通道的勾不动）——记下去掉了哪几个，暂停 / 删除时原样加回；
3. 同一通道上别的 Express 档案暂停（例如连 testerp 的那份 ERP 档案）——记下暂停了哪几个，暂停 / 删除时恢复。

改配置前先停 CC、改完再启动（CC 运行中会自己写这个文件）；每次改前整份备份到 ``<状态目录>\\backup``；改完用 CC 自己的
``ProfileStore.load`` 读回校验，失败就还原备份。状态（名字 → 档案号、通道、让出了什么）记在 ``<状态目录>\\profiles.json``，
默认 ``C:\\PearnlyAcceptance\\maclab``。末行打印一行 JSON（不含密钥）。
"""
from __future__ import annotations

import base64
import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

APPDATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
CFG = APPDATA / "Pearnly" / "companion.json"
EXE = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Programs" / "CC" / "companion.exe"
STATE_DIR = Path(r"C:\PearnlyAcceptance\maclab")
CCSRC = Path(r"C:\PearnlyAcceptance\ccsrc")
# 配对要的第三方包（httpx 一族）单独放这里（scripts/acceptance/maclab_pc.py prepare 拷过来），不动测试机现有的 Python 环境
SITE = Path(r"C:\PearnlyAcceptance\maclab\site")
CHANNELS = ("cowork", "core")
PREFIXES = {"exp_": "cowork", "pcc_": "core"}
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?$")
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


# ── 纯函数（tests/test_maclab.py 离线测）────────────────────────────────────────────


def split_code(code: str) -> tuple[str, str]:
    """配对码 → (密钥, 站点 URL)；与 CC ``pairing.split_pairing_code`` 同一规则（单测逐例对照）。格式不对回 ("", "")。"""
    value = "".join(str(code or "").split())
    token, sep, host = value.rpartition("@")
    name = host.split(":")[0].lower()
    local = name in _LOCAL_HOSTS or name.endswith(".localhost")
    if not sep or not token.startswith(("exp_", "pcc_")) or not _HOST.match(host) or ("." not in name and not local):
        return "", ""
    return token, f"{'http' if local else 'https'}://{host.lower()}"


def channel_of(token: str) -> str:
    return next((name for prefix, name in PREFIXES.items() if str(token or "").startswith(prefix)), "")


def norm(path) -> str:
    """同 CC ``express_places.norm_root``：同一个文件夹的不同写法算一个。"""
    text = str(path or "").strip()
    return ntpath.normpath(text).lower().rstrip("\\/") if text else ""


def under(root, place) -> bool:
    child, parent = norm(root), norm(place)
    return bool(child) and bool(parent) and (child == parent or child.startswith(parent.rstrip("\\") + "\\"))


def _express(profile: dict) -> bool:
    return (profile.get("kind") or "express") != "folder"


def _profile(doc: dict, profile_id: str) -> dict:
    found = [item for item in doc.get("profiles") or [] if item.get("profile_id") == profile_id]
    if len(found) != 1:
        raise ValueError(f"档案 {profile_id} 有 {len(found)} 份，要恰好一份")
    return found[0]


def activate(doc: dict, profile_id: str, channel: str, root: str) -> tuple[dict, dict]:
    """启用这份档案（不改传入的那份）→ (新配置, 让出了什么)。见文件头 1–3。"""
    if channel not in CHANNELS:
        raise ValueError(f"认不出的通道 {channel}")
    out = json.loads(json.dumps(doc))
    box = out["global"]
    mine = _profile(out, profile_id)
    places = [str(place) for place in box.get("express_places") or []]
    # 位置按「在它下面」算（express_places.within）：验收数据根在别的位置里面（或反过来），那个位置的成员照样读到它，隔离不成立
    nested = [place for place in places if norm(place) != norm(root) and (under(root, place) or under(place, root))]
    if nested:
        raise ValueError(f"验收数据根 {root} 与已有位置 {nested} 互相包含：换一个不在任何位置里的本机文件夹")
    pairs = {norm(item[0]): [str(item[0]), [name for name in item[1] if name in CHANNELS]]
             for item in box.get("place_channels") or [] if isinstance(item, (list, tuple)) and len(item) == 2}
    added_root = not any(norm(place) == norm(root) for place in places)
    if added_root:
        places.append(root)
    # 验收数据根可以同时给两种通道（ERP、Cowork 两份 maclab 档案读同一本验收账套）：在它原有的通道上加这一种，不覆盖
    had = [] if added_root else pairs.get(norm(root), [root, list(CHANNELS)])[1]
    root_had_channel = channel in had
    stripped = []
    for place in places:
        key = norm(place)
        if key == norm(root):
            pairs[key] = [place, [name for name in CHANNELS if name in had or name == channel]]
            continue
        # 没登记的位置 CC 当「两种都给」（express_places.channels_of），所以也要显式登记成只给另一种
        current = pairs.get(key, [place, list(CHANNELS)])[1]
        if channel in current:
            stripped.append(place)
        pairs[key] = [place, [name for name in current if name != channel]]
    box["express_places"] = places
    box["place_channels"] = [pairs[norm(place)] for place in places]
    paused = []
    for item in out.get("profiles") or []:
        if item is mine or not _express(item) or (item.get("channel") or "cowork") != channel or item.get("paused"):
            continue
        item["paused"] = True
        paused.append(item["profile_id"])
    mine.update(paused=False, express_root=root, authorized_roots=[root])
    return out, {"added_root": added_root, "root_had_channel": root_had_channel, "stripped": stripped, "paused": paused}


def deactivate(doc: dict, profile_id: str, channel: str, root: str, undo: dict, *, drop: bool = False) -> dict:
    """把 :func:`activate` 让出来的还回去（不改传入的那份）：本档案暂停（``drop`` 时删掉），暂停过的同通道档案恢复，
    去掉过这条通道的位置加回来；验收数据根上这条通道是启用时加的就拿掉，拿掉后一种通道都不剩就把这个位置去掉、也从别的档案的
    授权根里去掉它下面的（CC 会在成员之间互相补数据根）。已经不在的档案 / 位置跳过。"""
    out = json.loads(json.dumps(doc))
    box = out["global"]
    mine = _profile(out, profile_id)
    # 配对时 CC 把全局 backend_url 改成了这份档案的地址（11-16 首跑 release 只差这一项）：还指着它就还回配对前的值
    if drop and "global_backend_url" in undo and box.get("backend_url") == mine.get("backend_url"):
        if undo["global_backend_url"] is None:
            box.pop("backend_url", None)
        else:
            box["backend_url"] = undo["global_backend_url"]
    if drop:
        out["profiles"] = [item for item in out["profiles"] if item is not mine]
    else:
        mine["paused"] = True
    for item in out.get("profiles") or []:
        if item.get("profile_id") in set(undo.get("paused") or []) and item is not mine:
            item["paused"] = False
    back = {norm(place) for place in undo.get("stripped") or []}
    places = [str(place) for place in box.get("express_places") or []]
    pairs = []
    for item in box.get("place_channels") or []:
        place, kinds = str(item[0]), [name for name in item[1] if name in CHANNELS]
        if norm(place) in back and channel not in kinds:
            kinds = [name for name in CHANNELS if name in kinds or name == channel]
        pairs.append([place, kinds])
    dropped = False
    if not undo.get("root_had_channel"):
        for pair in pairs:
            if norm(pair[0]) == norm(root) and channel in pair[1]:
                pair[1] = [name for name in pair[1] if name != channel]
                # 验收数据根上最后一种通道是这份档案拿走的：整个位置去掉（另一份 maclab 档案还在用时它还有那一种，不去）
                dropped = not pair[1]
    if dropped:
        places = [place for place in places if norm(place) != norm(root)]
        pairs = [pair for pair in pairs if norm(pair[0]) != norm(root)]
        for item in out.get("profiles") or []:
            if item is mine or not _express(item):
                continue
            item["authorized_roots"] = [path for path in item.get("authorized_roots") or [] if not under(path, root)]
    box["express_places"] = places
    box["place_channels"] = pairs
    return out


def import_paths(ccsrc: Path, site: Path, flat: bool, nested: bool, has_site: bool) -> list:
    """要放到 ``sys.path`` 最前面的目录（按顺序）：CC 源码副本（``ccsrc\\companion`` 优先——maclab_pc.py prepare 刷新的就是它，
    其次 ``ccsrc\\src\\companion``），再是第三方包目录（排在测试机自己的 site-packages 前面，版本以它为准）。"""
    out = [str(ccsrc)] if flat else [str(ccsrc / "src")] if nested else [str(ccsrc)]
    return out + ([str(site)] if has_site else [])


def view(doc: dict, names: dict | None = None) -> dict:
    """配置的摘要（不含密钥）。``names``：档案号 → 本脚本登记的名字。"""
    names = names or {}
    keys = ("channel", "kind", "paused", "backend_url", "express_root", "authorized_roots", "account_set")
    return {"places": doc.get("global", {}).get("express_places"), "channels": doc.get("global", {}).get("place_channels"),
            "profiles": [{"name": names.get(item.get("profile_id"), ""), "profile_id": item.get("profile_id"),
                          **{key: item.get(key) for key in keys}} for item in doc.get("profiles") or []]}


# ── 测试机上的动作 ────────────────────────────────────────────────────────────────


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    temp = path.with_name(path.name + ".maclab.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def cc_running() -> bool:
    listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq companion.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return any(line.startswith('"companion') for line in listed.splitlines())


def stop_cc() -> None:
    subprocess.run(["taskkill", "/IM", "companion.exe", "/F"], capture_output=True)
    time.sleep(3)


def start_cc() -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process '{EXE}' -ArgumentList '--background'"], capture_output=True)
    time.sleep(6)


class Box:
    """一次改配置：备份 → 停 CC → 改 → CC 自己读回校验（失败还原备份）→ 原来开着就再启动。"""

    def __init__(self, state_dir: Path, label: str):
        self.state_dir, self.label = state_dir, label
        self.was_running = False
        self.backup = None

    def __enter__(self):
        (self.state_dir / "backup").mkdir(parents=True, exist_ok=True)
        self.backup = self.state_dir / "backup" / f"companion-{self.label}-{int(time.time())}.json"
        shutil.copy2(CFG, self.backup)
        self.was_running = cc_running()
        if self.was_running:
            stop_cc()
        return self

    def __exit__(self, kind, error, trace):
        if error is not None:
            shutil.copy2(self.backup, CFG)
        else:
            try:
                from companion.profile_store import ProfileStore

                ProfileStore.load(CFG)  # CC 自己的读法：档案结构、每把密钥能解开、位置登记
            except Exception:
                shutil.copy2(self.backup, CFG)
                raise
        if self.was_running:
            start_cc()
        return False


def load_state(state_dir: Path) -> dict:
    path = state_dir / "profiles.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _write(state_dir / "profiles.json", state)


def add(state_dir: Path, name: str, code_file: Path, root: str, ledger: str) -> dict:
    state = load_state(state_dir)
    if name in state:
        raise SystemExit(f"已有名为 {name} 的档案：先 remove")
    code = code_file.read_text(encoding="utf-8").strip()
    code_file.unlink()   # 配对码读完就删，不留在磁盘上
    token, site = split_code(code)
    if not token:
        raise SystemExit("配对码格式不对")
    channel = channel_of(token)
    from companion import pairing
    from companion.account_probe import probe_account_sets
    from companion.profile_store import ProfileStore

    sets = probe_account_sets(root)
    chosen = [item for item in sets if norm(item.path) == norm(ledger)]
    if len(chosen) != 1:
        raise SystemExit(f"数据根 {root} 下认出 {len(sets)} 本账套，其中是 {ledger} 的有 {len(chosen)} 本，要恰好一本")
    with Box(state_dir, name):
        global_url = (_read(CFG).get("global") or {}).get("backend_url")   # 配对会把它改成本栈地址，删档案时还回去
        store = ProfileStore.load(CFG)
        cfg = store.new_runtime_config()
        result = pairing.pair(token=code, chosen=chosen[0], all_sets=sets, express_root=root, enable_autostart=False,
                              authorized_roots=[root], config=cfg, profile_store=store)
        if not result.ok:
            raise SystemExit(f"配对没成：{result.error_key}")
        changed, undo = activate(_read(CFG), cfg.profile_id, channel, root)
        undo["global_backend_url"] = global_url
        _write(CFG, changed)
    state[name] = {"profile_id": cfg.profile_id, "channel": channel, "site": site, "root": root, "ledger": ledger,
                   "active": True, "undo": undo}
    save_state(state_dir, state)
    return {"added": name, "site": site, "channel": channel, "account_sets": result.account_sets, "undo": undo}


def pause(state_dir: Path, name: str) -> dict:
    state = load_state(state_dir)
    entry = state.get(name) or {}
    if not entry:
        raise SystemExit(f"没有名为 {name} 的档案")
    if not entry.get("active"):
        return {"paused": name, "already": True}
    with Box(state_dir, name):
        _write(CFG, deactivate(_read(CFG), entry["profile_id"], entry["channel"], entry["root"], entry["undo"]))
    entry.update(active=False, undo={})
    save_state(state_dir, state)
    return {"paused": name}


def resume(state_dir: Path, name: str) -> dict:
    state = load_state(state_dir)
    entry = state.get(name) or {}
    if not entry:
        raise SystemExit(f"没有名为 {name} 的档案")
    if entry.get("active"):
        return {"resumed": name, "already": True}
    with Box(state_dir, name):
        changed, undo = activate(_read(CFG), entry["profile_id"], entry["channel"], entry["root"])
        _write(CFG, changed)
    entry.update(active=True, undo=undo)
    save_state(state_dir, state)
    return {"resumed": name, "undo": undo}


def _leave(profile_id: str) -> int:
    """能连上就按 CC 删成员时那样告诉服务端断开（作废这把密钥）；Mac 栈不在就跳过（站点那边下次 reset 时一起清）。"""
    try:
        import httpx
        from companion import channel
        from companion.profile_store import ProfileStore

        cfg = ProfileStore.load(CFG).runtime_config(profile_id)
        path = channel.path(cfg.bearer_token, "leave")
        response = httpx.post(cfg.backend_url.rstrip("/") + path, json={}, timeout=10,
                              headers={"Authorization": f"Bearer {cfg.bearer_token}"})
        return response.status_code
    except Exception:
        return 0


def remove(state_dir: Path, name: str) -> dict:
    state = load_state(state_dir)
    entry = state.get(name) or {}
    if not entry:
        raise SystemExit(f"没有名为 {name} 的档案")
    left = _leave(entry["profile_id"])
    with Box(state_dir, name):
        doc = _read(CFG)
        if any(item.get("profile_id") == entry["profile_id"] for item in doc.get("profiles") or []):
            undo = entry["undo"] if entry.get("active") else {"added_root": False, "stripped": [], "paused": []}
            _write(CFG, deactivate(doc, entry["profile_id"], entry["channel"], entry["root"], undo, drop=True))
    del state[name]
    save_state(state_dir, state)
    return {"removed": name, "leave_status": left}


def main(argv) -> int:
    args = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in argv]
    state_dir, ccsrc, site = STATE_DIR, CCSRC, SITE
    while args and args[0].startswith("--"):
        flag, value = args[0], args[1]
        args = args[2:]
        if flag == "--state":
            state_dir = Path(value)
        elif flag == "--ccsrc":
            ccsrc = Path(value)
        elif flag == "--site":
            site = Path(value)
    for entry in reversed(import_paths(ccsrc, site, (ccsrc / "companion").is_dir(), (ccsrc / "src" / "companion").is_dir(),
                                       site.is_dir())):
        sys.path.insert(0, entry)
    mode = args[0] if args else "show"
    if mode == "add":
        out = add(state_dir, args[1], Path(args[2]), args[3], args[4])
    elif mode in ("pause", "resume", "remove"):
        out = {"pause": pause, "resume": resume, "remove": remove}[mode](state_dir, args[1])
    else:
        out = {}
    state = load_state(state_dir)
    out["config"] = view(_read(CFG), {entry["profile_id"]: key for key, entry in state.items()})
    out["maclab"] = {key: {"active": bool(entry.get("active")), "channel": entry.get("channel"), "site": entry.get("site")}
                     for key, entry in state.items()}
    out["cc_running"] = cc_running()
    print(json.dumps(out, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
