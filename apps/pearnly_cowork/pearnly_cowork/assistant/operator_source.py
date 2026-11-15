"""代录 Express 的 Cowork 这一侧（共用层 ``pearnly_core.express_operator``，``hooks.pearnly_express_operator`` 声明）。

- 谁能发起：与推送同一条判据（这个工作空间里有收单权限）；
- 单据来源：这家公司这个月「等着录入」的识别单据（没推过、推送失败或停下等人选；成功与在途的不算），与 CC 的
  ``cowork_list_documents`` 同一个查询、同一个公司范围与权限；
- 交单：逐张走推送同一条路（``tools._push_one``：要人选的停在单据上照旧），载荷加代录标记，派给选定的电脑；
  回执照推送记录原来的口径回到单据上。
"""
import calendar
import json

import frappe

from pearnly_cowork.access import scope


def allowed(user, company) -> bool:
    return bool(company) and scope.allowed("intake", user)


def waiting_on(company, user) -> str:
    """代录任务排着等电脑时在等的本人电脑名称（共用层 ``products.waiting_on``）：这家账套所在各条连接上，``user`` 自己能写、
    此刻不在线的那几台（`folder_intake.destinations.waiting_on`）；没有回空串。"""
    from pearnly_cowork.companies import service
    from pearnly_cowork.folder_intake import destinations

    if not service.exists(company):
        return ""
    connections = sorted({place[0] for book in service.listed_books(service.get(company), None)
                          for place in book.get("places") or [(book.get("connection"), book.get("key"))] if place[0]})
    names = []
    for connection in connections:
        names += destinations.waiting_on(connection, user)
    return ", ".join(dict.fromkeys(names))


def target(company) -> str:
    """这家公司代录录到哪种账本（ADR-026 第 6 条，共用层 ``products.target_of``）：账本是 MR.ERP 的公司在 MR.ERP 代录开了
    （全平台常量 ``contracts.MRERP_OPERATOR_ENABLED``，不按站点开）时回 ``mrerp``；其余（含常量关着时的 MR.ERP 公司）回 Express，
    与没有这个声明时一样，MR.ERP 公司的单在交单那一步照旧按 ``operator_express_only`` 拦下。"""
    from pearnly_core.express_operator import contracts

    if contracts.MRERP_OPERATOR_ENABLED and company \
            and frappe.db.get_value("Cowork Company", company, "adapter") == contracts.TARGET_MRERP:
        return contracts.TARGET_MRERP
    return contracts.TARGET_EXPRESS


def _month(period: str):
    year, month = (int(part) for part in str(period).split("-")[:2])
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def sources(company, period, offset, limit) -> dict:
    from pearnly_cowork.assistant import tools
    from pearnly_cowork.companies import rules

    first, last = _month(period)
    found = json.loads(tools.list_documents({"company": company, "push_state": "waiting", "invoice_date_from": first,
                                             "invoice_date_to": last, "limit": limit, "offset": offset}))
    if found.get("status") == "failed":
        return {"documents": [], "next_offset": None, "failed": found.get("reason")}
    rows = found.get("documents") or []
    # 本人在这家公司的代录任务里放进来、单据日期不在这个月的：也列出、带 other_month（只在第一页补）
    others = _other_month(company, first, last, {row.get("name") for row in rows}) if not int(offset or 0) else []
    rows = rows + others
    pages = _primary_fields([row.get("name") for row in rows])
    memory = rules.groups(company)
    directions = _directions([row.get("name") for row in others]) if others else {}
    other_names = {row.get("name") for row in others}
    documents = []
    for row in rows:
        direction = row.get("direction") or directions.get(row.get("name")) or ""
        fields = pages.get(row.get("name")) or {}
        group = _group(row, fields, row.get("posting_kind"), memory)[0]
        documents.append({"doc_id": row.get("name"), "kind": direction, "group": group,
                          "moves": [other for other in ENTRY_GROUPS if other != group] if group in ENTRY_GROUPS else [],
                          "date": str(row.get("invoice_date") or ""),
                          "party": row.get("buyer_name") if direction == "sales" else row.get("seller_name"),
                          "ref": row.get("invoice_no") or "", "total": str(row.get("total_amount") or ""),
                          "stopped": row.get("last_push_reason") or "",
                          **({"other_month": str(row.get("invoice_date"))[:7]} if row.get("name") in other_names else {})})
    return {"documents": documents, "next_offset": found.get("next_offset"), "total": found.get("total")}


