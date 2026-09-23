"""只读体检：把「物料 / 库存 / 入库出库 / 采购 / 销售 / POS / 客户 / 供应商 / 公司 / 仓库」
这一串关系上**对不上**的地方一次列出来。

用户 2026-09-23："物料库存入库出库，采购销售 pos 客户供应商公司仓库，这些数据关联不上，
要么对不上，要么出错……我一点点的去试不知道要试到啥时候才发现问题"。

所以这里不是"再跑一遍业务"，而是拿**已经落库的数据**做交叉核对：

| 检查 | 在问什么 |
| --- | --- |
| `balance_mismatch` | 结存能不能由流水推回来（结存 = 该位置流水之和） |
| `orphan_balance` | 有没有"有结存没流水""结存指向不存在的商品/仓库" |
| `movement_without_source` | 每一笔流水有没有**来源单据**，来源还在不在 |
| `movement_type_unknown` | 流水类型是不是本域登记过的那几种 |
| `entry_lines_mismatch` | 一张已提交的入库/出库单：明细行与它产生的流水对不对得上 |
| `count_link_mismatch` | 已过账的盘点：差异单在不在、提交没有 |
| `stock_reference_drift` | 流水/结存里的仓库、公司还在不在；商品默认仓是不是同公司的 |
| `party_drift` | 采购的供应商、销售的客户：主档还在不在、名字与主档一致不一致 |
| `zero_cost_stock` | 有货但成本未知（**提示**，不是错；盘点调增现在就是这个样子） |
| `negative_stock` | 负结存（**提示**；本域允许超卖，但要知道有） |

只读：全部 SELECT，不写任何东西。**不 whitelist** —— 只给 `bench execute` 和本仓脚本用，
不新增对外的 API 面。`run()` 打印一份人话报告并返回结构体。
"""

import frappe

from pearnly_erp_portal.inventory import contracts

MOVEMENT = "ERP Stock Movement"
BALANCE = "ERP Stock Balance"
ENTRY = "ERP Stock Entry"
COUNT = "ERP Stock Count"

# 流水指向的来源单据：`ref_type` → (表（可以是几张，任一命中即算追到）, 报错时怎么说)。
# 这张表就是"每笔流水都能追到来源"的判据 —— 来源类型没登记，等于这笔账**不知道谁记的**。
SOURCE_BY_REF = {
    "purchase": (("Purchase Document",), "采购单"),
    "sale": (("ERP Sale",), "POS 小票"),
    "refund": (("ERP Sale",), "POS 退货"),
    "stock_entry": ((ENTRY,), "入库/出库单"),
    "count": ((ENTRY,), "盘点差异单"),
    "opening": (("ERP Stock Opening",), "期初"),
    # 作废一张单据会追加**反向流水**（`stock.reverse_movements`）：来源是被作废的那张单，
    # 可能是采购单、POS 小票或入库/出库单 —— 三张里找到就算追到。
    "void": (("Purchase Document", "ERP Sale", ENTRY), "被作废的单据"),
    # ⚠️ 这里是**同一类东西的两种写法**：销售记录页落账时用的是 DocType 全名
    # （`sales/entry.py`：`ref_type=DOCTYPE`），而采购、POS、盘点、期初用的是短标记
    # （`purchase` / `sale` / `count` / `opening`）。两边各自自洽，但成套词汇不统一 ——
    # 体检先**认它**（否则一片假警），同时在报告头上把用到的 ref_type 列出来给人看。
    "ERP Sales Document": (("ERP Sales Document",), "销售单（销售记录页）"),
}

EXAMPLE_LIMIT = 12


def _rows(sql, **values):
    return frappe.db.sql(sql, values, as_dict=True)


def _issues(kind, note, rows, *, keys, limit=EXAMPLE_LIMIT):
    """把一组命中的行收成一条问题（带前几条例子，别把整库刷屏）。"""
    if not rows:
        return None
    return {"kind": kind, "note": note, "total": len(rows),
            "examples": [{key: row.get(key) for key in keys} for row in rows[:limit]]}


