# -*- coding: utf-8 -*-
"""写盘前账套快照(直写回滚安全网):备份生成 / 磁盘守卫 / 保留 K + 失败快照不清。"""
from src.companion import pack_backup


def _account(tmp_path, name="test", files=("APTRN.DBF", "ARTRN.DBF")):
    d = tmp_path / "70EXP" / name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_bytes(b"x" * 1024)
    return d


# ── 模块 ─────────────────────────────────────────────────────
def test_dir_size_and_make_backup(tmp_path):
    acc = _account(tmp_path)
    root = tmp_path / "bak"
    assert pack_backup.dir_size(acc) == 2048
    snap = pack_backup.make_backup(acc, root)
    assert snap.exists() and snap.name.startswith("test_")
    assert sorted(p.name for p in snap.iterdir()) == ["APTRN.DBF", "ARTRN.DBF"]


def test_has_enough_disk(tmp_path):
    assert pack_backup.has_enough_disk(tmp_path, 1024) is True
    assert pack_backup.has_enough_disk(tmp_path, 10**18) is False  # 1 EB → 不够


def test_prune_keeps_recent_k_and_protects(tmp_path):
    acc = _account(tmp_path)
    root = tmp_path / "bak"
    snaps = []
    for _ in range(4):
        s = pack_backup.make_backup(acc, root)
        # 错开 mtime 保证排序确定
        import os
        import time as _t
        os.utime(s, (_t.time() + len(snaps), _t.time() + len(snaps)))
        snaps.append(s)
    # 保留最近 2 份,保护最旧一份(失败快照)→ 最旧不删,中间被删
    removed = pack_backup.prune(root, "test", keep=2, protect=[snaps[0]])
    remaining = {p.name for p in root.iterdir()}
    assert snaps[0].name in remaining  # 受保护
    assert snaps[-1].name in remaining and snaps[-2].name in remaining  # 最近 2 份
    assert removed  # 中间的被清
