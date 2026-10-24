# -*- coding: utf-8 -*-
"""payload_version 协商在 worker 层的落地:

- 不支持的版本 → 诚实失败(ERR_PAYLOAD_VERSION),绝不碰 secret_store/DBF 写路径。
- 支持的版本(含缺失视同 1)→ 放行,继续走原有流程。
- 心跳随 max_payload_version + companion_version 上报。
"""

import time
import types
import threading
from unittest import mock

from src.companion.secret_store import ExpressSettings

from src.companion import worker
from src.companion.version import VERSION


class _FakeQC:
    def __init__(self):
        self.failures = []
        self.successes = []
        self.health_calls = []

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.failures.append(
            {
                "task_id": task_id,
                "error": error,
                "error_code": error_code,
                "outcome": outcome,
                "meta": meta,
            }
        )

    def ack_success(
        self, task_id, extracted_invoice_no=None, line_modes=None, meta=None
    ):
        self.successes.append(task_id)

    def health(self, **kwargs):
        self.health_calls.append(kwargs)
        return {}


def _task(payload):
    return types.SimpleNamespace(
        id="t1", payload=payload, invoice_no="INV1", attempt_count=0
    )


def _cfg():
    return types.SimpleNamespace(default_depcod="", ui_lang="en")


def test_unsupported_version_fails_honestly_zero_writes():
    qc = _FakeQC()
    task = _task({"payload_version": 6, "direction": "purchase"})
    with (
        mock.patch(
            "src.companion.secret_store.load_express_settings",
            side_effect=AssertionError("must not read settings"),
        ),
        mock.patch(
            "src.companion.dbf_writer.write_purchase",
            side_effect=AssertionError("must not write DBF"),
        ),
        mock.patch(
            "src.companion.dbf_sales.write_sale",
            side_effect=AssertionError("must not write DBF"),
        ),
    ):
        worker._handle_dbf_task(_cfg(), qc, task)

    assert len(qc.failures) == 1
    fail = qc.failures[0]
    assert fail["error_code"] == "ERR_PAYLOAD_VERSION"
    assert fail["meta"] == {"got": 6, "supported": [1, 2, 3, 4, 5]}
    # 原文会存进推送原因、交给 CC：只用正式名字（2026-10-13 起旧称不出现在用户看得到的地方）
    assert "CC Desktop" in fail["error"] and "companion" not in fail["error"].lower()
    assert qc.successes == []


def test_supported_version_passes_gate_and_proceeds():
    qc = _FakeQC()
    # payload_version 缺失 → 视同 1 → 应放行,继续走到下一关(账套未选 → 另一个错误码,
    # 不是 ERR_PAYLOAD_VERSION),证明网关没把它误拦。
    task = _task({"direction": "purchase"})
    fake_settings = types.SimpleNamespace(has_selection=lambda: False)
    with mock.patch(
        "src.companion.secret_store.load_express_settings", return_value=fake_settings
    ):
        worker._handle_dbf_task(_cfg(), qc, task)

    assert len(qc.failures) == 1
    assert qc.failures[0]["error_code"] == "NO_ACCOUNT_SELECTION"


def test_heartbeat_reports_max_payload_version_and_companion_version():
    qc = _FakeQC()
    cfg = types.SimpleNamespace(
        heartbeat_interval_s=60,
        method="dbf",
        account_set="",
        account_dir="",
        account_company="",
        account_set_row=0,
        revenue_acc="",
        ar_acc="",
        vat_output_acc="",
        fallback_acc="",
        ap_acc="",
        vat_input_acc="",
        resolved_express_root=lambda: "",
        backend_url="https://x",
    )
    state = {"last_heartbeat": 0.0, "last_update_check": time.time()}

    worker._run_periodic(cfg, qc, state, on_update_available=None)

    assert len(qc.health_calls) == 1
    call = qc.health_calls[0]
    assert call["max_payload_version"] == 5
    assert call["companion_version"] == VERSION
    # 头一次启动还没扫完：账套照旧报空，科目不报（报「0 个」服务端会盖掉上一份）
    assert call["account_sets"] == [] and call["accounts"] is None

    state.update(last_heartbeat=0.0, reported_account_sets=[{"path": "A"}], reported_accounts=[{"code": "1"}])
    worker._run_periodic(cfg, qc, state, on_update_available=None)
    assert qc.health_calls[-1]["account_sets"] == [{"path": "A"}]
    assert qc.health_calls[-1]["accounts"] == [{"code": "1"}]