def balance_mismatch():
    """结存 = 该位置流水之和。这条不成立，说明账和流水已经分家。"""
    return _rows("""
        SELECT b.product, b.warehouse, b.location, b.qty_on_hand,
               COALESCE(SUM(m.qty_base), 0) AS from_movements,
               b.qty_on_hand - COALESCE(SUM(m.qty_base), 0) AS drift
        FROM `tabERP Stock Balance` b
        LEFT JOIN `tabERP Stock Movement` m
               ON m.product = b.product AND m.warehouse = b.warehouse
              AND COALESCE(m.location, '') = COALESCE(b.location, '')
        GROUP BY b.product, b.warehouse, b.location, b.qty_on_hand
        HAVING ABS(b.qty_on_hand - COALESCE(SUM(m.qty_base), 0)) > 0.000001
        ORDER BY ABS(b.qty_on_hand - COALESCE(SUM(m.qty_base), 0)) DESC
    """)


def orphan_balance():
    """有结存、却一条流水都没有（或结存指向不存在的商品/仓库）。"""
    return _rows("""
        SELECT b.product, b.warehouse, b.location, b.qty_on_hand,
               (SELECT COUNT(*) FROM `tabERP Stock Movement` m
                 WHERE m.product = b.product AND m.warehouse = b.warehouse
                   AND COALESCE(m.location, '') = COALESCE(b.location, '')) AS movements,
               (SELECT COUNT(*) FROM `tabERP Product` p WHERE p.name = b.product) AS product_exists,
               (SELECT COUNT(*) FROM `tabERP Warehouse` w WHERE w.name = b.warehouse) AS warehouse_exists
        FROM `tabERP Stock Balance` b
        HAVING movements = 0 OR product_exists = 0 OR warehouse_exists = 0
    """)


def movement_without_source():
    """每笔流水都要追得到一个还在的来源单据（本域的核心口径：流水不可改、来源可追）。"""
    broken = []
    for row in _rows("""
        SELECT name, movement_type, ref_type, ref_id, doc_no, product, qty_base, warehouse, location
        FROM `tabERP Stock Movement` ORDER BY creation
    """):
        ref_type = (row.get("ref_type") or "").strip()
        source = SOURCE_BY_REF.get(ref_type)
        if not source:
            broken.append({**row, "why": f"来源类型没登记：{ref_type or '（空）'}"})
            continue
        tables, label = source
        ref_id = row.get("ref_id")
        if not ref_id or not any(frappe.db.exists(table, ref_id) for table in tables):
            broken.append({**row, "why": f"{label} {ref_id or '（空）'} 找不到"})
    return broken


def movement_type_unknown():
    """流水类型必须是本域登记过的那几种（`contracts.MOVEMENT_TYPES`）——
    出现别的值，说明有别的写入路径绕过了这层约定。"""
    return [row for row in _rows("""
        SELECT name, movement_type, product, qty_base, ref_type, ref_id
        FROM `tabERP Stock Movement`
    """) if row["movement_type"] not in contracts.MOVEMENT_TYPES]


def entry_lines_mismatch():
    """已提交的入库/出库单：每张单的**明细行**要能在流水里一一对上（数量与符号）。"""
    broken = []
    for doc in _rows(f"""
        SELECT name, direction, doc_no, ref_type, ref_id
        FROM `tabERP Stock Entry` WHERE status = 'submitted'
    """):
        lines = _rows("""
            SELECT COALESCE(SUM(qty), 0) AS qty FROM `tabERP Stock Entry Line` WHERE parent = %(name)s
        """, name=doc["name"])[0]["qty"] or 0
        moved = _rows("""
            SELECT COALESCE(SUM(qty_base), 0) AS qty FROM `tabERP Stock Movement` WHERE ref_id = %(name)s
        """, name=doc["name"])[0]["qty"] or 0
        signed = moved if doc["direction"] == "in" else -moved
        if abs(float(lines) - float(signed)) > 0.000001:
            broken.append({"name": doc["name"], "doc_no": doc["doc_no"], "direction": doc["direction"],
                           "lines_qty": float(lines), "movement_qty": float(signed)})
    return broken


