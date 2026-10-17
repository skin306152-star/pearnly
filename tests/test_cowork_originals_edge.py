#!/usr/bin/env python3
"""甲骨文原件存储（ADR-023）：签名地址、甲骨文上的服务（真起 HTTP，临时目录当存储）、按原件地址交 Claude 的读法。
不连 Frappe、不联网；在生产镜像里跑（要 pymupdf、Pillow）。"""
import base64
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))

from pearnly_core.originals import links as edge_links  # noqa: E402

KEY = "k" * 64
SITE = "erpcowork.pearnly.com"
NAME = edge_links.object_name(SITE, "CWS-" + "a" * 48, ".jpg")


def jpeg(color="white"):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(buffer, "JPEG")
    return buffer.getvalue()


class Links(unittest.TestCase):
    def test_signature_binds_every_parameter(self):
        url = edge_links.upload_url("https://originals.pearnly.com", KEY, NAME, 1234, now=1000)
        query = dict(part.split("=", 1) for part in urlsplit(url).query.split("&"))
        self.assertTrue(edge_links.verify(KEY, "PUT", NAME, query["e"], query["s"], 1234, now=1000))
        self.assertFalse(edge_links.verify(KEY, "PUT", NAME, query["e"], query["s"], 1235, now=1000))  # 改大小
        self.assertFalse(edge_links.verify(KEY, "GET", NAME, query["e"], query["s"], 1234, now=1000))  # 换方法
        self.assertFalse(edge_links.verify(KEY, "PUT", NAME, query["e"], query["s"], 1234, now=10_000))  # 过期
        self.assertFalse(edge_links.verify("other", "PUT", NAME, query["e"], query["s"], 1234, now=1000))  # 别的密钥

    def test_object_names_cannot_escape(self):
        for bad in ("../etc/passwd", f"{SITE}/originals/../../x.jpg", f"{SITE}/previews/CWS-{'a' * 48}.jpg",
                    f"{SITE}/originals/CWS-{'a' * 47}.jpg", "/abs/originals/CWS-x.jpg"):
            self.assertFalse(edge_links.valid_object(bad), bad)
        stored = edge_links.url_of(NAME, "f" * 64)
        self.assertEqual(edge_links.parse_url(stored), (NAME, "f" * 64))
        self.assertIsNone(edge_links.parse_url("gs://bucket/x#1"))
        self.assertEqual(edge_links.preview_prefix(NAME), f"{SITE}/previews/CWS-{'a' * 48}/")


class Server(unittest.TestCase):
    def setUp(self):
        from pearnly_core.originals import server as edge_server
        self.edge = edge_server
        self.dir = tempfile.mkdtemp()
        self.env = patch.dict(os.environ, {"ORIGINALS_ROOT": self.dir})
        self.env.start()
        self.server = edge_server.public_server(0, KEY)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.env.stop()

    def request(self, method, url, body=None, headers=None):
        parts = urlsplit(url)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(method, parts.path + "?" + parts.query, body=body, headers=headers or {})
        response = connection.getresponse()
        try:
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def put(self, content, size=None, name=NAME):
        url = edge_links.upload_url("http://x", KEY, name, len(content) if size is None else size)
        return self.request("PUT", url, content, {"Content-Type": "application/octet-stream"})

    def test_upload_once_then_inspect_download_preview(self):
        content = jpeg()
        status, _body, headers = self.put(content)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(self.put(content)[0], 412)  # 同一对象只写一次：页面认 412 = 之前已经传上去了
        code, answer = self.edge.inspect({"object": NAME, "filename": "a.jpg"})
        self.assertEqual((code, answer["status"], answer["meta"]["kind"], answer["meta"]["page_count"]), (200, "ok", "image", 1))
        self.assertEqual(answer["md5"], hashlib.md5(content).hexdigest())
        sha = answer["meta"]["sha256"]
        status, data, headers = self.request("GET", edge_links.download_url("http://x", KEY, NAME, sha, "ใบกำกับ.jpg"))
        self.assertEqual((status, data), (200, content))
        self.assertIn("attachment", headers["Content-Disposition"])
        status, picture, headers = self.request("GET", edge_links.preview_url("http://x", KEY, NAME, sha, "image", 1))
        self.assertEqual(status, 200)
        self.assertIn(headers["Content-Type"], ("image/png", "image/jpeg"))
        cached = list(Path(self.dir, edge_links.preview_prefix(NAME)).iterdir())
        self.assertEqual(len(cached), 1)  # 画过一次存下，下次直接给
        self.assertEqual(self.request("GET", edge_links.preview_url("http://x", KEY, NAME, sha, "image", 1))[1], picture)
        self.assertEqual(self.edge.fetch({"object": NAME, "sha256": sha})[1]["data"], base64.b64encode(content).decode())
        self.assertEqual(self.edge.delete({"objects": [NAME]})[0], 200)
        self.assertFalse(Path(self.dir, NAME).exists())
        self.assertFalse(Path(self.dir, edge_links.preview_prefix(NAME)).exists())

    def test_bad_signature_size_and_wrong_sha_are_refused(self):
        content = jpeg()
        url = edge_links.upload_url("http://x", KEY, NAME, len(content)).replace("s=", "s=0")
        self.assertEqual(self.request("PUT", url, content)[0], 403)
        self.assertEqual(self.put(content, size=len(content) + 1)[0], 400)
        self.assertFalse(Path(self.dir, NAME).exists())
        self.put(content)
        status = self.request("GET", edge_links.download_url("http://x", KEY, NAME, "0" * 64, "a.jpg"))[0]
        self.assertEqual(status, 404)  # sha256 钉住内容
        self.assertEqual(self.request("GET", "/o/" + NAME + "?e=1&s=x")[0], 403)

    def test_rejected_original_is_removed(self):
        self.put(b"not an image at all")
        code, answer = self.edge.inspect({"object": NAME, "filename": "a.jpg"})
        self.assertEqual(answer, {"status": "rejected", "code": "image_unreadable"})
        self.assertFalse(Path(self.dir, NAME).exists())  # 删掉后页面才能用同一个键重传
        self.assertEqual(self.edge.inspect({"object": NAME, "filename": "a.jpg"})[1]["code"], "original_missing")

    def test_chunked_upload_through_the_tunnel(self):
        content = jpeg("black")
        url = urlsplit(edge_links.upload_url("http://x", KEY, NAME, len(content)))
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.putrequest("PUT", url.path + "?" + url.query)
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders()
        for start in range(0, len(content), 1000):
            piece = content[start:start + 1000]
            connection.send(b"%x\r\n" % len(piece) + piece + b"\r\n")
        connection.send(b"0\r\n\r\n")
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(Path(self.dir, NAME).read_bytes(), content)


class ClaudeByReference(unittest.TestCase):
    def test_service_reads_the_stored_original(self):
        from pearnly_core.claude_ocr import server
        content = jpeg()
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"ORIGINALS_ROOT": root}):
            Path(root, NAME).parent.mkdir(parents=True)
            Path(root, NAME).write_bytes(content)
            pages = server.stored_pages({"object": NAME, "sha256": hashlib.sha256(content).hexdigest()})
            self.assertEqual([m for m, _d in pages], ["image/jpeg"])
            with self.assertRaises(ValueError):
                server.stored_pages({"object": NAME, "sha256": "0" * 64})
            with self.assertRaises(ValueError):
                server.stored_pages({"object": "../../etc/passwd", "sha256": "0" * 64})
            self.assertIsNone(server.stored_pages({"object": NAME.replace("a" * 48, "b" * 48), "sha256": "0" * 64}))


if __name__ == "__main__":
    unittest.main()
