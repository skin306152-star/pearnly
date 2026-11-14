"""测试机上 CC 桌面版配置的「启用前 / 还原后」核对（以桌面用户跑，经验收体系的一次性计划任务；本机由
scripts/acceptance/maclab_pc.py 调用，不单独用）。只用标准库。

    python cfg_guard.py snapshot <目录>              启用前：companion.json 逐字节备份 + SHA-256 记进 <目录>（已有就不覆盖）
    python cfg_guard.py hash [<目录>]                现在的哈希（与启用前记下的）
    python cfg_guard.py check <目录> [<期望前 8 位>]  现在 vs 启用前：字节相同？语义（JSON 解析后）相同？不同的键路径（不带值）
    python cfg_guard.py restore <目录>               停 CC → 把启用前那份逐字节拷回 → 启动 CC → 隔一会儿再核两次哈希
    python cfg_guard.py heartbeat <秒>               重启 CC，等 companion.log 里重启之后的 [heartbeat] INFO 行（其后没有告警 / 错误）
    python cfg_guard.py finish <目录>                成功收尾：<目录> 改名留档（下次启用重新记）

末行打印一行 JSON（不含密钥：差异只列键路径，不列值）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

APPDATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
CFG = APPDATA / "Pearnly" / "companion.json"
LOG = APPDATA / "Pearnly" / "logs" / "companion.log"
EXE = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Programs" / "CC" / "companion.exe"
BAD_LEVELS = ("WARNING", "ERROR", "CRITICAL")
SETTLE_AFTER_INFO = 15   # 看到心跳 INFO 后再看这么久有没有告警
_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} \[(\w+)\] (\S+) :: (.*)$")


# ── 纯函数（tests/test_maclab_pc.py 离线测）────────────────────────────────────────


def digest(raw: bytes) -> str:
    """SHA-256 前 8 位大写（与 cc_root.py、验收记录同一写法）。"""
    return hashlib.sha256(raw).hexdigest()[:8].upper()


def diff_paths(left, right, path: str = "") -> list[str]:
    """两份 JSON 不同的键路径（``global.place_channels[1]``、``profiles[2].paused``），只有路径、不带值。"""
    if isinstance(left, dict) and isinstance(right, dict):
        out = []
        for key in list(left) + [key for key in right if key not in left]:
            where = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                out.append(where)
            else:
                out.extend(diff_paths(left[key], right[key], where))
        return out
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [f"{path}[len {len(left)}→{len(right)}]"]
        out = []
        for index, (a, b) in enumerate(zip(left, right)):
            out.extend(diff_paths(a, b, f"{path}[{index}]"))
        return out
    return [] if left == right and type(left) is type(right) else [path or "(根)"]


def compare(now_raw: bytes, before_raw: bytes, expect: str = "") -> dict:
    """现在的文件 vs 启用前的备份：哈希、是否逐字节相同、是否语义相同、差异键路径；给了 ``expect`` 时哈希也要等于它。"""
    now_hash, before_hash = digest(now_raw), digest(before_raw)
    out = {"hash": now_hash, "before_hash": before_hash, "bytes_equal": now_raw == before_raw}
    if expect:
        out["expect"] = expect.upper()
        out["expect_matches_before"] = before_hash == expect.upper()
    try:
        now, before = json.loads(now_raw.decode("utf-8")), json.loads(before_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        out.update(semantic_equal=False, diff=["(文件不是合法 JSON)"])
        return out
    paths = diff_paths(before, now)
    out.update(semantic_equal=not paths, diff=paths[:50])
    out["ok"] = out["bytes_equal"] and (not expect or now_hash == expect.upper())
    return out


def parse_log(lines) -> list[dict]:
    """companion.log 的行 → ``[{at, level, text}]``；没有时间戳的续行（异常堆栈）并进上一条。"""
    entries: list[dict] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        match = _LINE.match(line)
        if match:
            entries.append({"at": datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"), "level": match.group(2),
                            "text": match.group(4)})
        elif entries:
            entries[-1]["text"] += "\n" + line
    return entries


def heartbeat_verdict(entries: list[dict], since: datetime, now: datetime) -> dict:
    """重启之后（``since`` 起）的心跳结论：

    - ``failed``：重启后有 ERROR / CRITICAL，或有带 ``[heartbeat]`` 的 WARNING（例如「上报失败」）；
    - ``ok``：有一条 ``[heartbeat]`` 的 INFO，且它之后已经看了 :data:`SETTLE_AFTER_INFO` 秒、没有上面那些；
    - ``waiting``：还没有 INFO，或刚看到、还在观察期。"""
    after = [entry for entry in entries if entry["at"] >= since]
    bad = [entry for entry in after if entry["level"] in ("ERROR", "CRITICAL")
           or (entry["level"] in BAD_LEVELS and "[heartbeat]" in entry["text"])]
    beats = [entry for entry in after if entry["level"] == "INFO" and "[heartbeat]" in entry["text"]]
    if bad:
        return {"state": "failed", "lines": [f"{e['at']:%H:%M:%S} {e['level']} {e['text'].splitlines()[0][:160]}" for e in bad[:5]]}
    if beats and now - beats[0]["at"] >= timedelta(seconds=SETTLE_AFTER_INFO):
        return {"state": "ok", "beats": len(beats), "first": f"{beats[0]['at']:%H:%M:%S} {beats[0]['text'][:160]}"}
    return {"state": "waiting", "beats": len(beats)}


# ── 测试机上的动作 ────────────────────────────────────────────────────────────────


def cc_running() -> bool:
    listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq companion.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return any(line.startswith('"companion') for line in listed.splitlines())


def stop_cc() -> None:
    subprocess.run(["taskkill", "/IM", "companion.exe", "/F"], capture_output=True)
    time.sleep(3)


def start_cc() -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process '{EXE}' -ArgumentList '--background'"], capture_output=True)
    time.sleep(6)


def _before(folder: Path) -> bytes:
    path = folder / "companion.json"
    if not path.is_file():
        raise SystemExit(f"{path} 不存在：没有启用前的备份")
    return path.read_bytes()


def snapshot(folder: Path) -> dict:
    if (folder / "companion.json").is_file():
        return {"created": False, "before_hash": digest(_before(folder)), "hash": digest(CFG.read_bytes())}
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CFG, folder / "companion.json")
    value = digest(CFG.read_bytes())
    (folder / "hash.txt").write_text(value + "\n", encoding="ascii")
    return {"created": True, "before_hash": digest(_before(folder)), "hash": value}


def restore(folder: Path) -> dict:
    raw = _before(folder)
    was = cc_running()
    stop_cc()
    temp = CFG.with_name(CFG.name + ".maclab-restore.tmp")
    temp.write_bytes(raw)
    os.replace(temp, CFG)
    start_cc()
    seen = [digest(CFG.read_bytes())]
    for _ in range(2):   # CC 启动后会不会又改写：隔 10 秒再看两次
        time.sleep(10)
        seen.append(digest(CFG.read_bytes()))
    return {"was_running": was, "before_hash": digest(raw), "hashes": seen, "stable": len(set(seen)) == 1 and seen[0] == digest(raw)}


def heartbeat(limit: int) -> dict:
    stop_cc()
    since = datetime.now().replace(microsecond=0)
    start_cc()
    deadline = time.monotonic() + limit
    verdict = {"state": "waiting"}
    while time.monotonic() < deadline:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines() if LOG.is_file() else []
        verdict = heartbeat_verdict(parse_log(lines[-3000:]), since, datetime.now())
        if verdict["state"] != "waiting":
            break
        time.sleep(5)
    return {"since": f"{since:%Y-%m-%d %H:%M:%S}", **verdict, "hash_after": digest(CFG.read_bytes())}


def main(argv) -> int:
    mode = argv[0] if argv else "hash"
    if mode == "snapshot":
        out = snapshot(Path(argv[1]))
    elif mode == "hash":
        out = {"hash": digest(CFG.read_bytes())}
        if len(argv) > 1 and (Path(argv[1]) / "companion.json").is_file():
            out["before_hash"] = digest(_before(Path(argv[1])))
    elif mode == "check":
        out = compare(CFG.read_bytes(), _before(Path(argv[1])), argv[2] if len(argv) > 2 else "")
    elif mode == "restore":
        out = restore(Path(argv[1]))
    elif mode == "heartbeat":
        out = heartbeat(int(argv[1]) if len(argv) > 1 else 150)
    elif mode == "finish":
        folder = Path(argv[1])
        kept = folder.with_name(f"{folder.name}-done-{int(time.time())}")
        folder.rename(kept)
        out = {"kept": str(kept)}
    else:
        raise SystemExit(f"unknown mode {mode}")
    out["mode"] = mode
    out["cc_running"] = cc_running()
    print(json.dumps(out, ensure_ascii=True, default=str))  # 计划任务里标准输出是系统代码页：一律转义
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