def count_link_mismatch():
    """已过账的盘点：**有差异**的必须有差异单（`ERP Stock Entry`，`ref_type=count`）且已提交。

    实盘数与系统数一样、差异为 0 的盘点本来就不会有差异单 —— 那不是问题（第一版没区分，
    把这种盘点也报出来了，属于判太严）。
    """
    broken = []
    for count in _rows("""
        SELECT name, title, status, warehouse, difference_total
        FROM `tabERP Stock Count` WHERE status = 'Posted'
    """):
        if not count.get("difference_total"):
            continue
        entry = _rows("""
            SELECT name, status, doc_no FROM `tabERP Stock Entry`
            WHERE ref_type = 'count' AND ref_id = %(name)s ORDER BY creation DESC LIMIT 1
        """, name=count["name"])
        if not entry:
            broken.append({**count, "why": "没有差异单"})
        elif entry[0]["status"] != "submitted":
            broken.append({**count, "why": f"差异单 {entry[0]['name']} 还是 {entry[0]['status']}"})
    return broken


def stock_reference_drift():
    """流水/结存里的仓库与公司；商品的默认仓是不是**同公司**的仓。"""
    drift = []
    for row in _rows("""
        SELECT m.name, m.product, m.warehouse, m.location, w.company AS warehouse_company,
               p.default_warehouse AS product_warehouse, pw.company AS product_warehouse_company
        FROM `tabERP Stock Movement` m
        LEFT JOIN `tabERP Warehouse` w ON w.name = m.warehouse
        LEFT JOIN `tabERP Product` p ON p.name = m.product
        LEFT JOIN `tabERP Warehouse` pw ON pw.name = p.default_warehouse
    """):
        if not row["warehouse_company"]:
            drift.append({**row, "why": f"仓库 {row['warehouse']} 不存在或没有公司"})
        elif row["product_warehouse"] and row["product_warehouse_company"] and \
                row["product_warehouse_company"] != row["warehouse_company"]:
            drift.append({**row, "why": f"商品默认仓 {row['product_warehouse']} 属于"
                                        f"{row['product_warehouse_company']}，不在 {row['warehouse_company']}"})
    return drift


def party_drift():
    """采购的供应商、销售的客户：主档还在不在、单据上的名字与主档一致不一致。"""
    drift = []
    for row in _rows("""
        SELECT d.name, d.doc_no, d.supplier, d.supplier_name, s.supplier_name AS master_name
        FROM `tabPurchase Document` d LEFT JOIN `tabERP Supplier` s ON s.name = d.supplier
        WHERE d.status != 'void'
    """):
        if not row["supplier"] or row["master_name"] is None:
            drift.append({**row, "why": f"供应商主档 {row['supplier'] or '（空）'} 找不到"})
        elif (row["supplier_name"] or "").strip() != (row["master_name"] or "").strip():
            drift.append({**row, "why": "单据上的供应商名称与主档不一致"})
    for row in _rows("""
        SELECT d.name, d.system_no AS doc_no, d.customer, d.buyer_name, c.customer_name AS master_name
        FROM `tabERP Sales Document` d LEFT JOIN `tabERP Customer` c ON c.name = d.customer
        WHERE d.status != 'void'
    """):
        if not row["customer"] or row["master_name"] is None:
            drift.append({**row, "why": f"客户主档 {row['customer'] or '（空）'} 找不到"})
        elif (row["buyer_name"] or "").strip() != (row["master_name"] or "").strip():
            drift.append({**row, "why": "单据上的客户名称与主档不一致"})
    return drift


def zero_cost_stock():
    """有货、但成本未知（`cost_known=0`）：价值与毛利都算不出来。**提示**，不是错。"""
    return _rows("""
        SELECT product, warehouse, location, qty_on_hand FROM `tabERP Stock Balance`
        WHERE qty_on_hand > 0 AND COALESCE(cost_known, 0) = 0
        ORDER BY qty_on_hand DESC
    """)


def negative_stock():
    return _rows("""
        SELECT product, warehouse, location, qty_on_hand FROM `tabERP Stock Balance`
        WHERE qty_on_hand < 0 ORDER BY qty_on_hand
    """)


