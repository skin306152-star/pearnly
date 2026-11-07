"""明细补读（2026-11-07，评估见 docs/work/2026-11-07-claude-ocr-missing-page-lines-评估.md 第 10 节）。

起因：BJC 12 页采购发票里两张票面印「หน้า 1/2」、第 2 页不在文件里的票，Opus 整段重读 6 次有 3 次把它们的明细整块省掉
（表头、金额都读对了）。改提示词三版都能修好它，但每版都让「附凭证页的文件拆不拆票」这类边界判断跟着动（6900205 等），
所以不动读法：每段读完、收尾之后，发票类结果里明细为空的票，只把这张票的页再交模型读一次明细。

- 只补 ``lines``：表头、金额、``settled``、``why_after`` 都不动；读法提示词、升级策略、合并逻辑都不变。
- 宁空勿错：补出的行金额（净额）之和要对上票面已读出的未税小计或含税合计；三个金额都没读出（缺页票只有本页小计）时，对上补读
  同时读出的本页小计。对不上就不写回，阶段记录里写原因。单行算术（``line_math``）只记录、不单独否决。
- 页：这张票在本段里从它的起始页到下一张票起始页之前（同一页上还有别的票就只给这一页）；一次读超限就逐页补读、按页序拼起来。
- 先 Haiku；Haiku 读出了行却对不上（BJC：每行两层折扣，Haiku 拿毛额当行金额）才交 Opus 再补一次，同样对上才写回；
  Haiku 回答「没印明细」不升级（主会话 11-07 定）。补读调用照计费用，进 ``stages``（``purpose: lines``），产品不把它算成升级。

纯计算 + 一个外面传进来的 ``call``（``engine._call`` 的同形函数），不导入 Frappe。
"""
import copy
from decimal import Decimal

from pearnly_core.claude_ocr import consistency, spec
from pearnly_core.tax_documents.amounts import money

PURPOSE = "lines"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["lines", "printed_subtotal"],
          "properties": {"lines": {"type": "array", "items": spec.LINE}, "printed_subtotal": {"type": "string"}}}
ASK = ("Transcribe ONLY the item lines printed for {which} on the pages above, in order, following the lines rules above. "
       "printed_subtotal: the subtotal printed for these lines on these pages (a page subtotal, a carried-forward subtotal, or the "
       "invoice subtotal before VAT), as printed; empty if none is printed. If no item lines are printed for this invoice on these "
       "pages, return an empty lines list. Never invent, merge or compute lines.")


def wanted(read):
    """要补读的票：发票类、录、明细为空。返回序号列表。"""
    if not read or read.get("entry") != "enter":
        return []
    return [i for i, doc in enumerate(read.get("documents") or []) if isinstance(doc, dict) and not (doc.get("lines") or [])]


def _start(doc, count):
    page = doc.get("page")
    return page if type(page) is int and 1 <= page <= count else 1


def span(read, index, count):
    """第 ``index`` 张票在本段 ``count`` 页里占哪几页：``(首页, 末页)``，从 1 数。"""
    docs = read.get("documents") or []
    first = _start(docs[index], count)
    others = [_start(doc, count) for i, doc in enumerate(docs) if i != index and isinstance(doc, dict)]
    if first in others:
        return first, first  # 同一页上几张票：只给这一页
    later = [page for page in others if page > first]
    return first, (min(later) - 1 if later else count)


def _sum(lines):
    """行金额之和；印了单价却没读出金额的行算缺（返回 None），没单价也没金额的行（赠品）不计，同 ``consistency``。"""
    rows = [(money(line.get("amount")), money(line.get("unit_price"))) for line in lines]
    priced = [amount for amount, price in rows if amount is not None or price]
    return None if not priced or any(amount is None for amount in priced) else sum(priced, Decimal(0))


def _matches(value, printed):
    target = money(printed)
    return value is not None and target is not None and consistency._near(value, target)