def test_heartbeat_updates_safe_connection_identity():
    saved = []

    class Config(types.SimpleNamespace):
        def update(self, **changes):
            for key, value in changes.items():
                setattr(self, key, value)
            return self

        def save(self):
            saved.append((self.endpoint_id, self.endpoint_name, self.pearnly_account))

    cfg = Config(endpoint_id="", endpoint_name="", pearnly_account="")
    worker._sync_connection_identity(
        cfg,
        {
            "connection": {
                "endpoint_id": "endpoint-a",
                "endpoint_name": "Cowork Express",
                "pearnly_account": "owner@example.com",
            }
        },
    )

    assert saved == [("endpoint-a", "Cowork Express", "owner@example.com")]


def test_slow_catalog_scan_never_blocks_heartbeat_or_polling():
    from src.companion import catalog_refresh

    catalog_refresh._reset_for_tests()
    started = threading.Event()
    release = threading.Event()

    def slow_catalog(_account_dir):
        started.set()
        release.wait(2)
        return {"products": [{"code": "P1"}]}

    qc = _FakeQC()
    cfg = types.SimpleNamespace(
        heartbeat_interval_s=60,
        method="dbf",
        account_set="TEST",
        account_dir=r"\\server\ACCOUNT\70EXP\TEST",
        account_company="Test",
        account_set_row=1,
        revenue_acc="",
        ar_acc="",
        vat_output_acc="",
        fallback_acc="",
        ap_acc="",
        vat_input_acc="",
        resolved_express_root=lambda: r"\\server\ACCOUNT\70EXP",
        backend_url="https://x",
    )
    state = {"last_heartbeat": 0.0, "last_update_check": time.time()}
    with (
        mock.patch(
            "src.companion.master_refresh.request_master", return_value=(None, 0.0)
        ),
        mock.patch.object(catalog_refresh, "catalog_payload", side_effect=slow_catalog),
    ):
        before = time.monotonic()
        worker._run_periodic(cfg, qc, state, on_update_available=None)
        elapsed = time.monotonic() - before
        # 后台线程什么时候真去调目录扫描由解释器排：等它调到之前桩一直在（离开 with 就换回真的扫描，后台线程会读到真的）
        assert started.wait(1)

    assert elapsed < 0.2
    assert qc.health_calls[0]["catalog"] is None
    assert state.get("last_catalog", 0.0) == 0.0
    release.set()


