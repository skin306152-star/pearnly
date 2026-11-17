"""只读统计：Cowork 站点里切换前排着的件与因「没有可写入该账套的电脑」没推成的单（替写上线前回读，用户 11-17）。

背景：11-17 起 Cowork 的单由谁的电脑写按用户定稿的一条（与 ERP 同一条）：公司里只要有一台开着 CC、够得到那本账套、登录人此刻
能用这条连接的电脑，任何成员提交的单就能写——提交人自己的电脑在线用自己的，否则由那台替他写，都不在线就排队、谁先上线谁写
（`route_contracts.decide` 的 ``stand_in``，在电脑来领的那一刻判；单上的录入人不变）。之前因 ``push.no_writer`` 没推成的单不会自己
再推，要人再点一次推送（主会话定：不写补丁、不自动改派）。切换时刻（站点配置 ``pearnly_writer_rule_since``）之前排着的件也不由
别人的电脑替写（旧件闸），照旧只等提交人自己的电脑。部署前用这个脚本只读数一遍，把数字告诉用户再定写还是作废。

在 Cowork 的 web 容器里对单个站点跑（只进 web 容器，不 exec 进数据库；只读，不写库）：

    env/bin/python /opt/pearnly/deploy-src/scripts/production/count-cowork-no-writer.py --site testcowork.pearnly.com

输出一行 JSON：``{site, since, queued: {total, oldest, would_move}, total, by_connection: {连接: {count, earliest}}, would_move}``：

- ``queued``：切换时刻之前排着（没配切换时刻就是全部）、不是定时任务的件——张数、最老的排队时刻、其中提交人自己没有在线的
  电脑而有别人的电脑能替写的张数（放开旧件闸就会由别人写的件，只是算，不改）；
- 其余：因 ``push.no_writer`` 停下的单按连接的张数与最早时刻；``would_move`` 是若现在重推、有别人的电脑能替写的张数。
"""
from __future__ import annotations

import argparse
import json

HISTORY = "Cowork OCR History"
RECORD = "Cowork Push Record"
CONNECTION = "Cowork ERP Connection"
REASON = "push.no_writer"
SCAN_MAX = 5000  # 一个站点最多看这么多张


def summarize(rows) -> dict:
    """``rows`` = ``[{connection, at, moves}]`` → ``{total, by_connection: {连接: {count, earliest}}, would_move}``。纯函数。"""
    by, moves = {}, 0
    for row in rows:
        moves += 1 if row.get("moves") else 0
        slot = by.setdefault(str(row.get("connection") or ""), {"count": 0, "earliest": ""})
        slot["count"] += 1
        at = str(row.get("at") or "")[:19]
        if at and (not slot["earliest"] or at < slot["earliest"]):
            slot["earliest"] = at
    return {"total": sum(slot["count"] for slot in by.values()), "by_connection": dict(sorted(by.items())),
            "would_move": moves}


def summarize_queued(rows) -> dict:
    """``rows`` = ``[{at, moves}]``（切换前排着的件）→ ``{total, oldest, would_move}``。纯函数。"""
    times = sorted(str(row.get("at") or "")[:19] for row in rows if row.get("at"))
    return {"total": len(rows), "oldest": times[0] if times else "", "would_move": sum(1 for row in rows if row.get("moves"))}


def collect_queued(frappe) -> tuple:
    """切换时刻之前排着、不是定时任务的推送记录；回 ``(切换时刻, rows)``。"""
    from pearnly_core.cc_desktop.wire import route_contracts
    from pearnly_cowork.connections import pool

    since = frappe.conf.get(route_contracts.WRITER_RULE_SINCE) or ""
    filters = {"status": "pending", "route_device": ["in", ["", None]]}
    if since:
        filters["created_at"] = ["<", since.replace("T", " ").replace("Z", "")[:19]]
    found = frappe.get_all(RECORD, filters=filters, fields=["name", "connection", "account_key", "actor", "entered_by",
                                                            "created_at"],
                           order_by="created_at asc", limit_page_length=SCAN_MAX)
    rows, connections = [], {}
    for row in found:
        if row.connection not in connections:
            connections[row.connection] = frappe.get_doc(CONNECTION, row.connection)  # loop-query: 只读统计，单站点有上限
        doc, actor = connections[row.connection], row.entered_by or row.actor
        peers = pool.presence(doc.name)  # loop-query: 同上
        mine = pool.owned(peers, actor)
        own_online = any((mine.get(device) or {}).get("online")
                         for device in pool.writers(doc, row.account_key, peers=mine)) if mine else False  # loop-query: 同上
        helpers = pool.stand_ins(doc, row.account_key, actor, peers=peers)  # loop-query: 同上
        rows.append({"at": row.created_at, "moves": bool(helpers) and not own_online})
    return since, rows


def collect(frappe) -> list:
    from pearnly_cowork.connections import pool

    found = frappe.get_all(HISTORY, filters={"last_push_reason": REASON},
                           fields=["name", "owner", "push_connection", "push_account_set", "last_push_record",
                                   "last_pushed_at", "modified"],
                           order_by="modified asc", limit_page_length=SCAN_MAX)
    rows, connections = [], {}
    for doc in found:
        entered = frappe.db.get_value(RECORD, doc.last_push_record, "entered_by") \
            if doc.last_push_record else None  # loop-query: 只读统计，单站点有上限
        actor = entered or doc.owner
        moves = False
        if doc.push_connection and doc.push_account_set and frappe.db.exists(CONNECTION, doc.push_connection):  # loop-query: 同上
            if doc.push_connection not in connections:
                connections[doc.push_connection] = frappe.get_doc(CONNECTION, doc.push_connection)  # loop-query: 同上
            moves = bool(pool.stand_ins(connections[doc.push_connection], doc.push_account_set, actor))  # loop-query: 同上
        rows.append({"connection": doc.push_connection, "at": doc.last_pushed_at or doc.modified, "moves": moves})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", required=True)
    parser.add_argument("--sites-path", default="/home/frappe/frappe-bench/sites")
    args = parser.parse_args(argv)
    import frappe

    frappe.init(site=args.site, sites_path=args.sites_path)
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        since, queued = collect_queued(frappe)
        print(json.dumps({"site": args.site, "since": since, "queued": summarize_queued(queued), **summarize(collect(frappe))},
                         ensure_ascii=False, default=str))
    finally:
        frappe.db.rollback()  # 只读：不留任何写
        frappe.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
