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
from pearnly_core.express_rules.contracts.push import clean_tax_id
from pearnly_cowork.history import storage, text_layer
from pearnly_cowork.push import choices as choice_store
from pearnly_cowork.push.application import service
from pearnly_cowork.push.application.provenance import original_history
from pearnly_cowork.push.automation import clock, rules
from pearnly_cowork.push.automation import service as automation_service
from pearnly_cowork.push.express import automation
from pearnly_core.express_rules.push import choices as choice_rules
from pearnly_core.express_rules.push.preflight import ExpressContext
from pearnly_core.express_rules.push.prepare import prepare_express
from pearnly_core.express_rules.targets import account_match

BANGKOK = ZoneInfo("Asia/Bangkok")
PRIOR_MAX = 50
# 重复分流算进去的推送记录状态：除作废（cancelled）以外都算（manual 是结果不明或推送失败待人看，宁严）
PRIOR_STATUSES = ("prepared", "pending", "leased", "manual", "success")
# 账套记忆的证据窗口（`automation.memory_window`，10-17 改取法）：先跳过凭记忆推出去、还没合格回读核过的推送，再在剩下能当证据的
# 推送里取最近的。按上传先后分批往前取这家供应商推成功的单：第一批 `RECENT_FIRST` 张（多数供应商一批就凑够），之后每批
# `RECENT_PAGE` 张，记忆定的每一类都凑够 `automation.MEMORY_STREAK` 张证据、或取完、或合计到 `automation.RECENT_SCAN_MAX` 张、或
# 碰到第 `automation.RECENT_PERIOD_MAX` + 1 个不同的税期（单据日期，`automation.window_take`）就停。最坏一张单：
# - 取票：查询 1 + ceil((1000 − 30) / 200) = 6 次、读 1,000 行、解析 2,000 段 JSON（载荷与回执各一），回读标记钩子调 6 次；
# - 回读：每个税期一次 `readback.service.month_changes`（同一请求 / 后台任务里按期缓存），至多 `RECENT_PERIOD_MAX` = 6 次，每次
#   最多读 `MONTH_SCAN_MAX` = 500 行回读、只解析这份账套那一行的 2 段 JSON（摘要与差异表），合计至多 3,000 行、12 段 JSON。
# 原先只取最新 30 张再跳过，高频供应商两次回读之间推了 30 张以上就攒不出证据（第五轮回放）；10-17 审查补上回读一侧的期数上限。
# 10-18 E1：科目由账套记忆定、连续张数又够了的，要算证据窗口里回读改动的比例（R2，`automation.change_share`），取满整个窗口才停
# （上界不变，只是这类单常走到上界）；人改值的记录（R1，`push.choices.overrides`）另查一次、≤ 200 行，只在账套记忆定的科目 / 现 / 赊
# 连续张数够了时才查。所以一张单最坏：取票 6 次、回读 6 次、改值 1 次查询。
RECENT_FIRST = 30
RECENT_PAGE = 200
_RECENT_SQL = """SELECT h.name AS history_name, h.creation AS uploaded, r.name, r.payload_json, r.response_json,
        r.completed_at, h.invoice_date
    FROM `tabCowork OCR History` h INNER JOIN `tabCowork Push Record` r ON r.name = h.last_push_record
    WHERE h.workspace=%s AND h.seller_tax IN %s AND h.name != %s AND h.last_push_status='success'
      AND r.connection=%s AND r.account_key=%s AND r.status='success'{after}
    ORDER BY h.creation DESC, h.name DESC LIMIT %s"""
