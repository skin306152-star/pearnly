"""Cloud Run 的 HTTP 入口（WSGI，gunicorn 多线程跑）。不对外公开：只有带我们服务器身份的请求能进来（Cloud Run IAM）。

POST /v1/cowork/invoice   一份发票识别（录入 / 销项税核查），回复是按行的 JSON（见 contract）
POST /v1/cowork/document  一份对账文档（银行账单 / 总账 / 销项税报告），同样按行回复
GET  /health              存活检查（Cloud Run 保留了以 z 结尾的路径，/healthz 到不了容器）
"""
import json
import os

from pearnly_core.ocr_models.orient import orient
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.remote import gcs, runner

MAX_BODY = 30 * 1024 * 1024
_bucket = None


def _backend():
    global _bucket
    if _bucket is None:
        token = gcs.Token()
        _bucket = (gcs.Bucket(os.environ["PEARNLY_OCR_BUCKET"], token), token)
    return _bucket


def application(environ, start_response):
    method, path = environ.get("REQUEST_METHOD"), environ.get("PATH_INFO")
    if method == "GET" and path == "/health":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]
    if method != "POST" or path not in ("/v1/cowork/invoice", "/v1/cowork/document"):
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
    bucket, token = _backend()
    start_response("200 OK", [("Content-Type", "application/x-ndjson"), ("Cache-Control", "no-store")])
    return runner.stream(body, bucket, token, orient=orient, page_source=PageSource)
