"""CC 的公司主档工具（ADR-020）：列出公司、读一家公司的资料、记 / 删知识库、存 / 删公司规则（第 2 期统一客户规则）。

只调 `companies` 的公开入口，权限、归属由它们判。知识库与公司规则的写都要用户在对话里点「允许」（`approval`），卡片上先给
改前、改后；CC 写入的记 `origin = cc` / 规则的对话（只作审计）。缺省的公司取本对话挂的那家（`projects.of_session`）。
公司规则的归类键由服务端按单据算（`_subject_of`，同推送的 `push.express.choices`），也可传资料里一行的 key 做提升。
单独成模块（不并进 `tools.py`）：两边各自登记在 hooks `pearnly_assistant_tools` / `pearnly_assistant_guidance`。
"""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_core.assistant import projects
from pearnly_cowork.access import scope
from pearnly_cowork.companies import choices as saved_choices
from pearnly_cowork.companies import identity, notes, rules, service
from pearnly_cowork.push.express import choices as choice_rules

LIST_MAX = 20
CHOICES_MAX = 200

GUIDANCE = [
    "When a <company> note is present, work for that company by default: use its account set covering the work "
    "month, its documents folder and month folder for cowork_folder_files, cowork_folder_fetch and new scheduled "
    "tasks; ask before working on another company. Do not move existing scheduled tasks to another folder unless the "
    "user asks.",
    "When the user states or confirms a lasting fact about how this client's books are kept that is not already in "
    "the team notes (who is paid rent and the withholding rate, how POS days are closed, how a kind of receipt is "
    "booked), offer once in one short sentence to save it with cowork_company_note_save. Do not save one-off details, "
    "amounts of a single document, passwords, ID card or bank account numbers; if the user declines, do not offer it "
    "again in this conversation.",
    "Remembered push choices are applied automatically when pushing; to change one, use the push choice tools as usual.",
    "When a client choice (<client_choices>) or a team note decided how you handled something in this chat, say which "
    "one in a few words. For a document already entered, the choice that decided it is in its written.company_rules, "
    "not in <client_choices>.",
    "A choice the user wants for the whole company (or one branch) rather than one account set goes in with "
    "cowork_company_choice_save; a choice said for this document only is saved with cowork_save_push_choices as usual.",
    # 数字会计员第 3 期第 13 条：档位只由数据升降，没有改档位的工具；为什么没自动推按票上的原因码照实说
    "Automatic pushing for a company (automation in cowork_company_profile: L0 propose only, L1 by rules, L2 whole "
    "month) moves only by data (qualified month readbacks, write checks); there is no tool or setting to change it, so "
    "never offer to. When asked why a document was not pushed automatically, answer from automation.reasons in "
    "cowork_document_detail as they are, in plain words.",
    "In a conversation filed under a company, cowork_list_documents, cowork_push_results and cowork_folder_tasks "
    "cover only that company's account sets (all its years, or only the work branch); scope in the result names the "
    "company. Answer for that company only and never list other companies' documents or tasks there. Pass "
    "all_companies true only when the user asks about all companies or other companies (" + "ทุกเจ้า, เจ้าอื่น" + "); "
    "for one other company pass its company id.",
]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# 主档给页面的时刻是 UTC（`service.when`：`2026-10-15T14:08:00Z`，页面按设备时区显示）；交给 CC 前换成站点时区并带上偏移
# （`2026-10-15T21:08:00+07:00`）。测试窗口第 3 轮：CC 把记住选择的 14:08Z 当曼谷时间说成「14:08 记下的」。
PROFILE_TIMES = (("company", "created_at"), ("notes", "updated_at"), ("choices", "set_at"), ("drafts", "updated_at"),
                 ("automation", "since"))


def local_iso(value) -> str:
    """带时区的 ISO 时刻（含 ``Z``）→ 站点时区、带偏移的 ISO（到秒）；只有日期的、空的、读不出的、没带时区的原样回。"""
    text = str(value or "").strip()
    if len(text) <= 10:
        return value
    try:
        moment = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return value
    if moment.tzinfo is None:
        return value
    return moment.astimezone(ZoneInfo(frappe.utils.get_system_timezone())).isoformat(timespec="seconds")


