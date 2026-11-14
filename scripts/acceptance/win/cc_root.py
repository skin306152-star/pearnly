"""测试机上 CC 桌面版的配置切换（以桌面用户跑）：让一种成员（ERP = core / Cowork = cowork）的档案只读验收专用数据根。

    python cc_root.py show
    python cc_root.py apply <core|cowork> <数据根> <验收账套完整路径>    备份 → 停 CC → 改 → 普通权限 --background 启动
    python cc_root.py restore                                            停 CC → 还原备份 → 启动；备份改名留档
    python cc_root.py restart                                            只重启（让 CC 把整份账套名单报给域名现在指向的站点）

**什么时候能跑由本机的 `cc_switch.py` 把关**：域名在测试机上连续多次解析到预期地址之后才 apply / restart（11-14 首跑：
切域名 1 分钟后就改配置重启，名单报到了旧指向）。已有备份时拒绝再 apply（先 restore）。不打印令牌、密钥。末行一行 JSON。
"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

APPDATA = Path.home() / "AppData"
CFG = APPDATA / "Roaming" / "Pearnly" / "companion.json"
EXE = APPDATA / "Local" / "Programs" / "CC" / "companion.exe"
KEEP = Path(r"C:\PearnlyAcceptance\cc-backup\companion-before-acceptance.json")
OTHER = {"core": "cowork", "cowork": "core"}


def switched(config: dict, channel: str, root: str, ledger: str) -> dict:
    """配置 → 这种成员只读验收专用数据根的配置（不改传入的那份）。原有位置全部改给另一种成员（它们的档案不动）；
    这种成员的档案必须恰好一个，位置、授权根、所选账套三处都只留验收的。"""
    out = json.loads(json.dumps(config))
    box = out["global"]
    same = lambda left, right: str(left).rstrip("\\/").casefold() == str(right).rstrip("\\/").casefold()  # noqa: E731
    olds = [place for place in box.get("express_places") or [] if not same(place, root)]
    box["express_places"] = olds + [root]
    box["place_channels"] = [[place, [OTHER[channel]]] for place in olds] + [[root, [channel]]]
    mine = [profile for profile in out["profiles"] if profile.get("channel") == channel and profile.get("kind", "express") == "express"]
    if len(mine) != 1:
        raise ValueError(f"{channel} 的 Express 档案有 {len(mine)} 个，要恰好一个")
    mine[0].update(authorized_roots=[root], express_root=root, account_dir=ledger, account_set=ledger, paused=False)
    return out


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8].upper()


def stop() -> None:
    subprocess.run(["taskkill", "/IM", "companion.exe", "/F"], capture_output=True)
    time.sleep(3)


def start() -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process '{EXE}' -ArgumentList '--background'"], capture_output=True)
    time.sleep(6)


def view() -> dict:
    data = json.loads(CFG.read_text(encoding="utf-8"))
    keys = ("endpoint_name", "channel", "paused", "express_root", "authorized_roots", "account_set", "pearnly_account")
    return {"hash": digest(CFG), "places": data["global"].get("express_places"), "channels": data["global"].get("place_channels"),
            "profiles": [{key: profile.get(key) for key in keys} for profile in data["profiles"]]}


def main(argv) -> int:
    mode = argv[0] if argv else "show"
    out = {"before": view()}
    if mode == "apply":
        if KEEP.exists():
            raise SystemExit("已有备份：先 restore")
        changed = switched(json.loads(CFG.read_text(encoding="utf-8")), argv[1], argv[2], argv[3])
        KEEP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CFG, KEEP)
        out["backup"] = digest(KEEP)
        stop()
        CFG.write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")
        start()
    elif mode == "restore":
        stop()
        shutil.copy2(KEEP, CFG)
        start()
        out["restored_from"] = digest(KEEP)
        KEEP.rename(KEEP.with_name(f"companion-restored-{int(time.time())}.json"))
    elif mode == "restart":
        stop()
        start()
    out["after"] = view()
    listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq companion.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    out["cc_pids"] = [line.split(",")[1].strip('"') for line in listed.splitlines() if line.startswith('"companion')]
    print(json.dumps(out, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    import base64

    sys.exit(main([base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv[1:]]))
