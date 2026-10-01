"""服务器 ↔ Cloud Run 的识别请求与回复（纯约定：无 IO、不依赖 Frappe，两边各存一份同样的代码）。

请求是一个 JSON：原件（字节 base64，或浏览器直传后存在原件桶里的对象 + 版本号 + sha256，ADR-016）+ 受理时冻结的配置
（环境、策略、示例快照、锚定日期）。
回复是按行的 JSON（NDJSON）：读到新的一页回一行 ``page``，最后一行是 ``result``。
"""
import base64
import hashlib
import json
import re

VERSION = 1
# Cloud Run 的 HTTP/1 请求体上限 32 MiB；base64 放大约 4/3，原件最多 20 MiB。
MAX_CONTENT_BYTES = 20 * 1024 * 1024
# 一次请求最多跑多久（与本机执行同一口径：超过就让出，带着检查点下次接着读，见 workflow._recognize）。
YIELD_SECONDS = 420
WORK = re.compile(r"[0-9a-f]{64}")
SITE = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}")
BATCH = re.compile(r"[0-9a-f]{64}")
STATUSES = ("done", "deferred", "uncertain", "refused", "error")
# 读法：发票录入；销项税核查的发票（抽取结果不明重发一次、一页读不出不作废整份，见 ocr.recon_reading.vat_callbacks）。
VARIANTS = ("intake", "vat_check")
# 引擎的内容错误类名：服务器照原类型重新抛出（本机执行时调用方据此判断「读不出」而不是程序错误）。
ENGINE_ERRORS = ("EngineError", "ContentError", "DirectFallback")


