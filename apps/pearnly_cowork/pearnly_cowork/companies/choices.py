"""公司主档页的「记住的选择」（ADR-020）：不搬家、不改键，按这家公司的账套与税号现算汇总，读、删、改都经各域的公开入口。

| 来源 | 归属 | 读 | 删 / 改 |
|---|---|---|---|
| `express`（对方档、存货、费用科目、付款、抵进项、部门、收款科目） | 账套在这家公司的账套里 | `push.choices.of_sets` | `forget`（这家公司各年度一起删），付款 / 抵进项可改（`record`） |
| `pos`（POS 版式） | 账套的公司键（`pos_import.service.company_key`） | `layouts.of_companies` | `layouts.forget` |
| `income`（收入对账预设） | 本公司税号（`presets.income_prefix`） | `preset_store.under` | `preset_store.forget` |
| `history`（从 Express 历史学到的「供应商 → 科目」，补充 丙） | 科目建议那一包产出 | hooks `cowork_company_history_choices` | hooks `cowork_company_history_forget` |
| `rule`（公司规则，第 2 期统一客户规则） | 主档 | `rules.listing` | `rules.delete`，付款 / 抵进项可改 |
| `policy`（商品单入账方式 `posting`、两份同期时现用的账套 `current_book`，数字会计员第 3 期） | 工作空间 ``document_policy`` 里按公司身份记的键 | `current_book.policy` + `contracts.document_policy.company_keys` | `push.application.goods_posting.forget_company`，入账方式可改（`set_company`） |

公司规则（第 2 期，docs/work/2026-10-16-customer-rules-phase2.md）：
- 账套行（`express`，各类别）与历史行可「设为全公司」（`promote`），CC 也可按单据存（`save_rule`）；存、改、提升都在同一事务里
  删掉这家各账套（分店规则只删那个分店的账套）同一类别同一归类键的记忆，规则立刻生效；之后某份账套再另选，以那份为准。
  从历史提升的同时经钩子把这家供应商的历史记成忘掉（幂等），删规则后回到「问人」。
- 权限：看要录入权限（同列表）；写要录入权限 + 覆盖这个月（没给月份按今天）那份账套所在连接的使用授权，值按那份账套的主档核
  （主档没报上来时放行，推送时再判），这家（或这个分店）没有覆盖这个月的账套就拒绝；删要录入权限 + 这家任一条 Express 连接的授权。

- 分店（补充 甲.5）：对方档（`party`）的归类键带分店号，同一税号各分店按税号合并成一行显示，删的时候各分店一起删；
  费用科目、付款、抵进项本来就按对方税号记（不分店），同一供应商不同分店共用。
- 页面上的 `key` 是服务端给的不透明串（`encode`），解出来后再核一次它确实属于这家公司。
- 对象只记了税号（记的时候票面没有名称，或旧记录）的行补上名称（第 5 轮测试 6：付款规则只显示 0105565099871）：
  先找同一税号别的行记的名称（只在对方档、付款、抵进项、费用科目之间借：存货行的标签是品名），再找覆盖这个月那份账套的
  供应商 / 客户主档；都没有照旧显示税号（`_name_parties`）。
- 入账方式、现用账套（用户 2026-10-17「加上，方便看和删」）：按主档的公司身份取，口径里没记谁 / 何时，不写；现用账套显示
  「本年 / 次年 · 分店 → 账套名」（账套名按这家的账套目录取，取不到原样写键），只能删（删了同一组候选再停下时重新问），
  改选走待处理 / CC 工具；入账方式可删可改。删、改的权限同删公司规则（录入权限 + 这家任一条 Express 连接的授权）。
- 每类最多显示 `SHOWN` 条，每张表一次 IN 查询、最多读 `READ_MAX` 条。预扣税记忆（按收款方、全所通用）、供应商默认、
  单据录不录、银行预设（键里没有公司）不显示。
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.companies import rules
from pearnly_cowork.push import choices as express_choices
from pearnly_cowork.push.express import company_choices
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
            "_tax": tax_in(row["subject"]),
            "subject_label": subject_name(row["kind"], row["subject"], row.get("label")),
            "value": row.get("value") or "", "editable": row["kind"] in EDITABLE,
            "year_label": years.get((connection, str(row.get("account_set") or "").casefold()), ""),
            "set_by": row.get("chosen_by") or "", "set_at": str(row.get("chosen_at") or ""), "merged": 1,
            "promotable": True}
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
                        "_tax": tax_in("purchase|" + str(item["supplier_tax"])),
                        "kind": "account", "subject_label": str(item.get("supplier_name") or item["supplier_tax"]),
                        "value": str(item["account"]), "value_label": str(item.get("account_label") or item["account"]),
                        "editable": False, "year_label": "", "set_by": "", "set_at": str(item.get("last_date") or ""),
                        "count": int(item.get("count") or 0), "merged": 1, "promotable": True})
    return out


def _value_label(kind, value) -> str:
    label = VALUE_LABELS.get((kind, value))
    return _(label) if label else ""


def rule_rows(company) -> list:
    """这家公司的规则（页面「记住的选择」里显示「全公司 / 分店 X」的那几行）：付款 / 抵进项可直接改。"""
    out = []
    for row in rules.listing(company["name"]):
        out.append({"key": encode("rule", row["id"]), "source": "rule", "kind": row["kind"], "_tax": tax_in(row["subject"]),
                    "subject_label": subject_name(row["kind"], row["subject"], row["subject_label"]), "value": row["value"],
                    "value_label": _value_label(row["kind"], row["value"]) or row["value_label"] or row["value"],
                    "editable": row["kind"] in EDITABLE, "branch": row["branch"], "year_label": "",
                    "set_by": row["set_by"], "set_at": row["set_at"], "merged": 1})
    return out


POLICY_LABELS = {"stock": "cowork.company.choice.stock", "service": "cowork.company.choice.service"}
PART_LABELS = {"cy": "cowork.company.choice.current_year", "ny": "cowork.company.choice.next_year"}


def _policy_scope(company) -> tuple:
    """按公司记的口径在哪个工作空间、这家的公司身份。"""
    return company.get("workspace") or scope.default_workspace(), str(company.get("identity") or "")


def policy_rows(company, sets) -> list:
    """这家公司在工作空间口径里按公司记的入账方式与现用账套（见文件头）。"""
    from pearnly_cowork.companies import current_book
    from pearnly_cowork.contracts import document_policy as policy_rules
    from pearnly_cowork.targets.identity import normalize_express_account_key

    workspace, company_identity = _policy_scope(company)
    if not company_identity:
        return []
    labels = {}
    for item in sets:
        labels.setdefault(normalize_express_account_key(item.get("key")) or item.get("key"), item.get("label") or "")
    out = []
    for key, value in policy_rules.company_keys(current_book.policy(workspace), company_identity).items():
        base = {"key": encode("policy", key), "source": "policy", "value": value, "year_label": "", "set_by": "",
                "set_at": "", "merged": 1}
        found = policy_rules.current_book_parts(key, company_identity)
        if found is None:
            out.append({**base, "kind": "posting", "subject_label": _("cowork.company.choice.goods"),
                        "value_label": _(POLICY_LABELS[value]), "editable": True})
            continue
        branch, part = found
        subject = " · ".join(item for item in (_(PART_LABELS[part]),
                                               _("cowork.company.branch_n").format(branch) if branch else "") if item)
        out.append({**base, "kind": "current_book", "subject_label": subject, "value_label": labels.get(value) or value,
                    "editable": False})
    return out


def _policy_key(company, parts) -> str:
    """页面给的 ``policy`` 行键 → 口径键（须是这家公司的）；不是回空串。"""
    from pearnly_cowork.contracts import document_policy as policy_rules

    if len(parts) != 2 or parts[0] != "policy":
        return ""
    _workspace, company_identity = _policy_scope(company)
    key = parts[1]
    goods = policy_rules.goods_posting_key(company_identity)
    return key if (goods and key == goods) or policy_rules.current_book_parts(key, company_identity) else ""


def _all(company, sets) -> list:
    """这家公司记住的全部选择（补好名称、不截断）。"""
    found = []
    for part in (lambda: rule_rows(company), lambda: _express(sets), lambda: _pos(sets), lambda: _income(company),
                 lambda: history_rows(company, sets), lambda: policy_rows(company, sets)):
        try:
            found += part()
        except (frappe.PermissionError, frappe.ValidationError):
            frappe.clear_messages()
    _name_parties(found, sets)
    return found


def find(company, sets, key):
    """按页面 / CC 给的 ``key`` 找一条（卡片说明用）：不经每类 `SHOWN` 截断，名称与列表一致；找不到回 None。"""
    return next((row for row in _all(company, sets) if row.get("key") == key), None) if key else None


def listing(company, sets) -> list:
    """这家公司记住的选择，按来源、类别分好，每类最多 `SHOWN` 条（新 → 旧）。"""
    found = _all(company, sets)
    shown, counts = [], {}
    for row in found:
        bucket = (row["source"], row["kind"])
        counts[bucket] = counts.get(bucket, 0) + 1
        if counts[bucket] <= SHOWN:
            shown.append(row)
    return shown


def tax_in(subject) -> str:
    """归类键里对方的税号（「方向|税号|…」的第二段，13 位）；按名字记的（name:…）、部门（*）等没有，回空串。纯函数。"""
    parts = str(subject or "").split("|")
    tax = parts[1] if len(parts) > 1 else ""
    return tax if len(tax) == 13 and tax.isdigit() else ""


# 对象就是对方本身、标签就是对方名称的类别；存货（标签是品名）、部门、收款科目不出借也不接收名称
PARTY_KINDS = ("party", "payment", "vat", "account")


def _named(row) -> bool:
    label = str(row.get("subject_label") or "").strip()
    return bool(label) and label != row.get("_tax")


def _party_tax(row) -> str:
    return row.get("_tax") or "" if row.get("kind") in PARTY_KINDS else ""


def name_parties(rows, masters=None) -> list:
    """对象只记了税号的行（名称空着或就是税号）补上名称：同一税号别的行记的名称优先，其次 ``masters``（供应商 / 客户主档的
    ``{tax_id, name}``）；都没有照旧。只在 `PARTY_KINDS` 之间借（存货行的标签是品名，不是对方名称）。就地改 ``rows``
    （行上内部的 ``_tax`` 由 `_name_parties` 收掉），返回还缺名称的税号。不读库。"""
    known = {}
    for row in rows:
        if _party_tax(row) and _named(row):
            known.setdefault(row["_tax"], str(row["subject_label"]).strip())
    for row in (masters or ()):
        tax, name = "".join(ch for ch in str(row.get("tax_id") or "") if ch.isdigit()), str(row.get("name") or "").strip()
        if len(tax) == 13 and name:
            known.setdefault(tax, name)
    missing = []
    for row in rows:
        tax = _party_tax(row)
        if tax and not _named(row):
            if tax in known:
                row["subject_label"] = known[tax]
            elif tax not in missing:
                missing.append(tax)
    return missing


def _name_parties(rows, sets) -> None:
    """`name_parties` 先用同一税号别的行；还缺的再读一次覆盖这个月那份账套的供应商 / 客户主档（没权限、没读过就算了）。"""
    try:
        if name_parties(rows):
            from pearnly_cowork.connections import preparation
            from pearnly_cowork.targets.identity import normalize_express_account_key

            target = target_set(sets, "", "")
            doc = frappe.get_doc("Cowork ERP Connection", target["connection"])
            masters = preparation.reported_masters(doc, normalize_express_account_key(target["key"]),
                                                   keys=("reported_suppliers", "reported_customers")) or {}
            name_parties(rows, [row for key in ("reported_suppliers", "reported_customers")
                                for row in masters.get(key) or [] if isinstance(row, dict)])
    except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
        frappe.clear_messages()
    finally:
        for row in rows:
            row.pop("_tax", None)


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
    if source == "rule" and len(parts) == 2:
        _require_any_connection(sets)
        rules.delete(company["name"], parts[1])
        return {"removed": 1}
    if source == "policy" and len(parts) == 2:
        from pearnly_cowork.push.application import goods_posting

        policy_key = _policy_key(company, parts)
        if not policy_key:
            frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
        _require_any_connection(sets)
        removed = goods_posting.forget_company(*_policy_scope(company), policy_key)
        if not removed:
            frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
        return {"removed": removed}
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
    if len(parts) == 2 and parts[0] == "rule":
        found = rules.one(company["name"], parts[1])
        if found["kind"] not in EDITABLE or str(value or "").strip() not in EDITABLE[found["kind"]]:
            frappe.throw(_("cowork.company.choice_not_editable"))
        return _save_rule(company, sets, found["kind"], found["subject"], str(value).strip(), found["branch"], period,
                          subject_label=found["subject_label"], evidence=found["evidence"])
    if len(parts) == 2 and parts[0] == "policy":
        from pearnly_cowork.contracts import document_policy as policy_rules
        from pearnly_cowork.push.application import goods_posting

        policy_key = _policy_key(company, parts)
        workspace, company_identity = _policy_scope(company)
        if not policy_key or policy_key != policy_rules.goods_posting_key(company_identity):
            frappe.throw(_("cowork.company.choice_not_editable"))
        if str(value or "").strip() not in policy_rules.GOODS_POSTING_VALUES:
            frappe.throw(_("cowork.company.choice_not_editable"))
        _require_any_connection(sets)
        goods_posting.set_company(workspace, company_identity, str(value).strip())
        return {"saved": True}
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


# ---------------- 公司规则：存、提升 ----------------


def _this_month() -> str:
    return datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m")


def _require_any_connection(sets):
    """删规则：这家公司至少一条 Express 连接本人此刻能用（同推送服务的判据）；一条都没有就拒绝。"""
    from pearnly_cowork.connections import access as connection_access

    connections = list(_express_sets(sets))
    for connection in connections:
        try:
            connection_access.require_connection(frappe.get_doc("Cowork ERP Connection", connection))
            return connection
        except (frappe.PermissionError, frappe.DoesNotExistError):
            frappe.clear_messages()
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def target_set(sets, branch, period) -> dict:
    """规则按哪份账套核值：这家（分店规则只看那个分店）覆盖这个月的 Express 账套，总公司在前；没有就拒绝
    （`cowork.company.choice_scope_invalid`）。本人须此刻能用它所在的连接。"""
    from pearnly_cowork.companies import identity
    from pearnly_cowork.connections import access as connection_access

    express = [item for item in sets if item.get("adapter") == "express"]
    if branch:
        express = [item for item in express if item.get("branch") == branch]
    covering = identity.covering(express, period or _this_month())
    if not covering and express and not any(item.get("period_start") for item in express):
        covering = express[:1]  # 目录没报会计期间（旧版 CC 桌面版）：用最新年度那份（`books` 新年度在前）
    covering = sorted(covering, key=lambda item: item.get("branch") not in ("", identity.HEAD_OFFICE))
    if not covering:
        frappe.throw(_("cowork.company.choice_scope_invalid"))
    target = covering[0]
    connection_access.require_connection(frappe.get_doc("Cowork ERP Connection", target["connection"]))
    return target


def _masters(target) -> dict | None:
    """覆盖这个月那份账套的主档（小助手报过的，出自当前凭据）；没读过回 None（放行，推送时再判）。"""
    from pearnly_cowork.connections import preparation
    from pearnly_cowork.targets.identity import normalize_express_account_key

    doc = frappe.get_doc("Cowork ERP Connection", target["connection"])
    return preparation.reported_masters(doc, normalize_express_account_key(target["key"]),
                                        keys=preparation.ALL_MASTER_LISTS)


def _master_name(kind, value, masters) -> str:
    """值在主档里的名称（科目、部门、存货、对方档；「代码 · 名称」由页面拼），没有就空串。"""
    keys = {"account": ("reported_accounts",), "receipt": ("reported_accounts",),
            "department": ("reported_departments",), "item": ("reported_products",),
            "party": ("reported_suppliers", "reported_customers")}.get(kind, ())
    for key in keys:
        for row in (masters or {}).get(key) or []:
            if isinstance(row, dict) and str(row.get("code") or "").strip() == value:
                return " ".join(str(part) for part in (value, row.get("name") or "") if part)
    return ""


def _branch(sets, branch) -> str:
    """规则的分店：空 = 全公司；否则须是这家公司账套里有的分店号。"""
    from pearnly_cowork.companies import identity

    found = company_choices.branch_of(branch)
    if found is None or (found and found not in {item["branch"] for item in identity.branches(sets)}):
        frappe.throw(_("cowork.company.choice_scope_invalid"))
    return found


def _drop_memories(sets, kind, subject, branch) -> int:
    """存了规则：这家各份 Express 账套（分店规则只看那个分店的）里同一类别同一归类键的记忆删掉（真删），规则立刻生效。"""
    removed = 0
    for connection, keys in _express_sets([item for item in sets if not branch or item.get("branch") == branch]).items():
        removed += int(express_choices.forget(connection, keys, kind, [subject]) or 0)
    return removed


def _scope(sets, branch, period) -> tuple:
    """核分店、找覆盖这个月那份账套并取它的主档 → (分店, 主档)；同一次提升的几条共用一次。"""
    branch = _branch(sets, branch)
    return branch, _masters(target_set(sets, branch, period))


def _save_rule(company, sets, kind, subject, value, branch, period, *, subject_label="", evidence=None,
               session="", scoped=None) -> dict:
    """核分店、核值（覆盖这个月那份账套的主档）、存规则、删同键的账套记忆，同一事务。``scoped`` 是 `_scope` 已算好的。"""
    if kind not in KINDS or not isinstance(subject, str) or not subject:
        frappe.throw(_("cowork.company.choice_unsupported"))
    branch, masters = scoped or _scope(sets, branch, period)
    if not company_choices.valid(kind, value, masters, subject):
        frappe.throw(_("cowork.company.choice_invalid"))
    saved = rules.save(company["name"], workspace=scope.default_workspace(), kind=kind, subject=subject,
                       value=value, branch=branch, subject_label=subject_label,
                       value_label=_value_label(kind, value) or _master_name(kind, value, masters),
                       evidence=evidence, session=session)
    saved["removed_memories"] = _drop_memories(sets, kind, subject, branch)
    return {"saved": True, "rule": saved, "key": encode("rule", saved["id"])}


def save_rule(company, sets, kind, subject, value, branch="", period="", *, subject_label="", evidence=None,
              session="") -> dict:
    """CC 按单据存一条公司规则（归类键由调用方按单据算好）。"""
    return _save_rule(company, sets, str(kind or ""), subject, str(value or "").strip(), branch, period,
                      subject_label=subject_label, evidence=evidence, session=session)


def promote(company, sets, key, branch="", period="", session="") -> dict:
    """「设为全公司」：把一行账套记忆（`express`）或 Express 历史学到的（`history`）提升成公司规则。账套行这一归类里每个归类键
    各取最新的那条（值、写法）各存一条规则：对方档按税号把各分店合并成一行显示，只存最新那个分店的话，其他分店的记忆还在、
    刷新后这一行又冒出来（复审 4）；其他分店的值已不在这份账套主档里的跳过（那份记忆照旧留着）。回最新那条，
    ``removed_memories`` 是几条合计。历史行只能提升为全公司规则（钩子按供应商忘掉全公司的历史，复审 3），同时经钩子把这家
    供应商的历史记成忘掉（删规则后回到问人）。``sets`` 给全集。"""
    parts = decode(key)
    if parts[0] == "express" and len(parts) == 4 and parts[2] in KINDS:
        _source, connection, kind, group = parts
        keys = _require_connection(connection, sets)
        rows = [row for row in express_choices.of_sets(connection, keys, 2000)
                if row["kind"] == kind and group_of(kind, row["subject"]) == group]
        if not rows:
            frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
        latest = {}
        for row in rows:  # 新 → 旧：每个归类键第一条就是它最新的
            latest.setdefault(row["subject"], row)
        years = _year_of(sets)
        scoped = _scope(sets, branch, period)
        saved, removed = None, 0
        for subject, row in latest.items():
            value = str(row.get("value") or "")
            if saved and not company_choices.valid(kind, value, scoped[1], subject):
                continue  # 最新那条必须能用（不能就报错、什么都不存）；其余不能用的留着原来的账套记忆
            evidence = {"from": "account_set", "account_set": str(row.get("account_set") or ""),
                        "year_label": years.get((connection, str(row.get("account_set") or "").casefold()), ""),
                        "chosen_by": row.get("chosen_by") or "", "chosen_at": str(row.get("chosen_at") or "")}
            one = _save_rule(company, sets, kind, subject, value, branch, period,
                             subject_label=subject_name(kind, subject, row.get("label")), evidence=evidence,
                             session=session, scoped=scoped)
            removed += int(one["rule"].get("removed_memories") or 0)
            saved = saved or one
        saved["rule"]["removed_memories"] = removed
        return saved
    if parts[0] == "history" and len(parts) == 3:
        if company_choices.branch_of(branch) != "":
            frappe.throw(_("cowork.company.choice_scope_invalid"))
        _source, supplier, account = parts
        found = next((row for row in history_rows(company, sets)
                      if row["key"] == key), None)
        if not found:
            frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
        saved = _save_rule(company, sets, "account", f"purchase|{supplier}", account, branch, period,
                           subject_label=found["subject_label"],
                           evidence={"from": "history", "count": found.get("count") or 0,
                                     "last_date": found.get("set_at") or ""}, session=session)
        for drop in _hooked(HISTORY_FORGET_HOOK):
            drop(company.get("tax_id") or "", supplier, account, [dict(item) for item in sets])
        return saved
    frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)


__all__ = ["decode", "encode", "find", "forget", "group_of", "history_rows", "listing", "merge_express", "name_parties", "policy_rows",
           "promote", "rule_rows", "save_rule", "set_value", "target_set", "tax_in"]