OTHER_TASKS_MAX, OTHER_BATCHES_MAX = 200, 500


def _other_month(company, first, last, seen) -> list:
    """本人在这家公司的代录任务里放进来的单（任务上传记下的识别批次，`express_operator.uploads`），单据日期不在这个月、还等着录的。
    11-10 线上压测 S5b（ERP 同类已修 0372c19c5）：11 月的任务里放了 8 月的票，只按月份列的资料里一张都不出现，CC 让用户「放进系统
    再说」；不是本月的由 CC 在对话里说清、再问怎么处理（主会话 11-10），不悄悄丢掉。"""
    from pearnly_core.express_operator import tasks as operator_tasks
    from pearnly_core.express_operator import uploads as operator_uploads
    from pearnly_cowork.assistant import tools

    owned = frappe.get_all(operator_tasks.DOCTYPE, filters={"company": company, "owner": frappe.session.user},
                           pluck="name", limit_page_length=OTHER_TASKS_MAX)
    batches = frappe.get_all(operator_uploads.UPLOAD, filters={"parent": ["in", owned], "batch": ["is", "set"]},
                             pluck="batch", limit_page_length=OTHER_BATCHES_MAX) if owned else []
    names = []
    for batch in dict.fromkeys(batches):
        try:
            names += tools._recognition().detail(batch).get("history_records") or []
        except (frappe.DoesNotExistError, frappe.PermissionError):
            frappe.clear_messages()
    names = [name for name in dict.fromkeys(names) if name and name not in seen]
    if not names:
        return []
    rows = frappe.get_all("Cowork OCR History", filters={"name": ["in", names],
                                                         "last_push_status": ["not in", tools.PUSHED_OR_ON_ITS_WAY]},
                          fields=["name", "posting_kind", "invoice_date", "invoice_no", "seller_name", "buyer_name",
                                  "total_amount", "last_push_reason"], limit_page_length=len(names))
    return [dict(row) for row in rows if row.get("invoice_date") and not (first <= str(row.get("invoice_date")) <= last)]


def precheck(company, doc_ids) -> dict:
    """开录前逐张预检（`assistant.precheck_source.operator_check`：写账前置判定同一次判断里原来的回包，不建推送记录）。按公司
    记住的分组先落到单据上（方向、入账类型，同拖动那条路），再按这张单交单时用的入账类型预检。开始前要人定的项由 CC 调
    ``write_precheck.api.check`` 取、用 ``ask_user`` 问（主会话 11-05），这里的形状不变。"""
    from pearnly_cowork.companies import rules
    from pearnly_cowork.assistant import precheck_source as checking

    memory = rules.groups(company)
    out = []
    for name in doc_ids:
        twin = written_twin(name)
        if twin:
            # 同一张票已由直写（或别的识别单）写进 Express：默认不再交，开始前卡片标出已写入的单号，人可以勾回再录（用户 11-09 定）
            out.append({"doc_id": name, "state": "written", "docnum": twin["docnum"], "twin": twin["doc_id"]})
            continue
        out.append({"doc_id": name, **checking.operator_check(name, posting_kind=_settle(name, memory))})
    return {"documents": out}


