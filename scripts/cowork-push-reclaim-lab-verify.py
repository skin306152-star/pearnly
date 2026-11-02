"""Cowork 推送账本：CC 被结束后写者锁的回收（ADR-021 11-02 修订第 59–61 条），在本机回归实验室的真实站点事务里验。

用产品自己的函数造数据（`scripts/flows/cowork_operator_lib.py`：一条合成 Express 连接、一家公司、识别单据、准备 → 激活）与实验室的
合成写入端（`native.trusted_adapter_context`，带电脑编号），不连 Express、不发 LINE、不调模型。每个场景跑完按连接清掉数据
（`cleanup`），站点回到跑之前的样子。

    PEARNLY_FLOWS_LAB=<实验室项目> python3 scripts/cowork-push-reclaim-lab-verify.py [--site cowork-new.regression.localhost]

场景：
1. 丢在电脑手里的单只重发给原来那台、写者锁一直归它（同账套下一张等着），丢了的那次领单的回执不再算数，重发后的回执记成功、放锁；
2. 过期转成结果不明的单重发给原电脑，满 3 次不再重发、锁留给人核对；
3. 电脑还在：续租约连同写者锁，续过的租约到期前回收不动它；电脑自己说丢了（重发次数满）时不等到期就转人工 + 结果不明，迟到的
   回执照收；
4. 代录页：排着的单被同账套一张结果不明的单挡着时，`operator_source.status` 报出挡着它的那张（单号、对方），核对后不再报。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "flows"))

from cowork_operator_lib import BASE, CLEANUP  # noqa: E402
from run import Lab  # noqa: E402

COMMON = CLEANUP + BASE + r'''
from pearnly_cowork.push.native import service as native
now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
pc_a = native.trusted_adapter_context(conn["name"], "synthetic-writer", device="PC-A")
pc_b = native.trusted_adapter_context(conn["name"], "synthetic-writer", device="PC-B")
def locks(record):  # 账本表不许通用读取（`push.native.documents`）：直接读库
    return frappe.db.sql("SELECT holder_fence, lease_expires_at FROM `tabCowork Push Writer Lock` WHERE holder=%s",
                         (record,), as_dict=True)
def state(record):
    return frappe.db.get_value("Cowork Push Record", record, ["status", "result_unknown", "fence", "reason"], as_dict=True)
def refused(call):
    try:
        call()
    except frappe.ValidationError as error:
        frappe.db.rollback()
        return type(error).__name__
    return ""
out = {}
'''

SCENARIOS = {
    "lost_goes_back_only_to_its_own_computer": r'''
a = prepared(recognized("RECLAIM-A1"))
b = prepared(recognized("RECLAIM-B1"))
first = native.lease(a, context=context_of(a), adapter=pc_a, now=now); frappe.db.commit()
out["b_waits"] = native.lease(b, context=context_of(b), adapter=pc_a, now=now)["reason"]
out["other_computer"] = refused(lambda: native.lease(a, context=context_of(a), adapter=pc_b, now=now, redeliver=True))
again = native.lease(a, context=context_of(a), adapter=pc_a, now=now, redeliver=True); frappe.db.commit()
out["again"] = {"leased": again["leased"], "fence": again["fence"], "first_fence": first["fence"]}
out["locks"] = [row.holder_fence for row in locks(a)]
out["old_attempt"] = frappe.db.get_value("Cowork Push Attempt", {"push_record": a, "fence": first["fence"]}, "status")
out["b_still_waits"] = native.lease(b, context=context_of(b), adapter=pc_a, now=now)["reason"]
out["old_receipt"] = refused(lambda: native.ack(a, context=context_of(a), adapter=pc_a, fence=first["fence"],
                                                attempt_token=first["attempt_token"], success=True, express_docnum="OLD"))
done = native.ack(a, context=context_of(a), adapter=pc_a, fence=again["fence"], attempt_token=again["attempt_token"],
                  success=True, express_docnum="RR-ONCE"); frappe.db.commit()
out["done"] = [done["status"], done["external_docnum"], len(locks(a))]
out["b_after"] = native.lease(b, context=context_of(b), adapter=pc_a, now=now)["leased"]; frappe.db.commit()
''',
    "unknown_goes_back_until_the_limit": r'''
a = prepared(recognized("RECLAIM-A2"))
claim = native.lease(a, context=context_of(a), adapter=pc_a, now=now); frappe.db.commit()
rounds = []
for number in range(1, native.MAX_REDELIVERIES + 1):
    later = now + timedelta(seconds=121 * number)
    native.recover_expired(a, context=context_of(a), adapter=pc_a, now=later); frappe.db.commit()
    row = state(a)
    rounds.append([row.status, row.result_unknown, len(locks(a))])
    if number == native.MAX_REDELIVERIES:
        break
    again = native.lease(a, context=context_of(a), adapter=pc_a, now=later, redeliver=True); frappe.db.commit()
    rounds.append(["redelivered", again["leased"], state(a).result_unknown])
out["rounds"] = rounds
out["after_limit"] = refused(lambda: native.lease(a, context=context_of(a), adapter=pc_a, now=now, redeliver=True))
out["locks_kept"] = len(locks(a))
native.resolve_unknown(a, context=context_of(a), entered=False); frappe.db.commit()
out["resolved"] = [state(a).status, state(a).reason, len(locks(a))]
''',
    "a_present_computer_keeps_its_lease": r'''
a = prepared(recognized("RECLAIM-A3"))
claim = native.lease(a, context=context_of(a), adapter=pc_a, now=now); frappe.db.commit()
later = now + timedelta(seconds=300)
native.extend(a, context=context_of(a), adapter=pc_a, now=later); frappe.db.commit()
expiry = frappe.db.get_value("Cowork Push Record", a, "lease_expires_at")
out["extended"] = [str(expiry), str((later + timedelta(seconds=120)).replace(tzinfo=None))]
out["locks_follow"] = sorted({str(row.lease_expires_at) for row in locks(a)})
out["early_recover"] = native.recover_expired(a, context=context_of(a), adapter=pc_a, now=later + timedelta(seconds=60))["status"]
frappe.db.commit()
lost = native.recover_expired(a, context=context_of(a), adapter=pc_a, now=later, lost=True); frappe.db.commit()
out["lost"] = [lost["status"], lost["result_unknown"], len(locks(a))]
late = native.ack(a, context=context_of(a), adapter=pc_a, fence=claim["fence"], attempt_token=claim["attempt_token"],
                  success=True, express_docnum="LATE"); frappe.db.commit()
out["late"] = [late["status"], len(locks(a))]
''',
    "a_queued_document_names_the_unknown_one_blocking_it": r'''
from pearnly_cowork.assistant import operator_source
first, second = recognized("RECLAIM-A4"), recognized("RECLAIM-B4")
a, b = prepared(first), prepared(second)
native.lease(a, context=context_of(a), adapter=pc_a, now=now); frappe.db.commit()
native.recover_expired(a, context=context_of(a), adapter=pc_a, now=now + timedelta(seconds=121)); frappe.db.commit()
company_name = company.get("name") or company.get("company") if isinstance(company, dict) else company
rows = {row["doc_id"]: row for row in operator_source.status(company_name, [first, second])}
out["blocked"] = [rows[second]["state"], rows[second].get("blocked_by")]
out["blocker"] = [rows[first]["state"], rows[first]["reason"], rows[first].get("blocked_by")]
native.resolve_unknown(a, context=context_of(a), entered=False); frappe.db.commit()
out["after"] = operator_source.status(company_name, [second])[0].get("blocked_by")
''',
}

EXPECT = {
    "lost_goes_back_only_to_its_own_computer": lambda o: (
        o["b_waits"] == "waiting_lock" and o["other_computer"] == "LeaseUnavailable"
        and o["again"]["leased"] and o["again"]["fence"] == o["again"]["first_fence"] + 1
        and o["locks"] == [o["again"]["fence"]] * len(o["locks"]) and o["locks"]
        and o["old_attempt"] == "unknown" and o["b_still_waits"] == "waiting_lock" and o["old_receipt"]
        and o["done"] == ["success", "RR-ONCE", 0] and o["b_after"] is True),
    "unknown_goes_back_until_the_limit": lambda o: (
        o["rounds"] == [["manual", 1, o["locks_kept"]], ["redelivered", True, 0]] * 2 + [["manual", 1, o["locks_kept"]]]
        and o["locks_kept"] > 0 and o["after_limit"] == "LeaseUnavailable"
        and o["resolved"] == ["manual", "confirmed_not_entered", 0]),
    "a_present_computer_keeps_its_lease": lambda o: (
        o["extended"][0] == o["extended"][1] and o["locks_follow"] == [o["extended"][0]]
        and o["early_recover"] == "leased" and o["lost"] == ["manual", True, o["lost"][2]] and o["lost"][2] > 0
        and o["late"] == ["success", 0]),
    "a_queued_document_names_the_unknown_one_blocking_it": lambda o: (
        o["blocked"][0] == "queued" and (o["blocked"][1] or {}).get("ref") == "RECLAIM-A4"
        and o["blocker"][:2] == ["not_done", "push.result_unknown"] and not o["blocker"][2] and o["after"] is None),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", default="cowork-new.regression.localhost")
    site = parser.parse_args().site
    lab, failed = Lab(), 0
    for name, body in SCENARIOS.items():
        seen = lab.python(site, COMMON + body + '\nout["cleanup"] = cleanup()\nprint(json.dumps(out, default=str))')
        ok = bool(EXPECT[name](seen))
        failed += not ok
        print(("ok   " if ok else "FAIL ") + name + "  " + json.dumps(seen, ensure_ascii=False, default=str))
    left = lab.python(site, CLEANUP + 'print(json.dumps(leftovers()))')
    print("leftovers", json.dumps(left))
    return 1 if failed or any(left.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
