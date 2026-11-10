"""CC 只写对话选中的公司与账套（用户 11-10：「CC 或模型录进别的账套就是产品缺陷」；11-10 线上压测 S7 CC 说要录进卖方的真账套）。

凡是 CC 在对话里发起的账本写入（识别时带推送目标、推送、待选账套当场选、选现用账套、POS 导出、文件夹任务的去向），
写之前由服务端核「这份账套是本对话挂的那家公司的」：本对话公司 → `companies.service.work_sets`（各年度、各分店；对话指定了
分店只算该分店的，同给 CC 的公司说明）→ 允许的（连接, 账套键）。不是 → 拒，原因交回模型照实说明。不靠提示词。

代录（任务的公司明传 ``company``）与直写同一判据。不在 CC 对话里执行（网页待处理 / 复核页由人亲手选的、定时任务运行时）不限。
MR.ERP 的账套键（``comidyear:seldb``）同样在 `work_sets` 里，同一判定覆盖。
"""
import frappe

from pearnly_core.assistant import projects
from pearnly_core.express_rules.targets.identity import normalize_express_account_key

NOT_THIS_COMPANY = "not_this_company_book"
NO_COMPANY = "no_company"
SHOWN_MAX = 10

# 给模型看的说明（只给模型，不经 _()）
NOTES = {
    NOT_THIS_COMPANY: ("not_this_company_book: this account set does not belong to the company this conversation is "
                       "filed under, so nothing was written to it. Write only to that company's account sets "
                       "(allowed_account_sets). If the document is not this company's, say so in one sentence."),
    NO_COMPANY: ("no_company: this conversation is not filed under a company, so nothing was written. Ask the user to "
                 "choose the company for this conversation first."),
}


def key_of(value) -> str:
    """账套键比较用的写法（同 Express 键的归一：斜杠、结尾分隔符、大小写；MR.ERP 键只按大小写）。"""
    return normalize_express_account_key(value).casefold()


def _session() -> str:
    return str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")


def _company_sets(company, work_scope="") -> list:
    from pearnly_cowork.companies import service

    return service.work_sets(company, work_scope)


def allowed(company=None) -> dict | None:
    """这次写入允许的账套：``{"company", "sets", "pairs"}``；对话没挂公司 ``{"company": "", "sets": [], "pairs": set()}``；
    没给 ``company`` 又不在 CC 对话里执行回 None（不限）。账套目录由 `companies.service` 的请求内缓存兜着，这里每次现算。"""
    if company:
        sets = _company_sets(str(company))
        return {"company": str(company), "sets": sets, "pairs": _pairs(sets)}
    session = _session()
    if not session:
        return None
    filed, _period, work_scope = projects.of_session(session)
    sets = _company_sets(filed, work_scope) if filed else []
    return {"company": filed or "", "sets": sets, "pairs": _pairs(sets)}


def _pairs(sets) -> set:
    return {(str(item.get("connection") or ""), key_of(item.get("key"))) for item in sets or ()}


def check(connection, account_set_key, *, company=None) -> dict | None:
    """要写的（连接, 账套）在不在允许里：在、或不受限回 None；不在回 ``{"reason", "note", "company",
    "allowed_account_sets"}``（只给模型）。账套没定（空）的不在这里判，由各写入点照旧停下要人选。"""
    found = allowed(company)
    if found is None:
        return None
    if not found["company"]:
        return refusal(found, NO_COMPANY)
    if not account_set_key:
        return None
    if (str(connection or ""), key_of(account_set_key)) in found["pairs"]:
        return None
    return refusal(found, NOT_THIS_COMPANY)


def refusal(found, reason) -> dict:
    return {"reason": reason, "note": NOTES[reason], "company": found["company"],
            "allowed_account_sets": [{"connection": item.get("connection"), "key": item.get("key"),
                                      "label": item.get("label")} for item in (found["sets"] or [])[:SHOWN_MAX]]}


def keep(options, connection_of=None, company=None) -> list | None:
    """候选账套里只留允许的（``options``：``[{key, ...}]``，连接取 ``connection_of(item)`` 或 item 的 ``connection``）；
    不受限、或对话没挂公司（写入照样由 `check` 拒，候选只是给人看的，不清空）回 None，调用方照旧用原候选。"""
    found = allowed(company)
    if found is None or not found["company"]:
        return None
    pick = connection_of or (lambda item: item.get("connection"))
    return [item for item in options or () if (str(pick(item) or ""), key_of(item.get("key"))) in found["pairs"]]


__all__ = ["NOT_THIS_COMPANY", "NO_COMPANY", "allowed", "check", "keep", "key_of", "refusal"]