def written_twin(name) -> dict:
    """这张识别单是不是已经写进 Express 的那张票（11-08 生产：同一份 PDF 的 24 张在代录里等着，岳父又逐张截图直写了其中 4 张）：
    同一工作区、同一条连接与账套里，另一张票号相同、对方税号相同、金额相同、已推送成功的识别单 → ``{doc_id, docnum}``（它在
    Express 里的单号）；没有回空。只读。"""
    from decimal import Decimal, InvalidOperation

    row = frappe.db.get_value(HISTORY, name, ["name", "workspace", "invoice_no", "seller_tax", "buyer_tax", "total_amount",
                                              "push_connection", "push_account_set"], as_dict=True)
    if not isinstance(row, dict):
        return {}
    number = str(row.get("invoice_no") or "").strip()
    if not number or not row.get("push_connection"):
        return {}

    def amount(value):
        try:
            return Decimal(str(value or "0")).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None

    for other in frappe.get_all(HISTORY, filters={"workspace": row.workspace, "invoice_no": number, "name": ["!=", row.name],
                                                  "push_connection": row.push_connection, "last_push_status": "success"},
                                fields=["name", "seller_tax", "buyer_tax", "total_amount", "push_account_set", "last_push_record"],
                                limit_page_length=TWIN_SCAN):
        same = ((other.seller_tax or "") == (row.seller_tax or "") and (other.buyer_tax or "") == (row.buyer_tax or "")
                and amount(other.total_amount) == amount(row.total_amount)
                and (other.push_account_set or "") == (row.push_account_set or ""))
        if same:
            docnum = frappe.db.get_value("Cowork Push Record", other.last_push_record, "external_docnum") \
                if other.last_push_record else ""
            return {"doc_id": other.name, "docnum": str(docnum or "")}
    return {}


HISTORY = "Cowork OCR History"
TWIN_SCAN = 20


# 资料区分组（设计稿右栏「资料」）：只用识别已有的字段，不另判
GROUPS = ("purchase", "sales", "expense", "payment", "skip")
ENTRY_GROUPS = ("purchase", "expense", "sales")  # 入账的三组：拖动与按公司记住只在这三组之间（收付款、不入账随第 1 期）
_SKIP_TYPES = ("order_evidence", "other")
_EXPENSE_TYPES = ("receipt", "simplified_tax_invoice")  # 同共用写账规则的「费用类票据」（express_rules.push.choices）
# 每组写到单据上的方向与入账类型（同复核页的方向 / 过账类型两个字段）
_FIELDS = {"purchase": {"direction": "purchase", "posting_kind": "stock"},
           "expense": {"direction": "purchase", "posting_kind": "service"},
           "sales": {"direction": "sales"}}


def group_of(row, posting="") -> str:
    """一张识别单在资料区归哪组：不是发票 / 订单凭据 / 其他 → 不入账；付款凭据 → 收付款；销项 → 销售；进项按入账类型
    （``posting``：交单时用的那个，走库存 → 采购、记费用 → 费用），没定的收据、简易税票 → 费用，其余 → 采购。"""
    kind = str(row.get("document_type") or "")
    if row.get("is_not_invoice") or kind in _SKIP_TYPES:
        return "skip"
    if kind == "payment_evidence":
        return "payment"
    if (row.get("direction") or "") == "sales":
        return "sales"
    if posting in ("stock", "service"):
        return "purchase" if posting == "stock" else "expense"
    return "expense" if kind in _EXPENSE_TYPES else "purchase"


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def party_key(fields, direction) -> tuple:
    """按公司记分组的归类键：对方（进项看卖方、销项看买方）税号 + 分店，没有税号按名称（主会话 10-27）。回 (键, 对方名)；
    都没有回 ("", "")。"""
    from pearnly_core.express_rules.push.choices import norm_name

    side = "buyer" if direction == "sales" else "seller"
    name = str(fields.get(f"{side}_name") or "").strip()
    tax = _digits(fields.get(f"{side}_tax") or fields.get(f"{side}_tax_id"))
    if len(tax) == 13:
        branch = _digits(fields.get(f"{side}_branch"))
        return f"party|{tax}|{int(branch) if branch else 0}", name
    key = norm_name(name)
    return (f"party|name:{key}", name) if key else ("", "")


def _posting(fields, column) -> str:
    """交单时用的入账类型：单据页面上的（复核页、拖动写的）优先，没有用识别那一批选的（单据列）。"""
    from pearnly_core.express_rules.contracts.push import normalize_posting_kind

    return normalize_posting_kind(fields.get("posting_kind")) or normalize_posting_kind(column) or ""


