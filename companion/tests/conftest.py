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

import datetime as _datetime  # noqa: E402
import types as _types  # noqa: E402


@pytest.fixture
def frozen_clock(monkeypatch):
    """把写入代码看到的「现在」钉在测试开始那一刻：companion 各模块里引用的 datetime 模块（``_dt`` / ``dt`` 等别名）换成
    ``now()`` / ``today()`` 恒回这一刻的版本，其余照真的。「两本副本各写一次再逐字节比」的测试必须用它——凭证明细的 CHGTIM 记
    录入时刻 HHMM（``dbf_schema.journal_stamp``、``dbf_stock``），两次写入跨了一分钟（跨午夜时连 CHGDAT）就多一处差异
    （11-23 K2 分支 CI：test_purchase_no_invoice_number 多出 GLJNLIT.DBF）。"""
    moment = _datetime.datetime.now().replace(microsecond=0)

    class _Now(_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.combine(moment.date(), moment.time(), tzinfo=tz)

    class _Today(_datetime.date):
        @classmethod
        def today(cls):
            return cls(moment.year, moment.month, moment.day)

    frozen = _types.ModuleType("datetime")
    frozen.__dict__.update({k: v for k, v in vars(_datetime).items() if not k.startswith("__")})
    frozen.datetime, frozen.date = _Now, _Today
    for name, module in list(sys.modules.items()):
        if name == "companion" or name.startswith("companion."):
            for attr, value in list(vars(module).items()):
                if value is _datetime:
                    monkeypatch.setattr(module, attr, frozen)
    return moment


# 真靶场夹具两侧共用一份(桥端在 bridge/tests/conftest.py 同样注册)。
from _lab_target import lab_acct  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _disable_cdx_runner(monkeypatch):
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: False)
    # 合成表没有 Express 结构化 CDX：测试打开「只改 DBF」的开发开关（正式包缺 runner 会拒写，见 test_reindex_unavailable）
    monkeypatch.setenv(cdx_reindex.DBF_FALLBACK_ENV, "1")
    monkeypatch.setattr(cdx_reindex, "_status_cache", {"value": "ok"})


@pytest.fixture(autouse=True)
def _no_real_windows_notifications(monkeypatch):
    """Windows 应用通知（toast.py）：测试里一律当作没装 windows-toasts（CI 的 Windows 上装了，别真弹系统通知），
    托盘照常退回气泡；专测发通知的用例自己放假模块。"""
    monkeypatch.setitem(sys.modules, "windows_toasts", None)


@pytest.fixture(autouse=True)
def _write_backups_in_tmp(tmp_path, monkeypatch):
    """写前快照放本机(dbf_writer._local_backup_root):测试里放进本次的临时目录,不落到机器的公共临时目录。"""
    monkeypatch.setenv("PEARNLY_WRITE_BACKUP_DIR", str(tmp_path / "write-backups"))


@pytest.fixture(autouse=True)
def _account_set_cache_in_tmp(tmp_path, monkeypatch):
    """上次扫到的账套清单(projection_refresh 本机缓存):测试里放进本次的临时目录。"""
    import importlib

    for name in ("companion.projection_refresh", "src.companion.projection_refresh"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(
            module, "_sets_cache_file", lambda profile_id: tmp_path / f"account_sets_{profile_id or 'default'}.json"
        )


@pytest.fixture(autouse=True)
def _account_index_in_tmp(tmp_path, monkeypatch):
    """账套清单本机索引（account_index）放进本次的临时目录，每个用例当作刚启动（内存里的索引清空）；
    问服务器在不在（scan_pace.GATE）换成一个不碰网络的：用例里的 ``\\\\server\\books`` 这类数据根本来就是假的（扫描读盘那几步
    都换成了桩），在真的 Windows 上去问会是「找不到网络路径」、整台跳过。专测关卡的用例自己放关卡与假的 stat。"""
    import importlib

    for name in ("companion", "src.companion"):
        try:
            index = importlib.import_module(name + ".account_index")
            pace = importlib.import_module(name + ".scan_pace")
        except ImportError:
            continue
        monkeypatch.setattr(index, "index_file", lambda: tmp_path / "account_index.json")
        index.INDEX._reset_for_tests()
        monkeypatch.setattr(pace, "GATE", pace.HostGate(lambda path: True))
    yield
    for name in ("companion", "src.companion"):
        try:
            importlib.import_module(name + ".account_index").INDEX._reset_for_tests()
        except ImportError:
            continue


@pytest.fixture(autouse=True)
def _folder_state_in_tmp(tmp_path, monkeypatch):
    """文件夹自动录入的本机状态（folder_store）：测试里放进本次的临时目录。"""
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folder_intake"))


@pytest.fixture(autouse=True)
def _task_journal_in_tmp(tmp_path, monkeypatch):
    """已写任务的本机记录（task_journal）：测试里放进本次的临时目录，任务号不在用例之间串。"""
    import importlib

    for name in ("companion.task_journal", "src.companion.task_journal"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(module, "FILE", tmp_path / "written_tasks.json")


@pytest.fixture(autouse=True)
def _update_state_in_tmp(tmp_path, monkeypatch):
    """自动更新的本机结果（update_block）：测试里放进本次的临时目录；本进程要上报的「被拦下」每个用例从空开始。"""
    import importlib

    monkeypatch.setenv("PEARNLY_UPDATE_STATE_FILE", str(tmp_path / "update_state.json"))
    for name in ("companion.update_block", "src.companion.update_block"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setitem(module._REPORT, "value", None)


@pytest.fixture(autouse=True)
def _device_identity_fresh(monkeypatch):
    """多台电脑（device_link / device_facts / device_key_backup）：设备密钥、网卡与文件号的进程内缓存每个用例从空开始；
    默认的认电脑对象不接托盘回调，也绝不用本机真的配置文件生成设备密钥。"""
    import importlib

    for name in ("companion", "src.companion"):
        try:
            link = importlib.import_module(name + ".device_link")
            facts = importlib.import_module(name + ".device_facts")
            backup = importlib.import_module(name + ".device_key_backup")
        except ImportError:
            continue
        # 设备密钥的注册表备份与系统标识：用例里一律不碰真的注册表（要测的用例自己换成假的）
        monkeypatch.setattr(backup, "default_backup", backup.NoBackup)
        monkeypatch.setattr(backup, "machine_id", lambda: "")
        link._reset_for_tests()
        facts._reset_for_tests()
        monkeypatch.setattr(link.ANNOUNCER, "on_result", None)
        monkeypatch.setattr(link.ANNOUNCER, "_memory", {})
        monkeypatch.setattr(link.ANNOUNCER, "_key", lambda: "")
        try:
            monkeypatch.setattr(importlib.import_module(name + ".worker"), "_uid_mismatch_seen", {})
        except ImportError:
            pass
    yield


@pytest.fixture(autouse=True)
def _account_items_marker_in_tmp(tmp_path, monkeypatch):
    """本机记「哪些账套补建过直接科目行商品」(product_master.BACKFILL_FILE):测试里放进本次的临时目录,不串到别的用例。"""
    import importlib

    for name in ("companion.product_master", "src.companion.product_master"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(module, "BACKFILL_FILE", tmp_path / "account_items.json")
        monkeypatch.setattr(module, "_backfill_pending", set())
