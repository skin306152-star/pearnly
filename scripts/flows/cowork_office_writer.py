"""流程用例：Cowork 替写（用户 11-17 定稿「公司里只要有一台开着 CC、够得到那本账套、登录人有权限的电脑，任何成员提交的单就能写」，
`route_contracts.decide` 的 ``stand_in``，在电脑来领的那一刻判）。

实验栈里没有真的 CC 桌面版：按新版 CC 桌面版的样子每台电脑带自己的设备密钥登记（`credentials._register_device`）、按心跳的真实
入口报可达行（同 `cc_conversation_ledger.py`），
把登录记录设成指定的人；「关着」= 把那把密钥的最近报到时刻拨回一小时前。电脑来领走正式的领单入口（`companion.transport.lease`）。
B（录入员）在录入工作台推单（复核页「提交」同一个 `application.submit`，再走录入页「确认发送」同一个 `service.confirm`），核：

0. 旧件闸：存量站点迁移前排的件，迁移（补丁写上切换时刻）后别人的电脑不替；
1. B 一台电脑都没有：照样排队；办公室 A 那台（在线）来领 → 领到，替写的人记 A（``writer``）、按 ``stand_in`` 写，录入人仍是 B；
2. B 自己有一台够得到这本的电脑、但关着，A 的在线 → A 那台领到（不再「本人电脑关着就只等本人」）；
3. B 自己的那台在线 → A 那台不领，B 自己的领到（替写的人空）；
4. 能替写的人没有这条连接的使用权（办公室那台只有 C 登录过）、B 自己的也不是他的了 → 确认停在 ``push.no_writer``。

没有页面步骤。cleanup 按连接删掉造的数据与电脑。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cowork_operator_lib import BASE, CLEANUP, LABEL  # noqa: E402

TITLE = "Cowork 替写（11-17 定稿）：公司里在线、够得到、登录人有权限的电脑写任何成员的单，提交人自己的在线时用自己的；录入人不变；没人有权限照旧没有能写的电脑"
A, B, C = "admin@cowork.regression", "intake@cowork.regression", "stocktake@cowork.regression"
DEVICES = "Cowork CC Desktop Device"

# 连接也给 B 用（录入员）；C 不在名单里（兜底的人没有这条连接的使用权）
SEED = BASE.replace('users=["admin@cowork.regression"]', f'users=[{A!r}, {B!r}]') + f'''
import json as _json
from pearnly_cowork.companion import credentials, heartbeat, queue_heads, pool as pool_state
from pearnly_cowork.push.application import service as app_service
mapping = {{k: conf[k] for k in ("fallback_acc", "vat_input_acc", "ap_acc", "revenue_acc", "vat_output_acc", "ar_acc")}}
def computer(users):
    """签发一台电脑的密钥、按心跳报够得到 FLOWOP，登录记录设成 ``users``；回 ``{{machine（领单用的密钥上下文）, device}}``。"""
    # 新版 CC 桌面版的样子：每台电脑带自己的设备密钥登记（`credentials._register_device`，签发人 = 登录的那位），彼此不顶替
    frappe.set_user(sorted(users)[0])
    made = credentials._register_device(conn["name"], "express", sorted(users)[0], uuid4().hex, uuid4().hex,
                                        "flow office " + uuid4().hex[:6], False)
    frappe.db.commit()
    frappe.set_user({A!r})
    machine = credentials.authenticate(made["token"])
    heartbeat._receive(machine, {{"account_set": "FLOWOP", "express_root": "D:\\\\FLOWOP",
        "account_sets": [{{"code": "FLOWOP", "name": "Flow operator 69", "path": "FLOWOP", "root": "D:\\\\FLOWOP",
                           "writable": True, "mapping": mapping, "company": "Flow Operator Co", "tax_id": "0994000777001"}}],
        "accounts": [{{"code": code}} for code in ("A", "V", "P", "S", "O", "R", "5300")],
        "catalog": {{"products": [], "customers": []}}, "max_payload_version": 99}})  # 支持现有全部载荷版本
    device = credentials.device_id(machine.credential_id)
    frappe.db.set_value({DEVICES!r}, device, {{"label": {LABEL!r}, "signed_in_users": _json.dumps(sorted(users)),
                                             "signed_in_user": sorted(users)[0]}}, update_modified=False)
    frappe.db.commit()
    return {{"machine": machine, "device": device}}
def pushed(number):
    """B 推一张：提交（准备）→ 确认发送，回 {{"record", "status", "reason"}}。"""
    frappe.set_user({B!r})
    name = recognized(number)
    doc = storage.read(name)
    made = app_service.submit(name, conn["name"], uuid4().hex, doc.record_version,
                              connections.detail(conn["name"])["revision"], "FLOWOP", manual=True)
    frappe.db.commit()
    out = {{"doc": name, "status": made.get("status"), "reason": made.get("reason") or ""}}
    if made.get("status") == "prepared":
        try:
            got = app_service.confirm(made["name"], made["revision"], "confirm:" + uuid4().hex)
            out.update(status=got.get("status"), record=made["name"])
        except Exception as error:
            frappe.db.rollback()
            out.update(status="refused", reason=str(error), record=made["name"])
        frappe.db.commit()
    frappe.set_user({A!r})
    return out
'''


def prepare(lab, manifest):
    import uuid

    tag = uuid.uuid4().hex[:6].upper()
    out = lab.python(manifest["site"], CLEANUP + SEED + f"""