def _group(row, fields, column, memory) -> tuple:
    """(这张单现在归哪组, 识别 / 页面字段本身定的组)：入账三组里这家公司按对方记过的，以记住的为准。"""
    from pearnly_core.express_rules.contracts.push import normalize_direction

    direction = normalize_direction(fields.get("direction")) or row.get("direction") or ""
    own = group_of({**row, "direction": direction}, _posting(fields, column))
    if own not in ENTRY_GROUPS:
        return own, own
    subject, _name = party_key({**row, **fields}, direction)
    return memory.get(subject, own) if subject else own, own


def _primary_fields(names) -> dict:
    """这些单的主页面字段（一次查询；单号来自已按权限列出的单据）。"""
    from pearnly_core.express_rules.contracts.history import primary_page_fields

    names = [name for name in names if name]
    if not names:
        return {}
    rows = frappe.db.get_all("Cowork OCR History", filters={"name": ["in", names]}, fields=["name", "pages"],
                             limit_page_length=len(names))
    out = {}
    for row in rows:
        try:
            pages = json.loads(row.pages) if isinstance(row.pages, str) else row.pages
        except ValueError:
            pages = None
        out[row.name] = primary_page_fields(pages if isinstance(pages, list) else [])
    return out


def _directions(names) -> dict:
    """这些单各自的方向（`tools.judged_direction`：票上声明的，没声明按推送同一套税号判据；判不出是空串）。资料区分组、交单前
    落分组、对方说买方还是卖方都用它（11-09：本公司开出的销售票截图没声明方向，资料区归进了「采购」）。"""
    from pearnly_core.express_rules.contracts.history import primary_page_fields
    from pearnly_cowork.assistant import tools

    names = [name for name in names if name]
    if not names:
        return {}
    rows = frappe.db.get_all("Cowork OCR History", filters={"name": ["in", names]},
                             fields=["name", "pages", "workspace", "push_connection", "push_account_set"],
                             limit_page_length=len(names))
    anchors, out = tools.direction_anchors(), {}
    for row in rows:
        try:
            pages = json.loads(row.pages) if isinstance(row.pages, str) else row.pages
        except ValueError:
            pages = None
        fields = primary_page_fields(pages if isinstance(pages, list) else [])
        out[row.name] = tools.judged_direction(fields, row.get("workspace"), row.get("push_connection"),
                                               row.get("push_account_set"), anchors)
    return out


def _write_group(doc_id, group) -> dict:
    """把这一组的方向与入账类型写到单据各页（同复核页改方向 / 过账类型，`history.api.save` 那条路）。回写之前的主页面字段。"""
    from pearnly_core.express_rules.contracts.history import primary_page_fields
    from pearnly_cowork.history import api as history

    detail = history.detail(doc_id)
    pages = json.loads(detail["pages"]) if isinstance(detail.get("pages"), str) else (detail.get("pages") or [])
    before = dict(primary_page_fields(pages))
    for page in pages:
        page.setdefault("fields", {}).update(_FIELDS[group])
    history.save(doc_id, json.dumps(pages, ensure_ascii=False), detail.get("record_version"))
    return before


def _settle(doc_id, memory) -> str:
    """开录前：这家公司记住的分组与单据现在的不同 → 写到单据上；回交单时用的入账类型（没有回空）。"""
    from pearnly_cowork.history import storage

    doc = storage.read(doc_id)
    fields = _primary_fields([doc_id]).get(doc_id) or {}
    row = {"direction": _directions([doc_id]).get(doc_id) or "",
           "document_type": fields.get("document_type"), "is_not_invoice": fields.get("is_not_invoice"),
           "seller_name": doc.get("seller_name"), "seller_tax": doc.get("seller_tax"),
           "buyer_name": doc.get("buyer_name"), "buyer_tax": doc.get("buyer_tax")}
    group, own = _group(row, fields, doc.get("posting_kind"), memory)
    if group != own:
        _write_group(doc_id, group)
        fields = {**fields, **_FIELDS[group]}
    return _posting(fields, doc.get("posting_kind"))


