"""主档同步的批量新建（公司接入重做第 1 批，2026-11-14；评估 docs/work/2026-11-14-company-onboarding.md §1.4、§3.2）。

大账套首次落库（一万多客户）逐条 ``doc.insert`` 的时间几乎全花在 Frappe 每条的固定开销上（发号加锁、清缓存、实时推送、
每条一次往返）：实验栈 10,511 条 20.5 秒，生产约 7 分钟。这里把**新建**的行一批一批写：

- 每批先一次预留一段编号（与 Frappe 发号同一个 ``tabSeries`` 计数器、同一把行锁，预留完立刻提交放锁，网页上同时新建的
  档拿到的是这段之后的号，不会撞号）；
- 每行照 ``Document.insert`` 在写库之前的那一段原样走一遍（默认值、操作人与时间、命名、父子关系、``before_validate`` /
  ``validate`` / ``before_save``、字段长度与类型检查），所以落库的字段与逐条保存逐字段一样（`scripts/erp-masters-bulk-lab-verify.py` 核）；
- 写库用 ``frappe.db.bulk_insert``（父表、子表各一条多行 INSERT）。

``insert`` 之后被跳过的（逐项核过，均不需要）：``after_insert`` / ``on_update`` / ``on_change``（三个主档控制器与 hooks 的
doc_events 都没有）；清文档缓存（新建的档没有缓存）；列表实时刷新（同步在后台，落完公司页显示同步结果）；全局搜索（三个
DocType 没有 ``in_global_search`` 字段）；修改记录（Frappe 新建本来不写 Version）；「关注我建的文档」（后台任务的操作人是
系统）；链接校验（只有 ``express_company`` 一个链接，就是正在同步的这家公司）。

一批里撞了唯一索引（物料 ``code_key`` / ``internal_code``：同一本账套里两件编码按库的排序规则相同、内存里比不出来）：这一批
回滚到保存点、改回逐条新建（逐条的出错行只记日志，不挡整次同步，同原来）。
"""
import re

import frappe

CHUNK = 500
RESERVE_TRIES = 5
AUTONAME = re.compile(r"^format:([A-Z]+-)\{(#+)\}$")
SERIES_KEY = ""  # `format:XXX-{#####}` 的计数器键：Frappe `parse_naming_series` 只拿到 `#####` 一段，前缀是空串（各 DocType 共用一个计数器）


def reserve(doctype, count) -> list:
    """预留 ``count`` 个编号（同 Frappe 发号），立刻提交放开计数器的行锁。回编号列表。

    先提交再取：锁定读放在新事务的第一句，不带着之前的读视图（MariaDB 11.8 默认快照隔离，事务里先读过别的、计数器又被别的
    进程改过时，锁定读会报 1020「Record has changed since last read」；实验栈两本账套同时落库实测过）。仍撞上就重来几次。"""
    found = AUTONAME.match(frappe.get_meta(doctype).autoname or "")
    if not found:
        raise ValueError(f"{doctype}: unsupported autoname for bulk insert")
    prefix, digits = found.group(1), len(found.group(2))
    for attempt in range(RESERVE_TRIES):
        frappe.db.commit()
        try:
            start = _take(count)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == RESERVE_TRIES - 1:
                raise
            continue
        frappe.db.commit()
        return [prefix + ("%0" + str(digits) + "d") % (start + offset) for offset in range(1, count + 1)]


def _take(count) -> int:
    """锁住计数器、加上 ``count``，回加之前的值（不提交）。"""
    row = frappe.db.sql("select `current` from `tabSeries` where `name` = %s for update", (SERIES_KEY,))
    if row and row[0][0] is not None:
        start = int(row[0][0])
        frappe.db.sql("update `tabSeries` set `current` = %s where `name` = %s", (start + count, SERIES_KEY))
    else:
        start = 0
        frappe.db.sql("insert into `tabSeries` (`name`, `current`) values (%s, %s)", (SERIES_KEY, count))
    return start


def prepare(doctype, values, name):
    """一行要新建的档 → 已走完 ``insert`` 写库前那一段的 Document（不写库）。``values`` 里的 ``units`` 是物料单位子表。"""
    doc = frappe.get_doc({"doctype": doctype, **{key: value for key, value in values.items() if key != "units"}})
    for row in values.get("units") or []:
        doc.append("units", {"unit_name": row["unit_name"], "factor_to_base": float(row["factor"])})
    doc.flags.express_sync = True
    doc.flags.ignore_permissions = True
    doc.flags.ignore_links = True
    doc.set("__islocal", True)
    doc._action = "save"
    doc._set_defaults()
    doc.set_user_and_timestamp()
    doc.set_docstatus()
    doc.run_method("before_insert")
    doc.set_new_name(set_name=name)
    doc.set_parent_in_children()
    doc.flags.in_insert = True
    doc.run_before_save_methods()
    doc._validate()
    doc.set_docstatus()
    doc.flags.in_insert = False
    return doc


def _rows(docs):
    """按表分组的 (列, 行)：列取各行有值的字段并集（同 ``db_insert`` 的 ``get_valid_dict``），缺的填 None。"""
    tables = {}
    for doc in docs:
        for item in (doc, *doc.get_all_children()):
            tables.setdefault(item.doctype, []).append(item.get_valid_dict(convert_dates_to_str=True, ignore_virtual=True))
    out = {}
    for doctype, dicts in tables.items():
        columns = list(dict.fromkeys(column for row in dicts for column in row))
        out[doctype] = (columns, [[row.get(column) for column in columns] for row in dicts])
    return out


def write(docs) -> None:
    for doctype, (columns, rows) in _rows(docs).items():
        frappe.db.bulk_insert(doctype, columns, rows, chunk_size=CHUNK)


def insert_new(doctype, records, one) -> int:
    """批量新建 ``records``（每项是要写的字段，物料带 ``units``）。``one(values)``：逐条新建一行（撞唯一索引时的退路）。回建了几条。"""
    made = 0
    for start in range(0, len(records), CHUNK):
        chunk = records[start:start + CHUNK]
        names = reserve(doctype, len(chunk))
        docs = [prepare(doctype, values, name) for values, name in zip(chunk, names)]
        frappe.db.savepoint("erp_masters_bulk")
        try:
            write(docs)
        except Exception as error:  # noqa: BLE001  只接住撞唯一索引，其余照旧抛给同步任务记失败
            if not (frappe.db.is_duplicate_entry(error) or frappe.db.is_unique_key_violation(error)):
                raise
            frappe.db.rollback(save_point="erp_masters_bulk")
            made += sum(one(values) for values in chunk)
        else:
            made += len(docs)
        frappe.db.commit()
    return made