def test_cloud_refresh_request_forces_catalog_and_acknowledges_request():
    from src.companion import catalog_refresh

    qc = _FakeQC()
    cfg = types.SimpleNamespace(
        heartbeat_interval_s=60,
        method="dbf",
        account_set=r"C:\ACCOUNT\70EXP\TEST",
        account_dir=r"C:\ACCOUNT\70EXP\TEST",
        account_company="Test",
        account_set_row=1,
        revenue_acc="",
        ar_acc="",
        vat_output_acc="",
        fallback_acc="",
        ap_acc="",
        vat_input_acc="",
        resolved_express_root=lambda: r"C:\ACCOUNT\70EXP",
        backend_url="https://x",
    )
    requested_account = r"C:\ACCOUNT\70EXP\NEW"
    state = {
        "last_heartbeat": 0.0,
        "last_update_check": time.time(),
        "last_catalog": time.time(),
        "reported_account_sets": [
            {"path": cfg.account_dir, "writable": True},
            {"path": requested_account, "writable": True},
        ],
        "master_refresh_received_at": time.time() - 1,
        "master_refresh_request": {
            "request_id": "request-1",
            "scope_kind": "account_set",
            "account_set_key": requested_account.lower(),
        },
    }
    with (
        mock.patch(
            "src.companion.master_refresh.request_master", return_value=(None, 0.0)
        ),
        mock.patch.object(
            catalog_refresh,
            "request_target_catalog",
            return_value=(
                {
                    "catalog": {"products": [{"code": "P1"}]},
                    "accounts": [{"code": "1100"}],
                },
                time.time() + 1,
            ),
        ) as request_target_catalog,
    ):
        worker._run_periodic(cfg, qc, state, on_update_available=None)

    request_target_catalog.assert_called_once_with(
        requested_account,
        newer_than=mock.ANY,
    )
    heartbeat = qc.health_calls[0]
    assert heartbeat["catalog"] == {"products": [{"code": "P1"}]}
    assert heartbeat["accounts"] == [{"code": "1100"}]
    assert heartbeat["master_refresh_request_id"] == "request-1"
    assert heartbeat["master_refresh_scope"] == "account_set"
    assert heartbeat["master_refresh_account_set"] == requested_account.lower()
    assert "master_refresh_request" not in state


def test_endpoint_refresh_forces_account_scan_and_acknowledges_request():
    qc = _FakeQC()
    cfg = types.SimpleNamespace(
        heartbeat_interval_s=60,
        method="dbf",
        account_set=r"C:\ACCOUNT\70EXP\TEST",
        account_dir=r"C:\ACCOUNT\70EXP\TEST",
        account_company="Test",
        account_set_row=1,
        revenue_acc="",
        ar_acc="",
        vat_output_acc="",
        fallback_acc="",
        ap_acc="",
        vat_input_acc="",
        resolved_express_root=lambda: r"C:\ACCOUNT\70EXP",
        backend_url="https://x",
    )
    refreshed = {
        "account_sets": [{"path": cfg.account_dir, "writable": True}],
    }
    state = {
        "last_heartbeat": time.time(),
        "last_update_check": time.time(),
        "reported_account_sets": [
            {
                "path": cfg.account_dir,
                "company": "Legal Name",
                "mapping": {"revenue_acc": "4100"},
            }
        ],
        "reported_accounts": [{"code": "1100"}],
        "master_refresh_request": {
            "request_id": "request-2",
            "scope_kind": "endpoint",
            "account_set_key": "@endpoint",
        },
    }
    with mock.patch(
        "src.companion.master_refresh.request_master",
        return_value=(refreshed, time.time() + 1),
    ) as request_master:
        worker._run_periodic(cfg, qc, state, on_update_available=None)

    request_master.assert_called_once_with(
        cfg.resolved_express_root(),
        cfg.account_dir,
        newer_than=mock.ANY,
        force=True,
        account_sets_only=True,
        authorized_roots=[],
        discover=(),  # 新年度只在「Express 位置」下面找（用户 10-24）；这个配置没有位置清单
    )
    heartbeat = qc.health_calls[0]
    assert heartbeat["account_sets"][0]["path"] == cfg.account_dir
    assert heartbeat["account_sets"][0]["writable"] is True
    assert heartbeat["account_sets"][0]["company"] == "Legal Name"
    assert heartbeat["account_sets"][0]["mapping"] == {"revenue_acc": "4100"}
    assert heartbeat["master_refresh_request_id"] == "request-2"
    assert heartbeat["master_refresh_scope"] == "endpoint"