def intake(company, filename, content, request_key) -> dict:
    """资料区上传一张：存成来源文件（原件层），按这家公司现用的连接与账套交识别（同录入工作台的上传 → 识别两步），
    识别好后出现在这家公司的资料里。"""
    from pearnly_cowork.files import service as files
    from pearnly_cowork.ocr.execution import service as ocr

    row = frappe.db.get_value("Cowork Company", company, ["workspace", "seed_connection", "seed_account_set"],
                              as_dict=True)
    if not row or not row.seed_connection:
        frappe.throw(frappe._("cowork.company.pending.no_target"))
    source = files.create(row.workspace, filename, content, request_key)
    result = ocr.submit(source["name"], f"{request_key}:ocr"[:128], connection=row.seed_connection,
                        account_set_key=row.seed_account_set or None)
    return {"accepted": True, "source": source["name"], **{k: v for k, v in (result or {}).items() if k != "accepted"}}


_DONE_PHASES = ("saved", "not_invoice")
_FAILED_WORK = ("failed", "uncertain", "cancelled")


def upload_state(project) -> dict:
    """一份识别任务（`ocr.execution.service.detail` 的回包）→ 代录上传卡片的状态（共用层 ``shapes.UPLOAD_STATES``）：存好了 /
    不是单据 → 已识别；共用队列落定失败 / 结果不确定 / 取消 → 失败（带失败码）；其余按录入工作台等候页同一个判据分排队中、识别中
    （``reading``：已离开队列）。纯函数。"""
    from pearnly_core.express_operator import shapes

    state = str(project.get("state") or "")
    return shapes.upload_state(done=state in _DONE_PHASES, failed=state in _FAILED_WORK, reading=bool(project.get("reading")),
                               pages_done=project.get("pages_done") or 0, page_count=project.get("page_count") or 0,
                               code=str(project.get("last_code") or (state if state in _FAILED_WORK else "")))


def uploads(company, period, batches) -> dict:
    """代录任务里上传的那几份此刻识别到哪了（共用层 ``uploads`` 钩子，只读）：逐份读识别任务（同录入工作台等候页，权限在
    `ocr.execution.access.require_batch`），已识别的带出识别出的单（单号、日期、对方、还在不在等着录入，同 ``sources`` 的口径）。
    读不到的（删了、没权限了）不回，页面显示读不到。"""
    from pearnly_cowork.assistant import tools
    from pearnly_cowork.ocr.execution import service as ocr

    out, records = {}, {}
    for batch in dict.fromkeys(str(item) for item in batches or () if item):
        try:
            project = ocr.detail(batch)
        except (frappe.DoesNotExistError, frappe.PermissionError):
            frappe.clear_messages()
            continue
        out[batch] = upload_state(project)
        if out[batch]["state"] == "done":
            records[batch] = [str(name) for name in project.get("history_records") or [] if name]
    names = [name for found in records.values() for name in found]
    rows = {row.name: row for row in frappe.db.get_all(
        "Cowork OCR History", filters={"name": ["in", names]},
        fields=["name", "invoice_no", "invoice_date", "seller_name", "buyer_name", "last_push_status"],
        limit_page_length=len(names))} if names else {}
    directions = _directions(names)
    for batch, found in records.items():
        documents = []
        for name in found:
            row = rows.get(name)
            if not row:
                continue
            sales = directions.get(name) == "sales"
            documents.append({"doc_id": name, "ref": row.invoice_no or "", "date": str(row.invoice_date or ""),
                              "party": (row.buyer_name if sales else row.seller_name) or "",
                              "waiting": str(row.last_push_status or "") not in tools.PUSHED_OR_ON_ITS_WAY})
        out[batch]["documents"] = documents
    return out


