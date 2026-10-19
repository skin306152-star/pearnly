"""CC 网页「待处理」当场选（ADR-020，补充 乙）：记下会计员选的，再按 CC 推送同一条路重推这一张。

- 可选项由页面在展开那一行时取（`push.application.api.choices`，与复核页、CC 同一套判定），列表本身不读账套主档。
- 作答：`push.application.service.save_choices`（值须在这份账套主档里，定时任务默认处理定下的类别拒收），再
  `tools._push_one`（CC 的 `cowork_push_documents` 每一张走的那条：再看一次要选的项 → 提交 → Express 交 CC 桌面版写入）。
  只记选择不会自动重推：推送记录停在「要人选」，要再推一次才会进账；推出去后这张单的推送状态变了，「待处理」就不再列它。
- 要选的已由公司规则定下（第 2 期，展开时 ``needed`` 为空）：页面交空的 ``items``，这里不记、直接重推。
- 不等写账结论（CC 桌面版写入是几秒到几分钟的事，网页请求不占着等）；结论由推送状态的实时事件带回列表。
- 推送去向取这张单识别时选的连接与账套（`push_connection / push_account_set`）；权限：录入权限 + 这张单的读取范围 +
  这条连接的推送授权（都在被调的服务里判）。
- 停在「待选账套」的（定时任务跟随公司当年账套 T1：没有 / 多于一份 / 够不到单据日期的账套，ADR-021 第 6 批）：
  `account_sets` 列这张单归的公司在推送去向那条连接上覆盖单据日期的账套（与定时任务选账套同一判据
  `companies.tasks.books_of`，按出这张单的任务的公司与分店），`choose_account_set` 只收其中一份：按同一条路重推，推成了才抄到
  票上（`push_state.retarget`），推不成就放回「待选账套」能再选；推送照旧过 `account_match` 的期间、税号、分店硬闸与定时任务的
  默认处理（`push.fixed_for`）。自动推前判出多份同期账套的 C 类（``auto_reasons`` 有 ``book_many``）同样列在「待选账套」。
  候选是多份同期时，选的那份推成了（进了账本、或停在要人选的项）才同时记成这家公司这一组的现用账套（用户 2026-10-17「按公司
  问一次」，无确认步骤；推不成不记，同一组下一张照旧停下再问，第五轮后审查），之后同一组的单 T1 与自动推都照选；复核页与 CC
  工具 `cowork_choose_document_policy`（``current_book``）走的就是 `choose_account_set` 这一条。已经记住、不再停下的单要改选：
  CC 工具对推送去向那份账套这一天有多份同期的单只记新的那份（`rechoose_current_book`，不推）。两处记下时，同一组里还停在
  「多份账套」的别的单按记住的那份接着判（`_continue_group`，2026-10-19 验收「选定一次再复用」）：判到哪一步就停在哪一步
  （录不录、要人选的项），都定了的成提议或等人推，不替人推；回包带接着判了的张数 ``settled``，CC 工具另带这几张的单据名
  ``settled_documents``（非 0 时；页面的 `choose_account_set` 不给），工具据此列出同组每张停在哪一步交 CC 转告（10-19 验收）。
- CC 准备好的提议（数字会计员第 3 期）：点开那一行先取它的内容（`proposal_detail`，只读：推往的账套、每一项做法的值、日期、
  单号、对方、金额），下面「推送」走 `answer`（交空的选择），一张一张推（用户 2026-10-19 定：行上不露「推送」、不设「全部推送」，
  先看单据内容再推）。算人发起，照常重判。
- 定时任务的票补选后仍由读它所在文件夹的那台电脑写（`folder_intake.push.reader_of` → 推送记录的 ``route_device``，ADR-021
  第 51 条，10-16 审查）：点按钮的会计员只用网页或手机、或账套只在那台电脑上时照样推得进去。CC 对话里主动推送仍按发起人。
"""
import json

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.contracts import document_policy
from pearnly_cowork.history import push_state, storage
from pearnly_cowork.push.application import api as application_api

ITEMS_MAX = 50


