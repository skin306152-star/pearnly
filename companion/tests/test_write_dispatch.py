# -*- coding: utf-8 -*-
"""多台电脑写账（ADR-021 第 3 批服务端、第 5 批 CC 桌面版 1.7.0）的桌面端一侧：

- 领取请求每次带 ``index_pending``（主档索引还没重建的账套文件号，全部成员合起来），任务读可选的 ``attended``；
- 写前用本机目录文件号核服务端下发的 ``account_uid``：对不上、或这台上找不到这份账套 → 不写，按「等账套」回报
  ``[ERR_ACCOUNT_UID_MISMATCH]``（单子回到排队交给别的电脑），并让下一次心跳整套重扫；读不出文件号不算对不上；
- 有人值守时先探**这张单的**账套有没有被占用，占用就先放掉租约再弹询问框；替别人写（``attended=false``）不问；
- 每轮先给成员认一次电脑，密钥被说不能再用的成员这一轮起停用。"""
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from companion import account_probe, cdx_reindex, device_facts, master_refresh, task_journal, worker
from companion.multi_profile_worker import MultiProfileWorker
from companion.queue_client import QueueClient, QueueClientConfig


@pytest.fixture(autouse=True)
def _fresh():
    master_refresh._reset_for_tests()
    device_facts._reset_for_tests()
    yield
    master_refresh._reset_for_tests()


# ── 写前核文件号 ──

class _Queue:
    def __init__(self):
        self.acks = []

    def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
        self.acks.append(("success", task_id, extracted_invoice_no))
        return True

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.acks.append(("failed", task_id, error_code, outcome))
        return True


def _handle(monkeypatch, payload, *, settings="found", uid="0c9a53f6-0000000000000005", task_id="T-1"):
    from companion import dbf_writer
    from companion.write_result import DbfWriteResult

    writes = []
    monkeypatch.setattr(dbf_writer, "write_purchase",
                        lambda payload, account_dir, **kw: writes.append(account_dir) or DbfWriteResult(True, docnum="RR-1"))
    found = SimpleNamespace(account_dir="S:\\2569\\ACME", account_set="S:\\2569\\ACME", account_company="",
                            has_selection=lambda: True)
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, p: found if settings == "found" else None)
    monkeypatch.setattr(device_facts, "file_uid", lambda path, api=None: uid)
    queue = _Queue()
    outcome = worker._handle_dbf_task(SimpleNamespace(default_depcod="", ui_lang="en"), queue,
                                      SimpleNamespace(id=task_id, payload={"direction": "purchase", **payload},
                                                      invoice_no="X"))
    return outcome, queue.acks, writes


def test_a_matching_uid_is_written(monkeypatch):
    outcome, acks, writes = _handle(monkeypatch, {"account_uid": "0C9A53F6-0000000000000005"})
    assert outcome is None and acks == [("success", "T-1", "RR-1")] and writes == ["S:\\2569\\ACME"]


def test_a_different_folder_at_the_same_path_goes_back_to_the_queue_for_another_computer(monkeypatch):
    before = master_refresh._rescan_generation
    outcome, acks, writes = _handle(monkeypatch, {"account_uid": "c8d5876c-0000000000002a3b"})
    assert outcome == "waiting_lock" and writes == []
    assert acks == [("failed", "T-1", worker.ERR_ACCOUNT_UID_MISMATCH, "waiting_lock")]
    assert master_refresh._rescan_generation == before + 1   # 下一次心跳整套重扫，报上去的是现在的文件号


def test_a_registered_account_missing_on_this_computer_is_handed_back_not_sent_to_a_person(monkeypatch):
    outcome, acks, writes = _handle(monkeypatch, {"account_uid": "c8d5876c-0000000000002a3b"}, settings=None)
    assert outcome == "waiting_lock" and writes == []
    assert acks == [("failed", "T-1", "ERR_ACCOUNT_UID_MISMATCH", "waiting_lock")]


