#!/usr/bin/env python3
"""Express 验收账套的整本快照、核对、还原、前后对比（验收测试标准 §5.1、§4.3）。

每次真写场景从同一个起点开始：先探测账套没人开着 → 恢复到带哈希清单的基线快照 → 逐文件核哈希 → 跑场景 →
与基线逐表逐行对比，核「预期变化以外逐字节不变」→ 按场景登记保留现场或恢复基线。

收编自 11-12 共享写入实验的备份还原脚本（两次整本还原 167 个文件哈希全部一致）、`companion/tests/_lab_target.py`
（不 rmtree 目标：先删后拷中间崩了就没有账套了）与 `scripts/express-operator/dbf_rawdiff.py`（按原始字节逐行比）。
只用标准库，测试机上的 Python 3.11 可直接跑；在测试机上由运行器经 ssh 调用，也可手工跑。

三道保护：
1. 只认测试账套：目标目录名必须在白名单里（TEST、TEST2、NLTTEST，或 PearnlyExpress 下 T 开头的套）；真实客户账套一律拒绝。
2. 还原只能还到快照来源的那一本（清单里记了账套名），不能把 TEST2 的基线盖到 TEST 上。
3. 快照与还原前逐文件独占试开（Windows）：Express 或别的程序开着就停，不动任何文件。

用法：
    python ledger.py probe    <账套目录>
    python ledger.py snapshot <账套目录> <快照目录> [--note 说明]
    python ledger.py verify   <账套目录> <快照目录>
    python ledger.py restore  <快照目录> <账套目录> [--quarantine <多出文件挪到这里>]
    python ledger.py copy     <账套目录> <目标目录>           # 只读拷一份现场
    python ledger.py rows     <目录> <表名> [--where 字段=值]  # 按单号读回
    python ledger.py diff     <前> <后> [--out 结果.json] [--allow 表名,表名] [--show 表名,表名]
    python ledger.py hold     <账套目录> <秒数>               # 像 Express 开着窗口那样共享开着 ISRUN 等几张表（造「Express 开着」现场）
退出码：0 = 成功 / 一致；1 = 不一致、被占用或保护拦下；2 = 用法错。输出一行 JSON（给运行器读）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import time
from pathlib import Path

MANIFEST = "manifest.json"
# 白名单：用户 11-12 授权可自由增删改写的测试套（记忆 express-test-ledgers-free）与测试机本机盘的 T 套
ALLOWED = re.compile(r"^(TEST|TEST2|NLTTEST)$", re.I)
SIDECARS = (".CDX", ".FPT", ".DBF")  # 一张表 = DBF + 索引 + 备注


class Refused(Exception):
    pass


def is_test_ledger(path: Path) -> bool:
    extra = os.environ.get("PEARNLY_LEDGER_ALLOW")  # 只给单测用：临时目录
    parts = [part.upper() for part in re.split(r"[\\/]+", str(path)) if part]
    local = "PEARNLYEXPRESS" in parts[:-1] and parts[-1].startswith("T")  # 测试机本机盘 C:\PearnlyExpress\…\T*
    return bool(ALLOWED.match(path.name) or local or (extra and re.search(extra, str(path))))


def files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def digest(root: Path, skip: tuple[str, ...] = ()) -> dict[str, dict]:
    """{相对路径（统一用 /）: {size, sha256}}。"""
    out = {}
    for path in files(root):
        rel = path.relative_to(root).as_posix()
        if rel not in skip:
            out[rel] = {"size": path.stat().st_size, "sha256": sha256(path)}
    return out


def busy_files(root: Path) -> list[str]:
    """逐文件独占试开，返回打不开的（有人开着）。非 Windows 上测不了，返回空（单测另行注入）。"""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    invalid = ctypes.cast(-1, wintypes.HANDLE).value
    busy = []
    for path in files(root):
        handle = create(str(path), 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ、不共享、OPEN_EXISTING
        if handle == invalid:
            if ctypes.get_last_error() in (32, 33):  # 共享冲突 / 锁冲突
                busy.append(path.relative_to(root).as_posix())
        else:
            kernel32.CloseHandle(handle)
    return busy


def _guard(ledger: Path, probe=busy_files) -> None:
    if not ledger.is_dir():
        raise Refused(f"账套目录不存在：{ledger}")
    if not is_test_ledger(ledger):
        raise Refused(f"{ledger} 不在测试账套白名单里（只认 TEST、TEST2、NLTTEST 与 PearnlyExpress 下 T 开头的套），不动")
    busy = probe(ledger)
    if busy:
        raise Refused(f"{ledger} 有 {len(busy)} 个文件被别的程序开着（如 {busy[0]}）：Express 或别的写入还开着，先关掉")


def snapshot(ledger: Path, target: Path, note: str = "", probe=busy_files) -> dict:
    _guard(ledger, probe)
    if target.exists():
        raise Refused(f"快照目录已存在：{target}（基线不覆盖；要重取就换一个名字）")
    started = time.time()
    shutil.copytree(ledger, target)
    listing = digest(target)
    source = digest(ledger)
    if listing != source:
        raise Refused("快照与来源不一致：拷贝期间账套被改动了，这份快照不能用")
    manifest = {"ledger_name": ledger.name, "source": str(ledger), "taken_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": note, "files": listing}
    (target / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "files": len(listing), "bytes": sum(item["size"] for item in listing.values()),
            "seconds": round(time.time() - started, 1), "ledger_name": ledger.name}


def load_manifest(snap: Path) -> dict:
    path = snap / MANIFEST
    if not path.is_file():
        raise Refused(f"{snap} 不是快照（没有 {MANIFEST}）")
    return json.loads(path.read_text(encoding="utf-8"))


def compare(ledger: Path, snap: Path) -> dict:
    """账套现状对快照清单：不一致、缺少、多出的文件。"""
    want, have = load_manifest(snap)["files"], digest(ledger)
    return {"mismatch": sorted(name for name in want if name in have and have[name] != want[name]),
            "missing": sorted(name for name in want if name not in have),
            "extra": sorted(name for name in have if name not in want)}


def verify(ledger: Path, snap: Path) -> dict:
    result = compare(ledger, snap)
    return {"ok": not any(result.values()), **result}


def restore(snap: Path, ledger: Path, quarantine: Path | None = None, probe=busy_files) -> dict:
    manifest = load_manifest(snap)
    if manifest["ledger_name"].upper() != ledger.name.upper():
        raise Refused(f"这份快照是 {manifest['ledger_name']} 的，不能还原到 {ledger.name}")
    _guard(ledger, probe)
    kept = digest(snap, skip=(MANIFEST,))
    if kept != manifest["files"]:
        raise Refused("快照自身与清单不一致（快照被动过），不拿它还原")
    started, before = time.time(), compare(ledger, snap)
    for rel in before["mismatch"] + before["missing"]:
        out = ledger / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap / rel, out)
    moved = []
    for rel in before["extra"]:  # 多出的文件（写互斥 .LCK、新建的表、写前备份）不直接删，挪走留查
        folder = quarantine or snap.parent / f"_extra-{ledger.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        out = folder / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(ledger / rel), str(out))
        moved.append(rel)
    after = compare(ledger, snap)
    return {"ok": not any(after.values()), "restored": len(before["mismatch"]) + len(before["missing"]), "moved_extra": moved,
            "files": len(manifest["files"]), "seconds": round(time.time() - started, 1), **after}


# ---- 前后对比（逐表逐行，按原始字节解字段；不依赖 dbf 库） ----

def _load_dbf(path: Path):
    data = path.read_bytes()
    count, header, width = struct.unpack("<IHH", data[4:12])
    offset, fields = 32, []
    while data[offset] != 0x0D:
        fields.append((data[offset:offset + 11].split(b"\0")[0].decode("latin1"), data[offset + 16], chr(data[offset + 11])))
        offset += 32
    rows = []
    for index in range(count):
        record = data[header + index * width: header + (index + 1) * width]
        position, values = 1, []
        for _name, length, kind in fields:
            raw = record[position:position + length]
            position += length
            if kind == "B" and length == 8:
                values.append(repr(round(struct.unpack("<d", raw)[0], 6)))
            elif kind == "I" and length == 4:
                values.append(str(struct.unpack("<i", raw)[0]))
            else:
                values.append(raw.decode("cp874", "replace").strip())
        rows.append((record[:1] == b"*", values))
    return [name for name, _, _ in fields], rows


def diff(before: Path, after: Path) -> dict:
    """两份账套（目录或快照）的差异：
    - files：按内容哈希，added / removed / changed（清单文件不算）；
    - tables：内容变了的 DBF 逐行——新增行（非空字段）、改动行（字段 旧→新）、删除标记变化、行数。"""
    left, right = digest(before, skip=(MANIFEST,)), digest(after, skip=(MANIFEST,))
    changed = sorted(name for name in left if name in right and left[name] != right[name])
    result = {"files": {"added": sorted(set(right) - set(left)), "removed": sorted(set(left) - set(right)), "changed": changed},
              "tables": {}}
    for rel in changed + result["files"]["added"]:
        if not rel.upper().endswith(".DBF"):
            continue
        fields_a, rows_a = _load_dbf(before / rel) if (before / rel).is_file() else ([], [])
        fields_b, rows_b = _load_dbf(after / rel)
        fields, rows = fields_b or fields_a, []
        for index in range(max(len(rows_a), len(rows_b))):
            old = rows_a[index] if index < len(rows_a) else None
            new = rows_b[index] if index < len(rows_b) else None
            if old == new:
                continue
            if old is None:
                rows.append({"row": index, "added": {f: v for f, v in zip(fields, new[1]) if v not in ("", "0", "0.0", "0.00", ".00")},
                             "deleted": new[0]})
            elif new is None:
                rows.append({"row": index, "gone": True})
            else:
                change = {f: [old[1][k], new[1][k]] for k, f in enumerate(fields) if k < len(old[1]) and old[1][k] != new[1][k]}
                if old[0] != new[0]:
                    change["_deleted"] = [old[0], new[0]]
                rows.append({"row": index, "changed": change})
        result["tables"][Path(rel).stem.upper()] = {"rows_before": len(rows_a), "rows_after": len(rows_b), "rows": rows}
    return result


def untouched(result: dict, allowed_tables: list[str]) -> list[str]:
    """账本不变式「预期变化以外逐字节不变」：返回不在允许表（连同它的索引、备注文件）里却变了的文件。"""
    allowed = {name.upper() for name in allowed_tables}
    stray = []
    for kind in ("added", "removed", "changed"):
        for rel in result["files"][kind]:
            path = Path(rel)
            if not (path.suffix.upper() in SIDECARS and path.stem.upper() in allowed):
                stray.append(f"{kind}:{rel}")
    return stray


def rows(folder: Path, table: str, where: str = "") -> dict:
    """按「字段=值」读一张表的行（值是去掉两端空格后的文本；数值字段照 diff 的写法）。只读。"""
    path = next((p for p in folder.iterdir() if p.name.upper() == f"{table.upper()}.DBF"), None)
    if path is None:
        raise Refused(f"{folder} 里没有 {table}.DBF")
    fields, records = _load_dbf(path)
    key, _, value = where.partition("=")
    found = [dict(zip(fields, values), _deleted=deleted) for deleted, values in records
             if not where or dict(zip(fields, values)).get(key.strip().upper()) == value.strip()]
    return {"ok": True, "rows": found}


HOLD_TABLES = ("ISRUN.DBF", "ARTRN.DBF", "ARRCPIT.DBF")
HOLD_MAX = 900


def hold(ledger: Path, seconds: int, sleep=time.sleep) -> dict:
    """造「Express 开着这本账套」的现场：照 Express 开着单据窗口时的样子**共享**打开几张表（Express 除主界面外的窗口都开着
    ISRUN，CC 桌面版写前逐张独占试开、拿不到就按「等账套」回报，`companion/account_lock.py`），开够 ``seconds`` 秒再关。
    只读、不改任何字节；只认测试账套；最长 :data:`HOLD_MAX` 秒（忘了关也会自己放开）。"""
    if not is_test_ledger(ledger):
        raise Refused(f"{ledger} 不在测试账套白名单里，不开")
    seconds = max(1, min(int(seconds), HOLD_MAX))
    names = [name for name in HOLD_TABLES if (ledger / name).is_file()]
    if not names:
        raise Refused(f"{ledger} 里没有 {HOLD_TABLES[0]}：不像一本 Express 账套")
    handles = [open(ledger / name, "rb") for name in names]  # noqa: SIM115 —— 要一直开着到时限，Python 默认共享读写打开（同 Express）
    started = time.time()
    try:
        sleep(seconds)
    finally:
        for handle in handles:
            handle.close()
    return {"ok": True, "held": names, "seconds": round(time.time() - started, 1)}


def copy(ledger: Path, target: Path) -> dict:
    """只读拷一份现场（跑完后对比、留证据用）；不要求没人开着，不动账套。"""
    if not is_test_ledger(ledger):
        raise Refused(f"{ledger} 不在测试账套白名单里，不读")
    if target.exists():
        raise Refused(f"目标已存在：{target}")
    shutil.copytree(ledger, target)
    return {"ok": True, "files": len(files(target))}


def main(argv=None) -> int:
    import base64

    argv = [base64.b64decode(arg[4:]).decode("utf-8") if arg.startswith("b64:") else arg
            for arg in (sys.argv[1:] if argv is None else argv)]  # 运行器经 ssh 传参用 base64
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe").add_argument("ledger", type=Path)
    one = sub.add_parser("snapshot")
    one.add_argument("ledger", type=Path)
    one.add_argument("snap", type=Path)
    one.add_argument("--note", default="")
    two = sub.add_parser("verify")
    two.add_argument("ledger", type=Path)
    two.add_argument("snap", type=Path)
    three = sub.add_parser("restore")
    three.add_argument("snap", type=Path)
    three.add_argument("ledger", type=Path)
    three.add_argument("--quarantine", type=Path)
    five = sub.add_parser("copy")
    five.add_argument("ledger", type=Path)
    five.add_argument("target", type=Path)
    six = sub.add_parser("rows")
    six.add_argument("folder", type=Path)
    six.add_argument("table")
    six.add_argument("--where", default="")
    seven = sub.add_parser("hold")
    seven.add_argument("ledger", type=Path)
    seven.add_argument("seconds", type=int)
    four = sub.add_parser("diff")
    four.add_argument("before", type=Path)
    four.add_argument("after", type=Path)
    four.add_argument("--out", type=Path)
    four.add_argument("--allow", default=None, help="只允许这些表变化（逗号分隔）；给了就核「其余逐字节不变」")
    four.add_argument("--show", default="", help="这些表（逗号分隔）的逐行变化直接带在结果里（场景按新增行核单号、金额）")
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            busy = busy_files(args.ledger)
            out = {"ok": not busy, "busy": busy, "supported": sys.platform == "win32"}
        elif args.command == "snapshot":
            out = snapshot(args.ledger, args.snap, args.note)
        elif args.command == "verify":
            out = verify(args.ledger, args.snap)
        elif args.command == "restore":
            out = restore(args.snap, args.ledger, args.quarantine)
        elif args.command == "copy":
            out = copy(args.ledger, args.target)
        elif args.command == "rows":
            out = rows(args.folder, args.table, args.where)
        elif args.command == "hold":
            out = hold(args.ledger, args.seconds)
        else:
            result = diff(args.before, args.after)
            if args.out:
                args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            out = {"ok": True, "files": {kind: len(names) for kind, names in result["files"].items()},
                   "tables": {name: [table["rows_before"], table["rows_after"], len(table["rows"])] for name, table in result["tables"].items()}}
            if args.show:
                out["shown"] = {name.upper(): result["tables"].get(name.upper(), {}).get("rows", []) for name in args.show.split(",") if name}
            if args.allow is not None:
                out["stray"] = untouched(result, [name for name in args.allow.split(",") if name])
                out["ok"] = not out["stray"]
    except Refused as error:
        print(json.dumps({"ok": False, "refused": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