@frappe.whitelist(methods=["POST"])
def answer(document, items):
    """记下这张单要人选的项（``items``：``[{kind, subject, value, label}]``，JSON）并重推；回
    ``{"document", "status", "reason"?}``：``sent`` 已交出去（Express 由 CC 桌面版写入）、``needs_choice`` 还有要选的、
    ``failed`` 推不成（原因记在单上）、``waiting_for_cc`` 科目表还在读等。"""
    scope.require("intake")
    name = str(document or "")
    doc = storage.read(name)
    if not doc.push_connection:
        frappe.throw(_("cowork.company.pending.no_target"))
    parsed = json.loads(items) if isinstance(items, str) else items
    if not isinstance(parsed, list) or len(parsed) > ITEMS_MAX:
        frappe.throw(_("connection.config_invalid"))
    if parsed:
        application_api.save_choices(name, doc.push_connection, doc.push_account_set or None,
                                     json.dumps(parsed, ensure_ascii=False))
        frappe.db.commit()
    from pearnly_cowork.assistant import tools  # CC 工具模块很大：只在真要推时加载

    result = tools._push_one(name, route_device=_reader(name, doc.push_connection))
    frappe.db.commit()
    return _answer(name, result)


def _answer(name, result):
    """回给页面的结论：``reason`` 是原因码（页面按它判断），``reason_label`` 是给人看的话（与列表 `reason_label` 同一套词条，
    第 7 批 B7：页面原先把 ``document_policy_needed_payment_evidence`` 这样的码原样标红显示）。"""
    from pearnly_cowork.push import export_facts

    out = {"document": name, "status": str(result.get("status") or "")}
    if result.get("reason"):
        out["reason"] = str(result["reason"])[:300]
        out["reason_label"] = str(export_facts.reason_text(out["reason"]) or "")[:300]
        if result.get("note"):
            # 停在等电脑读这份账套的科目表（`tools._push_one` 带 ``note`` 的那两种，原因是主档状态 reading / offline …）：
            # 写主档状态那一句（同展开时的 ``wait.message``），不让页面显示 reading 这样的码（10-19 验收）
            from pearnly_cowork.push.application import service as application

            state = out["reason"] if out["reason"] in application.MASTERS_TEXT else "reading"
            out["reason_label"] = str(_(application.MASTERS_TEXT[state]))[:300]
    return out


def _reader(document, connection):
    """定时任务的票补选后仍算定时任务的活：由读它所在文件夹的那台电脑写（`folder_intake.push.reader_of`；不是定时任务的票回空，
    按点按钮的人自己的电脑写）。ADR-021 第 51 条，10-16 审查。"""
    from pearnly_cowork.folder_intake import push as folder_push

    return folder_push.reader_of(document, connection)


def _row(doc):
    return doc if isinstance(doc, dict) else doc.as_dict()


@frappe.whitelist(methods=["GET"])
def account_sets(document):
    scope.require("intake")
    name = str(document or "")
    from pearnly_cowork.companies import pending

    # ``current``：这一组已经记住、此刻仍算数的现用账套，页面预选它（只预选已定的，验收 F18）
    return {"document": name, **pending.account_set_options(_row(storage.read(name)))}


@frappe.whitelist(methods=["GET"])
def proposal_detail(document):
    """点开 CC 准备好的提议那一行时显示的内容（只读，不写库）：推往的账套、每一项做法、日期、单号、对方、三项金额
    （`companies.pending.proposal_view`，照票上冻结的提议，推的时候照常重判）。权限同 `answer`：录入权限 + 这张单的读取
    范围；名称只从本人能用的连接上读，读不到的回码本身。"""
    scope.require("intake")
    name = str(document or "")
    from pearnly_cowork.companies import pending

    return pending.proposal_view(_row(storage.read(name)))


# ---------------- MR.ERP：停在要选导入菜单 / 默认值的单当场选（2026-10-19） ----------------

def _mrerp_target(name):
    """这张单推往的 MR.ERP 连接、账套与票面方向；不是这种单（`companies.pending.mrerp_setup_stop`）回 None。"""
    from pearnly_cowork.assistant import tools
    from pearnly_cowork.companies import pending

    doc = storage.read(name)
    row = _row(doc)
    if not pending.mrerp_setup_stop(row):
        return None
    return doc.push_connection, doc.push_account_set, tools._mrerp_direction(doc, doc.push_connection,
                                                                              doc.push_account_set)