from datetime import timedelta
from pearnly_cowork.companion import transport
def off(machine):
    frappe.db.set_value(credentials.DOCTYPE, machine.credential_id, "last_seen_at",
                        frappe.utils.now_datetime() - timedelta(hours=1), update_modified=False)
    frappe.db.commit()
def why(machine):
    # 领不到时看卡在哪（只读，结果带进 seen）：这台够得到的账套、排头、谁能替写
    from pearnly_cowork.companion import devices as _devices
    from pearnly_core.cc_desktop.wire import route_contracts as _rc
    device = credentials.device_of(machine.credential_id) or ""
    users = _devices.signed_in_of(device)
    reach = sorted(pool.writable_reach(conn["name"], device))
    doc = frappe.get_doc("Cowork ERP Connection", conn["name"])
    helper = pool.stand_in_user(doc, users, "")
    heads = queue_heads.heads(doc.name, doc.workspace, doc.client, reach, device=device, users=users,
                              since=_rc.db_time(frappe.conf.get("pearnly_writer_rule_since")) if helper else "")
    return {{"device": device, "users": sorted(users), "reach": reach, "helper": helper,
            "heads": [(h.name, h.entered_by, str(h.created_at), h.payload_version) for h in heads],
            "reachers": pool.reachers(conn["name"], "flowop", writable=True)}}
def take(machine):
    got = transport.lease(machine, {{}})
    frappe.db.commit()
    if not got.get("jobs"):
        diagnosis.append(why(machine))
    return [job.get("history_id") for job in (got.get("jobs") or [])]  # 领到的是哪张票
def record(name):
    return frappe.db.get_value("Cowork Push Record", name or "", ["status", "entered_by", "writer", "write_route",
                                                                   "written_by_device"], as_dict=True) or {{}}
diagnosis = []
office = computer([{A!r}])
from datetime import datetime, timezone
frappe.conf.pearnly_writer_rule_since = None       # 0. 旧件闸：这件排在存量站点迁移之前
old = pushed("FLOW-OW-{tag}-0")
# 迁移那一刻补丁写上切换时刻（`writer_rule.stamp_since`；这里只配在这个造数据进程里，站点配置文件不动）
frappe.conf.pearnly_writer_rule_since = datetime.now(timezone.utc).isoformat()
taken_old = take(office["machine"])                 # 迁移后来领：迁移前排的这件不替，照旧等 B 自己的电脑
old_row = record(old.get("record"))
first = pushed("FLOW-OW-{tag}-1")                  # 1. B 一台电脑都没有
taken_first = take(office["machine"])
first_row = record(first.get("record"))
mine = computer([{B!r}])                            # 2. B 自己的电脑关着、A 的在线（每台同时只领一件：另起一台 A 的）
off(mine["machine"])
office2 = computer([{A!r}])
second = pushed("FLOW-OW-{tag}-2")
taken_second = take(office2["machine"])
second_row = record(second.get("record"))
mine_online = computer([{B!r}])                     # 3. B 自己的那台在线：A 不替
office3 = computer([{A!r}])
third = pushed("FLOW-OW-{tag}-3")
taken_by_office = take(office3["machine"])
taken_by_mine = take(mine_online["machine"])
third_row = record(third.get("record"))
for machine in (mine, mine_online):                 # 4. 能替写的人没有使用权、B 的电脑也不是他的了
    frappe.db.set_value({DEVICES!r}, machine["device"], {{"signed_in_users": _json.dumps(["nobody@example.com"]),
                                                       "signed_in_user": "nobody@example.com"}}, update_modified=False)
