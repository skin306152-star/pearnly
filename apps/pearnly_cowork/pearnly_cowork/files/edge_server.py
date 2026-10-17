"""甲骨文上的 Cowork 原件存储（ADR-023，不依赖 Frappe）：选了 Claude 识别的租户，原件存这台、页数与预览在这台算。

两组接口，同一个存储目录（``ORIGINALS_ROOT``）：

- **对浏览器**（:class:`PublicHandler`，只听本机端口，经 Cloudflare 隧道接 ``originals.pearnly.com``，这台不对外开端口）：
  签名地址上传（PUT，同一对象只写一次，写过回 412）、下载（GET ``/o/``）、预览（GET ``/p/``，按需渲染并存下，下次直接给）。
  签名由生产签（``edge_links``），这里只验；生产不经手原件字节。
- **对生产**（:func:`route`，挂在 claude-ocr 服务的 8443 上：只放行生产 IP、钉死证书、带令牌）：上传后核对（与服务器收原件
  同一份 ``ocr.originals.inspect``，不通过就删掉）、删除（删票后原件与预览一起删）、取回字节（导出打包时用）。

渲染与核对同一时刻最多 ``WORK_SLOTS`` 份，多的稍等；这台 4 核 24G，压测前按这个保守值。
"""
import base64
import hashlib
import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from pearnly_cowork.files import edge_links
from pearnly_cowork.ocr import originals
from pearnly_cowork.ocr.inputs import MAX_FILE_BYTES

PREVIEW_VERSION = "1"  # 画法变了就加一，旧图自然不再用
WORK_SLOTS = threading.BoundedSemaphore(int(os.environ.get("ORIGINALS_WORK_SLOTS", "3")))
CHUNK = 1024 * 1024


def root():
    value = os.environ.get("ORIGINALS_ROOT", "")
    if not value:
        raise RuntimeError("ORIGINALS_ROOT is not set")
    return value


def path_of(name):
    if not edge_links.valid_object(name):
        raise ValueError("bad object name")
    return os.path.join(root(), name)