@frappe.whitelist(methods=["GET"])
def mrerp_needs(document):
    """点开这张单时要选的（只有这张单的方向会用到、此刻还要人选的，同 CC 推之前那一步与网页推送前的弹窗）：
    ``{"document", "known", "items": [{"key", "label", "options": [{"value", "label"}]}]}``。``key`` 是 ``menu:单据类型`` /
    ``default:字段``；名称按用户语言（同网页弹窗的词条）。菜单与主档还没从 MR.ERP 读到过（``known`` 为假）时 ``items`` 为空，
    页面交给 CC（它能请服务器去读）。不是这种单回空的 ``items``。"""
    from pearnly_cowork.connections import mrerp_needs as needs
    from pearnly_cowork.connections import service as connections

    scope.require("intake")
    name = str(document or "")
    target = _mrerp_target(name)
    if not target:
        return {"document": name, "known": False, "items": []}
    connection, account, direction = target
    view = connections.mrerp_menu(connection, account)
    found = needs.needed(view, direction or None)
    items = [{"key": "menu:" + kind, "label": _(f"intake.mrerpMenu.{kind}"),
              "options": [{"value": option["idmenu"], "label": option.get("label") or option["idmenu"]}
                          for option in view["kinds"][kind]["options"]]} for kind in found["menus"]]
    items += [{"key": "default:" + field, "label": _(f"intake.mrerpDefault.{field}"),
               "options": [{"value": option["code"],
                            "label": " · ".join(part for part in (option["code"], option.get("name")) if part)}
                           for option in view["defaults"]["fields"][field]["options"]]} for field in found["defaults"]]
    return {"document": name, "known": found["known"], "items": items}


@frappe.whitelist(methods=["POST"])
def mrerp_answer(document, values):
    """记下这张单要选的 MR.ERP 导入菜单 / 默认值（``values``：``{key: 值}``，JSON，键同 `mrerp_needs`；值只收这份账套最近一次
    读到的可选值，连接服务核），再按 CC 推送同一条路重推这一张（同 `answer`）；回同 `answer`。给空的 ``values`` 是直接重推
    （别处已经选好了）。这份账套的选择大家共用，同网页弹窗。"""
    from pearnly_cowork.connections import service as connections

    scope.require("intake")
    name = str(document or "")
    target = _mrerp_target(name)
    if not target:
        frappe.throw(_("cowork.company.pending.no_target"))
    connection, account, _direction = target
    parsed = json.loads(values) if isinstance(values, str) else values
    if not isinstance(parsed, dict) or len(parsed) > ITEMS_MAX:
        frappe.throw(_("connection.config_invalid"))
    for key, value in parsed.items():
        part, _sep, field = str(key).partition(":")
        if part == "menu":
            connections.choose_mrerp_menu(connection, account, field, str(value or ""))
        elif part == "default":
            connections.choose_mrerp_default(connection, account, field, str(value or ""))
        else:
            frappe.throw(_("connection.config_invalid"))
    frappe.db.commit()
    from pearnly_cowork.assistant import tools

    result = tools._push_one(name, route_device=_reader(name, connection))
    frappe.db.commit()
    return _answer(name, result)


# 推不成、也没进账本的结论：这张单回到「待选账套」，会计员能再选一份（第 6 批审查第 4 条）
NOT_ENTERED = ("failed", "not_found")


@frappe.whitelist(methods=["POST"])
def choose_account_set(document, account_set_key):
    """换到会计员选的那份账套并重推这一张；回同 `answer`。只收 `account_sets` 列出的那几份；不在列（目录刚变、已不覆盖单据
    日期）回 ``status = "stale"``，页面重取可换的账套再让人选，不弹错误（第 6 批审查第 13 条）。

    先推、推成了（进了账本、或停在要人选科目等）才把去向抄到票上（`push_state.retarget`；停下时 `_push_one` 自己抄）。推不成
    也没进账本（`account_match` 硬闸拒了、科目表还没读到等）：票上的去向与「待选账套」原因原样放回，会计员还能再选一份，
    这次推不成的原因只回给页面显示在那一行下面（第 6 批审查第 4 条：以前先抄去向再推，失败后原因被换掉，再也选不了）。"""
    out = push_to_account_set(document, account_set_key)[0]
    out.pop("settled_documents", None)  # 只给 CC 工具（列同组每张），页面只用张数
    return out