def test_endpoint_refresh_failure_is_acknowledged_instead_of_staying_pending():
    qc = _FakeQC()
    cfg = types.SimpleNamespace(
        heartbeat_interval_s=60,
        method="dbf",
        account_set="TEST",
        account_dir=r"C:\ACCOUNT\70EXP\TEST",
        account_company="Test",
        account_set_row=1,
        revenue_acc="",
        ar_acc="",
        vat_output_acc="",
        fallback_acc="",
        ap_acc="",
        vat_input_acc="",
        resolved_express_root=lambda: r"C:\ACCOUNT\70EXP",
        backend_url="https://x",
    )
    state = {
        "last_heartbeat": time.time(),
        "last_update_check": time.time(),
        "master_refresh_request": {
            "request_id": "request-failed",
            "scope_kind": "endpoint",
            "account_set_key": "@endpoint",
        },
    }
    with mock.patch.object(
        worker.projection_refresh,
        "collect_endpoint",
        return_value=(None, time.time(), "ERR_MASTER_SCAN_FAILED"),
    ):
        worker._run_periodic(cfg, qc, state, on_update_available=None)

    heartbeat = qc.health_calls[0]
    assert heartbeat["master_refresh_request_id"] == "request-failed"
    assert heartbeat["master_refresh_error"] == "ERR_MASTER_SCAN_FAILED"


def test_opening_items_maps_key_to_name_and_parses_date():
    """补期初透传行:Pearnly 键 `key`(品名)→ `name`;日期字符串 → date;空名行丢弃。"""
    import datetime

    out = worker._opening_items(
        {
            "opening_stock": [
                {"key": "ข้าวหอม", "qty": 50, "unit_cost": 4.5, "date": "2026-01-01"},
                {
                    "key": "   ",
                    "qty": 1,
                    "unit_cost": 1,
                    "date": "2026-01-01",
                },  # 空名 → 丢
            ]
        }
    )
    assert len(out) == 1
    assert out[0]["name"] == "ข้าวหอม"
    assert out[0]["qty"] == 50 and out[0]["unit_cost"] == 4.5
    assert out[0]["date"] == datetime.date(2026, 1, 1)


def test_opening_items_none_when_absent_and_tolerates_bad_date():
    """无 opening_stock / 空列表 → None(常规过账);坏日期 → date=None 不炸,仍映射该行。"""
    assert worker._opening_items({}) is None
    assert worker._opening_items({"opening_stock": []}) is None
    out = worker._opening_items(
        {
            "opening_stock": [
                {"key": "x", "qty": 1, "unit_cost": 0, "date": "not-a-date"}
            ]
        }
    )
    assert out[0]["date"] is None and out[0]["name"] == "x"


def test_task_account_is_resolved_from_local_root_registry():
    current = ExpressSettings(
        method="dbf",
        express_root=r"S:\\2569\\EXP69",
        express_exe="",
        account_dir=r"S:\\2569\\EXP69\\OLD",
        account_company="Old Co",
        account_set=r"S:\\2569\\EXP69\\OLD",
        account_set_row=1,
    )
    selected = types.SimpleNamespace(
        path=r"S:\\2569\\EXP69\\ACME",
        company="ACME Co",
        row=4,
        writable=True,
    )
    with (
        mock.patch(
            "src.companion.secret_store.load_express_settings", return_value=current
        ),
        mock.patch(
            "src.companion.express_locate.candidate_roots",
            return_value=[r"S:\\2569\\EXP69"],
        ),
        mock.patch(
            "src.companion.account_probe.probe_account_sets", return_value=[selected]
        ),
    ):
        settings = worker._task_settings(
            types.SimpleNamespace(),
            {
                "account_root": r"S:\\2569\\EXP69",
                "account_set": r"S:\\2569\\EXP69\\ACME",
            },
        )

    assert settings.account_dir == selected.path
    assert settings.account_company == "ACME Co"
    assert settings.account_set_row == 4


