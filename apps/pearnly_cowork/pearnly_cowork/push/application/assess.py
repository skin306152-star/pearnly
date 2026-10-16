"""自动推前的判定（数字会计员第 3 期第 1–5、11 条 `application.assess`）：与网页「要选的项」（`service.choices_view`）同一套取数，
只看不记（结束整笔回滚），多回这张单的类别（`push.express.automation.classify`）、这家此刻的档位与这次该怎么办。

调用方（定时任务 `folder_intake.push`、网页上传 / LINE 的扫描 `push.automation.sweep`）先把自己的写提交掉，再调这里；
判出的结果由调用方经 `history.push_state.record_assessment` 记在票上、按 ``action`` 推、出提议或停下。人发起的推送（复核页、
待处理、CC）不经这里，照现在推。

只核 Express；MR.ERP 本期不进档位（定时任务照旧「没有要选的就推」，第 19 条已知缺口），回 ``{"assessed": False}``。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.connections import service as connections
from pearnly_cowork.contracts.push import clean_tax_id
from pearnly_cowork.history import storage, text_layer
from pearnly_cowork.push import choices as choice_store
from pearnly_cowork.push.application import service
from pearnly_cowork.push.application.provenance import original_history
from pearnly_cowork.push.automation import rules
from pearnly_cowork.push.automation import service as automation_service
from pearnly_cowork.push.express import automation
from pearnly_cowork.push.express.preflight import ExpressContext
from pearnly_cowork.push.express.prepare import prepare_express
from pearnly_cowork.targets import account_match

BANGKOK = ZoneInfo("Asia/Bangkok")
PRIOR_MAX = 50
# 重复分流算进去的推送记录状态：除作废（cancelled）以外都算（manual 是结果不明或推送失败待人看，宁严）
PRIOR_STATUSES = ("prepared", "pending", "leased", "manual", "success")
# 取这家供应商最近推成功的单最多这么多张（只要 `automation.MEMORY_STREAK` 张有科目的；载荷对不上的跳过）
RECENT_MAX = 10


def run(history, connection, account_set_key=None, posting_kind=None, *, fixed=None, trigger, route_device="",
        today=None) -> dict:
    """判这张票推往这份账套时的类别与这次怎么办（见文件头）。``trigger``：task / upload / line；``route_device``：定时任务
    读文件夹的那台电脑（能不能回写入核对按它判；其余按当前用户自己的电脑）。

    回 ``{"assessed", "class", "reasons", "items", "tier", "company", "action", "why", "account_set", "codes",
    "proposal", "automation", "needed"}``；``action`` 是 push / propose / stop（`push.automation.rules.action` 再加第 5 条
    的边界：测试站点路径、写的电脑太旧）。"""
    scope.require("intake")
    doc = storage.read(history)
    loaded = connections.read_for_push(connection, doc.workspace, doc.client, lock=True)
    try:
        if loaded.endpoint.get("adapter") != "express":
            return {"assessed": False}
        return _assess(doc, loaded, account_set_key, posting_kind, fixed, trigger, route_device,
                       today or datetime.now(BANGKOK).date())
    finally:
        frappe.db.rollback()  # 只是看：记付款方式、读历史请求这些都不在这里落


def _assess(doc, loaded, account_set_key, posting_kind, fixed, trigger, route_device, today):
    found = service._express_context(doc, loaded, account_set_key)
    target, flat, fields, direction = found["target"], found["flat"], found["fields"], found["direction"]
    preparation, selected, account_row = found["preparation"], found["selected"], found["account_row"]
    config, invoice_date = selected["config"], found["invoice_date"]
    connection, account_set = loaded.doc.name, target.account_set
    catalog = _catalog(loaded)
    day = invoice_date.isoformat() if invoice_date else ""
    dated = {**fields, "date": day or fields.get("date")}
    blocked = service._document_block(doc) or ""
    conflict = account_match.conflict("express", account_row, direction=direction, fields=dated, catalog=catalog,
                                      work_branch=lambda: service._work_branch(connection, account_set))
    if original_history(doc) != doc.name:
        blocked = blocked or "replacement"  # 改单 / 回导的票推往原单：交给人
    fixed = service._fixed(doc.name, connection, account_set, fixed)
    # 商品单入账方式按公司选过的照用（同准备推送，`service._goods_posting`）
    posting_kind, posting_from = service._goods_posting(doc, connection, account_row, posting_kind, flat, direction,
                                                        config, today)
    remembered = choice_store.remembered(connection, account_set)
    company, _read = service._company_rules(connection, account_set, account_row)
    decision = service._decisions(connection, account_set, flat, fields, direction, posting_kind, config, preparation,
                                  fixed, siblings=lambda: service._same_company(loaded, account_set),
                                  account_row=account_row, since=getattr(doc, "creation", None))
    payload, ready, reason = None, False, ""
    if not decision["needed"] and not blocked and not conflict:
        config["choices"] = decision["applied"]
        service._apply_stock_group(config, loaded.doc, account_set)
        prepared = prepare_express(selected, found["history_value"], posting_kind=posting_kind,
            context=ExpressContext(enabled=True, today=today, own_tax_id=found["own_tax"],
                account_tax_id=service._account_tax(account_row), tenant_id=loaded.access.tenant_id,
                mappings=preparation["mappings"],
                supplier_profile=preparation["supplier_profiles"].get(
                    clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id"))),
                bank_index=preparation["bank_index"]))
        payload = prepared.payload or None
        ready = prepared.disposition == "ready"
        reason = "" if ready else (prepared.reason or "preflight")
    pages = found["history_value"].get("pages") or []
    page_texts = text_layer.pages(doc)
    tier = automation_service.tier(connection, account_set, account_row)
    facts = automation.Facts(
        direction=direction, fields=dated, decision=decision, payload=payload, config=config, today=today,
        ready=ready, blocked=blocked or conflict or reason, pages=pages, review_reasons=_list(doc.review_reasons),
        text=text_layer.text(doc, page_texts), page_texts=page_texts, account=account_row, catalog=catalog,
        day_books=automation_service.day_books(connection, account_row, day, catalog), uploaded=_day(doc.creation),
        history_read=_read_day((decision.get("history") or {}).get("read")),
        source_total=int(doc.source_total or 1), prior_pushes=_prior(connection, account_set, doc, direction),
        posting_from=posting_from, account_recent=_recent_accounts(connection, account_set, doc, direction, dated),
        layers=automation.layers(dated, direction=direction, fixed=fixed, remembered=remembered,
                                 rules=(company or {}).get("rules") or (), branch=(company or {}).get("branch") or "",
                                 history=(decision.get("history") or {}).get("account")))
    verdict = automation.classify(facts)
    action, why = rules.action(verdict.cls, tier["tier"], trigger,
                               readback=rules.readback_ok(tier["readback"], day), baseline=tier["baseline"])
    if action == rules.PUSH and not automation_service.test_site_ok(account_set):
        action, why = rules.PROPOSE, "test_path"
    if action == rules.PUSH and not automation_service.verify_capable(
            connection, account_set, actor=frappe.session.user, route_device=route_device):
        action, why = rules.PROPOSE, "verify_version"
    codes = automation.reason_codes(automation.Verdict(verdict.cls, (*verdict.reasons, *([why] if why else ())),
                                                       verdict.items))
    at = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    return {"assessed": True, "class": verdict.cls, "reasons": list(verdict.reasons), "items": dict(verdict.items),
            "tier": tier["tier"], "company": tier["company"], "action": action, "why": why,
            "account_set": account_set, "codes": codes, "checked_at": at, "needed": decision["needed"],
            "proposal": rules.proposal(verdict.cls, tier["tier"], trigger, payload, dated, at=at.isoformat(),
                                       items=verdict.items) if verdict.cls in ("A", "B") else None,
            "automation": {"class": verdict.cls, "tier": tier["tier"], "trigger": trigger,
                           "reasons": list(verdict.reasons)}}


def _catalog(loaded):
    rows = (loaded.evidence.snapshot or {}).get("account_sets") or []
    if isinstance(rows, str):
        rows = json.loads(rows)
    return [row for row in rows if isinstance(row, dict)]


def _list(value):
    try:
        found = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return list(found) if isinstance(found, (list, tuple)) else []


def _day(value):
    if not value:
        return None
    return value.date() if isinstance(value, datetime) else frappe.utils.getdate(value)


def _read_day(value):
    """Express 历史读的日子（`readback.history_rules.stamp`）；没有、认不出回 None（各层说法不一不比 Express 历史）。"""
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _prior(connection, account_set, doc, direction):
    """这份账套里单号与这张票相同、已推成功或还在途中（准备好 / 排队 / 在写 / 结果不明）的单（`automation.prior_entry` 的形状），
    自动推专用的重复分流用（第 2 条）。作废的不算；还没有 Express 单号的也算：同一轮扫描里先判的那张刚排进队列，后一张不能
    再推（审查处理 10-16）。"""
    number = str(doc.invoice_no or "").strip()
    if not number:
        return []
    rows = frappe.db.sql("""SELECT payload_json FROM `tabCowork Push Record` WHERE connection=%s AND account_key=%s
        AND status IN %s AND invoice_no=%s AND history != %s ORDER BY creation DESC LIMIT %s""",
                         (connection, account_set, PRIOR_STATUSES, number, doc.name, PRIOR_MAX), as_dict=True)
    out = []
    for row in rows:
        item = automation.prior_entry(service._loads(row.payload_json))
        if item and item["direction"] == direction:
            out.append(item)
    return out


def _recent_accounts(connection, account_set, doc, direction, fields):
    """这家供应商在这份账套里最近几张推成功的单记的科目（新的在前，`automation.MEMORY_STREAK` 张为止）：账套记忆定的科目要
    最近连续这么多张同科目才算规则定（`automation.memory_settled`，第一轮回放后开发窗口定）。只看进项、只认 13 位税号
    （按名字作键的来源本来最高算 B）；取票按「本工作区 + 卖方税号」的索引（`workspace_seller_tax_index`），推送记录取它冻结的载荷。"""
    if direction != "purchase":
        return None
    raw = str(fields.get("seller_tax") or fields.get("seller_tax_id") or "").strip()
    tax = clean_tax_id(raw)
    if len(tax) != 13:
        return []
    rows = frappe.db.sql("""SELECT r.payload_json FROM `tabCowork OCR History` h
        INNER JOIN `tabCowork Push Record` r ON r.name = h.last_push_record
        WHERE h.workspace=%s AND h.seller_tax IN %s AND h.name != %s AND h.last_push_status='success'
          AND r.connection=%s AND r.account_key=%s AND r.status='success'
        ORDER BY h.creation DESC LIMIT %s""",
                         (doc.workspace, tuple({raw, tax}), doc.name, connection, account_set, RECENT_MAX), as_dict=True)
    out = []
    for row in rows:
        payload = service._loads(row.payload_json)
        if not isinstance(payload, dict) or payload.get("direction") != "purchase":
            continue
        supplier = payload.get("supplier") if isinstance(payload.get("supplier"), dict) else {}
        if clean_tax_id(supplier.get("tax_id")) not in ("", tax):
            continue
        out.append(str(payload.get("items_account") or "").strip())
        if len(out) >= automation.MEMORY_STREAK:
            break
    return out


__all__ = ["run"]
