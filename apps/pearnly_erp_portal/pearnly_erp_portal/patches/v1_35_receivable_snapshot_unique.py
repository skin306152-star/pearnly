"""应收快照（``ERP Receivable Snapshot``）加（book_key, docnum）唯一索引之前，先合并已有的重复行（11-28 并发冲突 ④ C1）。

为什么会有重复：网页 / LINE / CC 当场落库与每分钟的定时落库同时落同一份读回，两路的普通读都看不到对方刚插的行，各插一行
（线上关了快照隔离）。落库已改成排他取走、按账套串行、加锁读（`collection.snapshot`），唯一索引是最后一道。

放在 ``[pre_model_sync]``：建唯一索引时库里不能有重复。同一本账套同一张发票（按库的排序规则比，与唯一索引同一口径）留读取时刻
最新的一行（同时刻留后改的），其余删掉——快照只是 Express 的读回缓存，删掉的那几行下一次读回照样会对齐；收款记录不引用快照行。
删了几行记一条错误日志。然后建唯一索引（同 DocType 的 ``on_doctype_update``：定义没变时模型同步不会叫它）。可重复执行
（第二次没有重复、索引已在，什么都不改）。
"""
import frappe

DOCTYPE = "ERP Receivable Snapshot"
CHUNK = 500


def plan():
    """要删的行名（每组重复里除留下那行以外的）。只读。"""
    table = f"`tab{DOCTYPE}`"
    extra = []
    for book_key, docnum in frappe.db.sql(f"""select book_key, docnum from {table}
            group by book_key, docnum having count(*) > 1"""):
        names = frappe.db.sql_list(f"""select name from {table} where book_key = %s and docnum = %s
                order by read_at is null, read_at desc, modified desc, name desc""", (book_key, docnum))
        extra += names[1:]
    return extra


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    extra = plan()
    for start in range(0, len(extra), CHUNK):
        frappe.db.delete(DOCTYPE, {"name": ["in", extra[start:start + CHUNK]]})
    if extra:
        frappe.log_error(title="ERP receivable snapshot duplicates merged", message=f"deleted {len(extra)} duplicate rows")
    from pearnly_erp_portal.pearnly_erp_portal.doctype.erp_receivable_snapshot import erp_receivable_snapshot

    erp_receivable_snapshot.on_doctype_update()
