#!/usr/bin/env python3
"""对账多页文档：「分页预热 → 整份读」（Cloud Workflows 的读法）与「一次读完」逐行对比。真实谷歌，**会付费**（每份文件读一遍）。

只读站点配置文件（冻结的识别环境、Document AI 区域、识别服务账号密钥路径），**不连数据库、不写站点、不建识别任务、不扣客户的费**；
结果只打印到标准输出（每份文件一行 JSON）。

用法（压测窗口在生产机一次性容器里跑，挂分支代码，测试租户；语料留在原地，不复制）：

    cd /home/frappe/frappe-bench/sites
    ../env/bin/python /candidate/scripts/cowork-compare-paged-reading.py --site testcowork2.pearnly.com \\
        --type bank_statement /root/pearnly-corpus/<…>/B07-GIZE-BAY-CA-2026-03.pdf  [更多文件…]

做法（每份文件只付一遍费）：
1. 新路径：各块读字、各页第一遍抽取并行预热（与 Workflows 同一函数：``recon_reading.warm_document`` →
   ``enterprise.first_pass``，最多 8 路同时），再原封不动读整份（``recon_reading.read_document``，与 Cloud Run 同一读法）。
   记下整份那一遍真正发出的付费调用数：应只剩按页序的再读（第一遍全部从检查点取回），外加整份那一遍里连接中断 / 超时后
   读法自带的那一次重发（报告 ``unknown_outcomes.whole``）；预热里中断的，重发结果在检查点里，整份那一遍不再付费。
2. 旧路径：一次读完（``recon_reading.read_document``）在第 1 步的检查点上**重放**：不连谷歌；检查点里没有的请求一律回 429
   （模拟被限流，让读法照原路换区域 / 换入口），并记下来——出现第 1 步从没发过的请求，就说明整份读法的输入变了。
3. 逐页逐行比两份结果（每页 document 全部字段、extraction_audit、validation_warnings、needs_manual_review）。

退出码：0 = 全部一致；1 = 有不一致（报告里列出）；2 = 用法 / 环境错误。
"""
import argparse
import hashlib
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import partial
from unittest import mock

import requests

PARALLEL = 8  # 与 infrastructure/cloud-workflows/cowork-document.yaml 的 concurrency_limit 一致


class Memory:
    """内存版检查点桶：与 ``ocr.remote.gcs.Bucket`` 同样的 get / create / put / delete 与版本号前置条件。"""

    def __init__(self, objects=None):
        self.objects, self.lock = deepcopy(objects or {}), threading.Lock()

    def get(self, name):
        with self.lock:
            found = self.objects.get(name)
            return (deepcopy(found[0]), found[1]) if found else None

    def create(self, name, value):
        with self.lock:
            if name in self.objects:
                return None
            self.objects[name] = (deepcopy(value), 1)
            return 1

    def put(self, name, value, if_generation=None):
        with self.lock:
            current = self.objects.get(name)
            if if_generation is not None and (current[1] if current else 0) != if_generation:
                return None
            generation = (current[1] if current else 0) + 1
            self.objects[name] = (deepcopy(value), generation)
            return generation

    def delete(self, name):
        with self.lock:
            self.objects.pop(name, None)


def digest(url, payload):
    return url.split("/")[2], hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Recording:
    """真连谷歌（每线程一个会话），记下每次真正发出的请求（主机 + 请求内容摘要）与当前阶段。"""

    def __init__(self):
        self.sent, self.lock, self.local, self.phase = [], threading.Lock(), threading.local(), "warm"
        self.unknown = []  # (阶段, 主机)：连接中断 / 超时（结果不明，读法会重发一次，重发也算一次付费）

    def post(self, url, headers=None, json=None, timeout=None, stream=False):
        phase = self.phase
        with self.lock:
            self.sent.append((phase,) + digest(url, json))
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
        try:
            return self.local.session.post(url, headers=headers, json=json, timeout=timeout, stream=stream)
        except requests.RequestException:
            with self.lock:
                self.unknown.append((phase, url.split("/")[2]))
            raise