def test_an_old_server_without_account_uid_keeps_todays_behaviour(monkeypatch):
    outcome, acks, _writes = _handle(monkeypatch, {}, settings=None)
    assert outcome == "failed" and acks == [("failed", "T-1", "NO_ACCOUNT_SELECTION", None)]
    outcome, acks, writes = _handle(monkeypatch, {}, uid="ffffffff-0000000000000001", task_id="T-2")
    assert outcome is None and writes == ["S:\\2569\\ACME"]   # 不带文件号就不核


def test_a_folder_whose_uid_cannot_be_read_is_written_by_path(monkeypatch):
    outcome, acks, writes = _handle(monkeypatch, {"account_uid": "c8d5876c-0000000000002a3b"}, uid="")
    assert outcome is None and writes == ["S:\\2569\\ACME"]


def test_a_task_already_written_here_is_acknowledged_even_if_the_account_is_gone_now(monkeypatch):
    task_journal.remember("T-9", {"docnum": "RR-7", "line_modes": None, "meta": {}})
    outcome, acks, writes = _handle(monkeypatch, {"account_uid": "c8d5876c-0000000000002a3b"}, settings=None,
                                    task_id="T-9")
    assert outcome is None and writes == [] and acks == [("success", "T-9", "RR-7")]


def test_the_mismatch_reaches_the_server_as_waiting_lock_with_the_code_in_brackets():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"ok": True}})

    client = QueueClient(QueueClientConfig("https://site.test", "exp_C1_s", "comp-1"))
    client._client = httpx.Client(base_url="https://site.test", transport=httpx.MockTransport(handler))
    client.ack_failure("T-1", "the account set on this computer is not the registered one",
                       error_code=worker.ERR_ACCOUNT_UID_MISMATCH, outcome="waiting_lock")
    assert sent[0]["outcome"] == "waiting_lock"
    assert sent[0]["error"].startswith("[ERR_ACCOUNT_UID_MISMATCH] ")


# ── 领取请求与任务 ──

def _lease_client(jobs, sent):
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"ok": True, "lease_seconds": 120, "jobs": jobs}})

    client = QueueClient(QueueClientConfig("https://site.test", "exp_C1_s", "comp-1"))
    client._client = httpx.Client(base_url="https://site.test", transport=httpx.MockTransport(handler))
    return client


def test_every_lease_request_says_which_account_sets_still_need_their_index_rebuilt():
    sent = []
    client = _lease_client([], sent)
    client.poll()
    client.poll(index_pending=["b", "a", "a", " ", ""])
    client.poll(index_pending=[f"u{n:04d}" for n in range(600)])
    assert sent[0] == {"max": 1, "agent_id": "comp-1", "index_pending": []}   # 都重建了也报（空列表），不是旧版
    assert sent[1]["index_pending"] == ["a", "b"]
    assert len(sent[2]["index_pending"]) == 500


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (None, None), ("false", None)])
def test_a_job_says_whether_the_person_who_entered_it_is_at_this_computer(value, expected):
    job = {"log_id": "11111111-1111-4111-8111-111111111111", "history_id": "", "invoice_no": "INV-1",
           "payload": {"direction": "purchase"}}
    if value is not None:
        job["attended"] = value
    task = _lease_client([job], []).poll()
    assert task.attended is expected and task.payload == {"direction": "purchase"}


def test_the_index_batch_lists_the_account_sets_still_waiting_for_a_rebuild(monkeypatch):
    batch = cdx_reindex.IndexBatch()
    batch.add("S:\\B", ["STMAS"])
    batch.add("S:\\A", ["APMAS"])
    assert batch.pending_dirs() == ["S:\\A", "S:\\B"]
    monkeypatch.setattr(cdx_reindex, "reindex", lambda path, tables: None)
    batch.flush()
    assert batch.pending_dirs() == []


class _Batch:
    def __init__(self, dirs=()):
        self.dirs = list(dirs)

    def pending_dirs(self):
        return list(self.dirs)

    def flush(self):
        self.dirs = []