def test_task_account_rejects_unreported_root():
    current = ExpressSettings(
        method="dbf",
        express_root=r"S:\\2569\\EXP69",
        express_exe="",
        account_dir=r"S:\\2569\\EXP69\\OLD",
        account_company="Old Co",
        account_set=r"S:\\2569\\EXP69\\OLD",
        account_set_row=1,
    )
    with (
        mock.patch(
            "src.companion.secret_store.load_express_settings", return_value=current
        ),
        mock.patch("src.companion.express_locate.candidate_roots", return_value=[]),
    ):
        settings = worker._task_settings(
            types.SimpleNamespace(),
            {"account_root": r"D:\\OTHER", "account_set": r"D:\\OTHER\\ACME"},
        )

    assert settings is None


def test_task_account_without_root_only_accepts_the_profile_default():
    current = ExpressSettings(
        method="dbf",
        express_root=r"S:\\2569\\EXP69",
        express_exe="",
        account_dir=r"S:\\2569\\EXP69\\OLD",
        account_company="Old Co",
        account_set=r"S:\\2569\\EXP69\\OLD",
        account_set_row=1,
    )
    with mock.patch(
        "src.companion.secret_store.load_express_settings", return_value=current
    ):
        default = worker._task_settings(
            types.SimpleNamespace(), {"account_set": r"s:/2569/exp69/old/"}
        )
        changed = worker._task_settings(
            types.SimpleNamespace(), {"account_set": r"S:\\2568\\EXP68\\ACME"}
        )

    assert default is current
    assert changed is None


def test_task_account_accepts_authorized_non_default_root():
    current = ExpressSettings(
        method="dbf",
        express_root=r"S:\\2569\\EXP69",
        express_exe="",
        account_dir=r"S:\\2569\\EXP69\\OLD",
        account_company="Old Co",
        account_set=r"S:\\2569\\EXP69\\OLD",
        account_set_row=1,
    )
    selected = types.SimpleNamespace(
        path=r"S:\\2568\\EXP68\\ACME",
        company="ACME 2025",
        row=2,
        writable=True,
    )
    cfg = types.SimpleNamespace(authorized_roots=[r"S:\\2568\\EXP68"])
    with (
        mock.patch(
            "src.companion.secret_store.load_express_settings", return_value=current
        ),
        mock.patch(
            "src.companion.account_probe.probe_account_sets", return_value=[selected]
        ) as probe,
    ):
        settings = worker._task_settings(
            cfg,
            {
                "account_root": r"s:/2568/exp68/",
                "account_set": r"s:/2568/exp68/acme/",
            },
        )

    probe.assert_called_once_with(r"S:\\2568\\EXP68")
    assert settings.account_dir == selected.path
    assert settings.express_root == r"S:\\2568\\EXP68"


def test_task_account_rejects_read_only_account_in_authorized_root():
    current = ExpressSettings(
        method="dbf",
        express_root=r"S:\\2569\\EXP69",
        express_exe="",
        account_dir=r"S:\\2569\\EXP69\\OLD",
        account_company="Old Co",
        account_set=r"S:\\2569\\EXP69\\OLD",
        account_set_row=1,
    )
    selected = types.SimpleNamespace(
        path=r"S:\\2568\\EXP68\\ACME",
        company="ACME 2025",
        row=2,
        writable=False,
    )
    cfg = types.SimpleNamespace(authorized_roots=[r"S:\\2568\\EXP68"])
    with (
        mock.patch(
            "src.companion.secret_store.load_express_settings", return_value=current
        ),
        mock.patch(
            "src.companion.account_probe.probe_account_sets", return_value=[selected]
        ),
        mock.patch("os.path.isdir", return_value=True),  # 文件夹在、就是不可写 → 要人处理
    ):
        settings = worker._task_settings(
            cfg,
            {
                "account_root": r"S:\\2568\\EXP68",
                "account_set": selected.path,
            },
        )

    assert settings is None


def _acme_current():
    return ExpressSettings(
        method="dbf",
        express_root=r"S:\\2569\\EXP69",
        express_exe="",
        account_dir=r"S:\\2569\\EXP69\\OLD",
        account_company="Old Co",
        account_set=r"S:\\2569\\EXP69\\OLD",
        account_set_row=1,
    )