def _read(name):
    try:
        with open(path_of(name), "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


# ── 对生产（内部接口）─────────────────────────────────────────────────────────────────────────────────────


def inspect(body):
    """上传后核对：读出这份原件，过与服务器收原件同一份检查；不通过就删掉（页面才能用同一个键重传）。"""
    name, filename = body.get("object"), body.get("filename")
    if not edge_links.valid_object(name):
        return 400, {"status": "error", "code": "invalid_request"}
    with WORK_SLOTS:
        content = _read(name)
        if content is None:
            return 200, {"status": "rejected", "code": "original_missing"}
        try:
            meta = originals.inspect(filename, content)
        except (originals.Rejected, originals.OcrInputError) as error:
            _remove(name)
            return 200, {"status": "rejected", "code": error.code}
    return 200, {"status": "ok", "meta": meta, "md5": hashlib.md5(content).hexdigest()}


def _remove(name):
    try:
        os.remove(path_of(name))
    except FileNotFoundError:
        pass
    shutil.rmtree(os.path.join(root(), edge_links.preview_prefix(name)), ignore_errors=True)


def delete(body):
    """删掉这些原件与它们的预览（删票后，``files.service.purge``）；已经不在的算删过。"""
    names = body.get("objects") or []
    if not isinstance(names, list) or not all(edge_links.valid_object(name) for name in names):
        return 400, {"error": "invalid_request"}
    for name in names:
        _remove(name)
    return 200, {"deleted": len(names)}


def fetch(body):
    """取回原件字节（导出打包等少数要整份原件的地方）：核对 sha256 后 base64 返回。"""
    name, sha256 = body.get("object"), body.get("sha256")
    if not edge_links.valid_object(name):
        return 400, {"error": "invalid_request"}
    content = _read(name)
    if content is None:
        return 404, {"error": "original_missing"}
    if hashlib.sha256(content).hexdigest() != sha256:
        return 409, {"error": "original_mismatch"}
    return 200, {"data": base64.b64encode(content).decode()}


ROUTES = {"/v1/originals/inspect": inspect, "/v1/originals/delete": delete, "/v1/originals/fetch": fetch}


def route(path, body):
    handler = ROUTES.get(path)
    return handler(body) if handler else None


# ── 对浏览器（签名地址）────────────────────────────────────────────────────────────────────────────────────


def preview(name, sha256, kind, page):
    """一页预览图：存过就直接给，没有就画（与服务器、Cloud Run 同一份 ``render_page``）并存下。"""
    folder = os.path.join(root(), edge_links.preview_prefix(name))
    cached = os.path.join(folder, f"v{PREVIEW_VERSION}-{sha256[:16]}-p{int(page)}")
    try:
        with open(cached, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        pass
    with WORK_SLOTS:
        content = _read(name)
        if content is None or hashlib.sha256(content).hexdigest() != sha256:
            return None
        picture = originals.render_page(kind, content, int(page))
    os.makedirs(folder, exist_ok=True)
    temporary = cached + f".{os.getpid()}.{threading.get_ident()}.tmp"
    with open(temporary, "wb") as handle:
        handle.write(picture)
    os.replace(temporary, cached)
    return picture


class PublicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    signing_key = ""

    def log_message(self, fmt, *args):  # 只记方法与状态，不记签名与文件名
        sys.stderr.write("public %s\n" % (fmt % args).split("?")[0])

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "3600")

    def _reply(self, status, data=b"", content_type="text/plain; charset=utf-8", headers=()):
        if status >= 400:
            self.close_connection = True
        self.send_response(status)
        self._cors()
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _target(self):
        parts = urlsplit(self.path)
        kind, _sep, name = parts.path.lstrip("/").partition("/")
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        return kind, unquote(name), query

    def do_OPTIONS(self):
        self._reply(204)

    def do_GET(self):
        if self.path == "/healthz":
            return self._reply(200, b"ok")
        kind, name, q = self._target()
        if kind == "o" and edge_links.verify(self.signing_key, "GET", name, q.get("e"), q.get("s"), q.get("h", ""),
                                             q.get("d", "")):
            content = _read(name)
            if content is None or hashlib.sha256(content).hexdigest() != q.get("h"):
                return self._reply(404, b"not found")
            return self._reply(200, content, "application/octet-stream",
                               [("Content-Disposition", "attachment; filename*=UTF-8''" + quote(q.get("d", ""), safe="")),
                                ("Cache-Control", "private, no-store")])
        if kind == "p" and str(q.get("n", "")).isdigit() and edge_links.verify(
                self.signing_key, "PREVIEW", name, q.get("e"), q.get("s"), q.get("h", ""), q.get("k", ""), int(q["n"])):
            try:
                picture = preview(name, q.get("h", ""), q.get("k", ""), int(q["n"]))
            except originals.Rejected as error:
                return self._reply(422, error.code.encode())
            if picture is None:
                return self._reply(404, b"not found")
            return self._reply(200, picture, originals.preview_type(picture),
                               [("Cache-Control", "private, max-age=300"), ("Content-Disposition", "inline")])
        return self._reply(403, b"forbidden")

    def _body(self, limit):
        """请求正文：认 Content-Length，也认分块传输（经隧道转过来时可能是分块的）。超过 ``limit`` 回 ``None``。"""
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            data = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    return bytes(data)
                data += self.rfile.read(size)
                self.rfile.readline()
                if len(data) > limit:
                    return None
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if 0 <= length <= limit else None

    def do_PUT(self):
        kind, name, q = self._target()
        size = q.get("n", "")
        if kind != "o" or not size.isdigit() or not edge_links.verify(self.signing_key, "PUT", name, q.get("e"),
                                                                       q.get("s"), int(size)):
            return self._reply(403, b"forbidden")
        target = path_of(name)
        if os.path.exists(target):
            return self._reply(412, b"exists")  # 同一对象只写一次：页面认 412 = 之前已经传上去了
        content = self._body(min(int(size), MAX_FILE_BYTES))
        if content is None or len(content) != int(size):
            return self._reply(400, b"size mismatch")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = target + f".{os.getpid()}.{threading.get_ident()}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(content)
        try:
            os.link(temporary, target)  # 已经有了就失败：两个请求同时写同一对象，只有一个成功
        except FileExistsError:
            return self._reply(412, b"exists")
        finally:
            os.remove(temporary)
        self._reply(200, json.dumps({"ok": True}).encode(), "application/json")


def public_server(port, signing_key, host="127.0.0.1"):
    PublicHandler.signing_key = signing_key
    return ThreadingHTTPServer((host, int(port)), PublicHandler)


__all__ = ["PublicHandler", "ROUTES", "delete", "fetch", "inspect", "preview", "public_server", "route"]