for machine in (office, office2, office3):
    frappe.db.set_value({DEVICES!r}, machine["device"], {{"signed_in_users": _json.dumps([{C!r}]), "signed_in_user": {C!r}}},
                        update_modified=False)
frappe.db.commit()
none = pushed("FLOW-OW-{tag}-4")
print(json.dumps({{"old": old, "taken_old": taken_old, "old_row": old_row, "first": first, "taken_first": taken_first, "first_row": first_row, "second": second,
                  "taken_second": taken_second, "second_row": second_row, "third": third, "taken_by_office": taken_by_office,
                  "taken_by_mine": taken_by_mine, "third_row": third_row, "none": none,
                  "office": office["device"], "office2": office2["device"], "office3": office3["device"],
                  "diagnosis": diagnosis}}, default=str))
""")
    return {**out, "tag": tag, "cases": []}


def steps(params, camera_code=None):
    return []  # 没有页面：领单与状态都在 prepare 里按正式函数走完，verify 判


def judge(params, results, camera_code=None):
    return []


def verify(lab, manifest, params, camera_code=None):
    left = lab.python(manifest["site"], CLEANUP + f"""
cleanup()
frappe.db.delete({DEVICES!r}, {{"label": {LABEL!r}}})
frappe.db.commit()
print(json.dumps({{**leftovers(), "devices": frappe.db.count({DEVICES!r}, {{"label": {LABEL!r}}})}}))
""")
    first, second, third = (params.get(k) or {} for k in ("first_row", "second_row", "third_row"))
    office, none = params.get("office"), params.get("none") or {}
    old, old_row = params.get("old") or {}, params.get("old_row") or {}
    return [
        {"case": "旧件闸：存量站点迁移前排的件（B 没有电脑）迁移后不由 A 那台替写，照旧排着；迁移后排的照常替（下一格）",
         "seen": {"pushed": old, "taken": params.get("taken_old"), "row": old_row},
         "ok": old.get("status") == "pending" and old.get("doc") not in (params.get("taken_old") or [])
         and old_row.get("status") == "pending" and not old_row.get("writer")},
        {"case": "B 一台电脑都没有：照样排队；A 那台领到、替写的人记 A，录入人仍是 B",
         "seen": {"pushed": params.get("first"), "taken": params.get("taken_first"), "row": first,
                  "diagnosis": params.get("diagnosis")},
         "ok": (params.get("first") or {}).get("status") == "pending" and first.get("status") == "leased"
         and first.get("writer") == A and first.get("entered_by") == B and first.get("write_route") == "stand_in"
         and first.get("written_by_device") == office},
        {"case": "B 自己的电脑关着、A 的在线：A 那台领到（不再只等本人的电脑）",
         "seen": {"taken": params.get("taken_second"), "row": second},
         "ok": second.get("status") == "leased" and second.get("writer") == A and second.get("entered_by") == B
         and second.get("written_by_device") == params.get("office2")},
        {"case": "B 自己的那台在线：A 那台不领，B 自己的领到，替写的人空",
         "seen": {"by_office": params.get("taken_by_office"), "by_mine": params.get("taken_by_mine"), "row": third},
         "ok": (params.get("third") or {}).get("doc") not in (params.get("taken_by_office") or [])
         and (params.get("third") or {}).get("doc") in (params.get("taken_by_mine") or [])
         and third.get("status") == "leased" and not third.get("writer")
         and third.get("written_by_device") != params.get("office3")},
        {"case": "能替写的人没有这条连接的使用权：确认停在没有可写入该账套的电脑（push.no_writer）", "seen": none,
         "ok": none.get("status") in ("refused", "failed") and any(
             word in str(none.get("reason")) for word in ("push.no_writer", "No computer with CC Desktop"))},
        {"case": "造的连接、公司、锁、电脑都清掉了", "seen": left, "ok": not any((left or {}).values())},
    ]


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], CLEANUP + f"""
cleanup()
frappe.db.delete({DEVICES!r}, {{"label": {LABEL!r}}})
frappe.db.commit()
print("{{}}")
""")