def encode(value):
    """与 ``ocr.execution.store.encode`` 同一写法（摘要要一致）。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def evidence(calls):
    """与 ``workflow._evidence`` 同一口径：只留摘要与计量，不带原始回复（原始回复留在检查点里）。"""
    seen, result = set(), []
    for call in calls:
        key = call.checkpoint_key or digest([call.stage, call.page_number, call.attempt, call.model, call.raw])
        if key in seen:
            continue
        seen.add(key)
        result.append({"checkpoint_key": key, "stage": call.stage, "page": call.page_number,
            "attempt": call.attempt, "model": call.model, "raw_sha256": digest(call.raw),
            "input_tokens": call.input_tokens, "output_tokens": call.output_tokens,
            "finish_reason": call.finish_reason})
    return result


# 原件桶里的对象：``<站点>/originals/<原件名><扩展名>``（``files.cloud.object_name``）。
ORIGINAL = re.compile(r"originals/CWS-[0-9a-f]{48}\.[a-z0-9]{1,5}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SOURCE = re.compile(r"CWS-[0-9a-f]{48}")

# 识别时顺手存好的预览图（与 ``ocr.originals.render_page`` 同一份画法）。画法变了就加 1：旧版本存的图不再用，
# 服务器回到现画，保证页面上看到的和现画的一模一样。
PREVIEW_VERSION = 1
PREVIEW_TYPES = ("image/png", "image/jpeg")


def preview_object(site, source, sha256, page):
    """一页预览在原件桶里的对象名：站点 + 原件 + 内容哈希 + 画法版本 + 页码（同一原件同一页永远是同一个名字，只写一次）。"""
    return f"{site}/previews/{source}/v{PREVIEW_VERSION}-{sha256[:16]}/p{int(page)}"


def previews(*, source, sha256, kind):
    """识别请求里的预览规格：这份原件是哪条原件记录（``CWS-…``）、内容 sha256、类型（pdf / image）。"""
    return {"source": source, "sha256": sha256, "kind": kind}


def _previews(body):
    value = body.get("previews")
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"source", "sha256", "kind"}
            or not isinstance(value["source"], str) or not SOURCE.fullmatch(value["source"])
            or not isinstance(value["sha256"], str) or not SHA256.fullmatch(value["sha256"])
            or value["kind"] not in ("pdf", "image")):
        raise ValueError("invalid previews")
    original = body.get("original")
    if original is not None and original.get("sha256") != value["sha256"]:
        raise ValueError("invalid previews")
    return value


def parse_previews(items, *, site, source, sha256, page_count):
    """Cloud Run 回来的已存预览（``done`` 结果里的 ``previews``）逐项核对：页码在范围内、对象名正是这份原件这一页的、
    版本号与类型合规。不合规的整批不用（服务器照旧现画），返回 ``{页码: {"object", "generation", "type"}}``。"""
    if not isinstance(items, dict) or not isinstance(items.get("bucket"), str) or not isinstance(items.get("pages"), list):
        raise ValueError("invalid previews")
    pages = {}
    for item in items["pages"]:
        if not isinstance(item, dict):
            raise ValueError("invalid previews")
        page = item.get("page")
        if type(page) is not int or not 1 <= page <= page_count or page in pages:
            raise ValueError("invalid previews")
        if item.get("object") != preview_object(site, source, sha256, page):
            raise ValueError("invalid previews")
        if type(item.get("generation")) is not int or item["generation"] < 1 or item.get("type") not in PREVIEW_TYPES:
            raise ValueError("invalid previews")
        pages[page] = {"object": item["object"], "generation": item["generation"], "type": item["type"]}
    return items["bucket"], pages


def original(*, object, generation, sha256):
    """存在原件桶里的原件：Cloud Run 按版本号读出、核对 sha256 后再读，不经过我们服务器。"""
    return {"object": object, "generation": int(generation), "sha256": sha256}


def _original(body):
    value = body.get("original")
    if not isinstance(value, dict) or not isinstance(value.get("object"), str):
        raise ValueError("invalid original")
    site, _sep, rest = value["object"].partition("/")
    if site != body["site"] or not ORIGINAL.fullmatch(rest):
        raise ValueError("invalid original")
    if type(value.get("generation")) is not int or value["generation"] < 1:
        raise ValueError("invalid original")
    if not isinstance(value.get("sha256"), str) or not SHA256.fullmatch(value["sha256"]):
        raise ValueError("invalid original")
    return value


def request(*, site, work, batch, filename, content=None, page_count, anchor_date, environment, policy,
            examples=None, variant="intake", yield_seconds=YIELD_SECONDS, original=None, previews=None):
    body = {"version": VERSION, "kind": "cowork.invoice", "site": site, "work": work, "batch": batch,
            "filename": filename, "page_count": page_count, "anchor_date": anchor_date, "environment": environment,
            "policy": policy, "examples": examples, "variant": variant, "yield_seconds": yield_seconds}
    if previews is not None:
        body["previews"] = previews
    if original is not None:
        body["original"] = original
    else:
        body["content"] = base64.b64encode(content).decode("ascii")
    parse_request(body)
    return body


def inspect_request(*, site, object, filename):
    """浏览器直传的原件：让 Cloud Run 读一遍，核对类型 / 页数、算 sha256（同 ``ocr.originals.inspect``），并告诉
    服务器这个对象的版本号（同一个对象名只能写一次，所以只有一个版本）。"""
    body = {"version": VERSION, "kind": "cowork.inspect", "site": site, "original": {"object": object},
            "filename": filename}
    parse_inspect(body)
    return body


def preview_request(*, site, object, generation, sha256, kind, page):
    """原件桶里一份原件的一页预览（ADR-016）：Cloud Run 按版本号读、核对 sha256、生成预览图，只传回图。"""
    body = {"version": VERSION, "kind": "cowork.preview", "site": site,
            "original": original(object=object, generation=generation, sha256=sha256), "file_kind": kind, "page": page}
    parse_preview(body)
    return body


def parse_preview(body):
    if not isinstance(body, dict) or body.get("version") != VERSION or body.get("kind") != "cowork.preview":
        raise ValueError("unsupported request")
    if not isinstance(body.get("site"), str) or not SITE.fullmatch(body["site"]):
        raise ValueError("invalid site")
    if body.get("file_kind") not in ("pdf", "image"):
        raise ValueError("invalid file_kind")
    if type(body.get("page")) is not int or not 1 <= body["page"] <= 500:
        raise ValueError("invalid page")
    return _original(body)


def parse_inspect(body):
    if not isinstance(body, dict) or body.get("version") != VERSION or body.get("kind") != "cowork.inspect":
        raise ValueError("unsupported request")
    if not isinstance(body.get("site"), str) or not SITE.fullmatch(body["site"]):
        raise ValueError("invalid site")
    if not isinstance(body.get("filename"), str) or not 1 <= len(body["filename"]) <= 255:
        raise ValueError("invalid filename")
    value = body.get("original")
    if not isinstance(value, dict) or not isinstance(value.get("object"), str):
        raise ValueError("invalid original")
    site, _sep, rest = value["object"].partition("/")
    if site != body["site"] or not ORIGINAL.fullmatch(rest) or set(value) != {"object"}:
        raise ValueError("invalid original")
    return value


# 对账三类文档（银行账单 / 总账 / 销项税报告）：企业档管线（ocr.recon_reading.read_document）。
DOCUMENT_TYPES = ("bank_statement", "general_ledger", "vat_report")


def document_request(*, site, work, filename, content, page_count, document_type, environment, regions=None,
                     yield_seconds=YIELD_SECONDS):
    body = {"version": VERSION, "kind": "cowork.document", "site": site, "work": work, "filename": filename,
            "content": base64.b64encode(content).decode("ascii"), "page_count": page_count,
            "document_type": document_type, "environment": environment, "regions": regions,
            "yield_seconds": yield_seconds}
    parse_request(body)
    return body


def parse_request(body):
    """Cloud Run 收到请求先过这一关；服务器发出前也用它自检。不合规就 ValueError。"""
    if not isinstance(body, dict) or body.get("version") != VERSION:
        raise ValueError("unsupported request")
    if body.get("kind") == "cowork.document":
        return _parse_document(body)
    if body.get("kind") != "cowork.invoice":
        raise ValueError("unsupported request")
    for key, pattern in (("site", SITE), ("work", WORK), ("batch", BATCH)):
        if not isinstance(body.get(key), str) or not pattern.fullmatch(body[key]):
            raise ValueError("invalid " + key)
    if not isinstance(body.get("filename"), str) or not 1 <= len(body["filename"]) <= 255:
        raise ValueError("invalid filename")
    if type(body.get("page_count")) is not int or not 1 <= body["page_count"] <= 500:
        raise ValueError("invalid page_count")
    if not isinstance(body.get("anchor_date"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body["anchor_date"]):
        raise ValueError("invalid anchor_date")
    environment, policy = body.get("environment"), body.get("policy")
    if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                for k, v in environment.items()):
        raise ValueError("invalid environment")
    if not isinstance(policy, dict):
        raise ValueError("invalid policy")
    if body.get("examples") is not None and not isinstance(body["examples"], dict):
        raise ValueError("invalid examples")
    if body.get("variant", "intake") not in VARIANTS:
        raise ValueError("invalid variant")
    yield_seconds = body.get("yield_seconds", YIELD_SECONDS)
    if type(yield_seconds) is not int or not 30 <= yield_seconds <= YIELD_SECONDS:
        raise ValueError("invalid yield_seconds")
    if "original" in body:
        if "content" in body:
            raise ValueError("invalid content")
        _original(body)
        _previews(body)
        return None  # 原件在原件桶里：Cloud Run 读出后再核对（runner.original_bytes）
    try:
        content = base64.b64decode(body.get("content") or "", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid content") from exc
    if not content or len(content) > MAX_CONTENT_BYTES:
        raise ValueError("invalid content size")
    spec = _previews(body)
    if spec is not None and hashlib.sha256(content).hexdigest() != spec["sha256"]:
        raise ValueError("invalid previews")
    return content


def _parse_document(body):
    for key, pattern in (("site", SITE), ("work", WORK)):
        if not isinstance(body.get(key), str) or not pattern.fullmatch(body[key]):
            raise ValueError("invalid " + key)
    if not isinstance(body.get("filename"), str) or not 1 <= len(body["filename"]) <= 255:
        raise ValueError("invalid filename")
    if type(body.get("page_count")) is not int or not 1 <= body["page_count"] <= 500:
        raise ValueError("invalid page_count")
    if body.get("document_type") not in DOCUMENT_TYPES:
        raise ValueError("invalid document_type")
    environment = body.get("environment")
    if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                for k, v in environment.items()):
        raise ValueError("invalid environment")
    regions = body.get("regions")
    if regions is not None and (not isinstance(regions, list) or not all(isinstance(r, dict) for r in regions)):
        raise ValueError("invalid regions")
    yield_seconds = body.get("yield_seconds", YIELD_SECONDS)
    if type(yield_seconds) is not int or not 30 <= yield_seconds <= YIELD_SECONDS:
        raise ValueError("invalid yield_seconds")
    try:
        content = base64.b64decode(body.get("content") or "", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid content") from exc
    if not content or len(content) > MAX_CONTENT_BYTES:
        raise ValueError("invalid content size")
    return content


def read(pages):
    """对账文档：读完一块的字（Document AI，一块最多几页）。"""
    return {"event": "read", "pages": pages}


def extracted(page, text, reread=False):
    """对账文档：一页抽完（模型回的原文，服务器据此更新看板「刚读出」）。"""
    return {"event": "extracted", "page": page, "text": text, "reread": reread}


def unread(page):
    return {"event": "unread", "page": page}


def line(event):
    return (encode(event) + "\n").encode()


def page(number):
    return {"event": "page", "page": number}


def tick():
    """一次调用很久没回（密的页 3.8 可达 180 秒）时隔一段发一行，让中间的连接不因空闲被断开。"""
    return {"event": "tick"}


def result(status, **fields):
    if status not in STATUSES:
        raise ValueError("unknown status")
    return {"event": "result", "status": status, **fields}


def parse_line(raw):
    event = json.loads(raw)
    if not isinstance(event, dict) or event.get("event") not in ("page", "result", "tick", "read", "extracted", "unread"):
        raise ValueError("unknown event")
    if event["event"] in ("tick", "read", "extracted", "unread"):
        return event
    if event["event"] == "page" and (type(event.get("page")) is not int or event["page"] < 1):
        raise ValueError("invalid page event")
    if event["event"] == "result" and event.get("status") not in STATUSES:
        raise ValueError("invalid result event")
    return event