def ref_type_counts():
    """体检报告头上那行"账是谁记的"：各来源类型各多少笔（顺带暴露词汇不统一）。"""
    rows = _rows("""
        SELECT ref_type, COUNT(*) AS n FROM `tabERP Stock Movement`
        GROUP BY ref_type ORDER BY n DESC
    """)
    return {row["ref_type"] or "（空）": row["n"] for row in rows}


CHECKS = [
    ("balance_mismatch", "结存 ≠ 该位置流水之和", balance_mismatch,
     ["product", "warehouse", "location", "qty_on_hand", "from_movements", "drift"]),
    ("orphan_balance", "有结存但没流水 / 指向不存在的商品或仓库", orphan_balance,
     ["product", "warehouse", "location", "qty_on_hand", "movements", "product_exists", "warehouse_exists"]),
    ("movement_without_source", "流水追不到来源单据", movement_without_source,
     ["name", "movement_type", "ref_type", "ref_id", "doc_no", "why"]),
    ("movement_type_unknown", "流水类型没登记", movement_type_unknown,
     ["name", "movement_type", "product", "qty_base"]),
    ("entry_lines_mismatch", "单据明细与它产生的流水对不上", entry_lines_mismatch,
     ["name", "doc_no", "direction", "lines_qty", "movement_qty"]),
    ("count_link_mismatch", "盘点差异单缺失或没提交", count_link_mismatch,
     ["name", "title", "warehouse", "why"]),
    ("stock_reference_drift", "商品默认仓与它落账的仓不在同一家公司（多公司租户要确认）", stock_reference_drift,
     ["name", "product", "warehouse", "why"]),
    ("party_drift", "供应商/客户与主档对不上", party_drift,
     ["name", "doc_no", "supplier", "supplier_name", "customer", "buyer_name", "why"]),
]

# 这两条是"提示"：不是数据错了，是数据还不够（成本没定 / 卖了还没有的货）。
NOTES = [
    ("zero_cost_stock", "有货但成本未知（价值与毛利算不出来）", zero_cost_stock,
     ["product", "warehouse", "location", "qty_on_hand"]),
    ("negative_stock", "负结存（本域允许超卖，但要有数）", negative_stock,
     ["product", "warehouse", "location", "qty_on_hand"]),
]


def report():
    """跑一遍所有检查，返回结构（`issues` 是问题，`notes` 是提示）。"""
    issues, notes, counts = [], [], {}
    for kind, note, check, keys in CHECKS:
        rows = check()
        counts[kind] = len(rows) if isinstance(rows, list) else 0
        found = _issues(kind, note, rows, keys=keys)
        if found:
            issues.append(found)
    for kind, note, check, keys in NOTES:
        rows = check()
        counts[kind] = len(rows) if isinstance(rows, list) else 0
        found = _issues(kind, note, rows, keys=keys)
        if found:
            notes.append(found)
    return {"counts": counts, "issues": issues, "notes": notes,
            "ref_types": ref_type_counts(),
            "checked": {"movements": frappe.db.count(MOVEMENT), "balances": frappe.db.count(BALANCE),
                        "entries": frappe.db.count(ENTRY), "counts": frappe.db.count(COUNT)}}


def run():
    """打印一份人话报告（`bench execute pearnly_erp_portal.inventory.audit.run`）。"""
    result = report()
    checked = result["checked"]
    print(f"[体检] 看了 {checked['movements']} 笔流水 / {checked['balances']} 条结存 / "
          f"{checked['entries']} 张出入库单 / {checked['counts']} 次盘点")
    print("[体检] 账是谁记的：" + "、".join(f"{kind} {n}" for kind, n in result["ref_types"].items()))
    if not result["issues"] and not result["notes"]:
        print("[体检] 没有发现问题，也没有提示。")
        return result
    for group in result["issues"]:
        print(f"\n✗ {group['kind']}：{group['note']} —— {group['total']} 条")
        for example in group["examples"]:
            print("   ", {key: value for key, value in example.items() if value not in (None, "")})
    for group in result["notes"]:
        print(f"\n! {group['kind']}：{group['note']} —— {group['total']} 条")
        for example in group["examples"]:
            print("   ", {key: value for key, value in example.items() if value not in (None, "")})
    if not result["issues"]:
        print("\n[体检] 没有硬问题（上面 `!` 那些是提示）。")
    return result