# 往前接着取：比上一批最后一张更早上传的（同一时刻按票名，不重不漏）
_RECENT_AFTER = " AND (h.creation < %s OR (h.creation = %s AND h.name < %s))"
# 账套记忆定了哪一类才取它的连续张数：科目（载荷 ``account_source``）、现 / 赊（``doctype_src``）、对方档（``decided_by``）
RECENT_KINDS = ("account", "payment", "party")


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
        return _assess(doc, loaded, account_set_key, posting_kind, fixed, trigger, route_device, today or clock.today())
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
    remembered = choice_store.remembered(connection, account_set)
    company, _read = service._company_rules(connection, account_set, account_row)
    # 商品单入账方式按公司选过的照用（同准备推送，`service._posting_decision`）
    posting_kind, posting_from, decision = service._posting_decision(
        doc, connection, account_row, posting_kind, flat, direction, config, today,
        lambda kind: service._decisions(connection, account_set, flat, fields, direction, kind, config, preparation,
                                        fixed, siblings=lambda: service._same_company(loaded, account_set),
                                        account_row=account_row, since=getattr(doc, "creation", None)))
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
    recent = _recent(connection, account_set, doc, direction, dated, payload, decision)
    overrides, overrule = _overrides(connection, account_set, payload, recent)
    # 按公司选的入账方式：这家供应商的 Express 历史定下的（只读存着的，用户 10-17），定档用（`automation.posting_by_history`）
    posting_history = service._posting_history(connection, account_set, dated, direction, posting_from)
    # 测试租户配了模拟日子（`clock`，第 17 条试点）：上传日当作那一天；R1 与 Express 历史新鲜度比的是库里的真实时刻，另给真实的今天
    simulated = clock.simulated()
    facts = automation.Facts(
        direction=direction, fields=dated, decision=decision, payload=payload, config=config, today=today,
        ready=ready, blocked=blocked or conflict or reason, pages=pages, review_reasons=_list(doc.review_reasons),
        text=text_layer.text(doc, page_texts), page_texts=page_texts, account=account_row, catalog=catalog,
        day_books=automation_service.day_books(connection, account_row, day, catalog),
        uploaded=simulated or _day(doc.creation), now=datetime.now(BANGKOK).date() if simulated else None,
        history_read=_read_day((decision.get("history") or {}).get("read")),
        source_total=int(doc.source_total or 1), prior_pushes=_prior(connection, account_set, doc, direction),
        posting_from=posting_from, posting_kind=posting_kind or "", posting_history=posting_history,
        account_recent=recent["account"], payment_recent=recent["payment"],
        party_recent=recent["party"], overrides=overrides, memory_since=recent["since"],
        account_share=recent["account_share"],
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
    judged = {"class": verdict.cls, "tier": tier["tier"], "trigger": trigger, "reasons": list(verdict.reasons)}
    # 判成 A 的账套记忆科目 / 现 / 赊（R1）：冻结记忆的值与证据时刻，领单时再看同账套有没有人刚改掉同一个值（`rules.frozen`）
    kept = {kind: item for kind, item in overrule.items() if verdict.items.get(kind) == "A"}
    if kept:
        judged["overrule"] = kept
    return {"assessed": True, "class": verdict.cls, "reasons": list(verdict.reasons), "items": dict(verdict.items),
            "tier": tier["tier"], "company": tier["company"], "action": action, "why": why,
            "account_set": account_set, "codes": codes, "checked_at": at, "needed": decision["needed"],
            "proposal": rules.proposal(verdict.cls, tier["tier"], trigger, payload, dated, at=at.isoformat(),
                                       items=verdict.items) if verdict.cls in ("A", "B") else None,
            "automation": judged}


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


def _recent(connection, account_set, doc, direction, fields, payload, decision):
    """这家供应商在这份账套里推成功的单（新的在前）里有证据的科目、现 / 赊、对方档（`automation.memory_window`）→
    ``{"account", "payment", "party", "since", "account_share"}``，前三项是序列或 None（没取）。账套记忆定的这几类都要最近连续
    `automation.streak_for` 张（科目 5、现 / 赊与对方档 3，R3）有证据的同值才算规则定（`automation.memory_settled`）；只取这张单真由
    账套记忆定的类（科目看载荷 ``account_source``、现 / 赊看 ``doctype_src``、对方档看 ``decided_by``），都不是、或没出载荷（本来就是
    C）就不查库。``since``：科目、现 / 赊最近 `automation.OVERRIDE_EVIDENCE` 张证据里最早那张的上传时刻（R1，`automation.memory_since`）；
    ``account_share``：科目由账套记忆定时，证据窗口里有合格回读结果的推送中科目被改的 ``(张数, 有结果的张数)``（R2，
    `automation.change_share`），没取为 None。

    证据：推的时候由人或规则定（科目：这张单上传后有人选的、任务默认、公司规则、Express 历史已定；现 / 赊：本票人工裁决、任务默认、
    公司规则；对方档只认回读），或被合格回读覆盖、整张没被改（经钩子 ``cowork_push_readback_marks``，三类共用）；凭记忆
    推出去还没回读核过的不算，回读里被改的三类一起断开（审查处理 10-16：CC 自己推出去的单不能攒成「连续 3 张」；10-17 推广到现 / 赊
    与对方档）。审查处理 10-17：票面写明 / 票种口径定现 / 赊的单不算现 / 赊的证据（`automation.PAYMENT_OTHER_CLASS`）；对方档取
    CC 桌面版实际写入的码（回执 ``meta.party_code``，回读比的也是它），载荷写定了码、桌面版却写了别的按断开算；真写了库存行的单
    （回执 ``line_modes``）回读不比分录，科目一类只认推的时候的来源。对方档只数同一 13 位税号 + 同一分店的（同记忆键
    `choices.party_subject` 的分店口径）。只看进项、只认 13 位税号（按名字作键的来源本来最高算 B）；销项不查（买方税号没有索引），
    记忆定的按 0 张算。

    窗口（10-17 改取法）：先跳过凭记忆推出去、还没合格回读核过的，再在剩下能当证据的里取最近的——取票按「本工作区 + 卖方税号」的
    索引（`workspace_seller_tax_index`）按上传先后分批往前取（`RECENT_FIRST`、之后每批 `RECENT_PAGE`），推送记录取它冻结的载荷与
    回执，每批先按税期截（`automation.window_take`：碰到第 `automation.RECENT_PERIOD_MAX` + 1 个不同的税期就截掉、不再往前取，
    每个税期要另查一次回读），再标回读（带上改了哪几类）；记忆定的每一类都凑够这一类的张数就停——只有科目连续张数够了时要取满窗口
    算 R2 的比例；取完、合计到 `automation.RECENT_SCAN_MAX` 张或碰到税期上限也停（硬上限，证据真不够的照样 B）。最坏查询、行数与
    JSON 段数见文件头 `RECENT_FIRST` 上的注释。"""
    payload = payload if isinstance(payload, dict) else {}
    decided = (decision or {}).get("decided_by") or {}
    wanted = {"account": payload.get("account_source") == "remembered",
              "payment": payload.get("doctype_src") == "remembered",
              "party": decided.get("party") == "remembered"}
    out = {**dict.fromkeys(RECENT_KINDS), "since": {}, "account_share": None}
    if direction != "purchase" or not payload or not any(wanted.values()):
        return out
    raw = str(fields.get("seller_tax") or fields.get("seller_tax_id") or "").strip()
    tax = clean_tax_id(raw)
    if len(tax) != 13:
        return {**{kind: [] if wanted[kind] else None for kind in RECENT_KINDS}, "since": {}, "account_share": None}
    branch = choice_rules.branch5(fields.get("seller_branch"))
    account = str(payload.get("items_account") or "").strip()
    taxes, entries, found, periods = tuple({raw, tax}), [], {}, set()
    after, scanned, size = None, 0, RECENT_FIRST

    def enough(kind):
        if not wanted[kind]:
            return True
        if len(found[kind]) < automation.streak_for(kind):
            return False
        # R2：科目连续张数够了，比例要看整个窗口（不够的反正是 B，不再往前取）
        return not (kind == "account" and automation.memory_settled(found[kind], account, "account"))

    while True:
        size = min(size, automation.RECENT_SCAN_MAX - scanned)
        if size <= 0:
            break
        rows = _recent_rows(doc, taxes, connection, account_set, after, size)
        scanned += len(rows)
        batch = [entry for entry in (_recent_entry(row, tax) for row in rows) if entry]
        keep = automation.window_take(batch, periods)
        capped, batch = keep < len(batch), batch[:keep]
        if batch:
            marks = automation_service.readback_marks(connection, account_set, [
                {key: entry[key] for key in ("record", "invoice_date", "completed_at")} for entry in batch], detail=True)
            for entry in batch:
                mark = marks.get(entry["record"])
                if isinstance(mark, dict):
                    entry["readback"], entry["changed_kinds"] = mark.get("readback") or "", mark.get("changed")
                else:  # 只给整张结果的提供者：改了哪几类按不知道（被改的三类都算）
                    entry["readback"], entry["changed_kinds"] = mark or "", None if mark else []
            entries.extend(batch)
        found = {"account": automation.memory_window(entries), "payment": automation.memory_window(entries, "payment"),
                 "party": automation.memory_window(entries, "party", branch=branch)}
        if capped or len(rows) < size or all(enough(kind) for kind in RECENT_KINDS):
            break
        after, size = rows[-1], RECENT_PAGE
    result = {kind: found.get(kind, []) if wanted[kind] else None for kind in RECENT_KINDS}
    result["since"] = {kind: automation.memory_since(entries, kind) for kind in automation.OVERRIDE_KINDS if wanted[kind]}
    result["account_share"] = automation.change_share(entries, "account") if wanted["account"] else None
    return result


def _overrides(connection, account_set, payload, recent):
    """R1 的取数（10-18）：账套记忆定的科目 / 现 / 赊里连续张数够了的（不够的本来就是 B，不查），取这份账套近
    `automation.OVERRIDE_DAYS` 天里有人把同一个记忆值改掉的记录（`push.choices.overrides`，一次查询）→ ``(记录, overrule)``；
    ``overrule`` ``{类: {"value": 记忆的值, "since": 证据时刻 ISO}}`` 给判成 A 的那几类冻结进自动推、领单时重判。"""
    payload = payload if isinstance(payload, dict) else {}
    values, overrule = {}, {}
    for kind in automation.OVERRIDE_KINDS:
        sequence = recent.get(kind)
        value = automation.memory_value(kind, payload)
        shown = str(payload.get("items_account" if kind == "account" else "doctype") or "").strip()
        since = (recent.get("since") or {}).get(kind)
        if sequence is None or not value or since is None:
            continue
        if not automation.memory_settled(sequence, shown.upper() if kind == "payment" else shown, kind):
            continue
        values[kind] = value
        overrule[kind] = {"value": value, "since": since.isoformat() if hasattr(since, "isoformat") else str(since)}
    if not values:
        return [], {}
    return choice_store.overrides(connection, account_set, values), overrule


def _recent_rows(doc, taxes, connection, account_set, after, size):
    """`_recent` 的一批：比 ``after``（上一批最后一行，None 从最新的起）更早上传的推成功的单，最多 ``size`` 张。"""
    values = [doc.workspace, taxes, doc.name, connection, account_set]
    if after is not None:
        values += [after.uploaded, after.uploaded, after.history_name]
    return frappe.db.sql(_RECENT_SQL.format(after=_RECENT_AFTER if after is not None else ""),
                         (*values, size), as_dict=True)


def _recent_entry(row, tax):
    """一张推成功的单 → `automation.memory_evidence` 的一条（不是进项、载荷里别家的税号回 None）。"""
    pushed = service._loads(row.payload_json)
    if not isinstance(pushed, dict) or pushed.get("direction") != "purchase":
        return None
    supplier = pushed.get("supplier") if isinstance(pushed.get("supplier"), dict) else {}
    if clean_tax_id(supplier.get("tax_id")) not in ("", tax):
        return None
    response = getattr(row, "response_json", None)
    response = service._loads(response) if response else {}
    response = response if isinstance(response, dict) else {}
    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    planned = str(supplier.get("code") or "").strip()
    written = str(meta.get("party_code") or "").strip() or planned
    modes = response.get("line_modes") if isinstance(response.get("line_modes"), list) else []
    return {"record": row.name, "at": getattr(row, "uploaded", None), "account": str(pushed.get("items_account") or "").strip(),
            "source": str(pushed.get("account_source") or ""),
            "payment": str(pushed.get("doctype") or "").strip().upper(),
            "payment_source": str(pushed.get("doctype_src") or ""),
            "party": written, "party_changed": bool(planned and written != planned),
            "stock": any(isinstance(mode, dict) and str(mode.get("mode") or "").startswith("stock") for mode in modes),
            "branch": choice_rules.branch5(supplier.get("branch")),
            "invoice_date": str(row.invoice_date or "")[:10], "period": rules.period_of(str(row.invoice_date or "")[:10]),
            "completed_at": row.completed_at}


__all__ = ["run"]
