"""通用提问工具 ``ask_user``（共用层，主会话 11-05，照 Claude Code 的 AskUserQuestion）：CC 凭当前问题出一张提问卡片，卡片的样子固定、
内容由 CC 定（用户：「卡片是固定，内容根据问题弹」）。开始写账前，CC 先调 ``pearnly_precheck``（`write_precheck`）拿要人定的项，
组织成题目；也可以凭自己的判断加题。

- 走本站「等人作答」那一套（`toolrun`）：工具声明 ``approval``，调用登记成 awaiting_approval，答案随对话里的确认
  （`api.confirm` 的确认项带 ``answers``）交回，落进执行记录、跨设备一致，页面重开还在；结果经 ``user.custom_tool_result`` 交回模型。
- 出卡片前按 :func:`problem` 核题目形状（`tools.refusal`），不合的直接把原因交回模型，不出卡片。
- 交回模型的结果 :func:`result`：``{"answers": [{header, question, labels, other?, skipped?, not_asked?}]}``，按题目顺序。
- 题目可带 ``show_if`` ``{question: 前面第几题（从 1 起）, labels: [那一题的选项]}``：那一题选中其中之一才问（测试机 11-09：选了
  「按服务录入」还问从哪个仓库出库）；不成立时这一题标 ``not_asked``，页面交来的答案不算数。

纯函数（不导入 Frappe）。
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Mapping, Optional

TOOL = "ask_user"
QUESTIONS_MIN, QUESTIONS_MAX = 1, 4
OPTIONS_MIN, OPTIONS_MAX = 2, 4
HEADER_MAX = 12
TEXT_MAX = 500
OTHER_MAX = 500
SEARCH_KINDS = ("accounts", "products", "products_all", "parties")  # products_all：没声明过账类型的行，库存品与非库存品都列


class AnswerError(ValueError):
    """页面交来的答案不合这张卡片（题数、选项对不上）：不记、不交回模型。"""


def _text(value, limit=TEXT_MAX) -> str:
    return str(value if value is not None else "").strip()[:limit]


def problem(tool_input: Mapping[str, Any]) -> Optional[str]:
    """题目形状不合时交回模型的话（英文，给模型看），合了回 None。"""
    questions = tool_input.get("questions") if isinstance(tool_input, Mapping) else None
    if not isinstance(questions, list) or not QUESTIONS_MIN <= len(questions) <= QUESTIONS_MAX:
        return f"ask_user needs {QUESTIONS_MIN}-{QUESTIONS_MAX} questions."
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, Mapping) or not _text(question.get("question")):
            return f"Question {index} needs question text."
        header = _text(question.get("header"), 100)
        if not header or len(header) > HEADER_MAX:
            return f"Question {index} needs a header of at most {HEADER_MAX} characters."
        options = question.get("options")
        if not isinstance(options, list) or not OPTIONS_MIN <= len(options) <= OPTIONS_MAX:
            return f"Question {index} needs {OPTIONS_MIN}-{OPTIONS_MAX} options (the card adds Other itself)."
        labels = [_text(option.get("label"), 100) if isinstance(option, Mapping) else "" for option in options]
        if not all(labels) or len(set(labels)) != len(labels):
            return f"Question {index} needs distinct option labels."
        condition = question.get("show_if")
        if condition is not None:
            earlier = condition.get("question") if isinstance(condition, Mapping) else None
            wanted = condition.get("labels") if isinstance(condition, Mapping) else None
            if (not isinstance(earlier, int) or isinstance(earlier, bool) or not 1 <= earlier < index
                    or not isinstance(wanted, list) or not wanted):
                return f"Question {index}: show_if needs question (an earlier question number) and labels."
            offered = [_text(option.get("label"), 100) for option in questions[earlier - 1].get("options") or ()
                       if isinstance(option, Mapping)]
            if any(_text(label, 100) not in offered for label in wanted):
                return f"Question {index}: show_if labels must be options of question {earlier}."
        search = question.get("search")
        if search is not None and (not isinstance(search, Mapping) or search.get("kind") not in SEARCH_KINDS
                                   or not _text(search.get("company")) or not _text(search.get("doc_id"))):
            return f"Question {index}: search needs company, doc_id and kind ({' / '.join(SEARCH_KINDS)})."
    return None


def result(tool_input: Mapping[str, Any], answers) -> str:
    """页面交来的答案 → 交回模型的结果（JSON 文本）。``answers``：``[{labels?, other?, skipped?}]`` 按题目顺序（也收
    ``{"answers": [...]}``）。选的 label 要在这一题的选项里；单选最多一个；「其他」是用户打的字或搜到的「编码 名称」；没选也没打字的
    算跳过（这一项不定，依赖它的单这次不录）。对不上抛 :class:`AnswerError`。"""
    questions = list(tool_input.get("questions") or ())
    if isinstance(answers, Mapping):
        answers = answers.get("answers")
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise AnswerError("answers must match the questions")
    out: List[dict] = []
    for question, answer in zip(questions, answers):
        if not isinstance(answer, Mapping):
            raise AnswerError("each answer must be an object")
        if not _shown(question, out):
            out.append({"header": _text(question.get("header"), HEADER_MAX), "question": _text(question.get("question")),
                        "labels": [], "not_asked": True})
            continue
        offered = [_text(option.get("label"), 100) for option in question.get("options") or ()]
        labels = [_text(label, 100) for label in answer.get("labels") or () if _text(label, 100)]
        if any(label not in offered for label in labels) or len(set(labels)) != len(labels):
            raise AnswerError("label not offered")
        if not question.get("multiSelect") and len(labels) > 1:
            raise AnswerError("single choice")
        other = _text(answer.get("other"), OTHER_MAX)
        row = {"header": _text(question.get("header"), HEADER_MAX), "question": _text(question.get("question")),
               "labels": labels}
        if other:
            row["other"] = other
        if answer.get("skipped") or (not labels and not other):
            row = {**row, "labels": [], "skipped": True}
            row.pop("other", None)
        out.append(row)
    return json.dumps({"answers": out}, ensure_ascii=False)


def _shown(question: Mapping[str, Any], earlier: List[dict]) -> bool:
    """``show_if`` 成立没有（按已整理好的前面几题）；没有条件的总问。"""
    condition = question.get("show_if")
    if not isinstance(condition, Mapping):
        return True
    at = condition.get("question")
    if not isinstance(at, int) or not 1 <= at <= len(earlier):
        return True
    wanted = {_text(label, 100) for label in condition.get("labels") or ()}
    return bool(wanted & set(earlier[at - 1].get("labels") or ()))


def unanswered(tool_input: Mapping[str, Any]) -> str:
    """工具声明要的执行函数：提问卡片不经执行（答案由 `toolrun.decide` 交回），万一被直接执行就照「每题跳过」交回。"""
    return skipped(tool_input)


def skipped(tool_input: Mapping[str, Any]) -> str:
    """整张卡片被关掉（拒绝）：每题都算跳过。"""
    return result(tool_input, [{"skipped": True} for _ in tool_input.get("questions") or ()])


# 卡片挂着时用户没在卡片上作答、直接在输入框里发了消息（`toolrun.replied`）交回模型的说明：只给模型读，不经 _()
REPLIED_INSTRUCTION = ("The user did not answer on the card. They sent a message instead: read their next message as the "
                       "reply to these questions. Do not ask the same questions again unless that message leaves them open.")


def replied(tool_input: Mapping[str, Any]) -> str:
    """卡片挂着时用户直接发了消息：每题没选（同关掉卡片，页面照旧读成答过、各题跳过），另写明回答在下一条用户消息里。"""
    data = json.loads(skipped(tool_input))
    return json.dumps({**data, "replied_in_message": True, "instruction": REPLIED_INSTRUCTION}, ensure_ascii=False)


def picked(result_text) -> List[str]:
    """一张答完的卡片（`result` 的 JSON 文本）里用户选的与在「其他」里打的字，按题目顺序；跳过的题不算。读不出回空表。"""
    try:
        data = json.loads(result_text or "{}")
    except (TypeError, ValueError):
        return []
    out: List[str] = []
    for row in (data.get("answers") or []) if isinstance(data, Mapping) else []:
        if not isinstance(row, Mapping) or row.get("skipped"):
            continue
        out.extend(_text(label, 100) for label in row.get("labels") or () if _text(label, 100))
        if _text(row.get("other"), OTHER_MAX):
            out.append(_text(row.get("other"), OTHER_MAX))
    return out


def _fold(value) -> str:
    return " ".join(str(value if value is not None else "").split()).casefold()


# 选项写成「名称 · 税号」「名称（税号）」「名称 - 分店」时只取名称那一段比
_HEAD = re.compile(r"\s+[·|\-–—]\s+|\s*[(（]")


def names_one_of(texts, names) -> bool:
    """用户的作答（`picked`）里有没有一条就是这一项：整条（或前面的名称段）等于 ``names`` 里的某一个（编号、名称、税号），
    比较时不分大小写、空白归一。只认整段相等，不认包含（名称是另一家的前缀时不会认错）。"""
    wanted = {_fold(name) for name in names or () if _fold(name)}
    for text in texts or ():
        heads = {_fold(text), _fold(_HEAD.split(str(text), 1)[0])}
        if heads & wanted:
            return True
    return False


__all__ = ["AnswerError", "HEADER_MAX", "OPTIONS_MAX", "OPTIONS_MIN", "QUESTIONS_MAX", "QUESTIONS_MIN", "SEARCH_KINDS",
           "TOOL", "names_one_of", "picked", "problem", "result", "skipped", "unanswered"]
