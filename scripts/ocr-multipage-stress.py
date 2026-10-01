#!/usr/bin/env python3
"""Cowork 多页识别压测：真实限流额度 + 真实调用 + 真实检查点，按线上队列的规则让出 / 续跑 / 过期。

与 `ocr-corpus-eval.py` 的区别：那边绕开了共用限流和检查点，只比读得对不对；这里走线上同一条路
（`pearnly_core.execution.budget/provider/work` + Cowork `build_client`），看一份多页文件在限流下
能不能在队列期限内读完、续跑多少次、续跑本身花多少时间、检查点有多大。

- 不扣费、不写 Cowork 单据：只临时建一条 `Pearnly OCR Work`（kind `stress.cowork`），跑完删除。
- 让出规则同线上：拿不到额度 / 429 → 按给的延迟再等 1 秒（调度器轮询）后从头续跑，完成的调用从检查点取回；
  单次运行超过 `--yield-seconds`（Cowork 为 420）主动让出；累计超过 `--max-age`（队列 1800）记为过期，但继续跑完以便量总时长。
- 花钱：每页一次到几次 3.1 / Vision 调用。

用法（线上镜像 + 测试租户站点，一次只跑一个）：
    env/bin/python scripts/ocr-multipage-stress.py --site testerp.pearnly.com --pdf /in/stress-20.pdf --out /out/stress-20.json
"""
import argparse
from collections import Counter
from datetime import date, timedelta
import json
import time

import frappe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-age", type=int, default=1800)
    parser.add_argument("--yield-seconds", type=int, default=420)
    args = parser.parse_args()
    frappe.init(site=args.site)
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        report = stress(args)
    finally:
        frappe.destroy()
    json.dump(report, open(args.out, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in report.items() if k not in ("runs", "pages")}, ensure_ascii=False, default=str))


def stress(args):
    import pymupdf

    from pearnly_core import ocr
    from pearnly_core.execution import work
    from pearnly_core.execution.errors import Deferred, Uncertain
    from pearnly_cowork.ocr.engine import run_invoice
    from pearnly_cowork.ocr.engine.config import config_from_environment
    from pearnly_cowork.ocr.inputs_runtime.source import PageSource
    from pearnly_cowork.ocr.policy import invoice_policy
    from pearnly_cowork.ocr.providers.core import build_client, core_identity

    content = open(args.pdf, "rb").read()
    with pymupdf.open(stream=content, filetype="pdf") as document:
        pages = len(document)
    environment = {"OCR_LLM_BACKEND": "vertex", "GCP_PROJECT": ocr.project(), "VERTEX_LOCATION": ocr.location()}
    selected = invoice_policy(environment, {"mode": "economy"})
    config = config_from_environment(environment, selected)
    name = "stress-cowork-" + frappe.generate_hash(length=12)
    now = work.utcnow()
    frappe.get_doc({"doctype": work.DT, "name": name, "kind": "stress.cowork", "product": "cowork",
                    "reference": name, "arguments": "{}", "user": "Administrator", "status": "running",
                    "next_run": now, "expires_at": now + timedelta(hours=3), "admission_key": name}
                   ).insert(ignore_permissions=True)
    frappe.db.commit()

    import requests

    posts = []  # 真正发到 Google 的请求（检查点取回的、没拿到额度的都不算）
    original_post = requests.post

    def counted_post(url, *a, **k):
        started = time.monotonic()
        response = original_post(url, *a, **k)
        if "googleapis.com" in str(url):
            posts.append({"at": round(started - begin, 1), "ms": int((time.monotonic() - started) * 1000),
                          "api": "vision" if "vision" in str(url) else str(url).rsplit("/models/", 1)[-1].split(":")[0],
                          "status": response.status_code})
        return response

    requests.post = counted_post
    client = build_client(environment, core_identity=core_identity())
    begin = time.monotonic()
    runs, reasons, expired_at, outcome = [], Counter(), None, "ok"
    result = None
    try:
        while True:
            run_start, posts_before = time.monotonic(), len(posts)
            first_new = []

            def guarded(callback):
                def invoke(*a, **k):
                    frappe.db.commit()
                    if time.monotonic() - run_start > args.yield_seconds:
                        raise Deferred("progress_yield", 1)
                    if len(posts) == posts_before and not first_new:
                        first_new.append(round(time.monotonic() - run_start, 2))
                    return callback(*a, **k)
                return invoke

            code, delay = None, 0
            try:
                with work.context(name):
                    with PageSource(content, "stress.pdf", expected_page_count=pages,
                                    enable_text_path=selected.text_fast_path, orient=ocr.orient) as source:
                        result = run_invoice(source, guarded(client.model_call), guarded(client.vision_call),
                                             config=config, anchor_year=date.today().year, today=date.today())
            except Deferred as exc:
                frappe.db.rollback()
                code, delay = exc.reason, exc.delay
            except Uncertain:
                frappe.db.rollback()
                code, outcome = "uncertain", "uncertain"
            elapsed = time.monotonic() - begin
            blob = json.dumps(work.stored_checkpoints(name))  # 检查点按行存后的总量
            runs.append({"run": len(runs) + 1, "seconds": round(time.monotonic() - run_start, 1),
                         "new_posts": len(posts) - posts_before, "code": code, "delay": delay,
                         "replay_until_first_call": first_new[0] if first_new else None,
                         "checkpoint_kb": round(len(blob) / 1024), "at": round(elapsed, 1)})
            print(f"run {len(runs)}: {runs[-1]}", flush=True)
            if code:
                reasons[code] += 1
            if expired_at is None and elapsed > args.max_age:
                expired_at = round(elapsed, 1)
            if not code or outcome == "uncertain":
                break
            time.sleep(max(1, delay) + 1)  # 调度器每秒轮询一次
    finally:
        requests.post = original_post
        frappe.db.delete(work.DT, {"name": name})
        frappe.db.commit()
    total = time.monotonic() - begin
    report = {"pages": pages, "outcome": outcome, "total_seconds": round(total, 1),
              "would_expire_at": expired_at, "runs_count": len(runs), "deferrals": dict(reasons),
              "real_posts": len(posts), "posts_by_api": dict(Counter(p["api"] for p in posts)),
              "http_status": dict(Counter(p["status"] for p in posts)),
              "replay_seconds": round(sum(r["seconds"] for r in runs if r["new_posts"] == 0), 1),
              "max_checkpoint_kb": max((r["checkpoint_kb"] for r in runs), default=0), "runs": runs}
    if result is not None:
        report.update(engine=result.pipeline.engine, page_results=len(result.pipeline.pages),
                      needs_review=sum(p.needs_manual_review for p in result.pipeline.pages),
                      chains=dict(Counter("→".join(p.layer_chain) for p in result.pipeline.pages)),
                      usage=[{"stage": c.stage, "model": c.model, "usage": (c.raw or {}).get("usageMetadata")}
                             for c in result.calls],
                      pages=[{"page": p.page_number, "total": p.invoice.total_amount, "no": p.invoice.invoice_number,
                              "band": p.confidence_band} for p in result.pipeline.pages])
    return report


if __name__ == "__main__":
    main()