class _Client:
    def __init__(self, cfg, polls):
        self.cfg = cfg
        self.polls = polls

    def poll(self, index_pending=None):
        self.polls.append((self.cfg.bearer_token, index_pending))
        return None

    def health(self, **kwargs):
        return {"ok": True}

    def close(self):
        pass


def _member(name, **extra):
    values = dict(profile_id=name, paused=False, device_state="", backend_url="https://site.test",
                  bearer_token=f"exp_{name}_s", companion_id="c", poll_interval_ms=1)
    values.update(extra)
    return SimpleNamespace(**values)


def test_the_computer_reports_the_union_of_every_members_pending_account_sets():
    batches = iter([_Batch(["S:\\A", "S:\\B"]), _Batch(["S:\\B", "C:\\LOCAL"])])
    polls = []
    uids = {"S:\\A": "u-a", "S:\\B": "u-b", "C:\\LOCAL": ""}
    scheduler = MultiProfileWorker([_member("A"), _member("B")], queue_factory=lambda cfg: _Client(cfg, polls),
                                   periodic_runner=lambda *a: None, index_batch_factory=lambda: next(batches),
                                   uid_of=uids.get)
    scheduler.run_round()
    assert polls == [("exp_A_s", ["u-a", "u-b"]), ("exp_B_s", ["u-a", "u-b"])]


def test_each_member_says_hello_before_it_polls_and_a_stopped_member_does_not_poll():
    polls, said, statuses = [], [], []

    def announce(cfg):
        said.append(cfg.profile_id)
        if cfg.profile_id == "B":
            cfg.device_state = "claimed_elsewhere"   # 服务端说这把密钥已被另一台认领

    members = [_member("A"), _member("B"), _member("C", device_state="superseded")]
    scheduler = MultiProfileWorker(members, queue_factory=lambda cfg: _Client(cfg, polls),
                                   periodic_runner=lambda *a: None, index_batch_factory=_Batch,
                                   announce=announce, on_status=lambda pid, key, detail: statuses.append((pid, key)))
    scheduler.run_round()
    scheduler.run_round()
    assert said == ["A", "B", "A"]
    assert [token for token, _pending in polls] == ["exp_A_s", "exp_A_s"]
    assert ("B", "offline") in statuses and ("C", "offline") in statuses and ("A", "offline") not in statuses


def test_a_failing_hello_never_stops_the_member():
    polls = []

    def announce(cfg):
        raise RuntimeError("boom")

    scheduler = MultiProfileWorker([_member("A")], queue_factory=lambda cfg: _Client(cfg, polls),
                                   periodic_runner=lambda *a: None, index_batch_factory=_Batch, announce=announce)
    assert scheduler.run_round().polled_profiles == 1 and polls


# ── 有人值守时探的是这张单的账套 ──

def _cfg(**extra):
    base = dict(method="dbf", express_root="S:\\2569", express_exe="", account_dir="S:\\2569\\HOME",
                account_company="", account_set="S:\\2569\\HOME", account_set_row=1,
                authorized_roots=["S:\\2568"], always_on=False)
    base.update(extra)
    return SimpleNamespace(**base)


def test_the_busy_probe_looks_at_the_tasks_own_account_set_under_an_authorized_root():
    cfg = _cfg()
    assert worker.task_account_dir(cfg, {"account_set": "S:\\2568\\ACME", "account_root": "S:\\2568"}) == "S:\\2568\\ACME"
    assert worker.task_account_dir(cfg, {"account_set": "S:\\2569\\X", "account_root": "s:/2569/"}) == "S:\\2569\\X"
    assert worker.task_account_dir(cfg, {"account_set": "\\\\evil\\x", "account_root": "\\\\evil"}) == ""
    assert worker.task_account_dir(cfg, {}) == "S:\\2569\\HOME"
    assert worker.task_account_dir(cfg, {"account_set": "S:\\2569\\HOME"}) == "S:\\2569\\HOME"
    assert worker.task_account_dir(cfg, {"account_set": "S:\\2569\\OTHER"}) == ""


def _poll_worker():
    from companion.gui_tray_workers import PollWorker

    return PollWorker([])


