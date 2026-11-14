"""接缝 1 真跨边（拼车 11-14）：CC 宿主真产出的半截单回执，经 worker 同样的包法，交服务端真净化，页面能读出单号；
人答完框后 CC 补报的迟到终态，服务端真判定收下。两边都跑真代码，不比源码字符串、不用替身。

约定：docs/work/2026-11-14-cc-operator-half-doc.md 接缝 a–e。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from test_operator_half_doc import _settings
from test_operator_host import fake_factory, payload

from companion.express_operator.host import Host

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "pearnly_core"))
from pearnly_core.express_rules.push import common  # noqa: E402


def _half_receipt(tmp_path):
    sims = []
    factory = fake_factory(sims)

    def with_choice(*args, **kwargs):
        runner, parts = factory(*args, **kwargs)
        sims[-1].inject["choice_mid_save"] = True
        return runner, parts

    host = Host(tmp_path, factory=with_choice, clock=lambda: 10_000.0)
    return host.handle("t1", payload("RR", "P001", "501780", "2", "10.00", 1, 2), _settings(tmp_path))


def test_the_half_document_receipt_cc_sends_is_kept_and_shown_by_the_server(tmp_path):
    result = _half_receipt(tmp_path)
    assert result["status"] == "human" and result["reason"] == "partial_saved"
    # worker._ack_operator 的包法：{"operator": 宿主 meta}
    sent = {"operator": dict(result["meta"])}
    stored = common.sanitize_push_meta(sent)
    kept = stored[common.OPERATOR_META]
    assert kept["partial"]["docnum"] == result["meta"]["partial"]["docnum"] == "RR0000001"
    assert set(kept["partial"]["missing"]) == set(result["meta"]["partial"]["missing"])
    assert set(kept["partial"]["missing"]) <= set(common.OPERATOR_PARTIAL_GAPS)
    assert kept["held"] == {"computer": result["meta"]["held"]["computer"], "account_set": "TEST"}
    assert "evidence" not in kept  # 本机截图路径不进服务端
    # 两产品 status() 读存下的回执 meta 显示单号
    assert common.operator_partial_docnum(stored) == "RR0000001"
    from pearnly_core.express_operator import contracts as server_contracts

    assert server_contracts.REASONS[result["reason"]] == server_contracts.HUMAN  # CC 回的原因服务端认得、同一类


def test_the_late_success_cc_sends_after_the_person_finishes_is_accepted_only_for_that_number(tmp_path):
    result = _half_receipt(tmp_path)
    stored = common.sanitize_push_meta({"operator": dict(result["meta"])})
    docnum = result["meta"]["partial"]["docnum"]
    # worker.operator_idle 补报：同一回执号、成功、extracted_invoice_no=记下的单号
    assert common.late_partial_success(True, "manual", stored, docnum)
    assert not common.late_partial_success(True, "manual", stored, "RR0000002")
    assert not common.late_partial_success(True, "queued", stored, docnum)
    # 补报的 meta 里只有 batch / doc / docnum：服务端净化后不留（单号走 express_docnum，判定读首次回执存下的 meta），这是约定内的
    assert common.OPERATOR_META not in common.sanitize_push_meta({"operator": {"batch": "b1", "doc": "d1", "docnum": docnum}})


class _Recorder:
    def __init__(self):
        self.bodies = []

    def post(self, path, json):
        self.bodies.append(json)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"message": {"ok": True}})


def _client():
    from companion.queue_client import QueueClient

    qc = QueueClient.__new__(QueueClient)
    qc.cfg, qc._paths, qc._client = SimpleNamespace(companion_id="cc-1"), {"ack": "/ack"}, _Recorder()
    return qc


def test_the_wire_bodies_cc_sends_pass_the_servers_ack_check_with_the_number_where_the_server_reads_it(tmp_path):
    from pearnly_core.cc_desktop.wire import queue_contracts

    result = _half_receipt(tmp_path)
    qc, log_id = _client(), str(uuid.uuid4())
    # 首次：未完成（人工），worker 用 ack_failure 交 {"operator": 宿主 meta}
    qc.ack_failure(log_id, "partial", error_code=result["reason"], outcome="needs_review", meta={"operator": dict(result["meta"])})
    first = queue_contracts.ack_request(qc._client.bodies[-1])
    assert common.operator_partial_docnum(common.sanitize_push_meta(first["meta"])) == "RR0000001"
    # 迟到终态：worker.operator_idle 用 ack_success(extracted_invoice_no=单号)，服务端从 express_docnum 读
    docnum = result["meta"]["partial"]["docnum"]
    qc.ack_success(log_id, extracted_invoice_no=docnum, meta={"operator": {"batch": "b1", "doc": "d1", "docnum": docnum}})
    late = queue_contracts.ack_request(qc._client.bodies[-1])
    assert late["result"] == "success" and late["log_id"] == log_id
    stored = common.sanitize_push_meta(first["meta"])
    assert common.late_partial_success(late["result"] == "success", "manual", stored, late["express_docnum"])