def regroup(company, doc_id, group) -> dict:
    """资料区拖到别的组：采购 / 费用 / 销售之间改这张单的方向与入账类型（同复核页），并按公司记住（公司规则「分组」，
    键是拖之前这张单的对方），这家以后同一对方的单照这次分。收付款、不入账的改法随第 1 期，这里回不支持。"""
    from pearnly_cowork.companies import rules

    if group not in ENTRY_GROUPS:
        return {"ok": False, "reason": "unsupported"}
    judged = _directions([doc_id]).get(doc_id) or "purchase"  # 拖之前这张单的方向（键是那时的对方）
    before = _write_group(doc_id, group)
    subject, name = party_key(before, judged)
    if subject:
        workspace = frappe.db.get_value("Cowork Company", company, "workspace")
        rules.save(company, workspace=workspace, kind=rules.GROUP, subject=subject, value=group, subject_label=name,
                   evidence={"document": doc_id})
    return {"ok": True}


def _release_kick(doc_ids):
    """人在开始前卡片上点了「开始」：这批单要用的 MR.ERP 账号被踢过的标记清一次（`push.mrerp.runner.release_kick`，11-09
    风险 7），同批后续被踢了照样快速失败、不再自己登录。读不到的票跳过（交单时照常报原因）。"""
    from pearnly_cowork.history import storage
    from pearnly_cowork.push.mrerp import runner

    seen = set()
    for name in doc_ids or ():
        try:
            doc = storage.read(name)
        except (frappe.DoesNotExistError, frappe.PermissionError):
            continue
        place = (doc.push_connection, doc.workspace, doc.client)
        if doc.push_connection and place not in seen:
            seen.add(place)
            runner.release_kick(*place)


def submit(company, doc_ids, batch, computer, answers=None) -> list:
    """``answers``：预检里人定的（``{doc_id: [{kind, subject, value, label}]}``），先按写账前置判定同一套记法记下
    （`assistant.precheck_source.remember`：写账规则的项走复核页同一条路，值须在账套主档里或「新建」），再交代录；不经待处理、
    不会按直写重推。"""
    from pearnly_cowork.assistant import tools
    from pearnly_cowork.companies import rules
    from pearnly_cowork.assistant import precheck_source as checking

    memory = rules.groups(company)
    results = []
    again = set((batch or {}).get("again") or ())
    if target(company) == "mrerp":
        _release_kick(doc_ids)
    for index, name in enumerate(doc_ids, start=1):
        twin = written_twin(name) if name not in again else {}
        if twin:
            # 同一张票已写进 Express：默认跳过（用户 11-09），原因带那张的单号；人在开始前卡片上点了「再录一次」的照交
            results.append({"doc_id": name, "status": "written", "reason": twin["docnum"]})
            continue
        given = (answers or {}).get(name)
        if given:
            checking.remember(company, {name: given})
            frappe.db.commit()
        # 入账类型明传：单据列（识别那一批选的）在推送里比页面字段优先，拖动 / 记住的分组写在页面上，不明传就不生效
        posting = _settle(name, memory) or None
        result = tools._push_one(name, posting_kind=posting, route_device=computer, operator={**batch, "index": index, "doc": name, "company": company})
        results.append({"doc_id": name, "status": result.get("status") or "", "reason": str(result.get("reason") or "")})
    return results


