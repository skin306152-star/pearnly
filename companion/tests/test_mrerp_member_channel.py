"""MR.ERP 连接的成员也走写账线（ADR-026 第 3 步，11-03 主会话选 A：没有 Express 连接的工作空间，CC 经 MR.ERP 连接领代录单）。

- 进不进取任务 / 心跳线程（``config.polls_queue``）：Express 成员照旧进；只读文件夹的成员不进——只有这一版能代录 MR.ERP
  （``OPERATOR_TARGETS`` 带 ``mrerp``）时，MR.ERP 连接的成员（不是 Express 池的 ``pool``）进；这一版还不会：与以前一样只有 Express 成员进；
- 进了之后的心跳（``worker._operator_heartbeat``）：只报版本、能代录的目标、本机授权的 MR.ERP 连接与用户名，不扫 Express、不报账套；
  Express 成员的心跳一行不变。
"""
from types import SimpleNamespace
from unittest import mock

from companion import config, payload_contract, worker


def _member(kind="express", pool=False):
    return SimpleNamespace(kind=kind, pool=pool, backend_url="https://testcowork.pearnly.com", heartbeat_interval_s=60,
                           profile_id="p1")


def test_this_version_keeps_folder_members_out_of_the_write_lane():
    assert payload_contract.OPERATOR_TARGETS == ("express",)  # 第 2 步执行层合入前
    assert config.polls_queue(_member())
    assert config.polls_queue(SimpleNamespace())  # 旧成员记录没有 kind：按 Express
    assert not config.polls_queue(_member("folder"))
    assert not config.polls_queue(_member("folder", pool=True))


def test_a_version_that_operates_mrerp_lets_mrerp_members_in_but_not_the_express_pool():
    with mock.patch.object(payload_contract, "OPERATOR_TARGETS", ("express", "mrerp")):
        assert config.polls_queue(_member())
        assert config.polls_queue(_member("folder"))
        assert not config.polls_queue(_member("folder", pool=True))  # 没装 Express 的 Express 池成员：照旧只读文件夹


def test_the_tray_builds_its_write_lane_from_polls_queue():
    from pathlib import Path

    source = (Path(worker.__file__).parent / "gui_tray.py").read_text(encoding="utf-8")
    assert "PollWorker([cfg for cfg in self.configs if polls_queue(cfg)]" in source


class _Client:
    def __init__(self):
        self.bodies = []

    def health(self, **body):
        self.bodies.append(body)
        return {"connected": True}


def test_an_mrerp_member_reports_only_its_computer_and_never_scans_express():
    qc, state = _Client(), {"last_heartbeat": 0.0}
    with mock.patch.object(worker, "_mrerp_logins", return_value=["CWEC-1"]), \
            mock.patch.object(worker, "_mrerp_users", return_value={"CWEC-1": "test01"}), \
            mock.patch.object(worker, "_adopt_machine_roots", side_effect=AssertionError("no Express scan")), \
            mock.patch.object(worker.projection_refresh, "collect_endpoint", side_effect=AssertionError("no scan")):
        worker._run_periodic(_member("folder"), qc, state)
        worker._run_periodic(_member("folder"), qc, state)  # 没到点：不再报
    from companion.version import VERSION

    assert qc.bodies == [{"companion_version": VERSION, "max_payload_version": max(payload_contract.SUPPORTED_PAYLOAD_VERSIONS),
                          "operator_targets": list(payload_contract.OPERATOR_TARGETS), "mrerp_logins": ["CWEC-1"],
                          "mrerp_users": {"CWEC-1": "test01"}}]
    assert state["last_heartbeat"] > 0


def test_an_mrerp_member_heartbeat_never_raises():
    class Broken:
        def health(self, **body):
            raise RuntimeError("offline")

    worker._run_periodic(_member("folder"), Broken(), {"last_heartbeat": 0.0})  # 不抛


def test_express_members_keep_the_full_heartbeat():
    calls = []
    with mock.patch.object(worker, "_operator_heartbeat", side_effect=lambda *a: calls.append("operator")), \
            mock.patch.object(worker, "_adopt_machine_roots", side_effect=lambda cfg: calls.append("express")) as adopt:
        adopt.side_effect = RuntimeError("stop after the first Express step")  # 只核走的是 Express 那条
        worker._run_periodic(_member(), _Client(), {"last_heartbeat": 0.0})
    assert calls == []
    assert adopt.called
