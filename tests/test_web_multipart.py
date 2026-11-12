"""网页进程收 multipart 不再整份读进内存（ADR-028，`pearnly_core.web.multipart`）——要在生产镜像里跑（要 Frappe / werkzeug），
候选离线车道 `scripts/ci-candidate-checks.sh` 登记。

Frappe 16.33.0 的 `frappe.app.make_form_dict` 第一行 `request.get_data(as_text=True)` 把整个请求体读进内存、缓存到请求结束并转成文本：
一份 40MB 上传峰值 +152MB（11-12 生产镜像实测）。替换后 multipart 请求一边解析一边把原始字节写进临时文件，`get_data` 改成按需从文件读；
这里逐项核对：`frappe.form_dict`、`request.files`、`request.form`、`get_data()` / `get_data(as_text=True)` / `request.data`、读完的流、
超限 413，与原实现完全一致；非 multipart 请求原样交给原函数；Frappe 的 `make_form_dict` 源码一变测试就红。
"""
import gc
import hashlib
import inspect
import io
import os
import resource
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
# 导入 frappe.app 时 Frappe 要在 ../logs 建日志文件：不管候选车道从哪个目录起，都先站到一个自带 logs 的临时目录里
_HOME = Path(tempfile.mkdtemp())
(_HOME / "logs").mkdir()
(_HOME / "sites").mkdir()
os.chdir(_HOME / "sites")

import frappe  # noqa: E402
import frappe.app  # noqa: E402
from frappe.utils import CallbackManager  # noqa: E402
from werkzeug.exceptions import RequestEntityTooLarge  # noqa: E402
from werkzeug.test import EnvironBuilder  # noqa: E402
from werkzeug.wrappers import Request  # noqa: E402

ORIGINAL = frappe.app.make_form_dict
from pearnly_core.web import multipart  # noqa: E402

multipart.install()  # 单独跑哪一类都一样（不靠先导入 wsgi）

LIMIT = 41 * 1024 * 1024
BOUNDARY = "----WebKitFormBoundaryAbCdEfGhIjKlMnOp"


def _raw(parts, epilogue=b""):
    """手写 multipart 请求体：parts 是 (头部字节, 内容字节)；能放 werkzeug 不会替我们拼的东西（非 UTF-8 文件名、结尾附言）。"""
    body = b""
    for head, content in parts:
        body += b"--" + BOUNDARY.encode() + b"\r\n" + head + b"\r\n\r\n" + content + b"\r\n"
    return body + b"--" + BOUNDARY.encode() + b"--\r\n" + epilogue


def _environ(path="/api/method/x", query="", data=None, body=None, content_type=None, method="POST", terminated=False):
    if body is not None:
        builder = EnvironBuilder(method=method, path=path, query_string=query, input_stream=io.BytesIO(body),
                                 content_type=content_type or f"multipart/form-data; boundary={BOUNDARY}",
                                 content_length=len(body))
    else:
        builder = EnvironBuilder(method=method, path=path, query_string=query, data=data, content_type=content_type)
    environ = builder.get_environ()
    raw = environ["wsgi.input"].read()
    environ["wsgi.input"] = io.BytesIO(raw)
    if terminated:
        environ.pop("CONTENT_LENGTH", None)
        environ["wsgi.input_terminated"] = True
    return environ


def _snapshot(function, environ, limit=LIMIT):
    """跑一次 make_form_dict，取出后面的代码可能读到的一切。超限时回异常类型。"""
    request = Request(dict(environ, **{"wsgi.input": io.BytesIO(environ["wsgi.input"].getvalue())}))
    request.max_content_length = limit
    request.after_response = CallbackManager()
    frappe.local.form_dict = frappe._dict()
    try:
        function(request)
    except RequestEntityTooLarge as error:
        return ("413", type(error).__name__)
    files = {key: [(item.filename, item.mimetype, item.stream.read()) for item in request.files.getlist(key)]
             for key in request.files}
    snapshot = {"form_dict": dict(frappe.local.form_dict), "files": files, "form": request.form.to_dict(flat=False),
                "args": request.args.to_dict(flat=False), "data": request.data, "raw": request.get_data(),
                "text": request.get_data(as_text=True), "raw_again": request.get_data(cache=False),
                "parsed_raw": request.get_data(parse_form_data=True), "rest": request.stream.read()}
    request.after_response.run()
    request.close()
    return snapshot