def test_nobody_is_asked_for_an_always_on_computer_or_a_task_written_for_someone_else():
    unattended = _poll_worker()._unattended
    assert unattended(_cfg(always_on=True), SimpleNamespace(attended=True)) is True
    assert unattended(_cfg(), SimpleNamespace(attended=False)) is True
    assert unattended(_cfg(), SimpleNamespace(attended=True)) is False
    assert unattended(_cfg(), SimpleNamespace()) is False   # 旧服务端不带：有人值守，照旧问


@pytest.mark.parametrize("choice,expected", [("retry", "waiting_lock"), ("abort", "error")])
def test_a_busy_account_set_releases_the_lease_before_the_prompt(choice, expected):
    pw = _poll_worker()
    order = []

    class Queue:
        def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
            order.append(("ack", task_id, error_code, outcome))

    def prompt(p):
        order.append(("prompt",))
        p.choice = choice
        p.done.set()

    pw.busy_prompt.connect(prompt)
    result = pw._release_then_ask("P1", Queue(), SimpleNamespace(id="T-1", invoice_no="INV"), "zh")
    assert result == expected
    assert order == [("ack", "T-1", "ACCOUNT_BUSY_LOCKED", "waiting_lock"), ("prompt",)]
    assert ("P1" in pw._abort_batches) is (choice == "abort")


# ── 账套行带文件号与网络路径；对不上时整套重扫 ──

def test_every_reported_account_set_row_carries_uid_and_share():
    class Finder:
        def uid(self, path):
            return {"S:\\2569\\A": "u-a"}.get(path, "")

        def share(self, path):
            return "\\\\nas\\pas212\\2569\\A" if path == "S:\\2569\\A" else ""

    rows = account_probe.with_identity([{"path": "S:\\2569\\A"}, {"path": "C:\\X"}, {"path": ""}], Finder())
    assert [(row["uid"], row["share"]) for row in rows] == [("u-a", "\\\\nas\\pas212\\2569\\A"), ("", ""), ("", "")]
    assert set(account_probe.IDENTITY_KEYS) == {"uid", "share"}


def test_account_sets_payload_adds_identity_only_with_details(monkeypatch):
    found = [account_probe.AccountSet("A", "1.A", "ACME", "S:\\2569\\A", "0105", True, 1)]
    monkeypatch.setattr(account_probe, "probe_account_sets", lambda root, include_details=True: found)
    monkeypatch.setattr(account_probe, "_account_details", lambda path: ("ACME", "0105", {}, {}))

    class Finder:
        def uid(self, path):
            return "u-a"

        def share(self, path):
            return "\\\\nas\\s\\A"

    detailed = account_probe.account_sets_payload("S:\\2569", locator=Finder())
    assert detailed[0]["uid"] == "u-a" and detailed[0]["share"] == "\\\\nas\\s\\A"
    quick = account_probe.account_sets_payload("S:\\2569", include_details=False, locator=Finder())
    assert "uid" not in quick[0] and "share" not in quick[0]


def test_rescan_soon_makes_the_next_heartbeat_scan_everything_again(monkeypatch):
    scans = []
    monkeypatch.setattr(account_probe, "account_sets_payload", lambda root, **kw: scans.append(root) or [])
    monkeypatch.setattr(account_probe, "chart_accounts_payload", lambda _path: [])
    from companion import express_locate

    monkeypatch.setattr(express_locate, "candidate_roots", lambda: [])

    def wait(newer=0.0):
        deadline = time.time() + 2
        while time.time() < deadline:
            found = master_refresh.request_master("C:/books", "C:/books/A", newer_than=newer)
            if found[0] is not None:
                return found
            time.sleep(0.01)
        return (None, 0.0)

    first = wait()
    assert master_refresh.request_master("C:/books", "C:/books/A", newer_than=first[1]) == (None, 0.0)
    master_refresh.rescan_soon()
    second = wait(first[1])
    assert second[0] is not None and scans == ["C:/books", "C:/books"]
