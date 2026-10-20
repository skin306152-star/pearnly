"""Haiku→Opus 分级（ADR-022）。``client`` 是 ``anthropic.Anthropic``（甲骨文服务或生产本机兜底各自建）。"""
import base64
import json
import time

from pearnly_core.claude_ocr import rules, spec

HAIKU, OPUS = "claude-haiku-5-5", "claude-opus-5-5"
# 美元 / 百万 token：输入、输出、写缓存、读缓存（只用于估算我们自己的成本，不参与扣费）。
PRICE = {HAIKU: (0.10, 0.50, 0.125, 0.01), OPUS: (4.0, 20.0, 5.0, 0.20)}
MAX_PAGES = 8


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


def run(client, kind, images, own_tax=None, text=None, page_texts=None):
    """images：[(media_type, bytes), ...]，按页序；``text``：表格文件（Excel / Word）导出的文字，代替图片；
    ``page_texts``：与 images 一一对应的 PDF 文字层（``pages.page_texts``，只给硬性自检认副本页；不给同以前）。
    返回 {"read", "stages", "escalated", "why", "usd", "seconds"}。
    Haiku 读 → 硬性自检 → 不过交 Opus（告诉它哪些字段可疑）→ 本公司税号更正。读不出（两次都没结果）时 read 为 None。"""
    if kind not in spec.SPECS:
        raise ValueError(f"unknown kind {kind}")
    images = list(images)[:MAX_PAGES]
    page_texts = list(page_texts)[:MAX_PAGES] if isinstance(page_texts, (list, tuple)) else None
    stages = []
    read, stage = _call(client, HAIKU, kind, images, "", text)
    stages.append(stage)
    if kind == "voucher":
        stage["own_tax_fixed"] = rules.fix_own_tax(read, own_tax)
        why = rules.voucher_problems(read, len(images), page_texts)
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
            read = second
    return {"read": read, "stages": stages, "escalated": len(stages) > 1, "why": why,
            "usd": round(sum(s["usd"] for s in stages), 6), "seconds": round(sum(s["seconds"] for s in stages), 2)}
