"""Haiku→Opus 分级（ADR-022）。``client`` 是 ``anthropic.Anthropic``（甲骨文服务或生产本机兜底各自建）。"""
import base64
import json
import re
import time

from pearnly_core.claude_ocr import consistency, rules, spec

HAIKU, OPUS = "claude-haiku-5-5", "claude-opus-5-5"
# 美元 / 百万 token：输入、输出、写缓存、读缓存（只用于估算我们自己的成本，不参与扣费）。
PRICE = {HAIKU: (0.10, 0.50, 0.125, 0.01), OPUS: (4.0, 20.0, 5.0, 0.20)}
# 一次调用最多交几页（10-17 评测口径）：更长的文件由 ``segments`` 按段读完再合并，不截掉后面的页。
PAGES_PER_CALL = 8


def cost(model, usage):
    pin, pout, pw, pr = PRICE[model]
    return (usage.input_tokens * pin + usage.output_tokens * pout + (usage.cache_creation_input_tokens or 0) * pw
            + (usage.cache_read_input_tokens or 0) * pr) / 1e6


def _blank_to_none(value):
    """结构化输出里没印的字段是空串，转回 None（与谷歌路线、打分口径一致）。"""
    if isinstance(value, dict):
        return {k: _blank_to_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_blank_to_none(v) for v in value]
    return None if isinstance(value, str) and not value.strip() else value


def _call(client, model, kind, images, hint, text=None):
    system, schema, ask = spec.SPECS[kind]
    content = [{"type": "text", "text": spec.TEXT_INTRO + text}] if text else []
    for number, (media_type, data) in enumerate(images, 1):
        content.append({"type": "text", "text": f"Page {number}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                    "data": base64.standard_b64encode(data).decode()}})
    content.append({"type": "text", "text": ask + (("\n\n" + hint) if hint else "")})
    started = time.monotonic()
    response = client.messages.create(
        model=model, max_tokens=16000, messages=[{"role": "user", "content": content}],
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}})
    stage = {"model": model, "seconds": round(time.monotonic() - started, 2), "usd": round(cost(model, response.usage), 6),
             "stop_reason": response.stop_reason,
             "usage": {k: getattr(response.usage, k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                                    "cache_read_input_tokens")}}
    read = None
    if response.stop_reason == "end_turn":
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            read = _blank_to_none(json.loads(text))
        except ValueError:
            stage["error"] = "json_invalid"
    else:
        stage["error"] = f"stop_{response.stop_reason}"
    return read, stage


def run(client, kind, images, own_tax=None, text=None):
    """images：[(media_type, bytes), ...]，按页序；``text``：表格文件（Excel / Word）导出的文字，代替图片。
    返回 {"read", "stages", "escalated", "why", "usd", "seconds"}；发票类另有 ``why_after``（Opus 重读后自检还剩的问题，交产品复核）
    与 ``settled``（由票面数字定下的字段，见 ``consistency.settle``）。
    Haiku 读 → 硬性自检 + 票面自洽表 → 不过交 Opus（告诉它哪一行、差多少）→ 按字段合并两次读法（:func:`combine`）
    → 本公司税号更正 → 数字能定的字段由数字定。
    读不出（两次都没结果）时 read 为 None。"""
    if kind not in spec.SPECS:
        raise ValueError(f"unknown kind {kind}")
    images = list(images)[:PAGES_PER_CALL]
    stages = []
    read, stage = _call(client, HAIKU, kind, images, "", text)
    stages.append(stage)
    if kind == "voucher":
        stage["own_tax_fixed"] = rules.fix_own_tax(read, own_tax)
        why = rules.voucher_problems(read, len(images))
    else:
        why = rules.PROBLEMS[kind](read)
    if why:
        hint = ("An automated first reading of this document failed these checks: " + "; ".join(why[:12])
                + ". Re-read those fields on the document with extra care; keep exactly what is printed.")
        second, stage = _call(client, OPUS, kind, images, hint, text)
        stages.append(stage)
        if second is not None:
            if kind == "voucher":
                stage["own_tax_fixed"] = rules.fix_own_tax(second, own_tax)
                read = combine(read, second, why)
            else:
                read = second
    result = {"read": read, "stages": stages, "escalated": len(stages) > 1, "why": why,
              "usd": round(sum(s["usd"] for s in stages), 6), "seconds": round(sum(s["seconds"] for s in stages), 2)}
    if kind == "voucher":
        result.update(finish(read, len(images), escalated=len(stages) > 1))
    return result


def finish(read, pages, *, escalated):
    """发票类的收尾（生产与回放基准同一份）：数字能定的字段由数字定；升级过的再查一遍自检，剩下的交产品复核。"""
    settled = consistency.settle(read)
    return {"settled": settled, "why_after": rules.voucher_problems(read, pages) if escalated and read is not None else []}


# 金额这一组字段一起取舍：明细与合计是一套账，混用两次读法会自相矛盾。
AMOUNTS = ("subtotal", "discount", "vat", "total", "price_mode", "wht_rate", "wht_amount", "lines")
_FIELD = re.compile(r"documents\[(\d+)\]\.([a-z_]+)")
_DOC = re.compile(r"documents\[(\d+)\]")


def _named(why):
    """自检点名了哪些字段：{票序号: {字段}}；``None`` = 点名的是整份（漏页、读不出），整份用 Opus 的。"""
    named = {}
    for reason in why:
        field, doc = _FIELD.match(reason), _DOC.match(reason)
        if doc is None:
            return None
        index = int(doc.group(1))
        if field and field.group(2) not in ("lines",):
            named.setdefault(index, set()).add(field.group(2))
        else:  # 明细与合计的检查（line_math、lines_vs_document、subtotal + vat != total …）
            named.setdefault(index, set()).update(AMOUNTS)
    return named


def _weak(value, field):
    if value in (None, "", []):
        return True
    return field in ("seller_tax", "buyer_tax") and not rules.thai_id_ok(value)


def combine(first, second, why):
    """升级后两次读法按字段合并（2026-10-27 回放基准：整份换成 Opus 的会把 Haiku 读对、没被怀疑的单号改错）。

    Opus 的值用于：自检点名的字段（金额相关的一整组）、Haiku 没读出或税号校验位不对的字段。其余没被怀疑的字段保留 Haiku 的。
    只合并「两次都只读出一张票」的：一份文件几张票时两次的票序常不同，按序号合并会把别的票的字段拼进来（回放实测：金标 6700869、
    6701664 合并后反而多错）。几张票、录不录（entry）两次不一致、或点名的是整份（漏页），整份用 Opus 的。"""
    import copy

    named = _named(why)
    docs_a, docs_b = (first or {}).get("documents") or [], (second or {}).get("documents") or []
    if (first is None or named is None or first.get("entry") != second.get("entry") or len(docs_a) != 1 or len(docs_b) != 1):
        return second
    merged = copy.deepcopy(first)
    merged["uncertain"] = second.get("uncertain")
    for index, (doc_a, doc_b) in enumerate(zip(docs_a, docs_b)):
        target = merged["documents"][index]
        for field, value in doc_b.items():
            if field in named.get(index, ()) or (_weak(doc_a.get(field), field) and not _weak(value, field)):
                target[field] = copy.deepcopy(value)
    return merged
