"""Cowork 对账的引导式（CC 对话里点「对账」，总表 CW-D-BR / IR / VC）：声明 :data:`GUIDES` 与它点名的产品函数。

只转调网页对账中心同一组函数（`recon_center.jobs.start / status`、`recon_center.sheets.load`、`recon_center.presets.answer`、
各对账域 ``export``；CC 工具 `cowork_recon_*` 也调它们），不改对账、不另写规则。主会话 11-16 定：菜单一项「对账」+ 第一题选种类；
一侧可「从 Express 读回」，没有现成回读时出卡就请电脑读一次；网页「新建」页没有付费说明，这里也不加。

- 种类（只列这个人有权限的）→ 来源（本对话账套是 Express 才问「从 Express 读回 / 交文件」，否则只能交文件、不问）→
  读回：月份 → 这本账套这个月的回读（有现成的直接用、不问；没有就出卡时请读一次、页面过一会儿再取）；交文件：两侧各交（同网页两个上传口，
  只有银行账单那一侧沿用网页的提示）→ 确定 → 后台对账（等）→ 结果：汇总 + 要定一次的问题（答了按预设重算 / 用原文件重跑）+ 结果工作簿进对话。
- 来源的选项码带种类（``bank:upload``），后面各步按它用 ``when.in`` 判问不问（引擎的 ``when`` 一次只看一步）。
- 不写账本、不写 Express；扫描件 / 照片照网页按页付费识别。对账任务、预设不按公司隔开（用户 11-16）。
"""
from __future__ import annotations

import contextlib

import frappe
from frappe import _

KINDS = ("bank", "income", "vat")
UPLOAD, READBACK = "upload", "readback"
# 每种的两侧（同网页 `recon.js` 的 left / right）：哪一步交、哪一侧能从读回取（读回工作簿的种类）
SIDES = {"bank": {"left": "statement", "right": "ledger"}, "income": {"left": "ledger", "right": "sales_vat"},
         "vat": {"left": "sales_vat", "right": "invoices"}}
READ_BACK = {"ledger": "general_ledger", "sales_vat": "sales_vat"}
TITLES = {"bank": "cowork.nav.reconBank", "income": "cowork.nav.reconIncome", "vat": "cowork.nav.reconVat"}
BANK_HINT = "cowork.recon.bank.leftHint"
EXPORT_TTL = 7 * 86400


@contextlib.contextmanager
def _conversation(ctx):
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def _answers(ctx) -> dict:
    return ctx.get("answers") or {}


def _access(kind):
    from pearnly_cowork.recon_center import jobs

    return jobs.KINDS[kind]


def _kinds(user=None) -> list:
    return [kind for kind in KINDS if _access(kind).allowed(user)]


def allowed(user) -> bool:
    """菜单上出不出「对账」：三种对账功能有一种就出（同网页对账中心，`recon_access.allowed`）。"""
    return bool(user) and user != "Guest" and bool(_kinds(user))


def _page(rows, codes):
    if codes is not None:
        wanted = {str(code) for code in codes}
        return {"options": [row for row in rows if row["code"] in wanted], "more": False}
    return {"options": rows, "more": False}


# ── 种类 ─────────────────────────────────────────────────────────────────────


def kind_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _page([{"code": kind, "name": _(TITLES[kind])} for kind in _kinds()], codes)


# ── 来源（选项码带种类）───────────────────────────────────────────────────────


def _kind(ctx) -> str:
    kind = str(_answers(ctx).get("kind") or "")
    return kind if kind in KINDS else ""


def _express_book(ctx) -> dict | None:
    """本对话账套是 Express 才能读回（读回引导同一判法）。"""
    from pearnly_cowork.assistant.guided import readback

    return readback.book_default(ctx)


def source_default(ctx):
    """本对话账套不是 Express（或没定）：只能交文件，不问。"""
    kind = _kind(ctx)
    if kind and _express_book(ctx) is None:
        return {"value": f"{kind}:{UPLOAD}", "source": "ledger"}
    return None