ACME_TASK = {"account_root": r"S:\\2569\\EXP69", "account_set": r"S:\\2569\\EXP69\\ACME"}


def test_a_share_that_hiccups_is_probed_again_instead_of_failing():
    # 2026-09-28 录制：网络共享卡了一下，下一张被判「账套不可用」直接失败
    selected = types.SimpleNamespace(
        path=r"S:\\2569\\EXP69\\ACME", company="ACME Co", row=4, writable=True
    )
    probe = mock.Mock(side_effect=[OSError("network name no longer available"), [selected]])
    with (
        mock.patch("src.companion.secret_store.load_express_settings", return_value=_acme_current()),
        mock.patch("src.companion.account_probe.probe_account_sets", probe),
        mock.patch.object(worker.time, "sleep") as sleep,
    ):
        settings = worker._task_settings(types.SimpleNamespace(), ACME_TASK)

    assert settings.account_dir == selected.path
    assert probe.call_count == 2 and sleep.call_count == 1


def test_an_account_set_still_unreachable_waits_instead_of_failing():
    qc = _FakeQC()
    unreachable = types.SimpleNamespace(
        path=r"S:\\2569\\EXP69\\ACME", company="ACME Co", row=4, writable=False
    )
    with (
        mock.patch("src.companion.secret_store.load_express_settings", return_value=_acme_current()),
        mock.patch("src.companion.account_probe.probe_account_sets", return_value=[unreachable]),
        mock.patch("os.path.isdir", return_value=False),
        mock.patch.object(worker.time, "sleep"),
        mock.patch("src.companion.dbf_writer.write_purchase", side_effect=AssertionError("must not write DBF")),
    ):
        outcome = worker._handle_dbf_task(_cfg(), qc, _task({"direction": "purchase", **ACME_TASK}))

    assert outcome == "waiting_lock"
    assert [(f["error_code"], f["outcome"]) for f in qc.failures] == [("ACCOUNT_SET_UNREACHABLE", "waiting_lock")]


def test_an_account_set_missing_on_this_pc_fails_for_a_person_to_fix():
    qc = _FakeQC()
    with (
        mock.patch("src.companion.secret_store.load_express_settings", return_value=_acme_current()),
        mock.patch("src.companion.account_probe.probe_account_sets", return_value=[]),
        mock.patch.object(worker.time, "sleep", side_effect=AssertionError("no wait for a missing set")),
    ):
        outcome = worker._handle_dbf_task(_cfg(), qc, _task({"direction": "purchase", **ACME_TASK}))

    assert outcome == "failed"
    assert [(f["error_code"], f["outcome"]) for f in qc.failures] == [("NO_ACCOUNT_SELECTION", None)]


def test_newly_found_year_folders_are_remembered_as_authorized(tmp_path):
    """「重新读取」找到的新年度文件夹记进授权名单,已有的不重复记。"""
    saved = []

    class Cfg:
        def update(self, **changes):
            saved.append(changes)
            return self

        def save(self):
            return None

    worker._remember_roots(Cfg(), [r"C:\ACCOUNT\69EXP"], [r"c:\account\69exp\\", r"C:\ACCOUNT\70EXP"])
    worker._remember_roots(Cfg(), [r"C:\ACCOUNT\69EXP"], None)
    assert saved == [{"authorized_roots": [r"C:\ACCOUNT\69EXP", r"C:\ACCOUNT\70EXP"]}]


