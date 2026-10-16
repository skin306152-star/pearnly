"""识别完的文件夹票自动推送：以规则负责人的身份，做与网页「提交」完全相同的事，只调用现有入口、不改推送规则。

网页「提交」的顺序（`public/desk/intake-upload.js` 的 `submit`）：先看这张票在这份账套里还缺哪项要人选
（`choices_view`），缺就弹框问、关掉弹框就不推、不留推送记录；都有了再「准备」（`submit`）并「确认发送」
（Express 的确认入口，`companion/submission.py`）。这里照同一顺序：

- 规则设了「这一类都这样处理」的项，推这条规则的票时直接当这张票的选择用（`fixed`），优先于账套里记过的，
  **不写进账套记忆**（用户 2026-10-08：规则的默认值只管自己的票，不影响网页推送和同账套的其他规则）；
- 仍缺的（典型是同税号有几个档）只在票上记原因（`push_state.record_failure`，网页列表照常显示「失败 + 选择」按钮），
  不建推送记录；
- 人已经动过的票（改过、推过）不碰，交给人；
- 确认发送失败就用现有的撤回入口把自己那条「已准备」撤掉，不把它留成卡住别人的记录；
- Express 池里有能写的电脑就确认、排队，电脑此刻关着也排，开机后写（ADR-021 第 4 批，D4，`destinations.queueable`）；
  池里一台能写的电脑都没有才算「小助手没连上」：不准备、回 ``retry`` 让调用方过一会儿再推；准备那一刻刚好没有了的，撤掉这次
  准备再重来，重试用新的请求号（旧号会重放已撤回的那条，重放出撤回的也按重试处理）。池里没有电脑够得到这份账套，照现有规则
  记失败；``final`` 时一律照现有规则走。
- 要选的科目还没有可选值（这份账套的科目表还没读到，推送服务已请电脑读，`choices_view` 的 ``wait``，2026-10-13）：
  不记成「要人选」，回 ``retry`` 一分钟后再推；电脑要升级 / 读不到这份账套、或最后一次仍没读到，原因记在票上。

人在网页复核页、手机或 CC 里重推停下的文件夹票，由推送服务经 `cowork_push_fixed_choices` 钩子（`fixed_for`）
取同一份规则默认值，与这里同一口径（2026-10-12）。

跟随公司当年账套（T1，2026-10-15，`follow`）：任务记了公司主档的，每张票按单据日期选这次推往的账套，选到的抄到票上
（`push_state.retarget`）、写进这张票的去向（``account_set`` / ``account_label``）；取不到、多于一份、够不到就停下等人
（``held`` + 原因码，原因也记在票上）。跟到的账套不是任务存下的那份时，默认科目 / 部门只在那份账套的主档里有才用。
任务没记公司（旧任务对不上公司、不是 Express）或主档已删：照旧推往任务的固定账套。
"""
import hashlib
import json

import frappe

from pearnly_cowork.folder_intake import contracts, destinations, follow
from pearnly_cowork.history import push_state
from pearnly_cowork.push.application import service as application

HISTORY = "Cowork OCR History"
CONNECTION = "Cowork ERP Connection"
FILE = "Cowork Folder Intake File"
RULE = "Cowork Folder Intake Rule"


OFFLINE = "cowork.folder.companion_offline"
READING = "cowork.folder.reading_masters"
# 等电脑读这份账套的科目表：一般十几秒就读完，每分钟再看一次（次数与离线重试共用 `pump.OFFLINE_RETRIES`）
READING_SECONDS = 60


def push_key(entry, history, version, attempt=0):
    seed = f"{entry}\0{history}\0{version}" + (f"\0{attempt}" if attempt else "")
    return "folder:" + hashlib.sha256(seed.encode()).hexdigest()[:56]


def rule_fixed(defaults_json):
    """规则里定的「这一类都这样处理」→ 推送用的 ``fixed``（``{kind: value}``；「停下选」与没设的不算）。"""
    defaults = json.loads(defaults_json or "{}")
    return {kind: value for kind in contracts.KINDS if (value := contracts.default_value(defaults, kind))}


