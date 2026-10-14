"""公司主档页的「记住的选择」（ADR-020）：不搬家、不改键，按这家公司的账套与税号现算汇总，读、删、改都经各域的公开入口。

| 来源 | 归属 | 读 | 删 / 改 |
|---|---|---|---|
| `express`（对方档、存货、费用科目、付款、抵进项、部门、收款科目） | 账套在这家公司的账套里 | `push.choices.of_sets` | `forget`（这家公司各年度一起删），付款 / 抵进项可改（`record`） |
| `pos`（POS 版式） | 账套的公司键（`pos_import.service.company_key`） | `layouts.of_companies` | `layouts.forget` |
| `income`（收入对账预设） | 本公司税号（`presets.income_prefix`） | `preset_store.under` | `preset_store.forget` |
| `history`（从 Express 历史学到的「供应商 → 科目」，补充 丙） | 科目建议那一包产出 | hooks `cowork_company_history_choices` | hooks `cowork_company_history_forget` |

- 分店（补充 甲.5）：对方档（`party`）的归类键带分店号，同一税号各分店按税号合并成一行显示，删的时候各分店一起删；
  费用科目、付款、抵进项本来就按对方税号记（不分店），同一供应商不同分店共用。
- 页面上的 `key` 是服务端给的不透明串（`encode`），解出来后再核一次它确实属于这家公司。
- 每类最多显示 `SHOWN` 条，每张表一次 IN 查询、最多读 `READ_MAX` 条。预扣税记忆（按收款方、全所通用）、供应商默认、
  单据录不录、银行预设（键里没有公司）不显示。
"""
from __future__ import annotations

import base64
import binascii
import json

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.push import choices as express_choices
from pearnly_cowork.push.express.choices import KINDS, PAYMENT_VALUES, VAT_VALUES

READ_MAX = 200
SHOWN = 30
EDITABLE = {"payment": PAYMENT_VALUES, "vat": VAT_VALUES}
VALUE_LABELS = {("payment", "cash"): "Cash", ("payment", "credit"): "Credit", ("vat", "claim"): "Claim VAT",
                ("vat", "expense"): "Expense it"}
HISTORY_HOOK = "cowork_company_history_choices"
HISTORY_FORGET_HOOK = "cowork_company_history_forget"
HISTORY_FIELDS = ("supplier_tax", "supplier_name", "account", "account_label", "count", "last_date")


def encode(*parts) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(parts), ensure_ascii=False).encode()).decode().rstrip("=")


def decode(key) -> list:
    text = str(key or "").strip()
    if not text or len(text) > 4000:
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    try:
        value = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    if not isinstance(value, list) or not value or not all(isinstance(part, str) for part in value):
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    return value


def group_of(kind, subject) -> str:
    """同一行显示的归类：对方档按「方向 | 对方」合并（去掉分店号与名称），其余原样。"""
    if kind == "party":
        parts = str(subject or "").split("|")
        if len(parts) >= 2:
            return "|".join(parts[:2])
    return str(subject or "")


def subject_name(kind, subject, label) -> str:
    if label:
        return str(label)
    parts = str(subject or "").split("|")
    name = parts[-1] if kind in ("item",) else (parts[1] if len(parts) > 1 else parts[0])
    return name[5:] if name.startswith("name:") else name


def _year_of(sets) -> dict:
    return {(item["connection"], item["key"].casefold()): item.get("year_label") or "" for item in sets}


def merge_express(rows, connection, years) -> list:
    """同一类别、同一归类只留最新一条（`rows` 已按选的时间新 → 旧）；记下合并了几条（各分店 / 各年度）。纯函数。"""
    merged = {}
    for row in rows:
        group = group_of(row["kind"], row["subject"])
        found = merged.get((row["kind"], group))
        if found:
            found["merged"] += 1
            continue
        merged[(row["kind"], group)] = {
            "key": encode("express", connection, row["kind"], group), "source": "express", "kind": row["kind"],
            "subject_label": subject_name(row["kind"], row["subject"], row.get("label")),
            "value": row.get("value") or "", "editable": row["kind"] in EDITABLE,
            "year_label": years.get((connection, str(row.get("account_set") or "").casefold()), ""),
            "set_by": row.get("chosen_by") or "", "set_at": str(row.get("chosen_at") or ""), "merged": 1}
    return list(merged.values())