def test_heartbeat_sends_the_account_lists_only_when_they_change():
    """心跳瘦身（2026-10-08）：服务端回过 `account_sets_unchanged_ok` 后，账套名单与科目表没变的那几拍只报在线；
    变了、满 30 分钟、网页要求重新读取时照旧整份上传。服务端没回这一项（旧版）就一直整份上传。"""

    class QC(_FakeQC):
        def __init__(self, accepts):
            super().__init__()
            self.accepts = accepts

        def health(self, **kwargs):
            self.health_calls.append(kwargs)
            return {"account_sets_unchanged_ok": True} if self.accepts else {}

    cfg = types.SimpleNamespace(
        heartbeat_interval_s=60, method="dbf", account_set="", account_dir="", account_company="",
        account_set_row=0, revenue_acc="", ar_acc="", vat_output_acc="", fallback_acc="", ap_acc="",
        vat_input_acc="", resolved_express_root=lambda: "", backend_url="https://x")
    sets, accounts = [{"path": "A"}], [{"code": "1"}]

    def beat(qc, state, **changes):
        state.update(last_heartbeat=0.0, **changes)
        worker._run_periodic(cfg, qc, state, on_update_available=None)
        return qc.health_calls[-1]

    old_server = QC(accepts=False)
    state = {"last_update_check": time.time(), "reported_account_sets": sets, "reported_accounts": accounts}
    for _ in range(3):
        call = beat(old_server, state)
        assert call["account_sets"] == sets and not call.get("account_sets_unchanged")

    qc = QC(accepts=True)
    state = {"last_update_check": time.time(), "reported_account_sets": sets, "reported_accounts": accounts}
    first = beat(qc, state)  # 刚启动：整份
    assert first["account_sets"] == sets and first["accounts"] == accounts
    slim = beat(qc, state)  # 没变：只报在线
    assert slim["account_sets"] is None and slim["accounts"] is None and slim["account_sets_unchanged"] is True
    changed = beat(qc, state, reported_account_sets=[{"path": "A"}, {"path": "B"}])  # 变了：整份
    assert changed["account_sets"] == [{"path": "A"}, {"path": "B"}]
    assert beat(qc, state)["account_sets"] is None
    state["sent_full_at"] = time.time() - worker._FULL_REPORT_S - 1  # 满 30 分钟：整份
    assert beat(qc, state)["account_sets"] == [{"path": "A"}, {"path": "B"}]
    assert beat(qc, state)["account_sets"] is None


def test_a_newer_release_comes_with_the_heartbeat_reply():
    """2026-10-09：不定时查更新；服务端在心跳回复里带上最新发布版本，比本机新才交给托盘。"""
    from src.companion import worker as W

    offered = []
    W._offer_update({"companion_latest": {"version": "99.0.0", "url": "/r.exe"}}, offered.append)
    W._offer_update({"companion_latest": {"version": VERSION, "url": "/same.exe"}}, offered.append)
    W._offer_update({"companion_latest": {"version": "0.0.1", "url": "/old.exe"}}, offered.append)
    W._offer_update({"companion_latest": "junk"}, offered.append)
    W._offer_update({}, offered.append)
    W._offer_update(None, offered.append)
    assert offered == [{"version": "99.0.0", "url": "/r.exe"}]

    class Reply(_FakeQC):
        def health(self, **kwargs):
            self.health_calls.append(kwargs)
            return {"companion_latest": {"version": "99.0.0", "url": "/r.exe"}}

    cfg = types.SimpleNamespace(
        heartbeat_interval_s=60, method="dbf", account_set="", account_dir="", account_company="",
        account_set_row=0, revenue_acc="", ar_acc="", vat_output_acc="", fallback_acc="", ap_acc="",
        vat_input_acc="", resolved_express_root=lambda: "", backend_url="https://x")
    offered.clear()
    state = {"last_heartbeat": 0.0}
    W._run_periodic(cfg, Reply(), state, on_update_available=offered.append)
    assert offered == [{"version": "99.0.0", "url": "/r.exe"}]
    W._run_periodic(cfg, Reply(), state, on_update_available=offered.append)  # 没到下一次心跳：不另查
    assert len(offered) == 1
    assert not hasattr(W, "_check_update")