def source_options(ctx, txt="", start=0, page_length=20, codes=None):
    kind = _kind(ctx)
    if not kind:
        return {"options": [], "more": False}
    rows = [{"code": f"{kind}:{READBACK}", "name": _("From Express readback")}] if _express_book(ctx) else []
    rows.append({"code": f"{kind}:{UPLOAD}", "name": _("Upload files")})
    return _page(rows, codes)


def _source(values) -> tuple:
    kind, _sep, how = str(values.get("source") or "").partition(":")
    return kind, how


# ── 读回一侧 ─────────────────────────────────────────────────────────────────


def period_default(ctx):
    period = str(ctx.get("period") or "")
    return {"value": period, "value_label": period, "source": "conversation"} if period else None


def period_check(ctx, value) -> str:
    from pearnly_cowork.assistant.guided import readback

    return readback.period_check(ctx, value)


def _readback_state(ctx, request=False) -> dict:
    """这本账套这个月的回读：有现成读完的就是它；``request`` 时没有就请电脑读一次（只在出卡时，同收款读发票）。"""
    from pearnly_cowork.assistant.guided import readback
    from pearnly_cowork.readback import service

    book = _express_book(ctx)
    period = str(_answers(ctx).get("period") or "")
    place = readback.place(ctx, book["value"]) if book else None
    if not place or not period:
        return {"status": "none"}
    with _conversation(ctx):
        try:
            found = service.latest(place[0], place[1], period)
            if found is None and request:
                found = service.request(place[0], place[1], period, None)
                frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"status": "failed", "reason_text": str(error)[:300]}
    return found or {"status": "none"}


def readback_default(ctx):
    """这本账套这个月已经读完：直接用，不问。"""
    found = _readback_state(ctx)
    if found.get("status") == "ready":
        return {"value": found["readback"], "value_label": f"{found.get('account_set') or ''} {found.get('period') or ''}".strip(),
                "source": "readback"}
    return None


def readback_options(ctx, txt="", start=0, page_length=20, codes=None):
    """还没读完：出卡时请电脑读一次（核答案、搜索、翻页不请读），题目下一行事实、页面过一会儿再取。"""
    from pearnly_cowork.assistant.guided import readback

    found = _readback_state(ctx, request=codes is None and not txt and not start)
    if found.get("status") == "ready":
        return _page([{"code": found["readback"], "name": f"{found.get('account_set') or ''} {found.get('period') or ''}".strip()}],
                     codes)
    out = {"options": [], "more": False}
    if found.get("status") == "reading":
        out.update(wait=True, facts=[_(readback.READING)])
    elif found.get("status") == "waiting_for_cc":
        out["facts"] = [_(readback.WAITING.get(found.get("reason"), readback.WAITING["offline"]))]
    elif found.get("reason_text"):
        out["facts"] = [found["reason_text"]]
    return out


# ── 交文件 ───────────────────────────────────────────────────────────────────


def statement_facts(ctx) -> list:
    """银行账单那一侧：网页上传口下面同一句提示（`recon.js` 的 ``hint.left``）。"""
    return [_(BANK_HINT)]


def _files(file_ids) -> list:
    from pearnly_core.assistant import files as chat_files

    return [chat_files.attachment(file_id)  # loop-query: 一步最多 20 份（引擎 steps.FILES_MAX）
            for file_id in file_ids or ()]


def files_check(ctx, value) -> str:
    """文件只认本对话、本人的（`assistant.files.attachment`，对账工具同一道）。"""
    with _conversation(ctx):
        try:
            _files(value)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
            frappe.clear_messages()
            return "attachment_not_in_conversation"
    return ""


def ledger_check(ctx, value) -> str:
    """总账那一侧：收入对账只收一份（同网页 `jobs.MULTIPLE`，收入对账的总账一侧不多份），银行对账可多份。"""
    from pearnly_cowork.recon_center import jobs

    kind, _how = _source(_answers(ctx))
    side = next((name for name, role in SIDES.get(kind, {}).items() if role == "ledger"), "")
    if len(value or ()) > 1 and side not in jobs.MULTIPLE.get(kind, ()):
        return "reconciliation.files_required"
    return files_check(ctx, value)


