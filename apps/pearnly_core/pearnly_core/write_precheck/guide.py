"""引导卡：由后台出题的一张卡（CC 引导式录入第 1 期，多产品共用，纯函数；评估 ``docs/work/2026-11-12-cc-guided-entry.md`` 6.2、12 节）。

输入是 :func:`contracts.merge` 合成的卡片（产品按 ``express_rules.push.choices.resolve(guided=True)`` 逐张判好、交给 ``merge``），
输出是页面直接渲染、直接作答的题目；模型不出业务题、不拼选项、不映射编码。

三档（用户 11-12 批准）：

- **已定**（:data:`contracts.DECIDED` 状态、且不是「做不做」类）：不问，收进 ``decided``（「已定 N 项」可展开改）；
- **怎么录**（:data:`HOW`）：推荐项排第一、标 ``recommended``、``preselected`` 给值，但**要用户点了才算**；
- **做不做**（:data:`WHETHER`，:data:`WHETHER_KINDS`）：不预选、不标推荐，必须明选；后台给了值也照问。

「卡上没点过的预选项不生效」：:func:`held_back` / :func:`writable` 把没答的 HOW 题与 OPEN 题同样算留下（共用的
``contracts.held_back`` 是「预选没答照写」的旧口径，LINE 一键确认、网页待处理仍用它）。

文字由服务端按本轮语言出：这里每题给 ``title_key`` + ``title_params``、每个固定值选项给 ``label_key``，接口层按产品术语表翻译
（同一组键四语齐全）；选项是主档（对方档、存货、科目、仓）的照带 ``name``。

不依赖 Frappe、不读库、不做 IO。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from pearnly_core.write_precheck import contracts as c

DECIDED, HOW, WHETHER = "decided", "how", "whether"
TIERS = (DECIDED, HOW, WHETHER)
# 「做不做」：录不录、按哪种业务录、写哪本账套、明细对不上怎么录、出库没有成本基础怎么录（覆盖表 D-2 不预选）
WHETHER_KINDS = frozenset({"document_policy", "direction", "account_set", "current_book", "detail", "cost_basis"})
NEW_OPTION_KEY = "guide.option.new"
VERSION = 1


def tier_of(item: Mapping[str, Any]) -> str:
    if item.get("kind") in WHETHER_KINDS:
        return WHETHER
    return DECIDED if item.get("state") == c.DECIDED else HOW


def _option(kind: str, option: Mapping[str, Any], recommend: bool) -> Dict[str, Any]:
    code = str(option.get("code") or "")
    out: Dict[str, Any] = {"code": code}
    if recommend and option.get("recommended"):
        out["recommended"] = True
    for field in ("name", "branch", "note"):
        if option.get(field):
            out[field] = option[field]
    if code == "__new__":
        out["label_key"] = NEW_OPTION_KEY
    elif not option.get("name"):
        out["label_key"] = f"guide.option.{kind}.{code}"
    return out


def _question(item: Mapping[str, Any]) -> Dict[str, Any]:
    tier = tier_of(item)
    recommend = tier == HOW
    options = [_option(item["kind"], option, recommend) for option in item.get("options") or ()]
    if not recommend:
        # 「做不做」：照原顺序列，不把后台给的值顶到第一个
        order = {str(option.get("code") or ""): index for index, option in enumerate(item.get("options") or ())}
        options.sort(key=lambda option: order.get(option["code"], 0))
    question = {"key": item["key"], "kind": item["kind"], "group": item["group"], "tier": tier,
                "title_key": f"guide.q.{item['kind']}", "title_params": {"label": item.get("label") or "",
                                                                       "count": len(item.get("docs") or ())},
                "docs": list(item.get("docs") or ()), "count": len(item.get("docs") or ()),
                "options": options, "preselected": item.get("value") or "" if recommend else "",
                "remember": bool(item.get("remember"))}
    if item.get("search"):
        question["search"] = item["search"]
    if item.get("when"):
        question["when"] = dict(item["when"])
    return question


def build(card: Mapping[str, Any]) -> Dict[str, Any]:
    """卡片 → 引导卡 ``{version, questions, decided, facts, targets, counts}``。

    ``questions`` 照卡片的分组与组内顺序（未定 → 预选），每题 ``{key, kind, group, tier, title_key, title_params, docs, count,
    options, preselected, remember, search?, when?}``；``decided`` 每项 ``{key, kind, group, label, value, value_label, source,
    source_key}``；``facts`` 是结果不明 / 写不了 / 等电脑的单（只列事实，不出题）。"""
    questions: List[Dict[str, Any]] = []
    decided: List[Dict[str, Any]] = []
    for item in c.items_of(card):
        if tier_of(item) == DECIDED:
            decided.append({"key": item["key"], "kind": item["kind"], "group": item["group"], "label": item.get("label") or "",
                            "value": item.get("value") or "", "value_label": item.get("value_label") or "",
                            "source": item.get("source") or "", "source_key": f"guide.source.{item.get('source') or ''}",
                            "docs": list(item.get("docs") or ()),
                            # 「已定 N 项」展开可改：有小列表的带上选项（同题目的选项写法，不标推荐）
                            **({"options": [_option(item["kind"], option, False) for option in item["options"]]}
                               if item.get("options") else {})})
        else:
            questions.append(_question(item))
    return {"version": VERSION, "questions": questions, "decided": decided,
            "facts": {"unknown": list(card.get("unknown") or ()), "not_writable": list(card.get("not_writable") or ()),
                      "waiting": list(card.get("waiting") or ())},
            "targets": list(card.get("targets") or ()),
            "counts": {"questions": len(questions), "decided": len(decided)}}


def needs_card(guide: Mapping[str, Any]) -> bool:
    """要不要投引导卡：有题，或有结果不明的单（要人核对）。全部已定就不出卡。"""
    return bool(guide.get("questions")) or bool((guide.get("facts") or {}).get("unknown"))


def _answered(answers) -> set:
    return {(str(a.get("key") or ""), str(a.get("doc_id") or "").strip()) for a in answers or ()
            if isinstance(a, Mapping) and str(a.get("value") or "").strip()}


def held_back(card: Mapping[str, Any], answers: Iterable[Mapping[str, Any]] = ()) -> List[str]:
    """引导卡口径：没答的题（未定、预选、「做不做」类都算）它的单留下；已定（非「做不做」类）不用答。"""
    answers = list(answers or ())
    answered = _answered(answers)
    by_key = {item["key"]: item for item in c.items_of(card)}
    held: List[str] = []
    for item in c.items_of(card):
        if tier_of(item) == DECIDED or (item["key"], "") in answered or not c.applies(item, by_key, answers):
            continue
        held.extend(doc_id for doc_id in item["docs"] if doc_id not in held and (item["key"], doc_id) not in answered)
    return held


def writable(card: Mapping[str, Any], answers: Iterable[Mapping[str, Any]] = ()) -> List[str]:
    """答完这些后照写的单（判过的单去掉留下的）。"""
    held = set(held_back(card, answers))
    docs: List[str] = list(card.get("ready") or ())
    for item in c.items_of(card):
        docs.extend(doc_id for doc_id in item["docs"] if doc_id not in docs)
    return [doc_id for doc_id in docs if doc_id not in held]


__all__ = ["DECIDED", "HOW", "NEW_OPTION_KEY", "TIERS", "VERSION", "WHETHER", "WHETHER_KINDS", "build", "held_back",
           "needs_card", "tier_of", "writable"]