def _express_sets(sets) -> dict:
    found = {}
    for item in sets:
        if item.get("adapter") == "express":
            found.setdefault(item["connection"], []).append(item["key"])
    return found


def _express(sets) -> list:
    years = _year_of(sets)
    out = []
    for connection, keys in _express_sets(sets).items():
        rows = express_choices.of_sets(connection, keys, READ_MAX)
        out += merge_express(rows, connection, years)
    for row in out:
        label = VALUE_LABELS.get((row["kind"], row["value"]))
        row["value_label"] = _(label) if label else row["value"]
    return out


def _pos_keys(sets) -> dict:
    from pearnly_cowork.pos_import import service as pos_service

    keys = {}
    for item in sets:
        if item.get("adapter") == "express":
            keys.setdefault(pos_service.company_key(item["connection"], item["key"]), item)
    return keys


def _pos(sets) -> list:
    from pearnly_cowork.pos_import import layouts

    keys = _pos_keys(sets)
    if not keys:
        return []
    rows = layouts.of_companies(scope.default_workspace(), list(keys), READ_MAX)
    return [{"key": encode("pos", row["company"], row["fingerprint"]), "source": "pos", "kind": "pos_layout",
             "subject_label": " · ".join(row["headers"][:4]), "value": row["origin"], "value_label": "",
             "editable": False, "year_label": "", "set_by": row["set_by"], "set_at": row["set_at"], "merged": 1}
            for row in rows]


def _income_access():
    from pearnly_cowork.reconciliation import preset_store

    return preset_store.ACCESS["income"]


def _income(company) -> list:
    from pearnly_cowork.reconciliation import preset_store, presets

    prefix = presets.income_prefix(company.get("tax_id"))
    if not prefix or not _income_access().allowed():
        return []
    out = []
    for found_scope, rules in preset_store.under(scope.default_workspace(), "income", prefix).items():
        for rule, value in rules.items():
            out.append({"key": encode("income", found_scope, rule), "source": "income", "kind": rule,
                        "subject_label": presets.scope_label(found_scope), "value": value, "value_label": value,
                        "editable": False, "year_label": "", "set_by": "", "set_at": "", "merged": 1})
    return out


def _hooked(name):
    try:
        paths = frappe.get_hooks(name) or []
    except Exception:
        return []
    return [frappe.get_attr(path) for path in paths]


def history_rows(company, sets) -> list:
    """从 Express 历史学到的「供应商 → 科目」（科目建议那一包产出；没装就空）：只读，字段 `HISTORY_FIELDS`。"""
    out = []
    for read in _hooked(HISTORY_HOOK):
        try:
            rows = read(company.get("tax_id") or "", [dict(item) for item in sets]) or []
        except Exception:
            frappe.log_error(title="Cowork company history choices failed")
            continue
        for row in rows[:READ_MAX]:
            if not isinstance(row, dict) or not row.get("supplier_tax") or not row.get("account"):
                continue
            item = {field: row.get(field) for field in HISTORY_FIELDS}
            out.append({"key": encode("history", str(item["supplier_tax"]), str(item["account"])), "source": "history",
                        "kind": "account", "subject_label": str(item.get("supplier_name") or item["supplier_tax"]),
                        "value": str(item["account"]), "value_label": str(item.get("account_label") or item["account"]),
                        "editable": False, "year_label": "", "set_by": "", "set_at": str(item.get("last_date") or ""),
                        "count": int(item.get("count") or 0), "merged": 1})
    return out


def listing(company, sets) -> list:
    """这家公司记住的选择，按来源、类别分好，每类最多 `SHOWN` 条（新 → 旧）。"""
    found = []
    for part in (lambda: _express(sets), lambda: _pos(sets), lambda: _income(company),
                 lambda: history_rows(company, sets)):
        try:
            found += part()
        except (frappe.PermissionError, frappe.ValidationError):
            frappe.clear_messages()
    shown, counts = [], {}
    for row in found:
        bucket = (row["source"], row["kind"])
        counts[bucket] = counts.get(bucket, 0) + 1
        if counts[bucket] <= SHOWN:
            shown.append(row)
    return shown


