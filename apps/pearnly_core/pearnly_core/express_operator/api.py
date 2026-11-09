"""代录 Express 的网页接口（多产品共用）：CC 对话页（Cowork 账号、ERP 账号登录都一样）在「代录」里调这里。

- ``documents``：这家公司这个月能代录的单据（服务端分页，产品各自的来源）；
- ``precheck``：开录前逐张按产品的写账准备判一遍，要人选的项一次列出（不排写账件）；
- ``intake`` / ``regroup``：资料区上传一张、把一张拖到别的组（产品可选声明）；``task_uploads``：任务里传过的每一份此刻识别到哪
  （``uploads``）；
- ``task_*``：实操任务的服务端记录（列表、「待处理」里需要你处理的一组、读取、新建、改名 / 置顶、删除、收尾、作答 / 单张重试、整批继续），只给本人看、改
  （``tasks``）；
- ``start``：建一批：权限由产品判（``products.allowed``，各用自己的角色），批次号在这里发，单据逐张交给产品按直写同一份载荷
  准备、加代录标记、交进产品自己的写账队列，派给选定的那台电脑。准备不了的（要人选、主档没有……）照产品原来的口径停在单据上，
  在回包里列出来，其余照交。这家公司录到哪种账本（执行目标，ADR-026）由 ``products.submit`` 按产品声明带进批次信息。
进度在执行的那台电脑上（CC 窗口经桥看）；每张的结论照产品原来的写账回执回到单据上。
"""
import uuid
from datetime import datetime

import frappe
from frappe import _

from pearnly_core.execution.errors import Deferred
from pearnly_core.express_operator import products, shapes, tasks, uploads

PAGE_MAX = 100
BATCH_MAX = 200


def _require(company):
    if not products.enabled() or not products.allowed(frappe.session.user, company):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


@frappe.whitelist()
def documents(company, period, offset=0, limit=50):
    _require(company)
    limit = max(1, min(int(limit or 50), PAGE_MAX))
    found = products.sources(company, str(period or ""), max(0, int(offset or 0)), limit)
    movable = products.can_regroup()
    for row in found.get("documents") or []:
        if isinstance(row, dict):
            row["moves"] = shapes.moves(row, movable)
    return found


def _ids(doc_ids) -> list:
    doc_ids = [str(d) for d in (frappe.parse_json(doc_ids) if isinstance(doc_ids, str) else doc_ids or [])]
    doc_ids = list(dict.fromkeys(d for d in doc_ids if d))
    if not doc_ids or len(doc_ids) > BATCH_MAX:
        frappe.throw(_("Invalid request"))
    return doc_ids


@frappe.whitelist(methods=["POST"])
def precheck(company, doc_ids):
    """开录前预检（主会话 10-27）：产品按自己的写账准备逐张判，要人选的项连同可选值一次列出；不排写账件。
    写进哪份账套（Code 版 CC B4）：产品在判得出的单上带 ``target``（与交单同一条准备算出的），这里合成 ``targets``（几份账套、
    各几张）；只有一份时 ``target`` 就是它，没有或不止一份时为 None。"""
    _require(company)
    found = products.precheck(company, _ids(doc_ids))
    documents = []
    for doc in found.get("documents") or []:
        target = shapes.target_view(doc.get("target")) if isinstance(doc, dict) else None
        documents.append({**doc, "target": target} if isinstance(doc, dict) and "target" in doc else doc)
    targets = shapes.precheck_targets(documents)
    return {**found, "documents": documents, "targets": targets, "target": targets[0] if len(targets) == 1 else None}


@frappe.whitelist()
def status(company, doc_ids):
    """CC 重启后恢复出来的任务：这几张单在站点上现在怎样（已入账 / 仍在队列 / 未完成 / 已取消），以站点为准收尾。只读。"""
    _require(company)
    return {"documents": products.status(company, _ids(doc_ids))}


@frappe.whitelist(methods=["POST"])
def resolve(company, doc_id, entered, docnum=""):
    """开始前确认里「结果不明」的单：人在 Express 里核对后标已入账（可填单号）或没有入账（之后可再交）。走产品复核页同一条路。"""
    _require(company)
    return products.resolve(company, str(doc_id or ""), str(entered) in ("1", "true", "True"), str(docnum or "")[:40])


UPLOAD_MAX = 30 * 1024 * 1024
UPLOAD_DEFERRED = "upload.deferred"  # 暂时无法上传，请稍后重试。
GROUPS = shapes.GROUPS


@frappe.whitelist(methods=["POST"])
def intake(company, request_key, task=""):
    """资料区上传一张（multipart 的 file）：产品存成原件并交识别，归到这家公司；产品不收上传时回 unsupported。
    ``task``：在哪个实操任务里传的（Code 版 CC，B1）：先核对是本人这家公司的任务再交识别，收下后在任务上记一行，回包多带
    ``upload``（同 ``task_uploads`` 里的一项），刷新、换设备都看得到这一份。"""
    _require(company)
    if task:
        uploads.check(task, company, request_key)
    incoming = frappe.request.files.get("file") if frappe.request else None
    if not incoming:
        frappe.throw(_("Select a file to upload."))
    content = incoming.stream.read(UPLOAD_MAX + 1)
    if len(content) > UPLOAD_MAX:
        frappe.throw(_("Invalid request"))
    try:
        result = products.intake(company, incoming.filename, content, str(request_key or ""))
    except Deferred:
        # 原件存放处 / 识别服务一时连不上（甲骨文、Cloud Run）：回一句可重试的话，页面把文件放回输入框，不回 500
        frappe.throw(_(UPLOAD_DEFERRED))
    if task and result.get("accepted"):
        result = {**result, "upload": uploads.record(task, request_key, incoming.filename, len(content), result)}
    return result