PDF = b"%PDF-1.7\n" + bytes(range(256)) * 300 + b"\n%%EOF\n"
CASES = lambda: {  # 每次新建（文件对象读过一次就关了）
    # Desk 原生上传（`upload_file`）：私有、文件夹、挂到单据
    "desk_upload_file": dict(path="/api/method/upload_file", data={
        "is_private": "1", "folder": "Home/Attachments", "doctype": "ERP Purchase", "docname": "PUR-0001",
        "fieldname": "", "file": (io.BytesIO(PDF), "invoice.pdf", "application/pdf")}),
    # 带普通字段的上传（CC 附件、代录资料区、Cowork 录入）
    "fields_and_file": dict(path="/api/method/pearnly_core.assistant.api.upload", query="_=1700000000000",
                            data={"session_id": "CCS-1", "request_key": "k-1", "file": (io.BytesIO(PDF), "a.pdf", "application/pdf")}),
    # 字段值像 JSON
    "json_like_values": dict(data={"data": '{"a": 1, "b": [1, 2]}', "args": "[1, 2, 3]", "filters": "null",
                                   "file": (io.BytesIO(b"x" * 10), "x.txt", "text/plain")}),
    # 非 UTF-8 文件名（latin-1 字节）、泰文 UTF-8 文件名与字段值、两种混在一起
    "non_utf8_filename": dict(body=_raw([
        (b'Content-Disposition: form-data; name="file"; filename="caf\xe9 \xff.pdf"\r\nContent-Type: application/pdf', PDF)])),
    "thai_filename": dict(body=_raw([
        ('Content-Disposition: form-data; name="file"; filename="ใบกำกับ.pdf"\r\nContent-Type: application/pdf'.encode(), b"%PDF"),
        (b'Content-Disposition: form-data; name="note"', "บันทึก".encode())])),
    "mixed_names": dict(body=_raw([
        (b'Content-Disposition: form-data; name="file"; filename="caf\xe9 \xff.pdf"\r\nContent-Type: application/pdf', PDF),
        ('Content-Disposition: form-data; name="other"; filename="ใบกำกับ.pdf"\r\nContent-Type: application/pdf'.encode(), b"%PDF"),
        (b'Content-Disposition: form-data; name="note"', "บันทึก".encode())])),
    # 坏的 multipart（分隔行对不上）：原实现解析不出、原始字节照样在
    "broken": dict(body=b"--not-the-boundary\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\n1\r\n"),
    # 空文件
    "empty_file": dict(data={"session_id": "CCS-1", "file": (io.BytesIO(b""), "empty.pdf", "application/pdf")}),
    # 多文件（同名字段两份 + 另一个字段一份）
    "many_files": dict(data={"file": [(io.BytesIO(b"one" * 1000), "1.jpg", "image/jpeg"), (io.BytesIO(b"two" * 2000), "2.png", "image/png")],
                             "extra": (io.BytesIO(b"3" * 3), "3.csv", "text/csv"), "label": "x"}),
    # 只有字段、没有文件；查询串与表单同名时表单覆盖、`_` 去掉
    "fields_only": dict(query="cmd=frappe.ping&_=1&a=1", body=_raw([(b'Content-Disposition: form-data; name="a"', b"2"),
                                                                    (b'Content-Disposition: form-data; name="_"', b"9")])),
    # 一个部分都没有；结尾附言（解析器不读的尾巴也要在原始字节里）
    "no_parts": dict(body=b"--" + BOUNDARY.encode() + b"--\r\n"),
    "epilogue": dict(body=_raw([(b'Content-Disposition: form-data; name="a"', b"1")], epilogue=b"trailing bytes\r\n")),
    # 跑大于 werkzeug 内存阈值（500KB）的文件：落临时文件那一路
    "large_file": dict(data={"file": (io.BytesIO(b"\x00\x01" * 400_000), "big.bin", "application/octet-stream")}),
}


