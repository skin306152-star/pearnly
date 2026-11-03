# -*- coding: utf-8 -*-
"""整批停下以后（人点了停止，或登录 / 授权名额 / 公司 / 服务器 / 费用上限等整批停的原因）站点按原批次号再交来的单（2026-11-03）：

- 换了写账件号 = 人的新指令（作答、重试、整批继续）：已停的这一批重新打开，这张重录；第一张照常重开 Express、核登录与公司；
- 这张已存 → 不重录，回原单号；等人 / 跳过 → 换上新草稿重录；停下时正在录（结果不明）→ 先读账套判断存没存，存了不重录；
- 同一个写账件再派来（租约过期后派回这台）不是新指令，照上次的结果回执；
- 没交回来的单保持原状；人点停止后剩下没轮到的单照旧按停止跳过回执；
- 批次文件新加的两项（写账件号、再交来先核）旧文件没有照样读。
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from types import SimpleNamespace

from _fake_express import FakeBook, FakeExpress

from companion import worker
from companion.express_operator import contract as K
from companion.express_operator import recipe as R
from companion.express_operator.batch import BatchState, BatchStore, DocState, Runner, no_lock
from companion.express_operator.engine import Engine, NeedHuman, Stopped
from companion.express_operator.host import Host

RECIPE = R.load()


def job(doc, index, total, *, party="P001", ref=None, batch="b1"):
    return {"doctype": "RR", "docdate_be": "691020", "ref_no": ref or f"R-{doc}", "supplier": {"code": party},
            "items": [{"code": "501780", "qty": "1", "unit_price": "1.00"}], "payload_version": 16,
            "entry_mode": "operator", "operator": {"batch": {"id": batch, "index": index, "total": total, "doc": doc}}}


class Rig:
    """一台电脑：一个假 Express + 账套（跨宿主重启都在），会话能按次数登录失败，引擎能插一步。"""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.sim = FakeExpress(RECIPE.data)
        self.book = FakeBook(self.sim)
        self.opened = 0
        self.fail_open = []  # 依次弹出：这一次开 Express 遇到的整批停原因
        self.before_run = None  # (draft) -> None：引擎录这张之前做点什么（抛 Stopped 等）
        self.after_run = None  # (draft, rec) -> rec：引擎录完这张之后改结果
        self.reachable = True
        self.st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")

    def factory(self, state, store, settings, root, notify):
        rig, engine = self, Engine(self.sim, RECIPE, self.book, sleep=lambda _s: None)
        real = engine.run

        def run(draft):
            if rig.before_run:
                rig.before_run(draft)
            rec = real(draft)
            return rig.after_run(draft, rec) if rig.after_run else rec

        engine.run = run

        class Session:
            def ensure_open(self):
                rig.opened += 1
                if rig.fail_open:
                    raise NeedHuman(rig.fail_open.pop(0), "login window")
                rig.sim.alive = True

            def close(self):
                rig.sim.alive = False

        runner = Runner(state, store, engine, Session(), self.book, lock=no_lock, sleep=lambda _s: None, on_change=notify,
                        reachable=lambda: rig.reachable, net_wait_s=0, net_poll_s=0)
        return runner, {"desk": self.sim}

    def host(self):
        return Host(self.tmp, factory=self.factory)

    def heads(self):
        return [r["DOCNUM"] for r in self.sim.book["APTRN"] if not r.get("_del")]


def docs_of(host, batch="b1"):
    found = next(s for s in host.snapshots() if s["id"] == batch)
    return {d["doc_id"]: (d["status"], d["reason"]) for d in found["docs"]}, found


def test_after_a_login_failure_the_site_hands_the_documents_back_and_the_batch_reopens(tmp_path):
    """登录失败整批停：卡住的那张与停止跳过的那张都按原批次号重交（整批继续）→ 重新打开这一批，重开 Express、核登录，两张都录上。"""
    rig = Rig(tmp_path)
    rig.fail_open = ["login_failed"]
    host = rig.host()
    first = host.handle("w1", job("D1", 1, 2), rig.st)
    assert (first["status"], first["reason"]) == ("human", "login_failed")
    second = host.handle("w2", job("D2", 2, 2), rig.st)
    assert (second["status"], second["reason"]) == ("stopped", "stopped")       # 剩下的照旧按停止跳过
    docs, snap = docs_of(host)
    assert snap["status"] == "stopped" and docs["D2"] == ("skipped", "stopped")
    # 人在 CC 授权后点「继续」：站点新建写账件（新号）重交这两张
    again = [host.handle("w1b", job("D1", 1, 2), rig.st), host.handle("w2b", job("D2", 2, 2), rig.st)]
    assert [r["status"] for r in again] == ["saved", "saved"]
    assert rig.opened == 2                                                          # 第一张照常重开 Express、核登录
    assert rig.heads() == [r["docnum"] for r in again]
    docs, snap = docs_of(host)
    assert snap["status"] == "finished" and snap["reason"] == "" and docs["D2"] == ("saved", "")


def test_after_a_stop_the_same_job_sent_again_keeps_its_answer_and_a_new_one_enters_it(tmp_path):
    """人点了停止：剩下的按停止跳过回执；同一个写账件再派来（租约过期派回这台）照上次的结果、不录；人点重试（新写账件）才重录。
    没交回来的那张保持原状。"""
    rig = Rig(tmp_path)
    host = rig.host()
    assert host.handle("w1", job("D1", 1, 3), rig.st)["status"] == "saved"
    assert host.control("b1", "stop")
    assert host.handle("w2", job("D2", 2, 3), rig.st)["status"] == "stopped"
    assert host.handle("w3", job("D3", 3, 3), rig.st)["status"] == "stopped"
    assert host.handle("w2", job("D2", 2, 3), rig.st)["status"] == "stopped"     # 同一件再派来：不是新指令
    assert len(rig.heads()) == 1 and docs_of(host)[1]["status"] == "stopped"
    retried = host.handle("w2-retry", job("D2", 1, 1), rig.st)                     # 单张重试：站点按原批次号重交
    assert retried["status"] == "saved" and len(rig.heads()) == 2
    docs, _snap = docs_of(host)
    assert docs["D3"] == ("skipped", "stopped")                                     # 没交回来的不自动重录
    assert host.handle("w2-retry", job("D2", 1, 1), rig.st)["docnum"] == retried["docnum"]  # 同一件再派来：照上次（不重录）
    assert len(rig.heads()) == 2


def test_a_saved_document_handed_back_returns_its_number_without_entering_again(tmp_path):
    rig = Rig(tmp_path)
    host = rig.host()
    saved = host.handle("w1", job("D1", 1, 2), rig.st)
    rig.fail_open = ["license"]
    host.close_express(job("D2", 2, 2))                                            # 退出过 Express：下一张要重开
    assert host.handle("w2", job("D2", 2, 2), rig.st)["reason"] == "license"
    back = host.handle("w1b", job("D1", 1, 1), rig.st)
    assert (back["status"], back["docnum"]) == ("saved", saved["docnum"]) and rig.heads() == [saved["docnum"]]
    assert docs_of(host)[1]["status"] == "stopped"                                 # 已存的回原单号，不为它重开这一批


def test_a_document_stopped_halfway_is_checked_in_the_book_before_it_is_entered_again(tmp_path):
    """停下时正在录的那张（Express 其实已存上、CC 还没读到结论）：不当跳过重录。同一件再派来照上次；新指令先读账套，
    找到就记已存、回那张的单号，账套里只一张。"""
    rig = Rig(tmp_path)
    host = rig.host()

    def stop_after_saving(draft, rec):
        rig.after_run = None
        raise Stopped()

    rig.after_run = stop_after_saving
    first = host.handle("w1", job("D1", 1, 1), rig.st)
    assert (first["status"], first["reason"]) == ("stopped", "stopped")
    batch = BatchStore(tmp_path / "batches").load("b1")
    assert (batch.docs[0].status, batch.docs[0].recheck) == (K.SKIPPED, True)
    assert host.handle("w1", job("D1", 1, 1), rig.st)["status"] == "stopped"      # 同一件再派来：照上次
    [written] = rig.heads()
    back = host.handle("w1b", job("D1", 1, 1), rig.st)
    assert (back["status"], back["docnum"]) == ("saved", written)
    assert rig.heads() == [written]                                                # 没有录第二张
    assert {"reason": "express_restarted"} in back["meta"]["reports"]


def test_a_server_outage_answers_waiting_once_then_the_same_job_is_not_looped(tmp_path):
    """服务器连不上超时（整批停）：第一次照旧回「等」（站点放回队列）；同一件再派来回要人处理（原因同整批），不再「等」来回转；
    整批继续（新写账件）后先读账套，没存上才重录一次。"""
    rig = Rig(tmp_path)
    crashed = []

    def factory(state, store, settings, root, notify):
        runner, parts = rig.factory(state, store, settings, root, notify)
        real = runner.engine.run

        def run(draft):
            if not crashed:
                crashed.append(draft.doc_id)
                return {"outcome": "crash", "seconds": 0.0}             # -70：Express 崩、什么都没存
            return real(draft)

        runner.engine.run = run
        return runner, parts

    host = Host(tmp_path, factory=factory)
    rig.reachable = False
    first = host.handle("w1", job("D1", 1, 2), rig.st)
    assert (first["status"], first["reason"]) == ("waiting", "network_down")
    again = host.handle("w1", job("D1", 1, 2), rig.st)                            # 站点按「等」放回、同一件再派来
    assert (again["status"], again["reason"]) == ("human", "network_down")
    assert host.handle("w2", job("D2", 2, 2), rig.st)["status"] == "stopped"
    rig.reachable = True
    out = [host.handle("w1b", job("D1", 1, 2), rig.st), host.handle("w2b", job("D2", 2, 2), rig.st)]
    assert [r["status"] for r in out] == ["saved", "saved"] and len(rig.heads()) == 2


def test_a_held_document_answered_after_the_batch_finished_reopens_express(tmp_path):
    """这一批录完已退出 Express，人在任务里作答交回「等人」的那张：先重开 Express、核公司再录（原来不重开，对着已退出的
    Express 录、按崩溃重开，这张记一次崩溃）。"""
    rig = Rig(tmp_path)
    host = rig.host()
    held = host.handle("w1", job("D1", 1, 1, party="NOPE"), rig.st)
    assert held["status"] == "human" and not rig.sim.alive and docs_of(host)[1]["status"] == "finished"
    opened = rig.opened
    out = host.handle("w1b", job("D1", 1, 1), rig.st)
    assert out["status"] == "saved" and rig.opened == opened + 1
    assert BatchStore(tmp_path / "batches").load("b1").docs[0].crashes == 0


def test_an_old_batch_file_without_the_new_fields_still_loads_and_continues(tmp_path):
    rig = Rig(tmp_path)
    store = BatchStore(tmp_path / "batches")
    state = BatchState("b1", "x", "TEST", rig.st.account_dir, [DocState("D1", "RR", {})], status=K.BATCH_STOPPED,
                       reason="login_failed", started=900.0)
    data = asdict(state)
    for doc in data["docs"]:
        doc.pop("task")
        doc.pop("recheck")
        doc["status"], doc["reason"] = K.SKIPPED, "stopped"
    store.root.mkdir(parents=True)
    store.path("b1").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    loaded = store.load("b1")
    assert (loaded.docs[0].task, loaded.docs[0].recheck) == ("", False)
    host = rig.host()                                                              # CC 升级后重启：续用旧批次文件
    out = host.handle("w9", job("D1", 1, 1), rig.st)
    assert out["status"] == "saved" and rig.opened == 1
    assert store.load("b1").docs[0].task == "w9"


def test_the_writing_line_passes_the_stable_job_key_not_the_delivery_number(monkeypatch, tmp_path):
    """Cowork 通道重派时投递号换新、``task_key`` 不变；ERP 通道投递号就是写账件号。宿主拿同一件每次都一样的那个。"""
    from companion.express_operator import host as host_module

    seen = []
    fake = SimpleNamespace(handle=lambda key, *_a: seen.append(key) or {"status": "human", "reason": "x", "meta": {}},
                           close_express=lambda _p: None)
    monkeypatch.setattr(host_module, "host", lambda notify=None: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(worker, "activity", lambda *_a, **_k: None)

    class Queue:
        def ack_failure(self, *_a, **_k):
            pass

    st = SimpleNamespace(account_dir=str(tmp_path), express_exe=r"\\s\ExpressI.exe")
    for task in (SimpleNamespace(id="delivery-2", key="record-key", payload={}, invoice_no=""),
                 SimpleNamespace(id="erp-job-1", key=None, payload={}, invoice_no="")):
        worker._handle_operator_task(SimpleNamespace(ui_lang="zh"), Queue(), task, st)
    assert seen == ["record-key", "erp-job-1"]
