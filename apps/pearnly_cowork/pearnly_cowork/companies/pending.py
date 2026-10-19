"""「待处理」统一入口（ADR-020，补充 乙）：设了定时任务，第二天会计员一眼看到哪些单要人处理。

- 从现有状态现算，不新增存储。范围 = 要人处理的单：推送停下等人（`manual`，含 `choice_needed:*` 要人选、结果不明）、
  推送失败（有原因、没在推）、识别了要推却停着没推的（选了 Express / MR.ERP 去向、还没推送状态，且建了超过
  `SETTLE_MINUTES`：刚上传的还在路上）。与 `cowork_list_documents` 的 `push_state="waiting"` 同一口径（推送成功、在途的
  不算），只是去掉「仅识别」不推的单（没人要推它，列进来只会淹掉真要处理的）。
- 每项归到哪家主档：先按推送去向的账套（`service.attribution`），再按本方税号（进项看买方、销项看卖方，先买方）；
  归不上的 `company` 为空。能当场选的（`choice_needed`，Express）给 `action = "choose"` 与推送去向，页面用现有的推送选择
  接口取可选项、作答（`push.application.api.choices / save_choices`）；其余 `open`，点开交给 CC。
- 早上汇总（`morning`）：每天 08:30（曼谷）起的那一段，给有待处理单的负责人（推送发起人，没推过的是上传人）各推一条
  对话提醒（`notices.post`：网页推送 + 电脑通知，不发 LINE），点开到「待处理」。数量为 0 不推；与这人上一条汇总之后
  没有新增（新建或新失败）也不推。接在定时任务提醒的同一个入口上（`folder_intake.schedule.notices` 经 hooks
  `cowork_scheduled_notices` 叫这里），不另开定时任务。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.companies import identity
from pearnly_cowork.targets.identity import normalize_express_account_key

HISTORY = "Cowork OCR History"
PUSH_RECORD = "Cowork Push Record"
FIELDS = ["name", "invoice_no", "invoice_date", "seller_name", "seller_tax", "buyer_name", "buyer_tax",
          "total_amount", "intake_target", "push_connection", "push_account_set", "last_push_status",
          "last_push_reason", "last_push_record", "last_pushed_at", "filename", "owner", "creation", "modified",
          "edit_count", "auto_class", "auto_reasons"]
TARGETS = ("express", "mrerp")
SETTLED = ("pending", "success")
SETTLE_MINUTES = 15
PENDING_MAX = 500
LIMIT_MAX = 200
REASONS = ("choice_needed", "result_unknown", "manual", "failed", "cc_proposal", "not_pushed")
NOT_PUSHED_LABEL = "cowork.company.pending.not_pushed"
PROPOSAL_LABEL = "cowork.company.pending.cc_proposal"
BOOK_MANY = "book_many"
# 自动推前判过、冻结了提议的类（数字会计员第 3 期）：A / B 在 L0 / L1 只出提议；C 类照旧落「没推」
PROPOSED = ("A", "B")
SKIPPED_POLICY = "document_policy_skip_"  # 同 `contracts.document_policy.SKIPPED_PREFIX`（这里只做纯判断，不引那个模块）
# 早上汇总
KIND = "cowork.pending_digest"
MORNING = time(8, 30)
MORNING_WINDOW = timedelta(hours=2)  # 08:30 之后这段时间里每 5 分钟看一次（部署重启跨过 08:30 也不漏）
BANGKOK = ZoneInfo("Asia/Bangkok")
DIGEST_TTL = 12 * 3600
DIGEST_COMPANIES = 5
REFS_MAX = 100


# ---------------- 纯判断 ----------------


def classify(row, cutoff, unknown=frozenset()) -> str | None:
    """一张单要不要人处理、为什么（`REASONS`）；不用处理回 None。`cutoff`：早于它建的「没推」才算停着（站点时区）；
    `unknown`：结果不明的推送记录名。"""
    status = str(row.get("last_push_status") or "")
    reason = str(row.get("last_push_reason") or "")
    if status in SETTLED:
        return None
    if reason.startswith(SKIPPED_POLICY):
        # 这个租户选了这类单据不录入（转账凭条、预扣税凭证）：人已定了不推，不算待处理（第 7 批 B7）
        return None
    if reason.startswith("choice_needed"):
        # 要人选：推送服务停在复核时状态是 manual；CC 对话、定时任务在推之前就停下时只记原因（`push_state.record_failure`），
        # 状态不变（空，或上一次的失败）——都是要人选，不能当成「失败、请重新提交」（2026-10-15 验收 F12）
        return "choice_needed"
    if status == "manual":
        if row.get("last_push_record") in unknown or "RESULT_UNKNOWN" in reason.upper():
            return "result_unknown"
        return "manual"
    if reason:
        return "failed"
    if proposal(row):
        return "cc_proposal"
    if row.get("intake_target") in TARGETS and row.get("creation") and _naive(row["creation"]) < _naive(cutoff):
        return "not_pushed"
    return None


def proposal(row) -> bool:
    """CC 准备好、等人点「推送」的提议（数字会计员第 3 期第 3、5、11 条）：自动推前判成 A / B、没人动过票（改了就作废提议、
    交给人）、有推送去向、还没推。调用方已排除推成功 / 在推 / 停下 / 失败的。"""
    return (str(row.get("auto_class") or "") in PROPOSED and not int(row.get("edit_count") or 0)
            and row.get("intake_target") in TARGETS and bool(row.get("push_connection")))


def _naive(value):
    return value.replace(tzinfo=None) if isinstance(value, datetime) else frappe.utils.get_datetime(value)


def company_of(row, by_set, by_tax) -> str:
    """单据归哪家主档：推送去向的账套 → 买方税号 → 卖方税号；都不是回空串。"""
    connection, account_set = row.get("push_connection"), row.get("push_account_set")
    if connection and account_set:
        key = normalize_express_account_key(account_set) or str(account_set)
        found = by_set.get((connection, key.casefold()))
        if found:
            return found
    for side in ("buyer_tax", "seller_tax"):
        found = by_tax.get(identity.digits(row.get(side)))
        if found:
            return found
    return ""


def counterparty(row, tax_of_company="") -> str:
    """对方名称：本方是买方就是卖方，本方是卖方就是买方；看不出按进项（卖方）。"""
    mine = identity.digits(tax_of_company)
    if mine and identity.digits(row.get("seller_tax")) == mine:
        return str(row.get("buyer_name") or "")
    return str(row.get("seller_name") or row.get("buyer_name") or "")


def amount(value) -> str:
    try:
        number = Decimal(str(value or "").replace(",", "").strip())
    except InvalidOperation:
        return ""
    return format(number.quantize(Decimal("0.01")), "f") if number.is_finite() else ""


def month_of(row) -> str:
    """这张单记在哪个月（YYYY-MM）：单据日期的月份；没有单据日期的按上传（建）的月份。与月结台账取单同一口径
    （`ledger._documents`：有日期按日期、没日期按本月上传），会话栏「待处理」卡据此只列对话月份以外的（当月的在「本月」卡）。"""
    when = row.get("invoice_date") or row.get("creation")
    return str(when or "")[:7]


def book_many(row) -> bool:
    """自动推前判成 C、原因里有「按日期选出多份同期账套」（`push.express.automation` 的 ``book_many``），还没人推过。别的 C 原因
    不影响：选好账套是人推（照常重判），页面只显示「多份账套」这一句。``book_many`` 在 ``auto_reasons`` 里排最前
    （`automation.reason_codes`），140 字截断截不掉它。"""
    return (str(row.get("auto_class") or "") == "C" and not row.get("last_push_status")
            and not row.get("last_push_reason") and BOOK_MANY in str(row.get("auto_reasons") or "").split(","))


# MR.ERP 推送停在「这份账套的导入菜单 / 默认值要选」（推之前 CC 的核对、或推送时 MR.ERP 那边核对出来的，原因码同
# `targets.mrerp_menus` / `targets.mrerp_masters`）：待处理与月结台账当场选（action ``mrerp``，2026-10-19），选完照常重推。
MRERP_SETUP_CODES = ("ERR_MRERP_MENU_CHOICE_REQUIRED", "ERR_MRERP_MENU_CHANGED", "ERR_MRERP_DEFAULT_CHOICE_REQUIRED",
                     "ERR_MRERP_DEFAULT_CHANGED")


def mrerp_setup_stop(row) -> bool:
    """停在要选 MR.ERP 导入菜单 / 默认值、知道推往哪条连接哪份账套的单（没推成：成功、在途、结果不明的不算）。纯判断。"""
    reason = str(row.get("last_push_reason") or "")
    return (bool(row.get("push_connection")) and bool(row.get("push_account_set"))
            and str(row.get("last_push_status") or "") not in SETTLED
            and any(code in reason for code in MRERP_SETUP_CODES))


def account_set_stop(row) -> bool:
    """停在「待选账套」：定时任务跟随公司当年账套时没有 / 多于一份 / 够不到单据日期的账套（T1 的三个原因码），或自动推前
    判出多份同期账套（:func:`book_many`，数字会计员第 3 期），且有推送去向。"""
    from pearnly_cowork.folder_intake.contracts import ACCOUNT_SET_STOPS

    return bool(row.get("push_connection")) and str(row.get("last_push_status") or "") not in SETTLED \
        and (str(row.get("last_push_reason") or "") in ACCOUNT_SET_STOPS or book_many(row))


def company_for(row) -> str:
    """这张单归哪家主档（同列表：推送去向的账套 → 买方 → 卖方税号，按本人能用的连接现算）；归不上回空串。"""
    by_set, by_tax, _tax_ids = _attribution()
    return company_of(row, by_set, by_tax)


def _task_company(row) -> tuple[str, str] | None:
    """这张票出自的定时任务记的公司与分店（推往同一条连接、记了公司的那条，同 `folder_intake.push.fixed_for` 的路）；
    找不到回 None。"""
    from pearnly_cowork.folder_intake import push as folder_push

    for rule in folder_push.source_rules(row.get("name")):
        if rule.connection == row.get("push_connection") and rule.get("company"):
            return rule.company, rule.get("company_branch") or ""
    return None


def _stop_scope(row) -> dict | None:
    """停在「待选账套」的单的候选：``{"workspace", "identity", "books"}``（`identity.book` 的样子，覆盖单据日期、还没按「现用的
    那份」筛）。定时任务停下的：出这张单的任务所跟的公司（与分店）在推送去向那条连接上的（同 `tasks.books_of`），找不到任务
    （已删）才退回按这张单现算归属的公司（推送去向账套 → 买方 → 卖方税号，不分店）。自动推判出多份同期的：同判档时
    （`automation.day_scope`，按推送去向账套在目录里那一行认公司与分店）。没有单据日期、归不上公司回 None。"""
    from pearnly_cowork.companies import tasks
    from pearnly_cowork.companies.access import COMPANY

    day = str(row.get("invoice_date") or "")[:10]
    connection = row.get("push_connection")
    if not day or not connection:
        return None
    if book_many(row):
        return _target_scope(row, day)
    company, branch = _task_company(row) or (company_for(row), "")
    if not company:
        return None
    books = tasks.books_of(company, connection, day, branch, remembered=False)
    if books is None:
        return None
    found = frappe.db.get_value(COMPANY, company, ["identity", "workspace"], as_dict=True) or {}
    return {"workspace": found.get("workspace"), "identity": found.get("identity"), "books": books}


def _target_scope(row, day) -> dict | None:
    """按推送去向那份账套在目录里那一行认公司与分店（同自动推判档 `automation.day_scope`）：``{"workspace", "identity",
    "books"}``；连接不是本人能用的、目录里没有这份回 None。"""
    from pearnly_cowork.companies import automation, service

    connection = row.get("push_connection")
    if not any(item["name"] == connection for item in service.usable_connections()):
        return None
    catalog = service.facts(connection).get("account_sets") or []
    wanted = normalize_express_account_key(row.get("push_account_set"))
    account_row = next((item for item in catalog if isinstance(item, dict) and wanted
                        and normalize_express_account_key(item.get("source_id")) == wanted), None)
    return automation.day_scope(connection, account_row, day, catalog) if account_row else None


def account_sets_for(row) -> list:
    """停在「待选账套」的单可换的账套：``[{"key", "label"}]``（:func:`_stop_scope`）。定时任务停下的与定时任务选账套同一判据
    （`tasks.books_of`，带分店），选了推送时任务默认处理照样套用（`follow.member`）。列全这一组（不按记住的现用账套筛，
    选另一份就是改选）。不是这种单、没有单据日期、归不上公司回空表（第 6 批，待处理页当场换账套；分店筛选见第 6 批审查第 4 条）。"""
    return account_set_options(row)["account_sets"]


def account_set_options(row) -> dict:
    """同 :func:`account_sets_for`，另回 ``current``：这一组候选是多份同期、这家公司已经记住且此刻仍算数的现用账套
    （`current_book.remembered` 只留下一份；另一份有了更新录入要重问的不算）的键，否则空串。待处理展开时预选它
    （验收 F18：只预选已定的，2026-10-19 验收：记住了还停着的单展开后账套框是空的）。"""
    from pearnly_cowork.companies import current_book

    if not account_set_stop(row):
        return {"account_sets": [], "current": ""}
    found = _stop_scope(row) or {}
    books = found.get("books") or []
    current = ""
    if len(books) > 1 and found.get("workspace"):
        picked = current_book.remembered(found["workspace"], found.get("identity"), str(row.get("invoice_date") or "")[:10],
                                         books)
        current = picked[0]["key"] if len(picked) == 1 else ""
    return {"account_sets": [{"key": item["key"], "label": item.get("label") or item["key"]} for item in books],
            "current": current}


def current_book_choice(row, key) -> dict | None:
    """会计员在停在「待选账套」的单上选了 ``key``：这组候选是多份同期（用户 2026-10-17「按公司问一次哪份是现用的」）→
    ``{"workspace", "key": 口径键, "value": 规范账套键, "seen": 这组候选此刻的最新录入}``，调用方经
    `history.document_policy.record` 记下（``seen`` 给之后判「另一份有了更新录入就重问」，`current_book.seen_of`，用户 10-18）；
    只有一份、选的不在候选里、认不出公司回 None（只换这一张，不记）。"""
    from pearnly_cowork.companies import current_book

    if not account_set_stop(row):
        return None
    found = _stop_scope(row) or {}
    wanted = normalize_express_account_key(key)
    books = found.get("books") or []
    if not wanted or not any(normalize_express_account_key(item["key"]) == wanted for item in books):
        return None
    policy_key = current_book.key_of(found.get("identity"), str(row.get("invoice_date") or "")[:10], books)
    if not policy_key or not found.get("workspace"):
        return None
    return {"workspace": found["workspace"], "key": policy_key, "value": wanted, "seen": current_book.seen_of(books)}


def current_book_options(row) -> list:
    """停在「待选账套」、这一组候选能问「哪份是现用的」（多份同期、键算得出：认得出公司、同一段、不跨分店）的单：可选的
    账套 ``[{"key", "label"}]``；否则空表（只能换这一张，CC 不提「现用账套」）。"""
    from pearnly_cowork.companies import current_book

    if not account_set_stop(row):
        return []
    found = _stop_scope(row) or {}
    books = found.get("books") or []
    if not found.get("workspace") or not current_book.key_of(found.get("identity"), str(row.get("invoice_date") or "")[:10],
                                                              books):
        return []
    return [{"key": item["key"], "label": item.get("label") or item["key"]} for item in books]


def current_book_rechoice(row, key) -> dict | None:
    """已经记住、不再停下的单改选现用账套（CC 工具）：推送去向那份账套这一天的候选（:func:`_target_scope`，同自动推判档）是
    多份同期、``key`` 在其中 → ``{"workspace", "key": 口径键, "value": 规范账套键, "seen"}``（同 :func:`current_book_choice`）；
    否则 None。停在「待选账套」的单不走这里（要换这一张并推，走 :func:`current_book_choice`）。"""
    from pearnly_cowork.companies import current_book

    day = str(row.get("invoice_date") or "")[:10]
    if account_set_stop(row) or not day or not row.get("push_connection"):
        return None
    found = _target_scope(row, day) or {}
    wanted = normalize_express_account_key(key)
    books = found.get("books") or []
    if not wanted or not any(normalize_express_account_key(item["key"]) == wanted for item in books):
        return None
    policy_key = current_book.key_of(found.get("identity"), day, books)
    if not policy_key or not found.get("workspace"):
        return None
    return {"workspace": found["workspace"], "key": policy_key, "value": wanted, "seen": current_book.seen_of(books)}


def account_set_pick(row, key) -> dict | None:
    """会计员选的那份（只认 :func:`account_sets_for` 列出的，按规整后的账套键比）；不在列回 None。"""
    wanted = normalize_express_account_key(key)
    return next((item for item in account_sets_for(row)
                 if wanted and normalize_express_account_key(item["key"]) == wanted), None)


# ---------------- 提议展开（数字会计员第 3 期，用户 10-19「先点开这一行看单据内容，展开后才有推送」） ----------------

PROPOSAL_MONEY = ("before_vat", "vat", "total")
# 冻结的抵不抵进项（`push.automation.rules.values`：capitalize = 税额计入成本）→ 页面与「记住的选择」同一套值
PROPOSAL_VAT = {"claim": "claim", "capitalize": "expense"}
PROPOSAL_NEW = "new"  # `rules.values`：对方档要新建


def _proposal_of(row) -> dict:
    raw = row.get("auto_proposal")
    try:
        found = json.loads(raw) if isinstance(raw, str) and raw else raw
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _proposal_book(connection, key) -> dict:
    """推往的那份账套 ``{key, label}``：名称同待处理账套框（目录这一行经 `identity.book`）；本人用不了这条连接、目录里没有
    这份时名称就是键。"""
    from pearnly_cowork.companies import service

    label = ""
    usable = {item["name"]: item.get("adapter") for item in service.usable_connections()}
    if connection in usable and key:
        for row in service.facts(connection).get("account_sets") or []:
            if isinstance(row, dict) and (normalize_express_account_key(row.get("source_id"))
                                          or str(row.get("source_id") or "")) == key:
                label = identity.book(usable[connection], connection, row)["label"]
                break
    return {"key": key, "label": label or key}


def _proposal_masters(connection, key):
    """这份账套报过的主档（科目、部门、存货、供应商 / 客户，只读，同「记住的选择」）；本人用不了这条连接、没读过回 None。"""
    from pearnly_cowork.companies import service
    from pearnly_cowork.connections import preparation

    if not key or not any(item["name"] == connection for item in service.usable_connections()):
        return None
    return preparation.reported_masters(frappe.get_doc("Cowork ERP Connection", connection), key,
                                        keys=preparation.ALL_MASTER_LISTS)


def _proposal_item(kind, value, masters) -> dict:
    """一项做法 → ``{kind, label, value, value_label}``：付款、抵进项换成「记住的选择」的值（现 / 赊、抵 / 记费用）；
    对方档新建写「新建（按票面）」；码按主档写「码 名称」，几个码逗号隔开；主档里没有的就是码本身。"""
    from pearnly_cowork.companies import choices
    from pearnly_cowork.push.express.pushed import PAYMENT_OF

    label = ""
    if kind == "payment":
        # 进项的 HP / RR 换成 cash / credit（页面用现购 / 赊购的词）；销项的 HS / IV 保留单据类型，名称写现金 / 赊账
        direction = "purchase" if ("purchase", value) in PAYMENT_OF else "sales"
        fixed = PAYMENT_OF.get((direction, value), "")
        label = choices.value_label("payment", fixed) if fixed else ""
        value = fixed if direction == "purchase" and fixed else value
    elif kind == "vat":
        value = PROPOSAL_VAT.get(value, value)
        label = choices.value_label("vat", value)
    elif kind == "party" and value == PROPOSAL_NEW:
        label = _("cowork.choice.new")
    else:
        codes = [code for code in value.split(",") if code] if kind in ("item", "receipt") else [value]
        label = ", ".join(choices.master_name(kind, code, masters) or code for code in codes)
    return {"kind": kind, "label": _("cowork.choice." + kind), "value": value, "value_label": label or value}


def proposal_view(row) -> dict:
    """CC 准备好的提议给人看的样子（待处理、月结台账点开那一行时）：``{document, account_set: {key, label}, items: [{kind,
    label, value, value_label}], date, ref_no, party, party_tax, before_vat, vat, total}``。照票上冻结的
    ``auto_proposal``（`push.automation.rules.proposal`）显示：票改过、规则变了也照旧，推的时候照常重判；没有提议时
    ``items`` 为空、日期单号金额取票面。只读，不写库、不请电脑读主档。"""
    from pearnly_cowork.push.automation.rules import PROPOSAL_KINDS

    proposed = _proposal_of(row)
    connection = str(row.get("push_connection") or "")
    raw_key = str(row.get("push_account_set") or "")
    key = normalize_express_account_key(raw_key) or raw_key
    values = proposed.get("values") if isinstance(proposed.get("values"), dict) else {}
    wanted = [(kind, str(values.get(kind) or "").strip()) for kind in PROPOSAL_KINDS]
    wanted = [(kind, value) for kind, value in wanted if value]
    masters = _proposal_masters(connection, key) if wanted else None
    party_tax = identity.digits(proposed.get("party_tax"))
    buyer = bool(party_tax) and party_tax == identity.digits(row.get("buyer_tax")) \
        and party_tax != identity.digits(row.get("seller_tax"))
    party = str((row.get("buyer_name") if buyer else row.get("seller_name")) or row.get("seller_name")
                or row.get("buyer_name") or "")
    out = {"document": row.get("name"), "account_set": _proposal_book(connection, key),
           "items": [_proposal_item(kind, value, masters) for kind, value in wanted],
           "date": str(proposed.get("date") or row.get("invoice_date") or "")[:10],
           "ref_no": str(proposed.get("ref_no") or row.get("invoice_no") or ""),
           "party": party or party_tax, "party_tax": party_tax}
    for field in PROPOSAL_MONEY:
        out[field] = amount(proposed.get(field))
    if not out["total"] and not proposed:
        out["total"] = amount(row.get("total_amount"))
    return out


def choice_kinds(reason) -> list:
    text = str(reason or "")
    if not text.startswith("choice_needed:"):
        return []
    return [kind for kind in dict.fromkeys(text.split(":", 1)[1].split(",")) if kind]


def counts(items) -> dict:
    """`{"total": n, "companies": {主档: n}, "unassigned": n}`。纯函数。"""
    by_company, unassigned = {}, 0
    for item in items:
        if item.get("company"):
            by_company[item["company"]] = by_company.get(item["company"], 0) + 1
        else:
            unassigned += 1
    return {"total": sum(by_company.values()) + unassigned, "companies": by_company, "unassigned": unassigned}


def is_new(row, since_site, since_utc) -> bool:
    """上次汇总之后新出现的：新建的（站点时区 `creation`），或新推失败 / 停下的（UTC `last_pushed_at`）。"""
    if since_site is None:
        return True
    if row.get("creation") and _naive(row["creation"]) > _naive(since_site):
        return True
    return bool(row.get("last_pushed_at") and _naive(row["last_pushed_at"]) > _naive(since_utc))


def morning_due(now_local: datetime) -> bool:
    """曼谷时间 08:30 起 `MORNING_WINDOW` 内。"""
    start = now_local.replace(hour=MORNING.hour, minute=MORNING.minute, second=0, microsecond=0)
    return start <= now_local < start + MORNING_WINDOW


def digest_text(total, by_company, titles) -> str:
    """早上汇总的正文（按收件人语言）：「N 份单等你处理（公司A 3、公司B 5）」，最多列 5 家，多的写「另有 N 家」。"""
    ranked = sorted(by_company.items(), key=lambda item: (-item[1], titles.get(item[0], "").casefold()))
    parts = [f"{titles.get(name) or '?'} {count}" for name, count in ranked[:DIGEST_COMPANIES]]
    if len(ranked) > DIGEST_COMPANIES:
        parts.append(_("cowork.company.pending.more").format(len(ranked) - DIGEST_COMPANIES))
    if not parts:
        return _("cowork.company.pending.text_plain").format(total)
    return _("cowork.company.pending.text").format(total, ", ".join(parts))


# ---------------- 读 ----------------


def _cutoff():
    return frappe.utils.now_datetime() - timedelta(minutes=SETTLE_MINUTES)


def _candidates(*, permitted=True, limit=PENDING_MAX) -> list:
    """可能要处理的单（库里粗筛，细判在 `classify`）：没推成功、也不在推的；且停下等人、有失败原因或选了推送去向。
    `permitted`：按当前用户的录入列表权限取（`get_list`）；后台汇总不按人（`get_all`）。"""
    getter = frappe.get_list if permitted else frappe.get_all
    return getter(HISTORY, filters={"last_push_status": ["not in", list(SETTLED)]},
                  or_filters=[["last_push_status", "=", "manual"], ["last_push_reason", "is", "set"],
                              ["intake_target", "in", list(TARGETS)]],
                  fields=FIELDS, order_by="modified desc, name desc", limit_page_length=limit)


def _unknown(rows) -> frozenset:
    names = [row.last_push_record for row in rows if row.get("last_push_status") == "manual" and row.get("last_push_record")]
    if not names:
        return frozenset()
    return frozenset(frappe.get_all(PUSH_RECORD, filters={"name": ["in", names], "result_unknown": 1}, pluck="name",
                                    limit_page_length=0))


def _reason_label(code, reason, row=None):
    from pearnly_cowork.push import export_facts

    if row is not None and book_many(row):
        # 自动推前判出多份同期账套：与定时任务「多于一份」同一句（`folder_intake.contracts.ACCOUNT_SET_AMBIGUOUS`）
        from pearnly_cowork.folder_intake.contracts import ACCOUNT_SET_AMBIGUOUS

        return _(ACCOUNT_SET_AMBIGUOUS)
    if code == "not_pushed":
        return _(NOT_PUSHED_LABEL)
    if code == "cc_proposal":
        return _(PROPOSAL_LABEL)
    return export_facts.reason_text(reason) if reason else ""


def _items(rows, by_set, by_tax, tax_ids) -> list:
    from pearnly_cowork.companies import exceptions

    cutoff, unknown = _cutoff(), _unknown(rows)
    out = []
    for row in rows:
        found = exceptions.document_code(row, cutoff, unknown)
        if not found:
            continue
        stable, code = found
        company = company_of(row, by_set, by_tax)
        # reason_code 是原有的粗分（`REASONS`，页面与早上汇总照旧用）；code 是统一异常清单的稳定码（`exceptions.CODES`）
        item = {"document": row.name, "company": company or None, "reason_code": code, "code": stable,
                "reason_label": _reason_label(code, row.get("last_push_reason"), row),
                "invoice_no": row.get("invoice_no") or "", "counterparty": counterparty(row, tax_ids.get(company, "")),
                "amount": amount(row.get("total_amount")), "date": str(row.get("invoice_date") or ""),
                "filename": row.get("filename") or "", "month": month_of(row),
                "action": "choose" if code == "choice_needed" and row.get("push_connection") else "open"}
        if item["action"] == "choose":
            item["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                              "kinds": choice_kinds(row.get("last_push_reason"))}
        elif code == "cc_proposal":
            # CC 准备好的提议：页面当场「推送」（`assistant.pending_api.answer` 交空的选择，同一条路重判、推）
            item["action"] = "push"
            item["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                              "kinds": []}
        elif mrerp_setup_stop(row):
            # MR.ERP 停在要选导入菜单 / 默认值：页面当场选（`assistant.pending_api.mrerp_needs` / `mrerp_answer`），选完重推
            item["action"] = "mrerp"
            item["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                              "kinds": []}
        elif account_set_stop(row):
            # 定时任务跟随公司当年账套停在「待选账套」（T1）、自动推前判出多份同期账套（第 3 期）：页面当场换一份账套重推
            # （`assistant.pending_api.choose_account_set`，可换的由 `account_sets` 按这张单的公司与单据日期现算；多份同期时
            # 选的那份记成这家公司现用的），不用交给 CC
            item["action"] = "account_set"
            item["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                              "kinds": []}
        out.append(item)
    return out


def _attribution():
    from pearnly_cowork.companies import service

    by_set, by_tax = service.attribution()
    return by_set, by_tax, {company: tax for tax, company in by_tax.items()}


def pending(company=None, limit=None) -> dict:
    """要人处理的单（当前用户看得到的），新 → 旧：`{"items": [...], "count": n}`；给了 `company` 只看这家。"""
    scope.require_read()
    try:
        limit = max(1, min(int(limit or LIMIT_MAX), LIMIT_MAX))
    except (TypeError, ValueError):
        limit = LIMIT_MAX
    by_set, by_tax, tax_ids = _attribution()
    items = _items(_candidates(), by_set, by_tax, tax_ids)
    if company:
        items = [item for item in items if item["company"] == company]
    return {"items": items[:limit], "count": len(items)}


def pending_counts() -> dict:
    """侧栏用：按公司计数与总数（`counts`）。同 `pending` 的判据：库里一次粗筛（最多 500 张），按主档分好。"""
    scope.require_read()
    by_set, by_tax, tax_ids = _attribution()
    return counts(_items(_candidates(), by_set, by_tax, tax_ids))


# ---------------- 早上汇总 ----------------


def responsible(row) -> str:
    """这张单找谁：推送发起人（推过的），没推过的是上传人。"""
    return row.get("actor") or row.get("owner") or ""


def _actors(rows) -> dict:
    names = [row.last_push_record for row in rows if row.get("last_push_record")]
    if not names:
        return {}
    return {row.name: row.actor for row in frappe.get_all(PUSH_RECORD, filters={"name": ["in", names]},
                                                          fields=["name", "actor"], limit_page_length=0)}


@contextmanager
def _as(user):
    """以收件人的身份与语言算（账套按这个人能用的连接认），做完还原（同 `folder_intake.machine._as`）。"""
    from frappe.translate import get_user_lang

    from pearnly_cowork.companies import service

    previous, previous_lang = frappe.session.user, getattr(frappe.local, "lang", None)
    frappe.set_user(user)
    frappe.local.lang = get_user_lang(user)
    job = getattr(frappe.local, "job", None)
    if job is not None:
        job.lang_resolved = True
    service.forget_cache()
    try:
        yield
    finally:
        service.forget_cache()
        frappe.set_user(previous)
        frappe.local.lang = previous_lang


def _local_now(now):
    tz = ZoneInfo(frappe.utils.get_system_timezone())
    site = frappe.utils.get_datetime(now) if now else frappe.utils.now_datetime()
    return site, site.replace(tzinfo=tz).astimezone(BANGKOK)


def _site_to_utc(value):
    if value is None:
        return None
    tz = ZoneInfo(frappe.utils.get_system_timezone())
    return _naive(value).replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def morning(now=None) -> int:
    """每 5 分钟由 `folder_intake.schedule.notices` 叫一次（hooks `cowork_scheduled_notices`）；只在曼谷 08:30 起的那段
    做事。回推了几条。`now`：站点时区的时间字符串，测试环境手动触发用。"""
    if frappe.conf.get("pearnly_product") != "cowork":
        return 0
    site_now, local = _local_now(now)
    if not morning_due(local):
        return 0
    from pearnly_core.assistant import notices

    rows = _candidates(permitted=False)
    actors = _actors(rows)
    cutoff, unknown = site_now - timedelta(minutes=SETTLE_MINUTES), _unknown(rows)
    mine = {}
    for row in rows:
        if classify(row, cutoff, unknown):
            row["actor"] = actors.get(row.get("last_push_record")) or ""
            mine.setdefault(responsible(row), []).append(row)
    sent = 0
    day = local.date().isoformat()
    for user, owned in sorted(mine.items()):
        key = f"{KIND}:{user}:{day}"
        try:
            if not user or user in ("Guest", "Administrator") or notices.exists(key) \
                    or not scope.allowed("intake", user):
                continue
            since = notices.latest(user, KIND)
            if not any(is_new(row, since, _site_to_utc(since)) for row in owned):
                continue
            with _as(user):
                by_set, by_tax, tax_ids = _attribution()
                items = _items(owned, by_set, by_tax, tax_ids)
                if not items:
                    continue
                total = counts(items)
                titles = _titles(total["companies"])
                title = _("cowork.company.pending.title")
                text = digest_text(total["total"], total["companies"], titles)
            notices.post(user, KIND, text, key, title=title, reference_doctype=HISTORY,
                         reference_names=[item["document"] for item in items][:REFS_MAX], ttl=DIGEST_TTL,
                         view="pending")
            frappe.db.commit()
            sent += 1
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="Cowork pending digest failed", message=frappe.get_traceback()[-1500:])
    return sent


def _titles(names) -> dict:
    from pearnly_cowork.companies.access import COMPANY

    if not names:
        return {}
    return {row.name: row.title or "" for row in frappe.get_all(COMPANY, filters={"name": ["in", list(names)]},
                                                                fields=["name", "title"], limit_page_length=0)}


__all__ = ["account_set_options", "account_set_stop", "account_sets_for", "book_many", "classify", "company_of", "counts",
           "current_book_choice", "current_book_options", "current_book_rechoice",
           "digest_text", "is_new", "month_of", "morning", "morning_due", "pending", "pending_counts", "proposal",
           "proposal_view"]
