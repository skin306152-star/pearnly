"""流程用例：Cowork 领单时判写账人（用户 11-17 定稿，两产品同一条）的管钱一面——两台电脑同时抢同一张只领到一次、只开一次写入。

与 ERP 的 `erp_writer_at_claim.py` 同一套断言：替写以后同一张单几台电脑都看得到，两台在同一刻来领只能有一台领到、``fence``
只加一次、只有一次领取记录（Cowork Push Attempt），另一台空手、不报错。电脑来领走正式入口同一条路（`queue_api._run` 的
``conflicts.retried(transport.lease)``：撞 1020 / 1213 整笔回滚重来）；两台各自连库、各开一个线程，认好密钥后一起放行，同一刻
发起领单。Cowork 领单头一步锁这条连接（`credentials.revalidate`），同一连接上同时来的几台在库里排成先后（ERP 那边在写账件行锁上
相撞、抢输的撞 1020）：后到的等先到的提交，再看这张已被领走、空手回去。

实验栈里没有真的 CC 桌面版：电脑、推单沿用 `cowork_office_writer.py` 的造法（每台带自己的设备密钥登记、按心跳真实入口报够得到
FLOWOP，登录记录设成指定的人）。两格（先做本人这格：账套还没人写过，不受「黏在刚写过的那台」影响）：

1. 提交人 B 自己的电脑在线、同事 A 那台也在线，同一刻来领 B 的单 → B 自己的领到、不记替写人；A 那台空手。写完回执，账套空出来。
2. B 的电脑关着，A 的两台办公室电脑同一刻来领 B 的下一张 → 只有一台领到，替写的人记 A，录入人仍是 B。

没有页面步骤。设备密钥只在实验栈这一个进程里用，不出结果。cleanup 按连接删掉造的数据与电脑。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cowork_office_writer import A, B, DEVICES, SEED  # noqa: E402
from cowork_operator_lib import CLEANUP, LABEL  # noqa: E402

TITLE = "Cowork 领单时判写账人：两台电脑同时抢同一张只领到一次、只开一次写入；本人与同事同时来领时本人的领到（11-17 定稿，同 ERP）"

RACE = f'''
import threading, time
from datetime import timedelta
from pearnly_core.transaction import conflicts
from pearnly_cowork.companion import receipt_auth, transport
site, sites = frappe.local.site, frappe.local.sites_path
gate = threading.Barrier(2, timeout=20)
def claim(machine, results):
    # 两台各自连好库、认好密钥后一起放行，同一刻发起领单；领单与正式入口 `queue_api._run` 同一条路（整笔撞 1020 / 1213
    # 回滚重来）。Cowork 领单先锁这条连接（`credentials.revalidate`），同一连接上的几台在库里排成先后：后到的等先到的提交，
    # 再看这张已被领走。met：两台确实同一刻发起（没碰上这一格不算真抢）；waited：这台领单花了多久（后到的那台含等锁）
    frappe.init(site=site, sites_path=sites)
    frappe.connect()
    met = False
    try:
        frappe.set_user({A!r})
        context = credentials.authenticate(tokens[machine.credential_id])
        frappe.db.commit()
        try:
            gate.wait()
            met = True
        except threading.BrokenBarrierError:
            pass
        began = time.monotonic()
        got = conflicts.retried(transport.lease, context, {{}})
        frappe.db.commit()
        results[machine.credential_id] = {{"docs": [job.get("history_id") for job in (got.get("jobs") or [])],
                                            "handles": [job.get("log_id") for job in (got.get("jobs") or [])],
                                            "met": met, "waited": round(time.monotonic() - began, 3)}}
    except Exception as error:
        frappe.db.rollback()
        results[machine.credential_id] = {{"error": type(error).__name__ + ": " + repr(error)[:300], "met": met}}
    finally:
        frappe.destroy()
def race(*machines):
    results = {{}}
    threads = [threading.Thread(target=claim, args=(machine, results)) for machine in machines]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(90)
    frappe.db.rollback()  # 本线程的读视图换到线程们提交之后
    return results
def off(machine):
    frappe.db.set_value(credentials.DOCTYPE, machine.credential_id, "last_seen_at",
                        frappe.utils.now_datetime() - timedelta(hours=1), update_modified=False)
    frappe.db.commit()
def done(results):
    # 领到的那台照电脑写完那样回执（正式回执入口 `transport.acknowledge`）：这本账套空出来
    for credential, got in results.items():
        for handle in got.get("handles") or []:
            context = receipt_auth.authenticate(tokens[credential], handle)
            transport.acknowledge(context, {{"log_id": handle, "result": "success"}})
            frappe.db.commit()
def outcome(name, results, machines):
    row = frappe.db.get_value("Cowork Push Record", name or "", ["status", "entered_by", "writer", "write_route",
                              "written_by_device", "fence"], as_dict=True) or {{}}
    attempts = frappe.db.count("Cowork Push Attempt", {{"push_record": name or ""}})
    by = {{label: {{k: v for k, v in (results.get(machine["machine"].credential_id) or {{}}).items() if k != "handles"}}
          for label, machine in machines.items()}}
    devices_by = {{label: machine["device"] for label, machine in machines.items()}}
    return {{"row": row, "attempts": attempts, "by": by, "devices": devices_by}}
'''


def prepare(lab, manifest):
    import uuid

    tag = uuid.uuid4().hex[:6].upper()
    out = lab.python(manifest["site"], CLEANUP + SEED + RACE + f"""