@frappe.whitelist()
def task_uploads(name, batches=None):
    """任务里上传过的每一份与此刻的识别状态（只读，B2；形状见 ``shapes.upload_view``）。``batches``：只现读这几份（JSON 列表）。"""
    batches = frappe.parse_json(batches) if isinstance(batches, str) and batches else batches
    if batches is not None and not isinstance(batches, list):
        frappe.throw(_("Invalid request"))
    return uploads.read(name, [str(item) for item in batches] if batches else None)


@frappe.whitelist(methods=["POST"])
def regroup(company, doc_id, group):
    """资料区把一张拖到别的组。"""
    _require(company)
    if group not in GROUPS or not doc_id:
        frappe.throw(_("Invalid request"))
    return products.regroup(company, str(doc_id), str(group))


@frappe.whitelist(methods=["POST"])
def start(company, doc_ids, computer="", start_at=0, answers=None, task="", groups=None):
    """``answers``：预检里人定的（``{doc_id: [{kind, subject, value, label}]}``），产品先记下再交代录。``task``：服务端的任务号
    （``tasks``，批次号就用它，CC 本机进度按它对上），交单结论写进任务、状态转等电脑空闲；``groups``：各张的资料分组（汇报用）。"""
    _require(company)
    doc_ids = _ids(doc_ids)
    answers = frappe.parse_json(answers) if isinstance(answers, str) else (answers or {})
    groups = frappe.parse_json(groups) if isinstance(groups, str) else (groups or {})
    if not isinstance(answers, dict) or not isinstance(groups, dict):
        frappe.throw(_("Invalid request"))
    if task:
        found = tasks.read(task)
        if found["company"] != company or found["status"] != "preparing":
            frappe.throw(_("Invalid request"))
    batch = {"id": str(task) if task else uuid.uuid4().hex[:12], "total": len(doc_ids), "start_at": float(start_at or 0)}
    results = products.submit(company, doc_ids, batch, str(computer or ""), answers)
    if task:
        tasks.attach(task, results, groups, str(computer or ""),
                     datetime.fromtimestamp(float(start_at)) if float(start_at or 0) else None)
    return {"batch": batch["id"], "results": results}


# ── 实操任务（服务端记录，``tasks``）：只给本人看、改 ─────────────────────────────────────────────
@frappe.whitelist()
def task_list(start=0, limit=50):
    return tasks.listing(int(start or 0), int(limit or 50))


@frappe.whitelist()
def task_attention():
    """CC「待处理」里的代录一组与它的数（``tasks.attention``：按单据去重）。"""
    return tasks.attention()


@frappe.whitelist()
def task_get(name):
    return tasks.read(name)


@frappe.whitelist(methods=["POST"])
def task_create(company, period, company_title=""):
    _require(company)
    return tasks.create(company, period, company_title)


@frappe.whitelist(methods=["POST"])
def task_update(name, title=None, starred=None, company=None, company_title=None, period=None):
    if company is not None:
        _require(company)
    return tasks.update(name, title=title, starred=starred, company=company, company_title=company_title, period=period)


@frappe.whitelist(methods=["POST"])
def task_delete(name):
    return tasks.delete(name)


@frappe.whitelist(methods=["POST"])
def task_running(name):
    return tasks.mark_running(name)


@frappe.whitelist(methods=["POST"])
def task_settle(name):
    return tasks.settle(name)


# ── 任务里的对话与选项（对话框甲）────────────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
def task_thread(name):
    """这个任务的对话（第一次打字发问时建）；回会话编号，页面照对话页那一套收发。"""
    return tasks.thread(name)


@frappe.whitelist(methods=["POST"])
def task_answer(name, doc_id, answers):
    """录到中途停下等人选的那一张：点了选项（``[{kind, subject, value, label}]``），产品记下后交回这个任务接着录。不调模型。
    单张「重试」走同一条路，``answers`` 为空列表。"""
    answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if not isinstance(answers, list):
        frappe.throw(_("Invalid request"))
    return tasks.answer(name, str(doc_id or ""), answers)


@frappe.whitelist(methods=["POST"])
def task_answer_many(name, answers):
    """同一题合成一张卡后的一次作答：``{单据: [{kind, subject, value, label}]}``，几张一起交回这个任务。不调模型。"""
    answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if not isinstance(answers, dict) or len(answers) > BATCH_MAX or any(not isinstance(v, list) for v in answers.values()):
        frappe.throw(_("Invalid request"))
    return tasks.answer_many(name, {str(k): v for k, v in answers.items()})


@frappe.whitelist(methods=["POST"])
def task_resume(name):
    """整批停下以后的「继续」：因整批停而没录成的单按原批次号交回原来那台电脑。"""
    return tasks.resume(name)


@frappe.whitelist(methods=["POST"])
def task_controls(name):
    """执行的那台电脑的 CC 页面取走对话里交代的跳过 / 重做（取走即清空）。"""
    return {"controls": tasks.take_controls(name)}
