"""测试默认禁用 Harbour cdx_reindex_runner。

写盘测试用 python `dbf` 合成表,没有 Express 结构化 CDX 标签,真跑 runner 会回查失败。
默认让写盘走「runner 不在 → 回落 dbf reindex」路(= 引入 V3 闭环前的行为),专测 runner 的
用例自行 patch `cdx_reindex.runner_available` 打开。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from companion import cdx_reindex  # noqa: E402

# 真靶场夹具两侧共用一份(桥端在 bridge/tests/conftest.py 同样注册)。
from _lab_target import lab_acct  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _disable_cdx_runner(monkeypatch):
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: False)


@pytest.fixture(autouse=True)
def _write_backups_in_tmp(tmp_path, monkeypatch):
    """写前快照放本机(dbf_writer._local_backup_root):测试里放进本次的临时目录,不落到机器的公共临时目录。"""
    monkeypatch.setenv("PEARNLY_WRITE_BACKUP_DIR", str(tmp_path / "write-backups"))
