"""引导运行记录在真实站点上的核对（只在任务自有的回归实验室里跑，PEARNLY_REGRESSION_LAB=1；总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 2.4）。

离线测试（``tests/test_cc_guide_run.py``）用内存里的记录；这里核真实库上的那几样：

1. ``Pearnly Assistant Guide Run`` 随 migrate 建好了表与列；
2. 开始 → 作答 → 复核 → 确定走一遍，记录落库、版本递增、再读出来一致；
3. **两个连接同时点同一个确定**：业务函数只被调一次，两边拿到同一个结果（行锁抢占 + 先落「已确定」再办）；
4. 两个连接同时答同一张卡：后到的被告知卡变了，不覆盖先到的答案；
5. 事件按版本出、增量只给锚点之后改过的；删对话时一起删。

对话上下文、声明与产品函数用替身（站点上还没有按表单的业务；会话与账套的真实读法在接上 ``sessions.py`` 之后由流程测试核）。只写本脚本
自己建的记录，跑完删。

    env/bin/python /workspace/scripts/regression/guide_run_check.py [站点]
"""
import json
import os
import sys
import threading

SITE = sys.argv[1] if len(sys.argv) > 1 else "erp.regression.localhost"
SESSION = "sesn_guide_run_check"
USER = "Administrator"
GUIDE = {"id": "labcheck", "label_key": "guide.business.labcheck", "icon": "wallet", "order": 1, "modes": ["chat"],
         "ledgers": ["express_local"], "opening_key": "guide.open.labcheck", "source": "form", "writes": True,
         "expires": ["ledger"],
         "steps": [{"key": "customer", "input": "choice", "rule": "always", "options": "lab.customers"},
                   {"key": "invoices", "input": "amounts", "rule": "always", "after": ["customer"], "options": "lab.invoices"}],
         "draft": "lab.draft", "review": "lab.review", "confirm": "lab.release"}
RELEASED = []
RELEASE_LOCK = threading.Lock()


def _options(codes, wanted):
    return {"options": [{"code": c, "name": c} for c in codes if wanted is None or c in wanted], "more": False}


def _release(ctx, values, draft, consent):
    import time

    with RELEASE_LOCK:
        RELEASED.append(consent)
    time.sleep(0.5)     # 让另一个连接有机会撞上来
    return {"status": "queued", "consent": consent}


CALLS = {"lab.customers": lambda ctx, txt="", start=0, page_length=20, codes=None: _options(["C001", "C002"], codes),
         "lab.invoices": lambda ctx, txt="", start=0, page_length=20, codes=None: _options(["IV001"], codes),
         "lab.draft": lambda ctx, values: "R:" + ctx["run"], "lab.review": lambda ctx, draft: {"draft": draft},
         "lab.release": _release}
NOW = {"session_id": SESSION, "mode": "chat", "company": "CO1", "ledger": "B1", "period": "2026-11", "stopped_at": None}


def connect():
    import frappe
    from pearnly_core.assistant.guide import registry, run

    frappe.init(site=SITE, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user(USER)
    registry._raw = lambda: [GUIDE]
    run._call = lambda path, *args, **kwargs: CALLS[path](*args, **kwargs)
    run._conversation = lambda session_id: dict(NOW)
    return frappe, run


def in_thread(fn, out, key):
    def body():
        frappe, run = connect()
        try:
            out[key] = fn(run)
            frappe.db.commit()
        except Exception as exc:  # noqa: BLE001 — 核对脚本：把出错原样带回主线程
            out[key] = exc
        finally:
            frappe.destroy()
    thread = threading.Thread(target=body)
    thread.start()
    return thread


def check(condition, text):
    print(("ok   " if condition else "FAIL ") + text)
    if not condition:
        check.failed += 1


check.failed = 0


def main():
    if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
        raise SystemExit("guide_run_check: only in the regression lab")
    frappe, run = connect()
    run.forget(SESSION)
    frappe.db.commit()

    columns = set(frappe.db.get_table_columns(run.DOCTYPE))
    check({"session_id", "user", "business", "status", "company", "ledger", "period", "answers", "draft", "version", "consent",
           "result", "ended_reason"} <= columns, "the table has every column of the run record")

    started = run.start(SESSION, "labcheck")
    frappe.db.commit()
    name = started["run"]
    check(started["status"] == "asking" and started["version"] == 1, "start writes a run in asking, version 1")

    # 两个连接同时答同一张卡（都基于版本 1）
    out = {}
    threads = [in_thread(lambda r: r.answer(name, {"customer": "C001"}, version=1), out, "a"),
               in_thread(lambda r: r.answer(name, {"customer": "C002"}, version=1), out, "b")]
    [t.join() for t in threads]
    outcomes = sorted(str((out[k] or {}).get("outcome", "")) if isinstance(out[k], dict) else repr(out[k]) for k in ("a", "b"))
    check(outcomes == ["", "changed"], f"two answers to the same card: one is taken, the other is told the card changed ({outcomes})")
    frappe.db.rollback()
    stored = frappe.db.get_value(run.DOCTYPE, name, ["answers", "version"], as_dict=True)
    winner = next(k for k in ("a", "b") if isinstance(out[k], dict) and not out[k].get("outcome"))
    check(json.loads(stored.answers) == {"customer": "C001" if winner == "a" else "C002"} and stored.version == 2,
          "the stored answer is the one that was taken, version 2")

    review = run.answer(name, {"invoices": [{"code": "IV001", "amount": "100"}]})
    frappe.db.commit()
    check(review["status"] == "reviewing" and review["review"] == {"draft": "R:" + name}, "all steps answered: draft made, under review")

    # 两个连接同时点同一个确定
    out = {}
    version = review["version"]
    threads = [in_thread(lambda r: r.confirm(name, version), out, "a"), in_thread(lambda r: r.confirm(name, version), out, "b")]
    [t.join() for t in threads]
    check(len(RELEASED) == 1, f"two confirms at once release once (released {len(RELEASED)})")
    states = sorted(str(out[k].get("status")) if isinstance(out[k], dict) else repr(out[k]) for k in ("a", "b"))
    check(all(s in ("done", "confirmed") for s in states), f"both confirms come back without an error ({states})")
    frappe.db.rollback()
    final = frappe.db.get_value(run.DOCTYPE, name, ["status", "consent", "result"], as_dict=True)
    check(final.status == "done" and final.consent == f"guide:{name}:{version}" and json.loads(final.result)["status"] == "queued",
          "the record ends done with the click as consent and the result stored")
    again = run.confirm(name, version)
    check(again["status"] == "done" and len(RELEASED) == 1, "the same confirm later returns the result, still released once")

    events = run.feed(SESSION)
    check([e["run"] for e in events] == [name] and events[0]["status"] == "done" and events[0]["id"].startswith(f"pgd_{name}_"),
          "the feed gives the run's current event")
    check(run.feed(SESSION, since=frappe.utils.add_days(frappe.utils.now_datetime(), 1)) == [], "an anchor after the last change gives nothing")

    second = run.start(SESSION, "labcheck")["run"]
    NOW["ledger"] = "B2"
    expired = run.answer(second, {"customer": "C001"})
    check(expired["status"] == "expired" and expired["reason"] == "ledger", "a changed account set expires the open run")
    NOW["ledger"] = "B1"
    frappe.db.commit()

    run.forget(SESSION)
    frappe.db.commit()
    check(frappe.db.count(run.DOCTYPE, {"session_id": SESSION}) == 0, "deleting the conversation deletes its runs")
    frappe.destroy()
    print("guide_run_check: " + ("FAILED %d" % check.failed if check.failed else "all ok"))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