def source_rules(history):
    """这张票出自哪几条定时任务（按原件的文件行，回导工作簿生成的新票查不到）：任务的 ``connection`` / ``account_set_key`` /
    ``defaults_json`` / ``company`` / ``company_branch``，任务已删的不在里面。只读。待处理页给停在「待选账套」的票列可换的
    账套时按它找回任务的公司与分店（第 6 批审查第 4 条），与 :func:`fixed_for` 同一条路。"""
    source = frappe.db.get_value(HISTORY, history, "source_file")
    if not source:
        return []
    found = []
    for row in frappe.get_all(FILE, filters={"source_file": source}, fields=["rule", "histories_json"],
                              ignore_permissions=True):
        if history not in json.loads(row.histories_json or "[]"):
            continue
        rule = frappe.db.get_value(RULE, row.rule, ["name", "connection", "account_set_key", "defaults_json", "company",
                                                    "company_branch"], as_dict=True)
        if rule:
            found.append(rule)
    return found


def fixed_for(history, connection, account_set):
    """推送服务的钩子（``cowork_push_fixed_choices``）：这张票来自某个定时任务、且这次推往该任务的连接与账套时，
    回该任务的默认处理，否则 ``{}``。调用方已核过这张票的读取范围。

    任务停用仍套用（这批票就是按该任务口径识别的）；任务删除后不套用（删任务连文件进度一起删）；推往别的连接或账套
    不套用（科目、部门代码按账套有效）——跟随公司当年账套的任务（T1）推往这家公司（这个分店）别的年度账套也套用，
    科目、部门代码只在那份账套的主档里有才用（`follow.fixed_in`）；回导工作簿生成的新票原件是工作簿，查不到任务，不套用。
    每次推送都会经过这里：取不到一律回 ``{}``、不抛错，推送照常问人。"""
    from pearnly_cowork.targets.identity import normalize_express_account_key

    try:
        if not connection:
            return {}
        key = normalize_express_account_key(account_set)
        for rule in source_rules(history):
            if rule.connection != connection:
                continue
            if normalize_express_account_key(rule.account_set_key) == key:
                return rule_fixed(rule.defaults_json)
            if follow.member(rule, key):  # 跟随公司当年账套的任务：这家公司（这个分店）的别的年度账套
                return follow.fixed_in(rule, key, rule_fixed(rule.defaults_json))
    except Exception:
        frappe.log_error(title="Cowork folder task defaults lookup failed", message=frappe.get_traceback()[-1500:])
    return {}


def push_one(entry, rule, history, attempt=0, final=True):
    """推一张票，回这张票的去向：``sent`` / ``held`` / ``failed`` / ``human`` / ``missing``，
    非 ``final`` 时还可能回 ``retry``（小助手没连上）。调用方已切成负责人身份。"""
    row = frappe.db.get_value(HISTORY, history, ["edit_count", "last_push_status", "last_push_record",
                                                 "record_version"], as_dict=True)
    if not row:
        return {"status": "missing"}
    if int(row.edit_count or 0) or row.last_push_status or row.last_push_record:
        return {"status": "human"}
    frappe.db.commit()  # choices_view 只看不记，结束时整笔回滚：先把前面的落掉
    # 规则里定的「这一类都这样处理」只管这条规则自己的票：推送时当作这张票的选择用，优先于账套里记过的，
    # 不写进账套记忆（用户 2026-10-08：以前记成整份账套的选择，网页推送和同账套的其他规则都会跟着变）。
    # 显式传（没有默认值时是 {}）：推送服务就不再按钩子查一遍。
    fixed = rule_fixed(rule.defaults_json)
    picked = follow.choose(rule, history)
    if not picked:
        return _push_to(entry, rule, history, row, rule.account_set_key, fixed, attempt, final)
    where = {"account_set": picked["key"], "account_label": picked["label"]} if picked.get("key") else {}
    if picked.get("stop"):
        adapter = frappe.db.get_value(CONNECTION, rule.connection, "adapter")
        if picked["stop"] == follow.UNREACHABLE and not final and not destinations.queueable(adapter, rule.connection):
            return {"status": "retry", "reason": OFFLINE}  # 池里一台能写的电脑都没有：同「电脑没连上」，过一会儿再推
        going = {"connection": rule.connection, "account_set": picked["key"]} if picked.get("key") else {}
        push_state.record_failure(history, picked["stop"], **going)
        frappe.db.commit()
        return {"status": "held", "reason": picked["stop"], **where}
    fixed = follow.fixed_in(rule, picked["key"], fixed)
    push_state.retarget(history, rule.connection, picked["key"])
    frappe.db.commit()
    result = _push_to(entry, rule, history, row, picked["key"], fixed, attempt, final)
    return result if result.get("status") == "retry" else {**result, **where}


