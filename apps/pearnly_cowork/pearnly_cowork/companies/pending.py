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
          "last_push_reason", "last_push_record", "last_pushed_at", "owner", "creation", "modified"]
TARGETS = ("express", "mrerp")
SETTLED = ("pending", "success")
SETTLE_MINUTES = 15
PENDING_MAX = 500
LIMIT_MAX = 200
REASONS = ("choice_needed", "result_unknown", "manual", "failed", "not_pushed")
NOT_PUSHED_LABEL = "cowork.company.pending.not_pushed"
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
    if status == "manual":
        if reason.startswith("choice_needed"):
            return "choice_needed"
        if row.get("last_push_record") in unknown or "RESULT_UNKNOWN" in reason.upper():
            return "result_unknown"
        return "manual"
    if reason:
        return "failed"
    if row.get("intake_target") in TARGETS and row.get("creation") and _naive(row["creation"]) < _naive(cutoff):
        return "not_pushed"
    return None


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


def _reason_label(code, reason):
    from pearnly_cowork.push import export_facts

    if code == "not_pushed":
        return _(NOT_PUSHED_LABEL)
    return export_facts.reason_text(reason) if reason else ""


def _items(rows, by_set, by_tax, tax_ids) -> list:
    cutoff, unknown = _cutoff(), _unknown(rows)
    out = []
    for row in rows:
        code = classify(row, cutoff, unknown)
        if not code:
            continue
        company = company_of(row, by_set, by_tax)
        item = {"document": row.name, "company": company or None, "reason_code": code,
                "reason_label": _reason_label(code, row.get("last_push_reason")),
                "invoice_no": row.get("invoice_no") or "", "counterparty": counterparty(row, tax_ids.get(company, "")),
                "amount": amount(row.get("total_amount")), "date": str(row.get("invoice_date") or ""),
                "action": "choose" if code == "choice_needed" and row.get("push_connection") else "open"}
        if item["action"] == "choose":
            item["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                              "kinds": choice_kinds(row.get("last_push_reason"))}
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


__all__ = ["classify", "company_of", "counts", "digest_text", "is_new", "morning", "morning_due", "pending",
           "pending_counts"]
