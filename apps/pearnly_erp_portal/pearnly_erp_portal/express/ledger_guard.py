"""单据上的账本栏只由写账链路写（11-28 并发冲突 B ③）。

账本栏（``ledger_*``：排队状态、写账件、Express 单号、原因、要问的项……）由 `express.push` 用不改修改时间的 ``set_value`` 写；
采购 / 销售 / 出入库 / 盘点 / 收银日汇总这几种单据在付款、附件、作废、修改等路径上会整份 ``doc.save()``。关快照隔离后，任何一条
路径拿着读视图里的旧副本保存，保存前比对拦不住（修改时间没变），就把账本栏盖回旧值：刚排上的写账件丢了（作废撤不掉、照样写进
Express），或刚记下的 Express 单号被盖空（巡检再写一遍、重复入账）。所以这几种单据保存前，用加锁读把库里账本栏的现值放回文档
对象：业务代码本来就不经保存改账本栏（只经 `push`），保存只会写回库里此刻的值。已提交的单的票面合计同理（只经
`express.printed.set_total` 改）。
"""
import frappe


# 已提交（不再是草稿）的单上只经静默写改的栏：票面合计只经 `express.printed.set_total` 改（草稿由提交接口照页面写，不在此列）
SUBMITTED_ONLY = ("printed_total",)


def keep_current(doc) -> None:
    """控制器 ``validate`` 第一句调。新建的单不管（账本栏由提交后的 `push.queue` 写）。真要经保存改账本栏的写法（现在没有）显式
    置 ``doc.flags.ledger_write = True``。"""
    if doc.is_new() or doc.flags.get("ledger_write"):
        return
    fields = [df.fieldname for df in doc.meta.fields
              if df.fieldname.startswith("ledger_") and df.fieldtype not in ("Section Break", "Column Break", "Tab Break")]
    extra = [name for name in SUBMITTED_ONLY if doc.meta.has_field(name)]
    if not fields and not extra:
        return
    row = frappe.db.get_value(doc.doctype, doc.name, [*fields, *extra, "status"], as_dict=True, for_update=True)
    if not row:
        return
    for field in fields:
        doc.set(field, row.get(field))
    if row.get("status") not in (None, "", "draft"):
        for field in extra:
            doc.set(field, row.get(field))