def _require_connection(connection, sets):
    """Express 选择的删改：这条连接要在这家公司的账套里，且本人此刻能用它推送（同推送服务的判据）。"""
    from pearnly_cowork.connections import access as connection_access

    keys = _express_sets(sets).get(connection)
    if not keys:
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    connection_access.require_connection(frappe.get_doc("Cowork ERP Connection", connection))
    return keys


def _subjects(connection, keys, kind, group) -> list:
    rows = express_choices.of_sets(connection, keys, 2000)
    return sorted({row["subject"] for row in rows if row["kind"] == kind and group_of(kind, row["subject"]) == group})


def forget(company, sets, key) -> dict:
    """删一条记住的选择（真删）。Express 的这家公司各年度、各分店一起删，否则 `latest()` 会从别处把它找回来。"""
    parts = decode(key)
    source = parts[0]
    if source == "express" and len(parts) == 4 and parts[2] in KINDS:
        _source, connection, kind, group = parts
        keys = _require_connection(connection, sets)
        subjects = _subjects(connection, keys, kind, group)
        if not subjects:
            frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
        return {"removed": express_choices.forget(connection, keys, kind, subjects)}
    if source == "pos" and len(parts) == 3:
        from pearnly_cowork.pos_import import layouts

        scope.require("intake")
        if parts[1] not in _pos_keys(sets):
            frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
        return {"removed": int(layouts.forget(scope.default_workspace(), parts[1], parts[2]))}
    if source == "income" and len(parts) == 3:
        from pearnly_cowork.reconciliation import preset_store, presets

        prefix = presets.income_prefix(company.get("tax_id"))
        if not prefix or not parts[1].startswith(prefix):
            frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
        preset_store.forget(scope.default_workspace(), "income", parts[1], parts[2])
        return {"removed": 1}
    if source == "history" and len(parts) == 3:
        removed = 0
        for drop in _hooked(HISTORY_FORGET_HOOK):
            removed += int(drop(company.get("tax_id") or "", parts[1], parts[2], [dict(item) for item in sets]) or 0)
        return {"removed": removed}
    frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)


def set_value(company, sets, key, value, period="") -> dict:
    """直接改一条：只收付款方式（cash / credit）与简易税票抵不抵（claim / expense）。推送按每份账套各自记的取
    （`push.choices.remembered`），所以写进这家公司在这条连接上**所有记过这一条的账套**（各分店、各年度），再加覆盖这个月
    的各分店账套（没给月份就是最新年度的各分店）；只写一份时别的分店 / 年度推送仍用旧值，会静默记错账。
    ``sets`` 要给不截断的账套全集（`service.books(row, limit=None)`）。"""
    from pearnly_cowork.companies import identity

    parts = decode(key)
    if len(parts) != 4 or parts[0] != "express" or parts[2] not in EDITABLE:
        frappe.throw(_("cowork.company.choice_not_editable"))
    _source, connection, kind, group = parts
    value = str(value or "").strip()
    if value not in EDITABLE[kind]:
        frappe.throw(_("cowork.company.choice_not_editable"))
    keys = _require_connection(connection, sets)
    rows = [row for row in express_choices.of_sets(connection, keys, 2000)
            if row["kind"] == kind and group_of(kind, row["subject"]) == group]
    if not rows:
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    mine = [item for item in sets if item["connection"] == connection]
    current = identity.covering(mine, period) if period else []
    if not current and mine:
        newest = mine[0].get("year_label") or ""
        current = [item for item in mine if (item.get("year_label") or "") == newest]
    targets, subjects = {}, {}
    for row in rows:  # 新 → 旧：每份账套用它自己最近那条的归类键（对方档键带分店号）
        folded = str(row["account_set"]).casefold()
        targets.setdefault(folded, row["account_set"])
        subjects.setdefault(folded, (row["subject"], row.get("label") or ""))
    for item in current:
        targets.setdefault(item["key"].casefold(), item["key"])
    written = []
    for folded, account_set in targets.items():
        subject, label = subjects.get(folded) or (rows[0]["subject"], rows[0].get("label") or "")
        express_choices.record(connection, account_set, kind, subject, value, label)
        written.append(account_set)
    return {"saved": True, "account_set_key": written[0], "account_sets": written}


__all__ = ["decode", "encode", "forget", "group_of", "history_rows", "listing", "merge_express", "set_value"]