def _local_profile(found) -> dict:
    for key, field in PROFILE_TIMES:
        block = found.get(key)
        for item in ([block] if isinstance(block, dict) else block or ()):
            if isinstance(item, dict) and item.get(field):
                item[field] = local_iso(item[field])
    return found


def _session() -> str:
    return (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or ""


def _filed() -> tuple:
    """本对话挂的公司、月份、分店（没挂回空串）。"""
    session = _session()
    return projects.of_session(session) if session else ("", "", "")


def resolve(wanted, filed="", write=False) -> tuple:
    """模型给的 ``company`` → (主档编号, 认不出的原因)。没给回 (``filed``, None)；是主档编号照用。2026-10-16 测试：说明里只有
    税号，CC 把税号当 company 传，查单先失败一次——13 位数字按税号认：是本对话公司的税号就当没给（本对话那家），本所只有
    一家是这个税号就用那家，几家同税号回失败并列出编号；都不是回 unknown company。
    ``write``（存 / 删公司规则、团队笔记）：只认本对话公司的税号，别家的税号回失败并列出那家的编号（第 7 轮审查：模型容易把
    这张单对方的税号当 company 传，对方恰好也是本所客户时规则就存到了那家名下），要改别家须传主档编号。"""
    wanted = str(wanted or "").strip()
    if not wanted:
        return filed, None
    if service.exists(wanted):
        return wanted, None
    tax = re.sub(r"[\s-]", "", wanted)
    if len(tax) == 13 and tax.isdigit():
        if filed and service.exists(filed) and (service.get(filed).get("tax_id") or "") == tax:
            return filed, None
        found = service.with_tax_id(tax)
        if write and found:
            return "", (f"{tax} is the tax ID of client company {', '.join(found)}, not of the company this conversation "
                        f"is filed under. Nothing was changed. For this conversation's company leave company out; a "
                        f"supplier's or customer's tax ID never goes in company. To change that other company, check "
                        f"with the user first, then pass its id as company.")
        if len(found) == 1:
            return found[0], None
        if found:
            return "", (f"several companies have tax ID {tax}: pass one company id from cowork_companies "
                        f"({', '.join(found)})")
    return "", "unknown company: pass an id from cowork_companies"


def _company(tool_input, write=False):
    """要读 / 写的那家：参数给了用参数（主档编号，或按税号认出的那家，`resolve`；写时只认本对话公司的税号），否则用本对话
    挂的那家；都没有回 None。"""
    wanted, _reason = resolve(tool_input.get("company"), _filed()[0], write)
    if not wanted or not service.exists(wanted):
        return None
    return service.get(wanted)


def _no_company(tool_input=None, write=False) -> str:
    reason = resolve(tool_input.get("company"), _filed()[0], write)[1] if (tool_input or {}).get("company") else None
    return _json({"status": "failed", "reason": reason or "no company: pass company (an id from cowork_companies)"})


def company_refusal(tool_input) -> str | None:
    """写公司的工具（存 / 删团队笔记、公司规则）出「允许」卡片前（工具声明的 ``refuse``）：给的 company 认不出、或是别家的税号
    就不出卡片，直接回失败与原因（卡上「公司」一行不写一家用户没说过的公司）。"""
    if not tool_input.get("company"):
        return None
    _wanted, reason = resolve(tool_input.get("company"), _filed()[0], write=True)
    return _json({"status": "failed", "reason": reason}) if reason else None


# ---------------- 公司对话的默认范围 ----------------

# 2026-10-15 测试机：在「T.8 ซินเซียร์ไอซ์」的 9 月对话里问「这个月哪些单进了 Express」，CC 调 cowork_list_documents 没带账套，
# 答出整个工作空间 9 月推送的 128 张（别家三份账套的）。按公司可筛的读工具（单据列表、推送结果、定时任务）在服务端兜底：
# 调用没点账套、连接或公司时，挂在公司下的对话默认只看这家的账套；会计员问所有公司 / 别家时才由 all_companies 放开。


def default_scope(tool_input) -> dict | None:
    """这次读哪家公司的账套。回 ``{"scope": 给模型看的范围, "sets": 账套（`service.book` 的样子）或 None = 不限}``：

    - 给了 ``company``（别的那家）：那家的全部账套（各年度、各分店）。
    - 本对话挂在某家公司：没给 ``all_companies`` → 这家的账套（对话指定了分店只看该分店，`service.work_sets`）；
      给了 → 不限，``scope`` 标 ``all_companies``，照「所有公司」逐家答。
    - 没挂公司、也没给 ``company``：回 None，照旧不限、不加 ``scope``。

    ``company`` 可以是主档编号或税号（`resolve`；本对话公司的税号等于没给）；给错回 ``{"failed": 原因}``。调用自己点了账套 / 连接的不走这里（由调用方判），照旧按点的取。"""
    filed, _period, work_scope = _filed()
    filed = filed if filed and service.exists(filed) else ""
    given = str(tool_input.get("company") or "").strip()
    wanted, reason = resolve(given, filed) if given else ("", None)
    if reason:
        return {"failed": reason}
    if wanted and wanted != filed:
        row = service.get(wanted)
        sets = service.work_sets(row.name)
        return {"scope": {"company": row.title, "company_id": row.name, "account_sets": len(sets)}, "sets": sets}
    if not filed:
        return None
    if tool_input.get("all_companies") and not wanted:
        return {"scope": {"all_companies": True}, "sets": None}
    row = service.get(filed)
    branch = identity.scope_value(work_scope) or ""
    sets = service.work_sets(row.name, branch)
    view = {"company": row.title, "company_id": row.name, "conversation_company": True, "account_sets": len(sets)}
    if branch and any(item.get("branch") == branch for item in sets):
        view["branch"] = branch
    return {"scope": view, "sets": sets}


def companies(tool_input) -> str:
    scope.require_read()
    found = service.listing(tool_input.get("search"), 0, LIST_MAX)
    return _json({"companies": [{"id": row["id"], "title": row["title"], "legal_name": row["legal_name"],
                                 "tax_id": row["tax_id"], "branches": row.get("branches") or [],
                                 "years": row.get("years") or []} for row in found]})


def profile(tool_input) -> str:
    row = _company(tool_input)
    if not row:
        return _no_company()
    filed, filed_period, filed_scope = _filed()
    period = str(tool_input.get("period") or (filed_period if filed == row.name else "") or "")
    found = service.detail(row.name, period or None)
    if found.get("choices"):
        # key 只留给能「设为全公司」的行与公司规则（cowork_company_choice_save / _remove 用），其余去掉免得结果太长
        found["choices"] = [{key: value for key, value in item.items()
                             if key != "key" or item.get("promotable") or item.get("source") == "rule"}
                            for item in found["choices"][:CHOICES_MAX]]
    if found.get("choices") is not None:
        found["rules"] = [{**item, "key": saved_choices.encode("rule", item["id"]), "set_at": service.when(item["set_at"])}
                          for item in rules.listing(row.name, CHOICES_MAX)]
    if filed == row.name and filed_scope:
        found["work_branch"] = filed_scope
    # 自动推送的档位（第 3 期第 13 条）：档位、自何时、原因码；有录入权限时带近两期统计（`automation.view`）
    from pearnly_cowork.companies import automation  # 档位要读报税期间（`tax.periods`），只在读资料时加载

    found["automation"] = automation.view(row.name)
    return _json(_local_profile(found))


def note_save(tool_input) -> str:
    service.require_write()
    row = _company(tool_input, write=True)
    if not row:
        return _no_company(tool_input, write=True)
    saved = notes.save(row.name, tool_input.get("text"), tool_input.get("note") or None,
                       workspace=scope.default_workspace(), origin="cc", session=_session())
    service.announce("upsert", row.name, part="notes")
    return _json({"status": "saved", "company": row.title, "note": saved})


def note_remove(tool_input) -> str:
    service.require_write()
    row = _company(tool_input, write=True)
    if not row:
        return _no_company(tool_input, write=True)
    removed = notes.delete(row.name, str(tool_input.get("note") or ""))
    service.announce("upsert", row.name, part="notes")
    return _json({"status": "removed", "company": row.title, "note": removed["id"]})


def _before(row, note) -> str:
    if not note or not row:
        return ""
    try:
        return notes.one(row.name, str(note)).text or ""
    except frappe.DoesNotExistError:
        frappe.clear_messages()
        return ""


def preview_note_save(tool_input) -> list:
    row = _company(tool_input, write=True)
    return [{"field": _("Company"), "before": "", "after": row.title if row else ""},
            {"field": _("cowork.company.notes"), "before": _before(row, tool_input.get("note")),
             "after": " ".join(str(tool_input.get("text") or "").split())}]


def preview_note_remove(tool_input) -> list:
    row = _company(tool_input, write=True)
    return [{"field": _("cowork.company.notes"), "before": _before(row, tool_input.get("note")), "after": ""}]


# ---------------- 公司规则（第 2 期） ----------------

RULE_KINDS = ("party", "item", "account", "payment", "vat", "department")
DOCUMENT_CANDIDATES = 5  # 传成票面单号、看得到的有几张时，原因里列几张


class UnknownDocument(frappe.ValidationError):
    """按单据存规则时认不出是哪张单（编号不在、票面单号对上不止一张或一张都没有）：原因交给模型，不出「允许」卡片。"""


def _document(value, company_tax=""):
    """按单据存规则时的那张单：``document`` 是单据编号（工具结果里的 name）。第 8 轮测试机：CC 传了票面单号
    「SD-6909-1010」，单据编号找不到，卡片上就算不出对方、只写了值，看着像全公司所有单都改这个科目。传成票面单号时，
    本人看得到、且规则所属公司（税号 ``company_tax``）是买方或卖方的只有一张就用它（第 8 轮审查：别家客户的同号单不算）；
    几张、一张都没有、或读不了就抛 `UnknownDocument`（原因里列出看得到的几张），由出卡片前的核对拒掉，不出卡片。"""
    from pearnly_cowork.history import storage

    value = " ".join(str(value or "").split())[:140]
    if not value:
        raise UnknownDocument("document is missing: pass the name (from the document tool results) of the document "
                              "the rule comes from. Nothing was saved.")
    try:
        return storage.read(value)
    except frappe.DoesNotExistError:
        frappe.clear_messages()
    except frappe.PermissionError:
        frappe.clear_messages()
        raise UnknownDocument(f"document {json.dumps(value, ensure_ascii=False)} cannot be read here. Nothing was "
                              f"saved.") from None
    found = storage.by_invoice_no(value, DOCUMENT_CANDIDATES + 1, party_tax=company_tax)
    if len(found) == 1:
        return storage.read(found[0]["name"])
    if not found:
        raise UnknownDocument(f"document {json.dumps(value, ensure_ascii=False)} is not a document name of this "
                              f"company: pass the name (from the document tool results) of the document the rule comes "
                              f"from. Nothing was saved.")
    listed = "; ".join(f"{row['name']} ({row.get('invoice_date') or ''}, seller {row.get('seller_name') or ''}, "
                       f"buyer {row.get('buyer_name') or ''})" for row in found[:DOCUMENT_CANDIDATES])
    raise UnknownDocument(f"Several documents have invoice number {json.dumps(value, ensure_ascii=False)}: {listed}. "
                          f"Pass the name of the one the user means as document. Nothing was saved.")


def _party(tool_input) -> tuple:
    """按单据存规则时这条规则的对象：传进来的那张单（``document``）自己的对方 → (kind, direction, side, name, tax, fields)。
    付款、抵进项、费用科目按卖方（进项）；对方档、存货按方向（进项卖方 / 销项买方）。认不出抛 choice_unsupported。
    对象只按这张单算、不收模型给的名字（2026-10-16 测试：CC 把这张单的规则说成另一家供应商的）。"""
    from pearnly_cowork.contracts.history import primary_page_fields
    from pearnly_cowork.history import storage

    kind = str(tool_input.get("kind") or "")
    if kind not in RULE_KINDS or kind == "department":
        frappe.throw(_("cowork.company.choice_unsupported"))
    doc = _document(tool_input.get("document"), _company_tax(tool_input))
    fields = primary_page_fields(storage.project(doc).get("pages") or [])
    direction = "sales" if tool_input.get("direction") == "sales" and kind in ("party", "item") else "purchase"
    side = "buyer" if direction == "sales" else "seller"
    # 同推送 `choices._party` 的取法（只认 ``{side}_tax``），否则按税号存的规则推送时按名字找、永远对不上
    name, tax = fields.get(f"{side}_name") or "", fields.get(f"{side}_tax") or ""
    if not str(name).strip() and not choice_rules.digits(tax):
        frappe.throw(_("cowork.company.choice_unsupported"))
    return kind, direction, side, str(name).strip(), tax, fields


def _company_tax(tool_input) -> str:
    """规则所属公司（参数或本对话那家）的税号；认不出回空串（票面单号就不按公司筛）。"""
    row = _company(tool_input, write=True)
    return str(row.get("tax_id") or "") if row else ""


def _evidence_document(tool_input) -> str:
    """规则上记的来源单据：按单据算对象的类别记认出的那张单的编号（传成票面单号时也是编号）；部门整份一个、不按单据算，照参数
    原样记（同以前；第 8 轮审查：部门规则不带单据也能存，不能在点「允许」之后才因认不出单据失败）。"""
    if str(tool_input.get("kind") or "") == "department":
        return str(tool_input.get("document") or "")
    return _document(tool_input.get("document"), _company_tax(tool_input)).name


def _subject_of(tool_input) -> tuple:
    """按单据算规则的归类键（同推送 `push.express.choices` 的键）→ (kind, subject, 写法)；认不出抛 choice_invalid。
    对象见 `_party`；对方档带分店；存货要给票面品名 ``item``；部门整份一个。"""
    kind = str(tool_input.get("kind") or "")
    if kind not in RULE_KINDS:
        frappe.throw(_("cowork.company.choice_unsupported"))
    if kind == "department":
        return kind, "*", ""
    kind, direction, side, name, tax, fields = _party(tool_input)
    # 卡片与规则上写的对方：单据上的名称；单据只有税号时写税号（不留空，免得卡上只剩值）
    label = name or choice_rules.digits(tax)
    if kind == "party":
        return kind, choice_rules.party_subject(direction, tax, fields.get(f"{side}_branch") or "", name), label
    if kind == "item":
        item = str(tool_input.get("item") or "").strip()[:50]
        if not item:
            frappe.throw(_("cowork.company.choice_unsupported"))
        return kind, choice_rules.item_subject(direction, tax, name, item), item
    return kind, choice_rules.supplier_subject("purchase", tax, name), label


NAME_COVER = 0.6  # 单据没有税号时，名称一方包含另一方：短的至少占长的这么多才算同一方


def _same_party(expected: str, name: str, tax) -> bool:
    """模型说给用户的对方（``expect_party``：税号或名称）是不是这张单的对方：给的是 13 位税号就比税号。给的是名称时按归一后
    的名称（同推送 `choices.norm_name`）比：单据带 13 位税号就要完全相同（第 7 轮审查：「สยาม」被「สยามแม็คโคร」包含也算对上，
    挡不住说成别家）；单据没有税号才允许一方包含另一方，且短的至少占长的 `NAME_COVER`。"""
    digits = choice_rules.digits(expected)
    if len(digits) == 13 and len(re.sub(r"[\s-]", "", expected)) == 13:
        return digits == choice_rules.digits(tax)
    wanted, actual = choice_rules.norm_name(expected), choice_rules.norm_name(name)
    if not wanted or not actual:
        return False
    if wanted == actual:
        return True
    if len(choice_rules.digits(tax)) == 13:
        return False
    short, long = sorted((wanted, actual), key=len)
    return len(short) >= 3 and short in long and len(short) >= NAME_COVER * len(long)


def mismatch(tool_input) -> str | None:
    """按单据存规则（不是 ``key`` 提升、不是部门）时核对规则对象：没给 ``expect_party``、或给的与这张单的对方对不上 → 给模型的
    原因（带这张单真正的对方）；对得上、或单据认不出（由保存时照常报错）回 None。只读。"""
    if tool_input.get("key") or str(tool_input.get("kind") or "") in ("department", ""):
        return None
    try:
        _subject_of(tool_input)  # 形状不对（缺品名、不支持的类别）由保存时照常报错，这里不抢先说对象
        _kind, _direction, side, name, tax, _fields = _party(tool_input)
    except UnknownDocument as err:
        # 认不出是哪张单：算不出对方，不出卡片（卡上只写值会像全公司所有单都改）
        frappe.clear_messages()
        return str(err)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return None
    tax = choice_rules.digits(tax)
    actual = f'"{name}"' + (f" (tax ID {tax})" if len(tax) == 13 else "")
    expected = " ".join(str(tool_input.get("expect_party") or "").split())[:200]
    if not expected:
        return (f"expect_party is missing: pass the tax ID (or name) of the party you told the user this rule is for. "
                f"A rule saved from this document is for its {side} {actual}.")
    if _same_party(expected, name, tax):
        return None
    return (f"This document's {side} is {actual}, not {json.dumps(expected, ensure_ascii=False)}: a rule saved from "
            f"it would be for {actual}. Nothing was saved. Pass the document the user means, or tell the user the "
            f"rule is for {actual} and ask again.")


def choice_refusal(tool_input) -> str | None:
    """出「允许」卡片前的核对（工具声明的 ``refuse``）：公司认不出或是别家的税号（`company_refusal`）、对象对不上就不出卡片、
    直接回失败与原因。"""
    refused = company_refusal(tool_input)
    if refused:
        return refused
    reason = mismatch(tool_input)
    return _json({"status": "not_saved", "reason": reason}) if reason else None


def _period(row, tool_input) -> str:
    filed, filed_period, _scope = _filed()
    return str(tool_input.get("period") or (filed_period if filed == row.name else "") or "")


def choice_save(tool_input) -> str:
    service.require_write()
    row = _company(tool_input, write=True)
    if not row:
        return _no_company(tool_input, write=True)
    sets = service.books(row, limit=None)
    branch, period = str(tool_input.get("branch") or ""), _period(row, tool_input)
    if tool_input.get("key"):
        saved = saved_choices.promote(row, sets, str(tool_input["key"]), branch, period, session=_session())
    else:
        reason = mismatch(tool_input)  # 卡片前已核过；点「允许」后再核一遍（单据可能已改）
        if reason:
            return _json({"status": "not_saved", "reason": reason})
        kind, subject, label = _subject_of(tool_input)
        saved = saved_choices.save_rule(row, sets, kind, subject, str(tool_input.get("value") or ""), branch, period,
                                        subject_label=label, session=_session(),
                                        evidence={"from": "cc", "document": _evidence_document(tool_input)})
    service.announce("upsert", row.name, part="choices")
    rule = saved["rule"]
    parts = str(rule.get("subject") or "").split("|")
    tax = parts[1] if len(parts) > 1 and len(parts[1]) == 13 and parts[1].isdigit() else ""
    return _json({"status": "saved", "company": row.title, "rule": {
        "key": saved["key"], "kind": rule["kind"], "subject_label": rule["subject_label"],
        **({"subject_tax_id": tax} if tax else {}), "value": rule["value"],
        "value_label": rule["value_label"], "branch": rule["branch"] or "whole company",
        "removed_account_set_choices": rule.get("removed_memories", 0)}})


def choice_remove(tool_input) -> str:
    service.require_write()
    row = _company(tool_input, write=True)
    if not row:
        return _no_company(tool_input, write=True)
    key = str(tool_input.get("key") or "")
    if saved_choices.decode(key)[0] != "rule":
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    removed = saved_choices.forget(row, service.books(row, limit=None), key)
    service.announce("upsert", row.name, part="choices")
    return _json({"status": "removed", "company": row.title, "removed": removed.get("removed", 0)})


def _scope_text(branch) -> str:
    return _("cowork.company.branch_n").format(branch) if branch else _("cowork.company.whole_company")


def _described(row, tool_input) -> tuple:
    """卡片上这条规则的「对象 → 值」与范围（只读、不写库；认不出的照参数显示）。按 key 找时不经列表的每类截断
    （`saved_choices.find`）；找不到（已删、不是这家的）回 None 当范围，卡上不露编码后的 key，执行时报找不到。"""
    key = str(tool_input.get("key") or "")
    if key and row:
        parts = saved_choices.decode(key)
        item = saved_choices.find(row, service.books(row, limit=None), key)
        if item:
            value = item.get("value_label") or item.get("value") or ""
            return f"{item.get('subject_label') or ''} → {value}", item.get("branch") or "", parts[0]
        return "", None, parts[0]
    value = str(tool_input.get("value") or "")
    label = {"cash": "Cash", "credit": "Credit", "claim": "Claim VAT", "expense": "Expense it"}.get(value)
    try:
        subject = _subject_of(tool_input)[2]
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        subject = ""
    return " → ".join(part for part in (subject, _(label) if label else value) if part), "", "document"


def _choice_rows(row, text, branch) -> list:
    """存 / 删公司规则的卡片行：公司、范围、这一条（对象 → 值），只列一次、不写「改前 → 改后」。``branch`` 为 None
    （按 key 找不到这一条）时不列范围，值那一行写「这条记住的选择已不在」，不像要删全公司的一条空规则。"""
    company = {"field": _("Company"), "before": "", "after": row.title if row else ""}
    if branch is None:
        return [company, {"field": _("Value"), "before": "", "after": _("cowork.company.choice_not_found")}]
    return [company, {"field": _("Scope"), "before": "", "after": _scope_text(branch)},
            {"field": _("Value"), "before": "", "after": text}]


def preview_choice_save(tool_input) -> list:
    row = _company(tool_input, write=True)
    text, branch, _source = _described(row, tool_input)
    return _choice_rows(row, text, None if branch is None else str(tool_input.get("branch") or ""))


def preview_choice_remove(tool_input) -> list:
    """删哪一条（第 5 轮测试 5）：同保存卡的三行，问句「删除这条公司规则？」已说明是删。原来公司写成「改前 → 改后」两遍、
    这一条写在「改前」后面跟一个空的「→」，卡上看着乱。找不到这一条（已删、不是这家的）时只写公司与「已不在」，执行时报找不到。"""
    row = _company(tool_input, write=True)
    text, branch, _source = _described(row, tool_input)
    return _choice_rows(row, text, branch)


COMPANY_PROP = {"type": "string", "description": "Company id from cowork_companies; leave out for the company this "
                                                 "conversation is filed under."}

TOOLS = [
    {"name": "cowork_companies", "label": "Listed companies",
     "description": "List the firm's client companies (the company masters shared by the whole firm): id, title, "
                    "legal name, tax ID, branches and account set years, up to 20; search matches the name or tax ID. "
                    "Use the id with cowork_company_profile.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
     "handler": "pearnly_cowork.assistant.company_tools.companies"},
    {"name": "cowork_company_profile", "label": "Read company details",
     "description": "Read one client company: its account sets (newest first, with branch and fiscal period), "
                    "branches, the account set covering the work month (period YYYY-MM), its documents folder on each "
                    "computer with its state and month folder, all team notes (with ids), remembered push choices "
                    "(account set choices, choices learned from Express history and company choices), rules (the "
                    "company choices with their keys), scheduled tasks and tax drafts. Without company it reads the "
                    "company this conversation is filed under; work_branch is the branch this conversation works on. "
                    "Times (set_at, updated_at, created_at) are the user's local time with its UTC offset: say them as "
                    "local time. Describe where a remembered choice or rule came from in the user's own words, never "
                    "by its source code.",
     "input_schema": {"type": "object", "properties": {"company": COMPANY_PROP, "period": {"type": "string"}}},
     "handler": "pearnly_cowork.assistant.company_tools.profile"},
    {"name": "cowork_company_note_save", "label": "Saved a company note",
     "description": "Add a line to a client company's team notes, or change one (give note, its id from "
                    "cowork_company_profile). One short sentence per note, up to 500 characters; 50 notes per "
                    "company. The user sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {
         "company": COMPANY_PROP, "text": {"type": "string"}, "note": {"type": "string"}}, "required": ["text"]},
     "approval": True,
     "ask": "Save this to the company's notes?",
     "ask_when": {"note": "Update this company note?"},
     "refuse": "pearnly_cowork.assistant.company_tools.company_refusal",
     "preview": "pearnly_cowork.assistant.company_tools.preview_note_save",
     "handler": "pearnly_cowork.assistant.company_tools.note_save"},
    {"name": "cowork_company_note_remove", "label": "Removed a company note",
     "description": "Remove one line from a client company's team notes (note is its id from cowork_company_profile). "
                    "The user sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {"company": COMPANY_PROP, "note": {"type": "string"}},
                      "required": ["note"]},
     "approval": True,
     "ask": "Remove this company note?",
     "refuse": "pearnly_cowork.assistant.company_tools.company_refusal",
     "preview": "pearnly_cowork.assistant.company_tools.preview_note_remove",
     "handler": "pearnly_cowork.assistant.company_tools.note_remove"},
    {"name": "cowork_company_choice_save", "label": "Saved a company choice",
     "description": "Save a push choice for a whole client company (or one branch with branch, its 5-digit number), "
                    "applied automatically to all its account sets; a choice saved in one account set still overrides "
                    "it there, and saving this removes the same choice saved in its account sets. Either pass key, a "
                    "row from cowork_company_profile choices marked promotable (an account set choice or one learned "
                    "from Express history, which only goes company-wide, without branch), to make it company-wide; "
                    "or pass document, kind (party, item, account, "
                    "payment, vat, department) and value (an account, product or party code from cowork_push_choices, "
                    "or cash / credit, claim / expense), with item (the product name as on the document) for item "
                    "and direction sales for a customer. The rule is for that document's own party (its seller, its "
                    "buyer for a sales customer), worked out from the document itself: pass only the document the "
                    "user is talking about, and as expect_party the tax ID (or the name when it has none) of the party "
                    "you told the user the rule is for; if it is not that document's party nothing is saved. The "
                    "value is checked against the account set covering the work month. Only what the user asked for; "
                    "the user sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {
         "company": COMPANY_PROP, "key": {"type": "string"},
         "document": {"type": "string", "description": "The document's name from the document tool results (not its "
                                                       "invoice number)."},
         "expect_party": {"type": "string", "description": "With document: the tax ID (or name) of the party the rule "
                                                           "is for, as you told the user."},
         "kind": {"type": "string", "enum": list(RULE_KINDS)}, "value": {"type": "string"},
         "item": {"type": "string"}, "direction": {"type": "string", "enum": ["purchase", "sales"]},
         "branch": {"type": "string"}, "period": {"type": "string"}}},
     "approval": True,
     "ask": "Save this choice for the company?",
     "refuse": "pearnly_cowork.assistant.company_tools.choice_refusal",
     "preview": "pearnly_cowork.assistant.company_tools.preview_choice_save",
     "handler": "pearnly_cowork.assistant.company_tools.choice_save"},
    {"name": "cowork_company_choice_remove", "label": "Removed a company choice",
     "description": "Remove one company choice (key from cowork_company_profile rules). Pushes then use the account set's "
                    "own choice, or ask again. The user sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {"company": COMPANY_PROP, "key": {"type": "string"}},
                      "required": ["key"]},
     "approval": True,
     "ask": "Remove this company choice?",
     "refuse": "pearnly_cowork.assistant.company_tools.company_refusal",
     "preview": "pearnly_cowork.assistant.company_tools.preview_choice_remove",
     "handler": "pearnly_cowork.assistant.company_tools.choice_remove"},
]