class SameAsFrappe(unittest.TestCase):
    """multipart 请求：替换后的结果与 Frappe 原实现逐项一致。"""

    def test_every_case_matches_the_original(self):
        for name, spec in CASES().items():
            with self.subTest(name):
                environ = _environ(**spec)
                self.assertEqual(_snapshot(multipart.make_form_dict, environ), _snapshot(ORIGINAL, environ))

    def test_over_the_limit_is_413_the_same_way(self):
        environ = _environ(**CASES()["large_file"])
        self.assertEqual(_snapshot(multipart.make_form_dict, environ, limit=1000), ("413", "RequestEntityTooLarge"))
        self.assertEqual(_snapshot(ORIGINAL, environ, limit=1000), ("413", "RequestEntityTooLarge"))
        # 不带 Content-Length、服务器负责截断的流（`wsgi.input_terminated`）：原实现超限时按上限静默截断再解析，
        # 这种请求整个交回原函数，结果一样
        streamed = _environ(**CASES()["large_file"], terminated=True)
        self.assertEqual(_snapshot(multipart.make_form_dict, streamed, limit=1000), _snapshot(ORIGINAL, streamed, limit=1000))
        self.assertEqual(_snapshot(multipart.make_form_dict, streamed), _snapshot(ORIGINAL, streamed))
        request = Request(_environ(**CASES()["large_file"], terminated=True))
        request.after_response = CallbackManager()
        with mock.patch.object(multipart, "_original", wraps=ORIGINAL) as original:
            multipart.make_form_dict(request)
        original.assert_called_once_with(request)

    def test_other_requests_go_to_the_original_untouched(self):
        others = [dict(data='{"a": 1}', content_type="application/json"), dict(data={"a": "1", "b": "2"}),
                  dict(method="GET", query="a=1&_=2", data=None), dict(data="raw text", content_type="text/plain"),
                  dict(data=b'{"a": 1}', content_type="application/vnd.api+json")]
        for spec in others:
            with self.subTest(spec):
                environ = _environ(**spec)
                seen = []
                with mock.patch.object(multipart, "_original", side_effect=lambda request: seen.append(request) or ORIGINAL(request)):
                    request = Request(environ)
                    request.after_response = CallbackManager()
                    multipart.make_form_dict(request)
                self.assertEqual([id(r) for r in seen], [id(request)], "整个请求原样交给原函数")
                self.assertEqual(_snapshot(multipart.make_form_dict, environ), _snapshot(ORIGINAL, environ))

    def test_the_raw_copy_is_closed_after_the_response(self):
        environ = _environ(**CASES()["fields_and_file"])
        request = Request(environ)
        request.max_content_length = LIMIT
        request.after_response = CallbackManager()
        frappe.local.form_dict = frappe._dict()
        multipart.make_form_dict(request)
        spool = multipart._spool_of(request)
        self.assertFalse(spool.closed)
        request.after_response.run()
        self.assertTrue(spool.closed)


class Memory(unittest.TestCase):
    def test_a_forty_megabyte_upload_is_not_held_in_memory(self):
        payload = b"%PDF-1.7\n" + bytes(range(256)) * (39 * 4096)
        environ = _environ(path="/api/method/upload", data={"session_id": "CCS-1", "file": (io.BytesIO(payload), "big.pdf", "application/pdf")})
        body = environ["wsgi.input"].getvalue()
        del payload
        gc.collect()

        def peak(function):
            request = Request(dict(environ, **{"wsgi.input": io.BytesIO(body)}))
            request.max_content_length = LIMIT
            request.after_response = CallbackManager()
            frappe.local.form_dict = frappe._dict()
            before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
            function(request)
            request.files["file"].stream.read(1)
            grown = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024 - before
            request.after_response.run()
            request.close()
            return grown

        # 先量替换后的（峰值只会升不会降），再量原实现
        self.assertLess(peak(multipart.make_form_dict), 10, "替换后一份 40MB 上传不该再整份进内存")
        self.assertGreater(peak(ORIGINAL), 60, "原实现整份读进内存并转成文本（对照，说明量得出来）")


class PinnedFrappe(unittest.TestCase):
    def test_frappe_make_form_dict_is_the_reviewed_one(self):
        source = inspect.getsource(ORIGINAL)
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), multipart.MAKE_FORM_DICT_SHA256,
                         f"Frappe {frappe.__version__} 的 frappe.app.make_form_dict 变了：按 ADR-028 重新核对替换的那段，"
                         "核对过再改 MAKE_FORM_DICT_SHA256")

    def test_install_replaces_it_once_and_refuses_a_different_frappe(self):
        saved = frappe.app.make_form_dict
        try:
            frappe.app.make_form_dict = ORIGINAL
            self.assertTrue(multipart.install())
            self.assertIs(frappe.app.make_form_dict, multipart.make_form_dict)
            self.assertTrue(multipart.install(), "再装一次不重复包")
            self.assertIs(multipart._original, ORIGINAL)
            frappe.app.make_form_dict = ORIGINAL
            with mock.patch.object(multipart, "MAKE_FORM_DICT_SHA256", "0" * 64), mock.patch("sys.stderr", new=io.StringIO()) as err:
                self.assertFalse(multipart.install())
            self.assertIs(frappe.app.make_form_dict, ORIGINAL, "Frappe 变了就不替换，照原样收")
            self.assertIn("ADR-028", err.getvalue())
        finally:
            frappe.app.make_form_dict = saved

    def test_the_entry_installs_it_and_serves_frappes_application(self):
        from pearnly_core.web import wsgi

        self.assertTrue(wsgi.INSTALLED)
        self.assertIs(frappe.app.make_form_dict, multipart.make_form_dict)
        self.assertIs(wsgi.application, frappe.app.application)


if __name__ == "__main__":
    unittest.main()