def _push_to(entry, rule, history, row, account_set, fixed, attempt, final):
    """把这张票推往 ``account_set``（任务的固定账套，或跟随公司选到的那份）。"""
    # POS 导出建的票按票上自己的口径（带商品明细的天是存货，其余按金额记、不动库存）
    posting_kind = None if contracts.is_table(entry.filename) else (rule.posting_kind or None)
    try:
        view = application.choices_view(history, rule.connection, account_set, posting_kind, fixed=fixed)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        # 与网页「提交」失败同一做法：原因记在票上（列表照常显示「失败」+ 原因），这份文件照样往下走、出表格。
        reason = str(error)[:300]
        application.submission_failed(history, reason)
        frappe.db.commit()
        return {"status": "failed", "reason": reason}
    remaining = view.get("needed") or []
    wait = view.get("wait")
    if wait and wait.get("state") in ("reading", "offline") and not final:
        # 这份账套的科目表还没读到（已请电脑读）：过一会儿再推，不记成「要人选」却没有可选的科目
        return {"status": "retry", "reason": READING if wait["state"] == "reading" else OFFLINE,
                "seconds": READING_SECONDS if wait["state"] == "reading" else None}
    # 停下时把这次要推往的连接与账套抄到票上，待处理页才能当场选（验收 F17）
    going = {"connection": rule.connection, "account_set": view.get("account_set") or account_set}
    if wait:
        # 电脑上的 CC 桌面端要升级 / 读不到这份账套，或一直没读到：原因记在票上（列表显示「失败」+ 原因），人处理后重推
        push_state.record_failure(history, wait["message"], **going)
        frappe.db.commit()
        return {"status": "failed", "reason": wait["message"][:300]}
    if remaining:
        reason = "choice_needed:" + ",".join(dict.fromkeys(item["kind"] for item in remaining))
        push_state.record_failure(history, reason, **going)
        frappe.db.commit()
        return {"status": "held", "reason": reason}
    adapter = frappe.db.get_value(CONNECTION, rule.connection, "adapter")
    if not final and not destinations.queueable(adapter, rule.connection):
        return {"status": "retry", "reason": OFFLINE}
    revision = frappe.db.get_value(CONNECTION, rule.connection, "record_revision")
    key = push_key(entry.name, history, row.record_version, attempt)
    result = application.submit(history, rule.connection, key, row.record_version, revision,
                                account_set, None, None, posting_kind, fixed=fixed)
    frappe.db.commit()
    if result.get("status") == "failed":
        return {"status": "failed", "reason": str(result.get("reason") or "")[:300]}
    if result.get("status") == "cancelled":
        # 同一请求号重放出之前撤回的那条（上一轮小助手没连上时撤的）：换新号再来，不当成已推。
        if not final:
            return {"status": "retry", "reason": OFFLINE}
        return {"status": "failed", "reason": str(result.get("reason") or "push.preparation_cancelled")[:300]}
    if result.get("status") == "prepared":
        if (not final and not destinations.ready(adapter, result)
                and not destinations.queueable(adapter, rule.connection)):
            _withdraw(result)
            return {"status": "retry", "reason": OFFLINE}
        try:
            destinations.of(adapter).confirm(rule.connection, result, "confirm:" + key)
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            _withdraw(result)
            application.submission_failed(history, str(error))
            frappe.db.commit()
            return {"status": "failed", "reason": str(error)[:300]}
    return {"status": "sent", "record": result.get("name") or ""}


def _withdraw(prepared):
    try:
        application.cancel_prepared(prepared["name"], prepared["revision"])
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
