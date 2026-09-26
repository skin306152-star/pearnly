#!/usr/bin/env python3
"""OCR 模型策略评测：同一张图按几种读法各调一次，全部原样落盘，离线组合出各方案比较准确率、成本和速度。

**付费外部读取**，不进默认回归；遵守线上共用的 OCR 预算（`pearnly_core.execution.budget`）。
走 ERP 采购识别的提示词与确定性检查（`purchasing.recognition`），不写业务数据、不扣租户费用。

每张图的调用：
- `r1`  gemini-3.1-flash-lite，思考 MINIMAL，温度 0（现状首读）
- `e1`  gemini-3.1-flash-lite，思考 LOW，温度 0（首读就加思考）
- `d2`  gemini-3.1-flash-lite，思考 MINIMAL，温度 0.6（第二次独立读，给投票用）
- 只在 r1 过不了硬闸时：
  - `a2` gemini-3.8-flash，思考 LOW，整张重读（现状升级）
  - `c2` gemini-3.1-flash-lite，思考 LOW，带上第一次的读数和没过的检查，请它重新核对

用法（bench 环境）：env/bin/python scripts/ocr-policy-bench.py --site <site> --corpus <dir> --out <dir>
语料目录同 `ocr-corpus-eval.py`（manifest.json + 图片）。
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
import traceback
from pathlib import Path

import frappe

LITE, FLASH = "gemini-3.1-flash-lite", "gemini-3.8-flash"


def call(prompt, images, *, model, thinking, temperature):
    """一次 Vertex 调用；走线上同一条带预算的发送（`ocr._post_with_backoff`），返回 (raw_json, 记录)。"""
    from pearnly_core import ocr
    from pearnly_core.execution.errors import Deferred, QueueFull

    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}
                                  for data, mime in images]
    payload = {"contents": [{"role": "user", "parts": parts}],
               "generationConfig": {"temperature": temperature, "maxOutputTokens": 16384,
                                    "responseMimeType": "application/json", "thinkingConfig": thinking}}
    headers = {"Authorization": "Bearer " + ocr.access_token(), "Content-Type": "application/json"}
    for attempt in range(8):
        started = time.monotonic()
        try:
            response = ocr._post_with_backoff(ocr.vertex_url(model), headers, payload, 90, model)
        except (Deferred, QueueFull) as exc:
            time.sleep(15 * (attempt + 1))
            continue
        ms = int((time.monotonic() - started) * 1000)
        record = {"model": model, "thinking": thinking, "temperature": temperature, "ms": ms,
                  "status": response.status_code}
        if response.status_code != 200:
            record["error"] = response.text[:300]
            if response.status_code in (429, 503):
                time.sleep(15 * (attempt + 1))
                continue
            return None, record
        body = response.json()
        record["usage"] = body.get("usageMetadata")
        try:
            text = "".join(part.get("text", "") for part in body["candidates"][0]["content"]["parts"]
                           if not part.get("thought"))
            return json.loads(text), record
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            record["error"] = f"unparsable: {exc}"
            return None, record
    return None, {"model": model, "error": "rate limited too long"}


def structured(raw):
    from pearnly_erp_portal.purchasing import recognition as pr

    if not isinstance(raw, dict):
        return None, []
    result = pr._structured(raw)
    if result is None:
        return None, []
    return result, pr.review(result)


def repair_prompt(prompt, raw, issues):
    from pearnly_erp_portal.recognition import checks

    keep = {key: raw.get(key) for key in ("doc_no", "doc_date", "doc_date_raw", "supplier_tax_id", "buyer_tax_id",
                                          "discount_total", "wht_rate", "wht_amount", "printed", "lines")}
    reasons = "; ".join(checks.MESSAGES.get(code, code) for code in issues)
    return (prompt + "\n\nA first reading of this image returned:\n" + json.dumps(keep, ensure_ascii=False)
            + f"\nIt failed these checks: {reasons}.\nLook at the image again carefully, especially the parts these "
              "checks concern (amounts at the foot, line amounts, tax IDs, dates), correct what was misread and "
              "return the complete JSON in the same format. Keep values that were read correctly.")


def run(content, mime):
    from pearnly_core import ocr
    from pearnly_erp_portal.purchasing import recognition as pr
    from pearnly_erp_portal.recognition import checks

    images = [ocr.upright(content, mime)]
    out = {"calls": {}}

    def one(name, prompt, model, thinking, temperature):
        raw, record = call(prompt, images, model=model, thinking=thinking, temperature=temperature)
        result, issues = structured(raw)
        out["calls"][name] = {**record, "raw": raw, "result": result, "issues": issues}
        return raw, issues

    raw1, issues1 = one("r1", pr.PROMPT, LITE, {"thinkingLevel": "MINIMAL"}, 0)
    one("e1", pr.PROMPT, LITE, {"thinkingLevel": "LOW"}, 0)
    one("d2", pr.PROMPT, LITE, {"thinkingLevel": "MINIMAL"}, 0.6)
    if raw1 is None or checks.hard(issues1):
        one("a2", pr.PROMPT, FLASH, {"thinkingLevel": "LOW"}, 0)
        one("c2", repair_prompt(pr.PROMPT, raw1 or {}, checks.hard(issues1) or ["total_missing"]), LITE,
            {"thinkingLevel": "LOW"}, 0)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    frappe.init(site=args.site, sites_path=os.environ.get("SITES_PATH", "."))
    frappe.connect()
    corpus, out = Path(args.corpus), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for entry in json.loads((corpus / "manifest.json").read_text(encoding="utf-8")):
        if args.only and not re.search(args.only, entry["id"]):
            continue
        target = out / f"{entry['id']}.json"
        if target.exists():
            continue
        content = (corpus / entry["file"]).read_bytes()
        mime = mimetypes.guess_type(entry["file"])[0] or "image/jpeg"
        started = time.monotonic()
        try:
            row = run(content, mime)
        except Exception as exc:  # noqa: BLE001 - one bad document must not stop the corpus
            row = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]}
        row.update(id=entry["id"], set=entry.get("set"), gold=entry.get("gold", {}),
                   seconds=round(time.monotonic() - started, 1))
        target.write_text(json.dumps(row, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"{entry['id']:45} {row['seconds']:6}s {','.join(row.get('calls', {}))}", flush=True)
    frappe.db.rollback()
    frappe.destroy()


if __name__ == "__main__":
    sys.exit(main())