def judge(doc, parts):
    """``parts``：按页序的 ``[(lines, printed_subtotal)]``（一次读完就只有一份）。返回 ``(lines 或 None, 原因)``。

    写回与否只看「行金额（净额）之和」对不对得上票面印的金额：已读出的未税小计 / 含税合计，三个金额都没读出（缺页票）时对上
    补读读出的本页小计（分页补读时每页对上各自的小计，或末页印的累计小计）。单行的 ``line_math`` 只记进原因、不单独否决
    （主会话 11-07：BJC 每行两层折扣，单行算术不一定照我们的式子成立，以加总为准）。"""
    lines = [line for part, _printed in parts for line in part if isinstance(line, dict)]
    if not lines:
        return None, "no lines printed"
    bad = consistency._line_math(0, lines)
    note = f"; line_math differs on {len(bad)} line(s)" if bad else ""
    total = _sum(lines)
    if total is None:
        return None, "a priced line has no amount" + note
    _problems, side = consistency._lines_vs_document(0, doc, lines)
    if side is not None:
        return lines, f"matches the {side} amount" + note
    if any(money(doc.get(field)) is not None for field in ("subtotal", "total")):
        return None, f"lines add up to {total:.2f}, not the printed amounts" + note
    if _matches(total, parts[-1][1]):
        return lines, "matches the printed page subtotal" + note
    if len(parts) > 1 and all(_matches(_sum(part), printed) for part, printed in parts):
        return lines, "each page matches its printed subtotal" + note
    return None, f"lines add up to {total:.2f}, no printed subtotal to match" + note


def _ask(call, model, pages, ask):
    """补读调用出错（限流、网络）不让整段识别失败重读：记一条阶段，这张票保持原样（与没有补读时相同）。"""
    try:
        return call(model, pages, ask)
    except Exception as error:  # noqa: BLE001  主读法已付费、已读完；补读失败只是这张票不补
        return None, {"model": model, "seconds": 0, "usd": 0, "stop_reason": None, "usage": {},
                      "error": f"{type(error).__name__}: {str(error)[:200]}"}


def _attempt(call, model, doc, pages, first, ask):
    """一个模型补读一次（超限就逐页）：返回 ``(lines 或 None, 原因, 是否读出了行, 阶段列表)``。"""
    answer, stage = _ask(call, model, pages, ask)
    stages = [dict(stage, fill_pages=[first, first + len(pages) - 1])]
    parts = None
    if answer is not None:
        parts = [(answer.get("lines") or [], answer.get("printed_subtotal"))]
    elif len(pages) > 1 and stage.get("stop_reason") == "max_tokens":
        parts = []
        for number, page in enumerate(pages, first):
            part, part_stage = _ask(call, model, [page], ask)
            stages.append(dict(part_stage, fill_pages=[number, number]))
            if part is None:
                parts = None
                break
            parts.append((part.get("lines") or [], part.get("printed_subtotal")))
    if parts is None:
        return None, "no answer", False, stages
    lines, why = judge(doc, parts)
    return lines, why, any(part for part, _printed in parts), stages


def fill(call, read, images, models):
    """补读 ``read`` 里明细为空的票（原地改 ``lines``）。``call(model, images, ask)`` → ``(结果, 阶段)``（``engine`` 用同一份发票
    系统提示与本模块的 ``SCHEMA``）。``models`` 依次试：前一个读出了行却对不上（不是「没印明细」）才交下一个（主会话 11-07：
    先 Haiku，被拒才 Opus）。返回补读的阶段列表。"""
    stages = []
    for index in wanted(read):
        doc = read["documents"][index]
        first, last = span(read, index, len(images))
        number = doc.get("invoice_number")
        ask = ASK.format(which=f"the invoice numbered {number}" if number else "the invoice that starts on the first page above")
        for model in models:
            lines, why, read_some, tried = _attempt(call, model, doc, images[first - 1:last], first, ask)
            note = f"documents[{index}] {model}: " + (f"+{len(lines)} lines ({why})" if lines is not None else f"not filled: {why}")
            stages += [dict(stage, purpose=PURPOSE, note=note) for stage in tried]
            if lines is not None:
                doc["lines"] = copy.deepcopy(lines)
                break
            if not read_some:
                break  # 没印明细 / 没回答：换模型也是白花钱
    return stages


def reading_stages(stages):
    """识别阶段（不含补读）：产品的层级链、升级模型、模型名只看这些。"""
    return [stage for stage in stages or [] if stage.get("purpose") != PURPOSE]