class Replay:
    """重放：不连谷歌。走到这里的都是检查点里没有完成的请求：一律回 429，记下来。"""

    def __init__(self):
        self.requested, self.lock = [], threading.Lock()

    def post(self, url, headers=None, json=None, timeout=None, stream=False):
        with self.lock:
            self.requested.append(digest(url, json))
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "5"
        response.raw = io.BytesIO(b'{"error": {"status": "RESOURCE_EXHAUSTED"}}')
        return response


def token_source(path):
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    lock = threading.Lock()

    def token():
        with lock:
            if not credentials.valid:
                credentials.refresh(Request())
            return credentials.token
    return token


def read_whole(content, filename, pages, document_type, client, orient):
    from pearnly_cowork.ocr import recon_reading
    from pearnly_cowork.ocr.inputs_runtime.source import PageSource
    from pearnly_cowork.ocr.remote import runner

    is_pdf = filename.lower().endswith(".pdf")
    limit = runner.page_threads() if os.environ.get("OCR_PAGE_THREADS") else PARALLEL
    page_map = runner.page_mapper(limit, lambda count: None) if pages > 1 else None
    with PageSource(content, filename, expected_page_count=pages, enable_text_path=False,
                    image_max_long_edge=2400, orient=orient) as source:
        return recon_reading.read_document(source, content, is_pdf, None if is_pdf else recon_reading.image_mime(content),
            document_type, partial(client.document_ai_call, retry_unknown=True),
            recon_reading.document_page_failure(partial(client.model_call, retry_unknown=True)), map_pages=page_map)


