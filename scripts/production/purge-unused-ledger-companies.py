"""清掉「授权后自动建出来、没有任何单据」的公司及只属于它们的主档（公司接入重做第 2 批随附；用户 11-14：「可以全部删除清空」）。

背景：第 2 批以前，CC 桌面版一报账套名单，ERP 就把位置下全部有税号的账套建成公司并整本同步（testerp 66 家、59 家从没用过、
客户 8.5 万条；mrerp 1 家）。第 2 批起只建人勾的。**必须在第 2 批上线之后才执行**，否则下一次心跳会把它们再建出来（发布清单写明）。

在 ERP 的 web 容器里对单个站点跑（只进 web 容器，不 exec 进数据库）：

    env/bin/python /opt/pearnly/deploy-src/scripts/production/purge-unused-ledger-companies.py --site testerp.pearnly.com
        → 预演（默认）：只列，不删；打印每家的引用计数、要删的条数和本次计划的指纹 plan
    ... --site testerp.pearnly.com --execute --plan <预演打印的指纹>
        → 执行：重新算一遍，指纹必须与用户确认过的那份预演一致（其间有人开了单、建了公司，指纹就变，拒绝执行）

判定「可删」（全部满足才算）：
- 公司接着 Express（``express_enabled``）——手工建的、只用 Pearnly ERP 的一律不碰；
- 没有任何业务引用：运行时从 DocType 定义里找出所有指向这家公司、它的仓、收银台、它的主档（客户 / 供应商 / 物料）的
  Link 字段，以及名为 company / project 的 Data 字段（CC 写入计划、定时任务、对话等按公司挂的记录），除下面「随公司删」的那几张外，
  任何一张表有一行引用就不删（单据、收银、班次、盘点、期初、收款、LINE 草稿与记忆、序列号、看板、知识库……都在这里被拦住）；
- 库存流水只有主档同步自己记的对齐调整（``ref_type = express_sync``）。

随公司删（只属于它的）：公司本身与子表（收款方式）、``express_company`` = 它的客户 / 供应商 / 物料（含物料单位子表）、它的仓（含仓位
子表）与这些仓的结存、同步调整流水、收银台、记住的选择（ERP Express Choice）、应收快照、单号序列、这些档的修改记录（Version）。
先处理：把它设成默认公司的人清掉默认值；把它分给某人的「可用公司」（User Permission）删掉。

幂等：删过的再跑一遍就是 0 家。只读预演不写库；执行在一个事务里逐家删、逐家提交，中途出错回滚这一家并停下。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

COMPANY = "ERP Company"
MASTERS = ("ERP Customer", "ERP Supplier", "ERP Product")
OWNER = "express_company"
SYNC_REF = "express_sync"
# 随公司删的表：表名 → 它是按什么挂到这家公司的（"company" 字段、"warehouse" 字段、主档归属）
OWN = {
    "ERP Company Payment Method": "child",
    "ERP Warehouse": "company",
    "ERP Warehouse Location": "child",
    "ERP Stock Balance": "warehouse",
    "ERP Stock Movement": "warehouse",   # 只在全是同步调整时才算「随公司删」，否则是引用（见 :func:`blockers`）
    "ERP POS Terminal": "company",
    "ERP Express Choice": "company",
    "ERP Receivable Snapshot": "company",
    "ERP Doc Sequence": "company",
    "ERP Product Unit": "child",
    **{doctype: OWNER for doctype in MASTERS},
}
TARGETS = (COMPANY, "ERP Warehouse", "ERP POS Terminal", *MASTERS)
NAME_FIELDS = ("company", "project")


def fingerprint(plan) -> str:
    """预演结果的指纹：要删的公司与各表条数（排好序）。执行时必须相同。"""
    body = json.dumps([[row["company"], sorted(row["delete"].items())] for row in plan], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def decide(candidates, references) -> tuple:
    """纯函数：``candidates`` = [{company, company_name}]，``references`` = {公司: {"表.字段": 行数}}（已去掉随公司删的）。
    回 (可删, 留下)：留下的带上拦住它的引用。"""
    keep, purge = [], []
    for row in candidates:
        found = {key: count for key, count in (references.get(row["company"]) or {}).items() if count}
        (keep if found else purge).append({**row, "blocked_by": found} if found else row)
    return purge, keep


def _links(frappe):
    """所有可能引用目标的字段：(DocType, 字段, 指向哪种目标)。来自 DocField 与 Custom Field，运行时取，不写死清单。"""
    out = []
    for table in ("DocField", "Custom Field"):
        parent = "parent" if table == "DocField" else "dt"
        rows = frappe.db.sql(f"""select `{parent}` as doctype, fieldname, fieldtype, options from `tab{table}`
            where (fieldtype = 'Link' and options in %(targets)s) or (fieldtype in ('Data', 'Link') and fieldname in %(names)s)""",
                             {"targets": TARGETS, "names": NAME_FIELDS}, as_dict=True)
        for row in rows:
            target = row.options if row.fieldtype == "Link" and row.options in TARGETS else COMPANY
            out.append((row.doctype, row.fieldname, target))
    singles = set(frappe.get_all("DocType", filters={"issingle": 1}, pluck="name", limit_page_length=0))
    return sorted({item for item in out if item[0] not in singles and item[0] != COMPANY})


def _owned(frappe, company):
    """这家公司名下的东西：仓、收银台、主档（各自的名字清单）。"""
    found = {COMPANY: [company],
             "ERP Warehouse": frappe.get_all("ERP Warehouse", filters={"company": company}, pluck="name", limit_page_length=0),
             "ERP POS Terminal": frappe.get_all("ERP POS Terminal", filters={"company": company}, pluck="name", limit_page_length=0)}
    for doctype in MASTERS:
        found[doctype] = frappe.get_all(doctype, filters={OWNER: company}, pluck="name", limit_page_length=0)
    return found


def _count(frappe, doctype, field, names, extra="") -> int:
    if not names:
        return 0
    total = 0
    for start in range(0, len(names), 1000):
        total += frappe.db.sql(f"select count(*) from `tab{doctype}` where `{field}` in %(names)s{extra}",
                               {"names": tuple(names[start:start + 1000])})[0][0]
    return int(total)


def blockers(frappe, company, owned, links) -> dict:
    """这家公司被「随公司删」以外的表引用了多少行：{"表.字段": 行数}。"""
    found = {}
    for doctype, field, target in links:
        if doctype in OWN and not (doctype == "ERP Stock Movement"):
            continue
        extra = f" and ifnull(ref_type, '') != '{SYNC_REF}'" if doctype == "ERP Stock Movement" else ""
        count = _count(frappe, doctype, field, owned.get(target) or [], extra)
        if count:
            found[f"{doctype}.{field}"] = count
    return found


def deletions(frappe, owned) -> dict:
    """执行时要删的条数（预演同样算出来给人看）。"""
    warehouses, products = owned["ERP Warehouse"], owned["ERP Product"]
    counts = {COMPANY: 1, "ERP Warehouse": len(warehouses), "ERP POS Terminal": len(owned["ERP POS Terminal"]),
              "ERP Stock Balance": _count(frappe, "ERP Stock Balance", "warehouse", warehouses),
              "ERP Stock Movement": _count(frappe, "ERP Stock Movement", "warehouse", warehouses),
              "ERP Product Unit": _count(frappe, "ERP Product Unit", "parent", products),
              **{doctype: len(owned[doctype]) for doctype in MASTERS}}
    for doctype in ("ERP Express Choice", "ERP Receivable Snapshot", "ERP Doc Sequence"):
        if frappe.db.has_column(doctype, "company"):
            counts[doctype] = frappe.db.count(doctype, {"company": owned[COMPANY][0]})
    return {key: value for key, value in counts.items() if value}


def plan(frappe) -> list:
    candidates = frappe.get_all(COMPANY, filters={"express_enabled": 1}, fields=["name as company", "company_name"],
                                order_by="name asc", limit_page_length=0)
    links = _links(frappe)
    references, owned_by = {}, {}
    for row in candidates:
        owned_by[row.company] = _owned(frappe, row.company)
        references[row.company] = blockers(frappe, row.company, owned_by[row.company], links)
    purge, keep = decide([dict(row) for row in candidates], references)
    for row in purge:
        row["delete"] = deletions(frappe, owned_by[row["company"]])
        row["defaults"] = frappe.db.count("DefaultValue", {"defkey": "company", "defvalue": row["company"]})
        row["permissions"] = frappe.db.count("User Permission", {"allow": COMPANY, "for_value": row["company"]})
    return purge, keep, owned_by


def _delete(frappe, doctype, field, names, ref_doctype=None):
    extra = " and ref_doctype = %(ref)s" if ref_doctype else ""
    for start in range(0, len(names), 1000):
        chunk = tuple(names[start:start + 1000])
        if chunk:
            frappe.db.sql(f"delete from `tab{doctype}` where `{field}` in %(names)s{extra}", {"names": chunk, "ref": ref_doctype})


def execute(frappe, row, owned) -> None:
    company = row["company"]
    warehouses, products = owned["ERP Warehouse"], owned["ERP Product"]
    frappe.db.sql("delete from `tabDefaultValue` where defkey = 'company' and defvalue = %s", (company,))
    frappe.db.delete("User Permission", {"allow": COMPANY, "for_value": company})
    _delete(frappe, "ERP Stock Balance", "warehouse", warehouses)
    _delete(frappe, "ERP Stock Movement", "warehouse", warehouses)
    _delete(frappe, "ERP Product Unit", "parent", products)
    _delete(frappe, "ERP Warehouse Location", "parent", warehouses)
    for doctype in (*MASTERS, "ERP Warehouse", "ERP POS Terminal"):
        _delete(frappe, "Version", "docname", owned[doctype], ref_doctype=doctype)
        _delete(frappe, doctype, "name", owned[doctype])
    for doctype in ("ERP Express Choice", "ERP Receivable Snapshot", "ERP Doc Sequence"):
        if frappe.db.has_column(doctype, "company"):
            frappe.db.delete(doctype, {"company": company})
    frappe.db.delete("ERP Company Payment Method", {"parent": company})
    frappe.db.delete("Version", {"ref_doctype": COMPANY, "docname": company})
    frappe.db.delete(COMPANY, {"name": company})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", required=True)
    parser.add_argument("--sites-path", default="/home/frappe/frappe-bench/sites")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", default="")
    args = parser.parse_args(argv)
    import frappe

    frappe.init(site=args.site, sites_path=args.sites_path)
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        purge, keep, owned_by = plan(frappe)
        digest = fingerprint(purge)
        report = {"site": args.site, "plan": digest, "purge": purge,
                  "keep": [{"company": row["company"], "company_name": row["company_name"], "blocked_by": row["blocked_by"]}
                           for row in keep], "executed": False}
        if args.execute:
            if args.plan != digest:
                print(json.dumps({**report, "error": "plan_changed"}, ensure_ascii=False, default=str))
                return 2
            for row in purge:
                try:
                    execute(frappe, row, owned_by[row["company"]])
                    frappe.db.commit()
                except Exception:
                    frappe.db.rollback()
                    raise
            frappe.cache.delete_value("erp-new-ledgers")
            report["executed"] = True
        else:
            frappe.db.rollback()
        print(json.dumps(report, ensure_ascii=False, default=str))
        return 0
    finally:
        frappe.destroy()


if __name__ == "__main__":
    sys.exit(main())
