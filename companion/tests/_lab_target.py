# -*- coding: utf-8 -*-
r"""真靶场账套的「真写 → 整目录还原」夹具,桥端与小助手两侧共用一份。

为什么不能沿用 `PEARNLY_REAL_ACCT` 那套(copytree 到 tmp 再写):那是**只读副本**上的写,
证不了写路径在真实落点上的行为 —— 兄弟目录建不建得出、写互斥文件落在哪、CDX 是不是
Express 自己那份。四类新单据的真账验证一直只在镜像副本上做过,靶场空白就是这么来的。

怎么开:
    set PEARNLY_LAB_ACCT=D:\pearnly-erp-lab\_mirror_20260727_accserver\69EXP_test
没配 / 目录里没有 ISINFO.DBF → 整模块 skip(CI 机器上没有这块盘,不能因此变红)。

🛑 只许指向**自己的靶场副本**。用例会真的往里写单据,跑完按快照整目录还原;指向客户在用的
账套等于拿生产账套当测试台。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Dict, Optional

import pytest

ENV_VAR = "PEARNLY_LAB_ACCT"
# 事务收尾没删干净的写前快照(账套目录的兄弟)。夹具在还原时一并清掉并回报,
# 这样「备份目录漏删」不会静默攒在盘上,也不会污染下一条用例。
BACKUP_GLOB = "*_pearnly_bak_*"


def lab_dir() -> Optional[Path]:
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    d = Path(raw)
    return d if (d / "ISINFO.DBF").is_file() else None


requires_lab = pytest.mark.skipif(
    lab_dir() is None,
    reason=f"需要可写靶场账套:设 {ENV_VAR}=<含 ISINFO.DBF 的目录>(用例会真写并还原)",
)


def digest(d: Path) -> Dict[str, str]:
    """{相对路径: sha256}。还原干不干净靠它逐文件判,不靠「跑完没报错」。"""
    out: Dict[str, str] = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def snapshot(src: Path, dst: Path) -> Dict[str, str]:
    shutil.copytree(src, dst)
    return digest(src)


def restore(snap: Path, dst: Path) -> None:
    """按快照覆盖回去,并删掉快照里没有的文件(写互斥 .LCK、新建的表都算)。

    不对目标 rmtree —— 靶场是真目录,先删后拷中间任何一步崩掉就没有靶场了。
    """
    for src in sorted(snap.rglob("*")):
        if src.is_file():
            out = dst / src.relative_to(snap)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
    keep = {p.relative_to(snap) for p in snap.rglob("*") if p.is_file()}
    for p in sorted(dst.rglob("*")):
        if p.is_file() and p.relative_to(dst) not in keep:
            p.unlink()


def sweep_backup_dirs(account_dir: Path) -> list:
    """清掉账套的兄弟快照目录,返回清掉的名字(用例可以拿它断言「成功路径不留快照」)。"""
    hit = [p for p in sorted(account_dir.parent.glob(BACKUP_GLOB)) if p.is_dir()]
    for p in hit:
        shutil.rmtree(p, ignore_errors=True)
    return [p.name for p in hit]


@pytest.fixture
def lab_acct(tmp_path):
    """真靶场目录本身。用例结束整目录还原,并逐文件 sha256 证明还原干净。"""
    acct = lab_dir()
    assert acct is not None
    snap = tmp_path / "lab_snapshot"
    before = snapshot(acct, snap)
    try:
        yield acct
    finally:
        sweep_backup_dirs(acct)
        restore(snap, acct)
    assert digest(acct) == before, "靶场未还原干净"