def push_to_account_set(document, account_set_key, posting_kind=None) -> tuple[dict, dict]:
    """同 `choose_account_set`，另回 `tools._push_one` 的原样结果（CC 的 `cowork_push_documents` 推停在「待选账套」的单走这里，
    等写账结论要用里面的推送记录名，不交给页面）；不在列时第二项为空。同组接着判了的（``settled`` > 0）第一项另带它们的单据名
    ``settled_documents``。"""
    scope.require("intake")
    name = str(document or "")
    from pearnly_cowork.companies import pending

    doc = storage.read(name)
    picked = pending.account_set_pick(_row(doc), account_set_key)
    if not picked:
        return {"document": name, "status": "stale"}, {}
    # 两份同期时要记的现用账套：按停下时的候选先算好（推完这张单就不再停在「待选账套」），推成了才记
    current = pending.current_book_choice(_row(doc), picked["key"])
    stop = {"reason": doc.last_push_reason, "connection": doc.push_connection, "account_set": doc.push_account_set,
            "record": doc.last_push_record}
    from pearnly_cowork.assistant import tools  # CC 工具模块很大：只在真要推时加载

    result = tools._push_one(name, doc.push_connection, picked["key"],
                             **({"posting_kind": posting_kind} if posting_kind else {}),
                             route_device=_reader(name, doc.push_connection))
    frappe.db.commit()
    status = str(result.get("status") or "")
    reason = str(result.get("reason") or "")
    after = frappe.db.get_value("Cowork OCR History", name, ["last_push_record"], as_dict=True) or {}
    accepted = status not in NOT_ENTERED
    if status in NOT_ENTERED and reason.startswith(document_policy.NEEDED_PREFIX):
        # 账套收下了，挡住它的是「这类单据录不录」（租户选一次，与选哪份账套无关）：去向记成选的这份、原因记成要选录不录，
        # 这张单从「待选账套」转成一般的待处理（交给 CC、复核页选录不录）。放回「待选账套」的话换哪份都是同一个原因，
        # 会计员在这一行永远过不去（第 7 批 B7，2026-10-18 lab）。同「停在要人选的项」，现用账套照记（并入 main 时补）。
        push_state.record_failure(name, reason, connection=doc.push_connection, account_set=picked["key"])
        accepted = True
    elif status in NOT_ENTERED and (after.get("last_push_record") or None) == (stop["record"] or None):
        push_state.record_failure(name, stop["reason"], connection=stop["connection"], account_set=stop["account_set"])
    elif status not in NOT_ENTERED:
        push_state.retarget(name, doc.push_connection, picked["key"])
    if accepted and current:
        _remember(current)
    frappe.db.commit()
    out = _answer(name, result)
    if accepted and current:
        out["current_book"] = current["value"]
        out.update(_settled(_continue_group(current, name)))
    return out, result


def _settled(names) -> dict:
    """同组接着判了的单 → 回包里的张数 ``settled``，非 0 时另带单据名 ``settled_documents``（CC 工具列出来交 CC 转告）。"""
    return {"settled": len(names), **({"settled_documents": list(names)} if names else {})}


def _remember(found) -> None:
    from pearnly_cowork.history import document_policy as policy_store

    policy_store.record(found["workspace"], found["key"], found["value"], seen=found.get("seen") or "")


HISTORY = "Cowork OCR History"
# 记住现用账套时，同一组还停在「多份账套」的单一次最多接着判这么多张（每张一次只看不记的判定）；多的仍停着，展开时预选记住的那份
GROUP_MAX = 50
GROUP_SCAN = 500
GROUP_FIELDS = ["name", "invoice_date", "seller_name", "seller_tax", "buyer_name", "buyer_tax", "intake_target",
                "push_connection", "push_account_set", "last_push_status", "last_push_reason", "last_push_record",
                "auto_class", "auto_reasons", "source", "posting_kind", "workspace", "owner", "creation"]
LINE_SOURCE = "cowork_line"  # 同 `push.automation.sweep.LINE_SOURCE`


def _continue_group(found, chosen) -> list:
    """刚记住了这家公司这一组现用的账套（``found``：`companies.pending.current_book_choice` / `current_book_rechoice` 的结果，
    已提交）：这个工作空间里同一组（同公司、同分店、同一段、同一组候选，按各单自己的候选现算出同一个键）还停在「多份账套」的
    别的单（定时任务 T1 的「多于一份」、自动推前判出的 ``book_many``；``chosen`` 是刚选的那张，已推），按记住的那份接着判
    （2026-10-19 验收：选了 A，同组的 B 还挂着「多份账套」、展开后账套框是空的；口径同 `history.document_policy._settle_waiting`
    了结等着录不录的单）。只判不推：

    - 这类单据录不录没选（或选了不录入）→ 原因记成 ``document_policy_needed_*`` / ``skip_*``；
    - 还有要人选的项 → 原因记成 ``choice_needed:…``（待处理当场选）；
    - 都定了 → 原因清掉，判定（`application.assess`，与扫描、定时任务同一套）记在票上：A / B 冻结提议（待处理「推送」），
      C 成「没推」交人。档位允许自动推的也只出提议：这几张不替人推，推送照旧由人发起。

    推送去向都改成记住的那份。判的时候出错、另一份有了更新录入要重问（仍判出 ``book_many``）的不动，照旧停着。回接着判了的
    单据名（按单据日期先后）。"""
    from pearnly_cowork.companies import pending
    from pearnly_cowork.folder_intake.contracts import ACCOUNT_SET_AMBIGUOUS

    rows = frappe.get_all(HISTORY, filters={"workspace": found["workspace"], "push_connection": ["is", "set"],
                                            "invoice_date": ["is", "set"],
                                            "last_push_status": ["not in", list(pending.SETTLED)]},
                          or_filters=[["last_push_reason", "=", ACCOUNT_SET_AMBIGUOUS],
                                      ["auto_reasons", "like", f"%{pending.BOOK_MANY}%"]],
                          fields=GROUP_FIELDS, order_by="invoice_date asc", limit_page_length=GROUP_SCAN)
    settled = []
    for row in rows:
        if len(settled) >= GROUP_MAX:
            break
        if row.name == chosen or not (row.last_push_reason == ACCOUNT_SET_AMBIGUOUS or pending.book_many(row)):
            continue
        try:
            same = pending.current_book_choice(row, found["value"])
            if not same or same["key"] != found["key"]:
                continue  # 别家、别的分店、别的年段或别的候选组合：不是这一组
            if _continue_one(row, found["value"]):
                settled.append(row.name)
            frappe.db.commit()
        except Exception:  # noqa: BLE001 — 一张出意外只记日志、照旧停着，不挡刚选的那张与别的
            frappe.db.rollback()
            frappe.clear_messages()
            frappe.log_error(title="Cowork current account set: continuing a waiting document failed",
                             message=frappe.get_traceback()[-1500:])
    return settled