def warm(content, filename, pages, document_type, make_client, orient):
    """与 Workflows 同样两步：各块读字并行 → 各页第一遍抽取并行（每一路各自打开原件，与各自一次 Cloud Run 请求相同）。"""
    from pearnly_cowork.ocr import recon_reading
    from pearnly_cowork.ocr.enterprise.pages import MAX_PAGES_PER_READ
    from pearnly_cowork.ocr.inputs_runtime.source import PageSource

    is_pdf = filename.lower().endswith(".pdf")
    chunks = -(-pages // MAX_PAGES_PER_READ) if is_pdf else 1
    outcomes = {}

    def one(stage, index):
        client = make_client()
        try:
            with PageSource(content, filename, expected_page_count=pages, enable_text_path=False,
                            image_max_long_edge=2400, orient=orient) as source:
                sent = recon_reading.warm_document(source, content, is_pdf,
                    None if is_pdf else recon_reading.image_mime(content), document_type,
                    partial(client.document_ai_call, retry_unknown=True),
                    recon_reading.document_page_failure(partial(client.model_call, retry_unknown=True)), stage, index)
            outcomes[(stage, index)] = "done" if sent else "skipped"
        except Exception as exc:  # noqa: BLE001 - 预热出错由整份那一遍补上（与 Workflows 相同）
            outcomes[(stage, index)] = "error:" + type(exc).__name__

    for stage, count in (("read", chunks), ("extract", pages)):
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            list(pool.map(lambda index: one(stage, index), range(1, count + 1)))
    return outcomes


def patiently(read, attempts=10):
    """谷歌限流超过原地等的上限时，Cloud Run 会交回、稍后续跑；这里同样等一会儿再从检查点接着读（已付的不重付）。"""
    from pearnly_core.execution.errors import Deferred

    for attempt in range(attempts):
        try:
            return read()
        except Deferred as exc:
            if attempt == attempts - 1:
                raise
            time.sleep(max(5.0, float(exc.delay or 5)))


def page_view(page):
    return {key: page.get(key) for key in ("page_number", "document", "document_type", "validation_warnings",
                                           "needs_manual_review", "extraction_audit")}


def compare(new, old):
    differences = []
    new_pages, old_pages = new.get("pages") or [], old.get("pages") or []
    if len(new_pages) != len(old_pages):
        differences.append({"pages": [len(new_pages), len(old_pages)]})
    for index, (a, b) in enumerate(zip(new_pages, old_pages), 1):
        a, b = page_view(a), page_view(b)
        if a == b:
            continue
        rows_a, rows_b = (a["document"] or {}).get("entries") or [], (b["document"] or {}).get("entries") or []
        rows = [number for number, pair in enumerate(zip(rows_a, rows_b), 1) if pair[0] != pair[1]]
        fields = sorted(key for key in a if a[key] != b[key])
        differences.append({"page": index, "fields": fields, "rows_new": len(rows_a), "rows_old": len(rows_b),
                            "differing_rows": rows[:50]})
    for key in ("_needs_review", "engine", "page_count"):
        if new.get(key) != old.get(key):
            differences.append({key: [new.get(key), old.get(key)]})
    return differences


def run_file(path, document_type, frozen, regions, token, orient):
    from pearnly_cowork.files.service import inspect_content
    from pearnly_cowork.ocr.remote import runner

    with open(path, "rb") as handle:
        content = handle.read()
    filename = os.path.basename(path)
    info = inspect_content(filename, content)
    pages = int(info["page_count"])
    environment = dict(frozen["environment"])
    site, work = "compare.local", hashlib.sha256(content).hexdigest()
    started = time.monotonic()

    bucket, google = Memory(), Recording()
    make_client = lambda: runner.build_client(environment, runner.Store(bucket, site, work), token, http=google,
                                              regions=regions)
    warmed = warm(content, filename, pages, document_type, make_client, orient)
    google.phase = "whole"
    new = patiently(lambda: read_whole(content, filename, pages, document_type, make_client(), orient))
    warm_seconds = round(time.monotonic() - started, 1)

    replay = Replay()
    replay_bucket = Memory(bucket.objects)
    with mock.patch.object(runner.time, "sleep"):  # 重放时检查点里没有的请求回 429：不真的等（只在重放这一段）
        old = read_whole(content, filename, pages, document_type,
                         runner.build_client(environment, runner.Store(replay_bucket, site, work), token, http=replay,
                                             regions=regions), orient)
    sent = {(host, value) for _phase, host, value in google.sent}
    unseen = [request for request in replay.requested if request not in sent]
    rereads = sum(bool((page.get("extraction_audit") or {}).get("self_check", {}).get("reread"))
                  for page in new.get("pages") or [])
    whole_paid = sum(1 for phase, _host, _value in google.sent if phase == "whole")
    differences = compare(new, old)
    return {"file": filename, "pages": pages, "type": document_type, "seconds": warm_seconds,
            "warm": {f"{stage}:{index}": value for (stage, index), value in sorted(warmed.items())},
            "paid_calls": len(google.sent), "whole_pass_paid_calls": whole_paid, "rereads_in_result": rereads,
            # 整份那一遍应付 = 再读页数 + 整份那一遍里结果不明后的重发；预热里结果不明的，重发结果整份那一遍从检查点取回
            "unknown_outcomes": {phase: sum(1 for p, _host in google.unknown if p == phase) for phase in ("warm", "whole")},
            "replay_requests_never_sent_before": len(unseen), "rows_new": sum(len((p.get("document") or {}).get("entries")
                                                                                   or []) for p in new.get("pages") or []),
            "identical": not differences and not unseen, "differences": differences}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--site", required=True)
    parser.add_argument("--type", required=True, choices=("bank_statement", "general_ledger", "vat_report"))
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()

    import frappe

    frappe.init(site=args.site)  # 只读站点配置文件，不连数据库、不连 Redis
    from pearnly_core.ocr_models.orient import orient
    from pearnly_cowork.ocr.execution import settings as ocr_settings

    frozen = ocr_settings.snapshot()
    regions = frappe.conf.get("pearnly_document_ai_regions")
    token = token_source(frozen["target"]["credentials_path"])
    reports = []
    for path in args.files:
        try:
            reports.append(run_file(path, args.type, frozen, regions, token, orient))
        except Exception as exc:  # noqa: BLE001
            reports.append({"file": os.path.basename(path), "identical": False, "error": f"{type(exc).__name__}: {exc}"[:500]})
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    frappe.destroy()
    sys.exit(0 if all(report.get("identical") for report in reports) else 1)


if __name__ == "__main__":
    main()