tokens = {{}}
mine, office = computer([{B!r}]), computer([{A!r}])
# 1. B 自己的在线、A 的也在线，同一刻来领 B 的单（账套还没人写过）
first = pushed("FLOW-WC-{tag}-1")
got_first = race(mine["machine"], office["machine"])
own = outcome(first.get("record"), got_first, {{"mine": mine, "office": office}})
done(got_first)
# 2. B 的关着，A 的两台办公室电脑同一刻来领 B 的下一张
off(mine["machine"])
office2 = computer([{A!r}])
second = pushed("FLOW-WC-{tag}-2")
got_second = race(office["machine"], office2["machine"])
stand = outcome(second.get("record"), got_second, {{"office": office, "office2": office2}})
done(got_second)
print(json.dumps({{"first": first, "own": own, "second": second, "stand": stand}}, default=str))
""")
    return {**out, "tag": tag, "cases": []}


def steps(params, camera_code=None):
    return []  # 没有页面：领单在 prepare 里按正式入口同一条路走完，verify 判


def judge(params, results, camera_code=None):
    return []


def _one_taker(result, doc):
    """两台确实同一刻来领（都碰了头）、恰好一台领到这张、另一台空手、两台都没报错；回领到的那台的标签（没有回 None）。"""
    by = result.get("by") or {}
    takers = [label for label, got in by.items() if doc and doc in (got.get("docs") or [])]
    clean = all("error" not in got and got.get("met") and len(got.get("docs") or []) <= 1 for got in by.values()) \
        and len(by) == 2
    return takers[0] if clean and len(takers) == 1 else None


def verify(lab, manifest, params, camera_code=None):
    left = lab.python(manifest["site"], CLEANUP + f"""
cleanup()
frappe.db.delete({DEVICES!r}, {{"label": {LABEL!r}}})
frappe.db.commit()
print(json.dumps({{**leftovers(), "devices": frappe.db.count({DEVICES!r}, {{"label": {LABEL!r}}})}}))
""")
    first, own = params.get("first") or {}, params.get("own") or {}
    second, stand = params.get("second") or {}, params.get("stand") or {}
    own_taker, stand_taker = _one_taker(own, first.get("doc")), _one_taker(stand, second.get("doc"))
    own_row, stand_row = own.get("row") or {}, stand.get("row") or {}
    return [
        {"case": "B 自己的电脑与同事 A 的同一刻来领 B 的单：B 自己的领到，A 那台空手、不报错；不记替写人；fence 与领取记录各一次",
         "seen": {"pushed": first, **own},
         "ok": first.get("status") == "pending" and own_taker == "mine" and own_row.get("status") == "leased"
         and not own_row.get("writer") and own_row.get("write_route") != "stand_in" and own_row.get("entered_by") == B
         and own_row.get("written_by_device") == (own.get("devices") or {}).get("mine")
         and int(own_row.get("fence") or 0) == 1 and own.get("attempts") == 1},
        {"case": "B 的电脑关着，A 的两台同一刻来领 B 的单：只有一台领到、替写的人记 A、录入人仍是 B；fence 与领取记录各一次，另一台空手、不报错",
         "seen": {"pushed": second, **stand},
         "ok": second.get("status") == "pending" and stand_taker in ("office", "office2")
         and stand_row.get("status") == "leased" and stand_row.get("writer") == A and stand_row.get("entered_by") == B
         and stand_row.get("write_route") == "stand_in"
         and stand_row.get("written_by_device") == (stand.get("devices") or {}).get(stand_taker)
         and int(stand_row.get("fence") or 0) == 1 and stand.get("attempts") == 1},
        {"case": "造的连接、公司、锁、电脑都清掉了", "seen": left, "ok": not any((left or {}).values())},
    ]


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], CLEANUP + f"""
cleanup()
frappe.db.delete({DEVICES!r}, {{"label": {LABEL!r}}})
frappe.db.commit()
print("{{}}")
""")
