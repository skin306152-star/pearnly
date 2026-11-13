"""写账前置判定的接口（多产品共用）：「开始前确认」卡片取数与作答。产品按 ``products`` 的钩子接入；三个接口先过产品的
``allowed``（这家公司能不能写账），产品在钩子里再按单据核。

- ``check``：这批单此刻要人定的项，按业务分组、跨单去重，每项已定 / 预选 / 未定（``contracts.merge``），另带 ``needs_card``
  （直写全部已定就不出卡片，主会话 11-04 Q1）与 ``planned``（``{doc_id: 产品给的「此刻写进去会怎么写」}``，只有什么都不用定的单才有，
  CC 推前说现 / 赊、科目照它）；只看不写。``mode``：operator / direct / pending（谁来问，原样带回）。
  问什么由 CC 定：拿这份事实与预选，用通用提问工具 ``ask_user`` 出题（主会话 11-05），推荐项排第一（``recommended``）。
- ``search``：大列表的项（科目、存货、对方）按这张单写进的账套服务端分页取可选值。
- ``answer``：卡片点「开始」交回的答案 ``[{key, value}]``：按点之前那张卡片展开到每张单，交产品按现有记法记下；有答错的整批不记、
  逐条回原因码。回记下后重判的卡片，带 ``writable``（照写的单）与 ``held``（因未定项留下的单）。``resume`` 为真时（待处理里的
  卡片：定时任务、自动推停下的单）再交产品把照写的单按原来的写入路径重新排队；CC 对话里只记答案、不推（写入只走计划，B1）。
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
    # 每张单判出的方向（采购 / 费用 / 销售）：引导卡按会话业务比对（选了采购却传了销售票，`assistant.guide_tool`）
    directions = {str(doc["doc_id"]): str(doc.get("direction") or "") for doc in documents
                  if isinstance(doc, dict) and doc.get("doc_id") and doc.get("direction")}
    if directions:
        out["directions"] = directions
    planned = {str(doc["doc_id"]): doc["planned"] for doc in documents
               if isinstance(doc, dict) and doc.get("doc_id") and doc.get("planned")}
    if planned:
        out["planned"] = planned
    return out


@frappe.whitelist(methods=["POST"])
def check(company, doc_ids, mode="direct"):
    """只读的判定：撞上快照冲突（1020，产品锁着连接读时别的请求刚改了它）或死锁就整笔回滚、退避重来（11-08 生产：一次 500），
    重来几次仍不行回一句「请稍后重试」，不是 500。"""
    _require(company)
    if mode not in MODES:
        frappe.throw(_("Invalid request"))
    ids = _ids(doc_ids)
    for _attempt in range(CHECK_ATTEMPTS):
        try:
            found = card(str(company or ""), ids)
            break
        except frappe.QueryDeadlockError:
            frappe.db.rollback()  # 结束这次的读快照，重来时读到别的请求刚提交的
    else:
        frappe.throw(_("Please try again later"))
    return {**found, "mode": mode}


CHECK_ATTEMPTS = 3


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


def record(company, doc_ids, answers, resume=False, guided=False) -> dict:
    """作答（白名单 ``answer`` 与 CC 工具 ``pearnly_precheck_answer`` 共用）。调用方已过 :func:`_require`。

    ``guided``：CC 引导卡（``assistant.guide_tool``）交来的答案——卡上没点过的预选不生效（不补记 :data:`KEPT_PROPOSALS`），
    哪些单能写、哪些留下按引导卡口径（``guide.writable`` / ``held_back``）；网页待处理、LINE 照旧。"""
    from pearnly_core.write_precheck import guide

    answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if not isinstance(answers, list):
        frappe.throw(_("Invalid request"))
    before = card(company, doc_ids)
    if not guided:
        answers = [*answers, *_kept_proposals(before, answers)]
    expanded, errors = contracts.expand(before, answers)
    if errors:
        return {**before, "errors": errors}
    products.remember(company, expanded)
    # 先落库：重判是「只是看」，产品判完会整笔回滚，不提交的话刚记下的答案也跟着回滚（11-05 实验室流程查出）
    frappe.db.commit()
    rules = guide if guided else contracts
    writable, held = rules.writable(before, answers), rules.held_back(before, answers)
    planned = resume and _in_conversation()
    results = products.resume(company, writable) if resume and not planned else []
    after = card(company, doc_ids)
    out = {**after, "writable": writable, "held": held}
    if results:
        out["results"] = results
    if planned:
        out["note"] = RESUME_PLAN_NOTE
    return out


# B1（A 线 11-12）：CC 对话里 resume 只记答案、不推（写入一律走写入计划）；只给模型
RESUME_PLAN_NOTE = ("The answers were saved; nothing was written (resume does not write in a conversation). To write these "
                    "documents, make the write plan (pearnly_write_plan), say it back and wait for the user's reply.")


def _in_conversation() -> bool:
    """CC 此刻在对话里执行工具（网页待处理的卡片不在对话里，照旧 resume）。"""
    return bool((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id"))


# 整家公司一个口径、不随单据变的项：交答案时没单独回答、带着预选值的，就按预选值记住（照这个值写进去了，下次不再当作要确认的
# 预选出现；11-08 生产：入账方式每次对话都出现、答了也没记住）。别的预选项（物料新建、科目建议……）只用在这一次，不记
KEPT_PROPOSALS = ("goods_posting",)


def _kept_proposals(before, answers) -> list:
    answered = {str(a.get("key") or "") for a in answers if isinstance(a, dict)}
    return [{"key": item["key"], "value": item["value"]} for item in contracts.items_of(before)
            if item.get("kind") in KEPT_PROPOSALS and item.get("state") == "proposed" and item.get("value")
            and item.get("key") not in answered]


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
    # CC 对话里口头作答与引导卡同一口径：没答的预选不生效（引导式录入第 1 期）
    found = record(company, doc_ids, (tool_input or {}).get("answers"), bool((tool_input or {}).get("resume")),
                   guided=_in_conversation())
    return json.dumps(found, ensure_ascii=False, default=str)
