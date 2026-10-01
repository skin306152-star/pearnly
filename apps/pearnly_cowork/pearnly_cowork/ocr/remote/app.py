"""Cloud Run 的 HTTP 入口（WSGI，gunicorn 多线程跑）。不对外公开：只有带我们服务器身份的请求能进来（Cloud Run IAM）。

POST /v1/cowork/invoice   一份发票识别（录入 / 销项税核查），回复是按行的 JSON（见 contract）
POST /v1/cowork/document  一份对账文档（银行账单 / 总账 / 销项税报告），同样按行回复
POST /v1/cowork/inspect   浏览器直传到原件桶的一份原件读一遍、核对（ADR-016），回复一个 JSON
POST /v1/cowork/preview   原件桶里一份原件的一页预览（PNG，大的照片 / 扫描件 JPEG）；不行时回 422 + {"code": …}
GET  /health              存活检查（Cloud Run 保留了以 z 结尾的路径，/healthz 到不了容器）
"""
import json
import os

from pearnly_core.ocr_models.orient import orient
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.originals import preview_type
from pearnly_cowork.ocr.remote import gcs, runner

MAX_BODY = 30 * 1024 * 1024
_bucket = None
_originals = None


def _backend():
    global _bucket
    if _bucket is None:
        token = gcs.Token()
        _bucket = (gcs.Bucket(os.environ["PEARNLY_OCR_BUCKET"], token), token)
    return _bucket


def _originals_bucket():
    global _originals
    name = os.environ.get("PEARNLY_ORIGINALS_BUCKET")
    if _originals is None and name:
        _originals = gcs.Bucket(name, _backend()[1])
    return _originals


def application(environ, start_response):
    method, path = environ.get("REQUEST_METHOD"), environ.get("PATH_INFO")
    if method == "GET" and path == "/health":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]
    if method != "POST" or path not in ("/v1/cowork/invoice", "/v1/cowork/document", "/v1/cowork/inspect",
                                                  "/v1/cowork/preview"):
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if not 0 < length <= MAX_BODY:
        start_response("413 Payload Too Large", [("Content-Type", "text/plain")])
        return [b"too large"]
    try:
        body = json.loads(environ["wsgi.input"].read(length))
    except ValueError:
        start_response("400 Bad Request", [("Content-Type", "text/plain")])
        return [b"bad json"]
    if path == "/v1/cowork/inspect":
        originals = _originals_bucket()
        result = runner.inspect(body, originals) if originals else {"status": "error", "code": "original_unavailable"}
        start_response("200 OK", [("Content-Type", "application/json"), ("Cache-Control", "no-store")])
        return [json.dumps(result).encode()]
    if path == "/v1/cowork/preview":
        png, code = runner.preview(body, _originals_bucket())
        if png is None:
            start_response("422 Unprocessable Entity", [("Content-Type", "application/json"), ("Cache-Control", "no-store")])
            return [json.dumps({"code": code}).encode()]
        start_response("200 OK", [("Content-Type", preview_type(png)), ("Cache-Control", "no-store")])
        return [png]
    bucket, token = _backend()
    start_response("200 OK", [("Content-Type", "application/x-ndjson"), ("Cache-Control", "no-store")])
    return runner.stream(body, bucket, token, orient=orient, page_source=PageSource, originals=_originals_bucket())
