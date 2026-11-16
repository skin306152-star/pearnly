"""只读统计：ERP 站点里「已排队、写账件归的人一台能写的电脑都没有」的写账件（写账人改走 office_actor 上线前回读，用户 11-16）。

背景：11-16 起 ERP 采购 / 销售 / 出入库 / 盘点的写账件归谁与收款一致（`express.push.actor_of` → `office_actor`：开单人自己没有
登录过 CC 的电脑时，由这家公司在够得到这本账套的电脑上登录过 CC 的同事兜底）。写账件的 actor 在排队那一刻定下，改代码不改已排的件：
已排着、actor 名下没电脑的件上线后仍一直排着（页面「CC 桌面版未连接」）。主会话定不做补丁改归属，部署前用这个脚本只读数一遍，
把数字告诉用户再定。另：还没排件、靠每分钟 `push.poll` 重交的单，上线后按新规则交给办公室电脑写——这是上线后的行为，写进部署说明。

在 ERP 的 web 容器里对单个站点跑（只进 web 容器，不 exec 进数据库；只读，不写库）：

    env/bin/python /opt/pearnly/deploy-src/scripts/production/count-erp-queued-no-computer.py --site testerp.pearnly.com

输出一行 JSON：``{site, total, by_doctype: {单据类型: {count, earliest}}, would_move}``——``would_move`` 是这些件里若现在重排、
按新规则会改归某位同事（兜底找得到人）的件数（只是算，不改）。
"""
from __future__ import annotations

import argparse
import json

JOB = "Pearnly CC Job"
# ERP 写进 Express 的单据类型（同 `express.push.DIRECTIONS`；收款另有 `collection.push`，已是 office_actor）
DOCTYPES = ("Purchase Document", "ERP Sales Document", "ERP Stock Entry", "ERP POS Day Summary", "ERP Stock Count")
SCAN_MAX = 5000  # 一个站点最多看这么多件排队中的写账件


def summarize(rows) -> dict:
    """``rows`` = ``[{doctype, created, missing, moves}]`` → ``{total, by_doctype: {类型: {count, earliest}}, would_move}``。
    只数 ``missing``（写账件归的人一台登录过 CC 的电脑都没有）的件。纯函数。"""
    by, total, moves = {}, 0, 0
    for row in rows:
        if not row.get("missing"):
            continue
        total += 1
        moves += 1 if row.get("moves") else 0
        slot = by.setdefault(str(row["doctype"]), {"count": 0, "earliest": ""})
        slot["count"] += 1
        created = str(row.get("created") or "")[:19]
        if created and (not slot["earliest"] or created < slot["earliest"]):
            slot["earliest"] = created
    return {"total": total, "by_doctype": dict(sorted(by.items())), "would_move": moves}


def collect(frappe) -> list:
    from pearnly_core.cc_desktop import jobs
    from pearnly_erp_portal.express import push

    found = frappe.get_all(JOB, filters={"status": "queued", "source_doctype": ["in", list(DOCTYPES)]},
                           fields=["name", "source_doctype", "source_name", "actor", "book_key", "creation"],
                           order_by="creation asc", limit_page_length=SCAN_MAX)
    rows = []
    for job in found:
        missing = jobs.writers(job.actor, job.book_key)["state"] == "missing"  # loop-query: 只读统计，单站点有上限
        company = push.company_of(job.source_doctype, job.source_name) if missing else None  # loop-query: 同上
        moves = bool(company) and push.office_actor(job.actor, company, job.book_key) != job.actor
        rows.append({"doctype": job.source_doctype, "created": job.creation, "missing": missing, "moves": moves})
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
        print(json.dumps({"site": args.site, **summarize(collect(frappe))}, ensure_ascii=False, default=str))
    finally:
        frappe.db.rollback()  # 只读：不留任何写
        frappe.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