def status(company, doc_ids) -> list:
    """CC 重启后恢复出来的任务，以站点为准收尾（共用层 ``status``）：按推送记录在票上的结论说这张单现在怎样——成功 → 已入账
    （带 Express 单号）、在途 → 仍在队列、停下等人 / 失败 → 未完成（带原因）、没推过或已撤回 → 已取消；读不到（不是本人能看的、
    已删除）→ unknown。"""
    from pearnly_cowork.history import storage
    from pearnly_cowork.push import lifecycle
    from pearnly_cowork.push.application.service import RESULT_UNKNOWN

    out, halves = [], {}
    for name in doc_ids:
        try:
            doc = storage.read(name)
        except (frappe.DoesNotExistError, frappe.PermissionError):
            frappe.clear_messages()
            out.append({"doc_id": name, "state": "unknown", "docnum": "", "reason": ""})
            continue
        pushed = str(doc.get("last_push_status") or "")
        docnum = ""
        if pushed == "success" and doc.get("last_push_record"):
            docnum = frappe.db.get_value("Cowork Push Record", doc.get("last_push_record"), "external_docnum") or ""
        state = {"success": "saved", "pending": "queued", "manual": "not_done", "failed": "not_done",
                 "blocked": "not_done"}.get(pushed, "cancelled")
        if state == "not_done" and doc.get("last_push_record"):
            halves[len(out)] = doc.get("last_push_record")  # 半截单号：循环外按名单一次取回推送记录的回执
        sales = _directions([name]).get(name) == "sales"
        # 单号与对方一起回：CC 重启后页面没有这一页的资料清单，靠它把单据写成「单号 · 对方」
        reason = str(doc.get("last_push_reason") or "") if state == "not_done" else ""
        if lifecycle.result_unknown(pushed, reason):
            reason = RESULT_UNKNOWN  # 推送结果不明：同开始前确认一个原因码，页面给「已入账」「没有入账」（10-29 测试机）
        row = {"doc_id": name, "state": state, "docnum": docnum, "reason": reason,
               "ref": str(doc.get("invoice_no") or ""),
               "party": str(doc.get("buyer_name" if sales else "seller_name") or "")}
        if state == "queued" and doc.get("last_push_record"):
            blocker = _blocker(doc.get("last_push_record"))
            if blocker:
                row["blocked_by"] = blocker
        out.append(row)
    if halves:
        names = list(set(halves.values()))
        found = {r.name: r.response_json for r in frappe.get_all("Cowork Push Record", filters={"name": ["in", names]},
                                                                  fields=["name", "response_json"], limit_page_length=len(names))}
        for index, record in halves.items():
            out[index]["docnum"] = _partial_docnum(found.get(record))
    return out


def _partial_docnum(raw) -> str:
    """代录保存途中停住、Express 里留下的半截单号（回执 ``meta.operator.partial``）：未完成的单只多显示这个单号。"""
    from pearnly_core.express_rules.push.common import operator_partial_docnum

    try:
        response = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return ""
    return operator_partial_docnum(response.get("meta") if isinstance(response, dict) else None)


def _blocker(record) -> dict:
    """排着的这张被同一份账套里哪张「结果不明」的单挡着（写者锁由它持有、人还没核对，ADR-021 11-02 修订）：
    ``{doc_id, ref, party}``，没有就空。只看同一连接同一账套键（同 `push.native.service._older_writer` 的第一道）；读不到的不说。"""
    from pearnly_cowork.history import storage

    found = frappe.db.get_value("Cowork Push Record", record, ["connection", "account_key"], as_dict=True)
    if not found:
        return {}
    rows = frappe.db.sql("""SELECT history FROM `tabCowork Push Record` WHERE connection = %s AND account_key = %s
        AND name <> %s AND status = 'manual' AND result_unknown = 1 ORDER BY created_at, name LIMIT 1""",
                         (found.connection, found.account_key, record), as_dict=True)
    if not rows:
        return {}
    try:
        doc = storage.read(rows[0].history)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return {}
    sales = _directions([doc.name]).get(doc.name) == "sales"
    return {"doc_id": doc.name, "ref": str(doc.get("invoice_no") or ""),
            "party": str(doc.get("buyer_name" if sales else "seller_name") or "")}


def pending_documents() -> list:
    """本人此刻在 Cowork「待处理」里列着的单（识别单据号，与 ``companies.pending`` 同一口径）：共用层「待处理」的代录一组按单据
    去重用。"""
    from pearnly_cowork.companies import pending

    return pending.documents()


def resolve(company, doc_id, entered, docnum="") -> dict:
    """开始前确认里「结果不明」的单，人在 Express 核对后标已入账 / 没入账：只认这张单自己的最近一次推送记录，走复核页「核对」
    同一条路（``push.application.service.resolve_unknown``，权限、状态校验都在那里）。"""
    from pearnly_cowork.history import storage
    from pearnly_cowork.push import lifecycle
    from pearnly_cowork.push.application import service

    doc = storage.read(doc_id)
    if not lifecycle.result_unknown(doc.get("last_push_status"), doc.get("last_push_reason")) or not doc.get("last_push_record"):
        return {"ok": False, "reason": "not_unknown"}
    service.resolve_unknown(doc.get("last_push_record"), bool(entered), docnum or None)
    return {"ok": True}
