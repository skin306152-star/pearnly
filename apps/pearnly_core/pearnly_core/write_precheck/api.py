"""写账前置判定的接口（多产品共用）：「开始前确认」卡片取数与作答。产品按 ``products`` 的钩子接入；三个接口先过产品的
``allowed``（这家公司能不能写账），产品在钩子里再按单据核。

- ``check``：这批单此刻要人定的项，按业务分组、跨单去重，每项已定 / 预选 / 未定（``contracts.merge``），另带 ``needs_card``
  （直写全部已定就不出卡片，主会话 11-04 Q1）与 ``planned``（``{doc_id: 产品给的「此刻写进去会怎么写」}``，只有什么都不用定的单才有，
  CC 推前说现 / 赊、科目照它）；只看不写。``mode``：operator / direct / pending（谁来问，原样带回）。
  问什么由 CC 定：拿这份事实与预选，用通用提问工具 ``ask_user`` 出题（主会话 11-05），推荐项排第一（``recommended``）。
- ``search``：大列表的项（科目、存货、对方）按这张单写进的账套服务端分页取可选值。
- ``answer``：卡片点「开始」交回的答案 ``[{key, value}]``：按点之前那张卡片展开到每张单，交产品按现有记法记下；有答错的整批不记、
  逐条回原因码。回记下后重判的卡片，带 ``writable``（照写的单）与 ``held``（因未定项留下的单）。``resume`` 为真时（待处理里的
  卡片：定时任务、自动推停下的单）再交产品把照写的单按原来的写入路径重新排队。
"""
import frappe
from frappe import _

from pearnly_core.write_precheck import contracts, products

BATCH_MAX = 200  # 一次判几张（代录一批的上限）：每张都跑一遍产品的写账准备
MODES = ("operator", "direct", "pending")
# products_all：没声明过账类型的行，库存品与非库存品都列；own_products：产品自己的物料档（ERP 代录放文件识别出、还没提交的收单记录里
# 定不了的行，选已有 ERP 物料 / 新建 / 只记账，A 批 1b）
SEARCH_KINDS = ("accounts", "products", "products_all", "parties", "own_products")
PAGE_MAX = 50


def _ids(doc_ids) -> list:
    doc_ids = frappe.parse_json(doc_ids) if isinstance(doc_ids, str) else doc_ids
    if not isinstance(doc_ids, (list, tuple)):
        frappe.throw(_("Invalid request"))
    doc_ids = list(dict.fromkeys(str(d) for d in doc_ids if d))
    if not doc_ids or len(doc_ids) > BATCH_MAX:
        frappe.throw(_("Invalid request"))
    return doc_ids


def _require(company) -> None:
    if not products.allowed(frappe.session.user, company):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def card(company, doc_ids, hint="") -> dict:
    """这批单的卡片（不经白名单的调用方用：代录预检、直写门）。产品交来的数据不合契约是程序错，照抛。``hint``：用户说的费用类别
    （CC 传，只影响科目建议，`products.check`）。"""
    found = products.check(company, doc_ids, hint)
    documents = found.get("documents") or []
    merged = contracts.merge(documents)
    out = {"company": company, "company_title": str(found.get("company_title") or company or ""), **merged,
           "needs_card": contracts.needs_card(merged)}
    planned = {str(doc["doc_id"]): doc["planned"] for doc in documents
               if isinstance(doc, dict) and doc.get("doc_id") and doc.get("planned")}
    if planned:
        out["planned"] = planned
    return out


@frappe.whitelist(methods=["POST"])
def check(company, doc_ids, mode="direct"):
    _require(company)
    if mode not in MODES:
        frappe.throw(_("Invalid request"))
    return {**card(str(company or ""), _ids(doc_ids)), "mode": mode}


@frappe.whitelist(methods=["GET"])
def search(company, doc_id, search, txt="", start=0, page_length=20):
    """大列表的可选值（服务端分页）：``search`` 是 item 上给的搜索类型，``doc_id`` 定账套；产品再按这张单核读取权限。"""
    _require(company)
    try:
        start, page_length = max(0, int(start or 0)), int(page_length or 20)
    except (TypeError, ValueError):
        frappe.throw(_("Invalid request"))
    if search not in SEARCH_KINDS or not doc_id or not 1 <= page_length <= PAGE_MAX:
        frappe.throw(_("Invalid request"))
    return products.search(str(company or ""), str(doc_id), search, str(txt or "")[:100], start, page_length)


@frappe.whitelist(methods=["POST"])
def answer(company, doc_ids, answers, resume=0):
    _require(company)
    return record(str(company or ""), _ids(doc_ids), answers, str(resume) in ("1", "true", "True"))


def record(company, doc_ids, answers, resume=False) -> dict:
    """作答（白名单 ``answer`` 与 CC 工具 ``pearnly_precheck_answer`` 共用）。调用方已过 :func:`_require`。"""
    answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if not isinstance(answers, list):
        frappe.throw(_("Invalid request"))
    before = card(company, doc_ids)
    expanded, errors = contracts.expand(before, answers)
    if errors:
        return {**before, "errors": errors}
    products.remember(company, expanded)
    # 先落库：重判是「只是看」，产品判完会整笔回滚，不提交的话刚记下的答案也跟着回滚（11-05 实验室流程查出）
    frappe.db.commit()
    writable, held = contracts.writable(before, answers), contracts.held_back(before, answers)
    results = products.resume(company, writable) if resume else []
    after = card(company, doc_ids)
    out = {**after, "writable": writable, "held": held}
    if results:
        out["results"] = results
    return out


# ── CC 工具（共用层 `assistant.tools.CORE_TOOLS` 声明）：开始写账前取要人定的项、用户在提问卡片里答完后记下 ──


def _tool_ids(tool_input) -> tuple:
    company = str((tool_input or {}).get("company") or "")
    _require(company)
    return company, _ids((tool_input or {}).get("doc_ids"))


def tool_check(tool_input) -> str:
    import json

    company, doc_ids = _tool_ids(tool_input)
    mode = (tool_input or {}).get("mode") or "direct"
    if mode not in MODES:
        frappe.throw(_("Invalid request"))
    found = card(company, doc_ids, str((tool_input or {}).get("hint") or ""))
    return json.dumps({**found, "mode": mode}, ensure_ascii=False, default=str)


def tool_answer(tool_input) -> str:
    import json

    company, doc_ids = _tool_ids(tool_input)
    found = record(company, doc_ids, (tool_input or {}).get("answers"), bool((tool_input or {}).get("resume")))
    return json.dumps(found, ensure_ascii=False, default=str)
