"""甲骨文上的 claude-ocr 服务（ADR-022）：POST /v1/read，读一份单据（Haiku→Opus），只调 Anthropic；单据可以随请求带图，
也可以只给存在这台上的原件地址（``original``：对象名 + sha256），由这里读文件、转正、渲染。
只放行生产 IP（安全列表 + iptables）、HTTPS（自签证书，生产钉死指纹）、令牌校验。常驻、与 Anthropic 保持长连接。
环境变量：CLAUDE_OCR_TOKEN、ANTHROPIC_API_KEY、CLAUDE_OCR_CERT / CLAUDE_OCR_KEY、CLAUDE_OCR_PORT（默认 8443）、ORIGINALS_ROOT。
"""
import base64
import hashlib
import hmac
import json
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pearnly_core.claude_ocr import engine, pages

MAX_BODY = 60 * 1024 * 1024
MEDIA = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    client = None

    def log_message(self, fmt, *args):  # 不记录请求内容；只记方法、路径、状态
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, body):
        if status != 200:
            self.close_connection = True  # 请求正文可能没读完：断开，别把残留当成下一个请求
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True}) if self.path == "/healthz" else self._send(404, {"error": "not_found"})

    def authorized(self):
        token = os.environ["CLAUDE_OCR_TOKEN"]
        given = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return hmac.compare_digest(given.encode(), token.encode())

    def do_POST(self):
        if not self.authorized():
            return self._send(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._send(413, {"error": "too_large"})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send(400, {"error": "bad_json"})
        if self.path == "/v1/read":
            return self.read(body)
        answer = self.route(self.path, body)
        return self._send(*answer) if answer else self._send(404, {"error": "not_found"})

    def route(self, path, body):
        """别的内部接口（同一服务里的原件存取，见 ``infrastructure/claude-ocr/serve.py``）：回 ``(状态, 正文)``，不认识回 ``None``。"""
        return None

    def read(self, body):
        try:
            text = body.get("text") or None
            if body.get("original"):
                images = stored_pages(body["original"])
                if images is None:
                    return self._send(404, {"error": "original_missing"})
            else:
                images = [(i["media_type"], base64.b64decode(i["data"])) for i in body.get("images") or []]
                if not (images or isinstance(text, str)) or any(m not in MEDIA for m, _ in images):
                    return self._send(400, {"error": "bad_images"})
            result = engine.run(self.client, body["kind"], images, body.get("own_tax"), text)
        except (KeyError, ValueError) as error:
            return self._send(400, {"error": type(error).__name__})
        except Exception as error:  # noqa: BLE001  Anthropic 出错：交给生产按工作单重试
            return self._send(502, {"error": type(error).__name__, "detail": str(error)[:300]})
        self._send(200, result)


# 存在这台上的原件（原件层，``pearnly_core.originals``）：``<站点>/originals/<原件名><扩展名>``，原件目录由 ORIGINALS_ROOT 给。
def original_path(name):
    """对象名 → 磁盘路径；形状不对（越出原件目录）抛 ``ValueError``（与原件层同一份校验）。"""
    from pearnly_core.originals import links

    root = os.environ.get("ORIGINALS_ROOT", "")
    if not root or not links.valid_object(name):
        raise ValueError("bad original name")
    return os.path.join(root, name)


def stored_pages(original):
    """按原件地址读：读文件、核对 sha256、转正渲染成交给 Claude 的页；不在了回 ``None``，内容不对抛 ``ValueError``。"""
    path = original_path(original.get("object"))
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return None
    if hashlib.sha256(data).hexdigest() != original.get("sha256"):
        raise ValueError("original checksum mismatch")
    media_type = pages.media_type_of(path)
    if not media_type:
        raise ValueError("unsupported original")
    return pages.prepare([(data, media_type)])


def main(handler=Handler):
    import anthropic
    handler.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=3, timeout=180)
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("CLAUDE_OCR_PORT", "8443"))), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(os.environ["CLAUDE_OCR_CERT"], os.environ["CLAUDE_OCR_KEY"])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