# ── 确定、结果、追问 ─────────────────────────────────────────────────────────


def _side(values, role) -> list:
    """一侧的 (文件名, 内容)：交了文件就用文件；选了读回就取读回的那份工作簿。"""
    from pearnly_cowork.readback import service

    if values.get(role):
        return _files(values[role])
    if values.get("readback") and role in READ_BACK:
        return [service.file(values["readback"], READ_BACK[role])]
    return []


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定」：照网页「新建」开一套对账（`recon_center.jobs.start`），回任务号；服务拒绝（文件不对、没权限）退回来源那一步。"""
    from pearnly_cowork.recon_center import jobs

    kind, _how = _source(values)
    with _conversation(ctx):
        try:
            sides = {side: _side(values, role) for side, role in SIDES[kind].items()}  # loop-query: 两侧
            started = jobs.start(kind, "", sides, {"lang": frappe.local.lang or "th"})
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"returned": {"source": {"reason": "recon_refused", "text": str(error)[:300]}}}
    return {"status": "queued", "kind": kind, "job": started["job"]}


def _question(index, item) -> dict:
    return {"key": f"q{index}", "input": "choice", "title": str(item.get("title") or ""),
            "options": [{"code": str(row["value"]), "name": str(row.get("label") or row["value"])}
                        for row in item.get("options") or () if row.get("value") not in (None, "")],
            **({"note": item["count"]} if item.get("count") else {})}


def _export(ctx, kind, task) -> list:
    """结果工作簿放进对话（同一段对话同一套只放一次），回结果卡的文件行。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.reconciliation import bank_recon_service, income_recon_service, vat_check_service

    key = f"pearnly_assistant:recon-guide:{frappe.local.site}:{ctx.get('session') or ''}:{task}"
    path = frappe.cache.get_value(key)
    if not path:
        service = {"bank": bank_recon_service, "income": income_recon_service, "vat": vat_check_service}[kind]
        built = service.export(task, frappe.local.lang or "th")
        filename, content = (built["filename"], built["content"]) if isinstance(built, dict) else (built[0], built[1])
        path = chat_files.add_to_conversation(str(filename or f"recon-{kind}-{task}.xlsx"), content,
                                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        frappe.cache.set_value(key, path, expires_in_sec=EXPORT_TTL)
    return [{"file": chat_files.resolve(path), "name": path.rsplit("/", 1)[-1]}]


def _sheet(kind, task) -> dict:
    from pearnly_cowork.recon_center import sheets

    view = sheets.load(kind, task, frappe.local.lang or "th")
    if view.get("moved"):
        view = sheets.load(kind, view["moved"], frappe.local.lang or "th")
    return view.get("task") or {}


DONE = "done"   # `recon_center.jobs` 里对账任务办完的状态


def result(ctx, draft):
    """结果卡：在跑（页面过一会儿再取）/ 完成（标题、要定一次的问题、工作簿）/ 失败（原因）。确定的回包在 ``ctx["done"]``（任务号）。"""
    from pearnly_cowork.recon_center import jobs

    done = ctx.get("done") or {}
    if not isinstance(done, dict) or not done.get("job") or done.get("kind") not in KINDS:
        return None
    kind = done["kind"]
    with _conversation(ctx):
        try:
            state = jobs.status(done["job"])
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.clear_messages()
            return {"status": "failed", "reason_text": str(error)[:300]}
        if state.get("state") in ("queued", "running"):
            return {"status": "queued", "wait": True}
        # 对账任务办完记的是 done（`recon_center.jobs` 的 _state / _recovered），原来只认 completed，真跑时结果卡永远是失败（11-19 Flows 查出）
        if state.get("state") != DONE or not state.get("task"):
            return {"status": "failed", "reason_text": str(state.get("error") or "")}
        head = _sheet(kind, state["task"])
        out = {"status": "ready", "kind": kind, "task": head.get("name") or state["task"], "title": head.get("title") or "",
               "notes": list(head.get("notes") or ()), "files": _export(ctx, kind, head.get("name") or state["task"])}
        questions = [_question(index, item) for index, item in enumerate(head.get("questions") or (), 1)]
        if questions:
            out["questions"] = questions
    return out


def follow(ctx, draft, answers) -> dict:
    """结果里的问题作答（同结果页，`recon_center.presets.answer`）：记成预设、这份结果按它重算；重跑类回新的后台任务号，结果卡接着等。"""
    from pearnly_cowork.recon_center import jobs
    from pearnly_cowork.recon_center import presets

    done = dict(ctx.get("done") or {})
    kind = done.get("kind")
    with _conversation(ctx):
        state = jobs.status(done["job"])
        asked = {f"q{index}": item for index, item in enumerate(_sheet(kind, state["task"]).get("questions") or (), 1)}
        job = done["job"]
        for key, value in answers.items():
            item = asked[key]
            made = presets.answer(kind, state["task"], item["rule"], str(value), item.get("subject"),  # loop-query: 一次只答结果里的几题
                                  frappe.local.lang or "th")
            if isinstance(made, dict) and made.get("job"):
                job = made["job"]
    return {"status": "queued", "kind": kind, "job": job}


# ── 声明 ─────────────────────────────────────────────────────────────────────

GUIDES = [
    {"id": "recon", "label_key": "guide.business.recon", "icon": "scale", "order": 50,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_cowork.assistant.guided.recon.allowed",
     "opening_key": "guide.open.recon", "source": "form", "writes": False,
     "steps": [
         {"key": "kind", "input": "choice", "rule": "always", "options": "pearnly_cowork.assistant.guided.recon.kind_options"},
         {"key": "source", "input": "choice", "rule": "auto", "after": ["kind"],
          "options": "pearnly_cowork.assistant.guided.recon.source_options",
          "default": "pearnly_cowork.assistant.guided.recon.source_default"},
         {"key": "period", "input": "month", "rule": "always",
          "when": {"key": "source", "in": ["bank:readback", "income:readback", "vat:readback"]},
          "default": "pearnly_cowork.assistant.guided.recon.period_default",
          "check": "pearnly_cowork.assistant.guided.recon.period_check"},
         {"key": "readback", "input": "choice", "rule": "auto", "after": ["period"],
          "when": {"key": "source", "in": ["bank:readback", "income:readback", "vat:readback"]},
          "options": "pearnly_cowork.assistant.guided.recon.readback_options",
          "default": "pearnly_cowork.assistant.guided.recon.readback_default"},
         {"key": "statement", "input": "file", "rule": "always", "when": {"key": "source", "in": ["bank:readback", "bank:upload"]},
          "facts": "pearnly_cowork.assistant.guided.recon.statement_facts",
          "check": "pearnly_cowork.assistant.guided.recon.files_check"},
         {"key": "ledger", "input": "file", "rule": "always", "when": {"key": "source", "in": ["bank:upload", "income:upload"]},
          "check": "pearnly_cowork.assistant.guided.recon.ledger_check"},
         {"key": "sales_vat", "input": "file", "rule": "always", "when": {"key": "source", "in": ["income:upload", "vat:upload"]},
          "check": "pearnly_cowork.assistant.guided.recon.files_check"},
         {"key": "invoices", "input": "file", "rule": "always", "when": {"key": "source", "in": ["vat:readback", "vat:upload"]},
          "check": "pearnly_cowork.assistant.guided.recon.files_check"},
     ],
     "confirm": "pearnly_cowork.assistant.guided.recon.confirm", "result": "pearnly_cowork.assistant.guided.recon.result",
     "follow": "pearnly_cowork.assistant.guided.recon.follow",
     "tool": "cowork_recon_start", "expires": ["ledger", "company"]},
]