def _continue_one(row, account_set) -> bool:
    """按记住的那份 ``account_set`` 接着判一张（见 `_continue_group`）；接着判了回 True。"""
    from pearnly_cowork.contracts.history import primary_page_fields
    from pearnly_cowork.folder_intake import push as folder_push
    from pearnly_cowork.history import document_policy as policy_store
    from pearnly_cowork.push.application import service as application
    from pearnly_cowork.push.automation import rules as automation_rules

    name, connection = row.name, row.push_connection
    doc = storage.read(name)
    block = document_policy.push_block(primary_page_fields(json.loads(doc.pages or "[]")),
                                       policy_store.read(doc.workspace))
    if block:
        push_state.record_failure(name, block, connection=connection, account_set=account_set)
        return True
    if name in folder_push.task_histories([name]):
        trigger = automation_rules.TASK
    else:
        trigger = automation_rules.LINE if row.source == LINE_SOURCE else automation_rules.UPLOAD
    frappe.db.commit()  # 判定只看不记，结束整笔回滚：先把前面的落掉
    try:
        judged = application.assess(name, connection, account_set, row.posting_kind or None, trigger=trigger,
                                    route_device=_reader(name, connection))
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.db.rollback()
        frappe.clear_messages()
        return False
    if not judged.get("assessed") or _still_many(judged.get("codes")):
        return False
    needed = judged.get("needed") or []
    reason = "choice_needed:" + ",".join(dict.fromkeys(item["kind"] for item in needed)) if needed else ""
    proposal = judged.get("proposal") if not needed and judged.get("class") in ("A", "B") else None
    push_state.record_assessment(name, cls=judged.get("class"), tier=judged.get("tier"), reasons=judged.get("codes"),
                                 proposal=proposal, checked_at=judged.get("checked_at"))
    push_state.record_failure(name, reason, connection=connection, account_set=judged.get("account_set") or account_set)
    return True


def _still_many(codes) -> bool:
    """判出的原因码里仍有「按日期选出多份同期账套」：记住的那份此刻不算数（另一份有了更新录入要重问）。"""
    from pearnly_cowork.companies import pending

    return pending.BOOK_MANY in str(codes or "").split(",")


def rechoose_current_book(doc, account_set_key) -> dict | None:
    """已经记住、不再停下的单改选这家公司这一组现用的账套（CC 工具 ``current_book``）：推送去向那份账套这一天有多份同期、
    选的在这一组里（`companies.pending.current_book_rechoice`）才记，不推、不改这张单；同一组还停在「多份账套」的别的单
    按新记的那份接着判（`_continue_group`）。回 ``{"current_book": 规范账套键, "settled": 接着判了的张数,
    "settled_documents"?: 它们的单据名}``，否则 None。
    调用方已核录入权限、读到这张单；候选只取本人能用的连接上的账套。"""
    from pearnly_cowork.companies import pending

    found = pending.current_book_rechoice(_row(doc), account_set_key)
    if not found:
        return None
    _remember(found)
    frappe.db.commit()  # 接着判同组停着的单之前先落下记住的那份（判定只看不记、结束回滚）
    return {"current_book": found["value"], **_settled(_continue_group(found, _row(doc).get("name")))}
