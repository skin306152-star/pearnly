"""长文件分段读（纯计算，2026-11-03）：一次调用最多 ``engine.PAGES_PER_CALL`` 页，超过的文件按段读完再合并成一份结果。

分段不用固定重叠：下一段从「本段读出的最后一张单的起始页」开始重读，这张单的续页（不超过一段的页数）就和它的开头落在同一段里，
由模型按读法规则（跨页算一张、记在第一页）读成一张；本段里从那页起的单丢弃，以下一段为准。本段没有单、或所有单都从本段第一页
开始（一张单本身超过一段，或一张单后面跟附件页）时，下一段从本段末页的下一页开始，下一段第一页上同单号的单当续页并入。
每段至少往后推进一页，必定读完；段数到 ``MAX_SEGMENTS`` 还没读完时，最后一段之后的页记进 ``pages_unread``，由产品标出
（Cowork 标「读不出」、ERP 整份拒收），不静默丢页。
不超过一段的文件只有一段，结果与原来一次调用的完全相同（:func:`merge` 原样返回）。
"""
import copy
import re

from pearnly_core.claude_ocr import engine

MAX_SEGMENTS = 16
_INDEX = re.compile(r"documents\[(\d+)\]")
# 续页并入时一起取续页的金额（合计印在最后一页）：与 ``engine.AMOUNTS`` 同一组，明细另外接在后面。
_AMOUNTS = tuple(field for field in engine.AMOUNTS if field != "lines")


def last_page(first, total):
    return min(total, first + engine.PAGES_PER_CALL - 1)


def _starts(read, first, last):
    """这一段每张单在原文件里的起始页（模型给的页号超出本段的按本段第一页，同 Cowork 原来的口径）。"""
    out = []
    for doc in (read or {}).get("documents") or []:
        page = doc.get("page")
        out.append(first + page - 1 if type(page) is int and 1 <= page <= last - first + 1 else first)
    return out


def next_first(first, last, read):
    """下一段从哪一页开始（一定大于 ``first``）。"""
    starts = _starts(read, first, last)
    begin = max(starts, default=first)
    return begin if begin > first else last + 1


def _number(doc):
    return re.sub(r"\s", "", str(doc.get("invoice_number") or "")).upper()


def _continue(target, more):
    """一张单的续页（下一段第一页上读出的同单号的单）并入：明细接在后面；续页印了合计就用续页的金额；其余空着的字段补上。"""
    target["lines"] = list(target.get("lines") or []) + list(more.get("lines") or [])
    if more.get("total") not in (None, ""):
        for field in _AMOUNTS:
            target[field] = copy.deepcopy(more.get(field))
    for field, value in more.items():
        if field not in ("page", "lines", *_AMOUNTS) and target.get(field) in (None, "") and value not in (None, ""):
            target[field] = copy.deepcopy(value)


def _renumber(texts, mapping):
    """``documents[i]`` 按合并后的序号改写；指向丢掉的单的那条去掉。"""
    out = []
    for text in texts or []:
        found = [int(i) for i in _INDEX.findall(str(text))]
        if any(mapping.get(i) is None for i in found):
            continue
        out.append(_INDEX.sub(lambda m: f"documents[{mapping[int(m.group(1))]}]", str(text)))
    return out


def merge(parts, total, unread=()):
    """``parts``：按顺序的 ``[(首页, 末页, 这段的结果)]``（结果同 ``engine.run``）→ 整份的结果，形状同 ``engine.run``，
    另加 ``page_total``，没读到的页在 ``pages_unread``。只有一段且读完了的原样返回。有一段读不出（read 为 None）整份读不出。"""
    if len(parts) == 1 and not unread:
        return parts[0][2]
    outcomes = [outcome for _first, _last, outcome in parts]
    stages = []
    for first, last, outcome in parts:
        stages += [dict(stage, pages=[first, last]) for stage in outcome.get("stages") or []]
    result = {"stages": stages, "escalated": any(o.get("escalated") for o in outcomes),
              "usd": round(sum(o.get("usd") or 0 for o in outcomes), 6),
              "seconds": round(sum(o.get("seconds") or 0 for o in outcomes), 2), "page_total": total,
              "where": "+".join(dict.fromkeys(str(o.get("where")) for o in outcomes if o.get("where")))}
    reads = [outcome.get("read") for outcome in outcomes]
    if any(read is None for read in reads):
        return {**result, "read": None, "why": ["no_result"], "pages_unread": list(unread)}
    lead = next((read for read in reads if read.get("entry") == "enter"), reads[0])
    merged = {"doc_kind": lead.get("doc_kind"), "entry": lead.get("entry"), "copy_marking": lead.get("copy_marking"),
              "documents": [], "uncertain": []}
    why, why_after, settled = [], [], []
    carry = []  # 上一段从第一页开始的单（可能延续到这一段）：(合并后的序号, 单号)
    for index, (first, last, outcome) in enumerate(parts):
        read = outcome["read"]
        starts = _starts(read, first, last)
        cut = parts[index + 1][0] if index + 1 < len(parts) else total + 1
        mapping, opened = {}, []
        for local, (doc, start) in enumerate(zip(read.get("documents") or [], starts)):
            if start >= cut:
                mapping[local] = None  # 下一段从这页起重读，以下一段的为准
                continue
            joined = next((at for at, number in carry if start == first and _number(doc) and number == _number(doc)), None)
            if joined is not None:
                _continue(merged["documents"][joined], doc)
                mapping[local] = joined
                opened.append((joined, _number(doc)))  # 还可能接着延续到再下一段
                continue
            doc = copy.deepcopy(doc)
            doc["page"] = start
            mapping[local] = len(merged["documents"])
            merged["documents"].append(doc)
            if start == first:
                opened.append((mapping[local], _number(doc)))
        carry = opened if cut == last + 1 else []
        merged["uncertain"] += _renumber(read.get("uncertain"), mapping)
        why += _renumber(outcome.get("why"), mapping)
        why_after += _renumber(outcome.get("why_after"), mapping)
        settled += _renumber(outcome.get("settled"), mapping)
    if unread:
        why.append(f"pages {unread[0]}-{unread[-1]} were not read (segment limit)")
    result.update(read=merged, why=why, pages_unread=list(unread))
    if any("settled" in o for o in outcomes):
        result.update(settled=settled, why_after=why_after)
    return result


def read_all(total, read_part):
    """按段读完一份 ``total`` 页的文件：``read_part(首页, 末页)`` → 这一段的结果（同 ``engine.run``）。
    第一段的末页由 ``total`` 定；``total`` 事先不知道时（原件在甲骨文）传 ``None``，第一段的结果里带 ``page_total``。"""
    parts, first, unread = [], 1, []
    while True:
        if len(parts) == MAX_SEGMENTS:
            unread = list(range(parts[-1][1] + 1, total + 1))  # 最后一段读到的单都留下，之后的页标出
            break
        last = last_page(first, total) if total else engine.PAGES_PER_CALL
        outcome = read_part(first, last)
        if total is None:
            total = int(outcome.get("page_total") or 0) or last
            last = last_page(first, total)
        parts.append((first, last, outcome))
        if last >= total or outcome.get("read") is None:
            break
        first = next_first(first, last, outcome["read"])
    return merge(parts, total, unread)
