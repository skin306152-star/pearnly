"""上传的文件不读成 bytes（ADR-028 第 2 步，`pearnly_core.originals.local_file`）——要在生产镜像里跑（pymupdf、pypdf、PIL、
anthropic、werkzeug、Frappe），候选离线车道登记。

CC 附件、代录资料区、Cowork 录入、CC 桌面版文件夹取文件原来都把整份文件读成 bytes，再一路交给原件检查、存原件、交引擎、读页数，
一份 40MB 在整个请求里占着 40MB；现在按块落到磁盘，各层按路径 / 按块读。这里核对：
- 磁盘那份与 bytes 走同一层得到的结果完全相同（原件检查的结论、交甲骨文的请求体逐字节）；
- 落盘、交甲骨文、交引擎（真实 HTTP 连接）、读页数、原件检查整条走下来，一份 40MB 进程内存峰值只涨几 MB。
"""
import base64
import gc
import hashlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
_HOME = Path(tempfile.mkdtemp())
(_HOME / "logs").mkdir()
(_HOME / "sites").mkdir()
os.chdir(_HOME / "sites")

from werkzeug.datastructures import FileStorage  # noqa: E402

from pearnly_core.originals import checks, local_file  # noqa: E402

MIB = 1024 * 1024


def _pdf(path, pages=3, side=200):
    """有页数、有图的 PDF（同压测的合成法：每页一张随机 RGB 图）。"""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for i in range(pages):
        img, content, page = len(objs) + 1, len(objs) + 2, len(objs) + 3
        raw = os.urandom(side * side * 3)
        objs.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 /Length %d >>\nstream\n"
                    % (side, side, len(raw)) + raw + b"\nendstream")
        draw = b"q 595 0 0 595 0 123 cm /Im0 Do Q"
        objs.append(b"<< /Length %d >>\nstream\n" % len(draw) + draw + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R /Resources << /XObject << /Im0 %d 0 R >> >> >>"
                    % (content, img))
        kids.append(page)
    objs[1] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (pages, b" ".join(b"%d 0 R" % k for k in kids))
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    Path(path).write_bytes(bytes(out))
    return path


def _png(path):
    from PIL import Image

    Image.new("RGB", (40, 30), (200, 10, 10)).save(path, "PNG")
    return path


class LocalFileBasics(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_size_head_digest_and_whole_bytes_match_the_file(self):
        data = os.urandom(3 * MIB + 7)
        path = self.dir / "a.bin"
        path.write_bytes(data)
        local = local_file.LocalFile(path)
        self.assertEqual((len(local), bool(local), local.head(9)), (len(data), True, data[:9]))
        self.assertEqual(local_file.digest(local), hashlib.sha256(data).hexdigest())
        self.assertEqual(local_file.digest(data), hashlib.sha256(data).hexdigest())
        self.assertEqual(local_file.digest(local, "md5"), hashlib.md5(data).hexdigest())
        self.assertEqual(local_file.as_bytes(local), data)
        self.assertEqual(local_file.head(data, 4), data[:4])
        empty = self.dir / "e.bin"
        empty.write_bytes(b"")
        self.assertFalse(local_file.LocalFile(empty))

    def test_an_upload_is_copied_to_disk_in_chunks_and_removed_after_the_response(self):
        from frappe.utils import CallbackManager

        import frappe

        data = os.urandom(MIB + 3)
        callbacks = CallbackManager()
        with mock.patch.object(frappe, "local", types.SimpleNamespace(request=types.SimpleNamespace(after_response=callbacks))):
            local = local_file.from_upload(FileStorage(io.BytesIO(data), "a.pdf"), MIB + 3)
            self.assertIsNone(local_file.from_upload(FileStorage(io.BytesIO(data), "a.pdf"), MIB + 2), "超限回 None")
            empty = local_file.from_upload(FileStorage(io.BytesIO(b""), "e.pdf"), MIB)
        self.assertEqual(Path(local.path).read_bytes(), data)
        self.assertEqual(len(empty), 0)
        callbacks.run()
        self.assertFalse(os.path.exists(local.path))
        self.assertFalse(os.path.exists(empty.path))


class SameResults(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_the_original_check_says_the_same_for_the_file_on_disk(self):
        pdf, png = _pdf(self.dir / "a.pdf"), _png(self.dir / "b.png")
        broken = self.dir / "c.pdf"
        broken.write_bytes(b"%PDF-1.7 not really")
        for path, name in ((pdf, "a.pdf"), (png, "b.png")):
            self.assertEqual(checks.inspect(name, local_file.LocalFile(path)), checks.inspect(name, Path(path).read_bytes()))
        for path, name in ((broken, "c.pdf"),):
            with self.assertRaises(checks.Rejected) as on_disk:
                checks.inspect(name, local_file.LocalFile(path))
            with self.assertRaises(checks.Rejected) as in_memory:
                checks.inspect(name, Path(path).read_bytes())
            self.assertEqual(on_disk.exception.code, in_memory.exception.code)
        empty = self.dir / "e.pdf"
        empty.write_bytes(b"")
        with self.assertRaises(checks.Rejected) as caught:
            checks.inspect("e.pdf", local_file.LocalFile(empty))
        self.assertEqual(caught.exception.code, "empty")

    def test_oracle_receives_the_same_bytes_from_the_file_on_disk(self):
        from pearnly_core.originals import oracle

        for size in (0, 1, 2, 3, 3 * MIB - 1, 3 * MIB, 3 * MIB + 1, 7 * MIB + 5):
            data = os.urandom(size)
            path = self.dir / f"{size}.bin"
            path.write_bytes(data)
            head = {"object": "t1/ab/cd.pdf", "filename": "ใบกำกับ.pdf"}
            from_disk, from_bytes = oracle.JsonUpload(head, local_file.LocalFile(path)), oracle.JsonUpload(head, data)
            sent = from_disk.read(-1)
            self.assertEqual(sent, from_bytes.read(-1), size)
            self.assertEqual(len(from_disk), len(sent))
            self.assertEqual(json.loads(sent)["data"], base64.b64encode(data).decode())

    def test_heic_photos_are_still_turned_into_jpeg_from_disk(self):
        from pearnly_core.desk import heic

        photo = self.dir / "p.heic"
        photo.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
        self.assertTrue(heic.is_heic(local_file.head(local_file.LocalFile(photo), 16)))


def _rss_mib():
    """匿名内存（RssAnon）：网页容器撞上限算的就是它；按路径打开 PDF 时映射进来的文件页是可回收的页缓存，不算。"""
    with open("/proc/self/status") as status:
        return next(int(line.split()[1]) for line in status if line.startswith("RssAnon:")) // 1024


class Sink(http.server.BaseHTTPRequestHandler):
    """只收不存的本地服务（代 Anthropic Files 接口）：按块读完请求体，记下收到多少。"""
    received = []

    def do_POST(self):
        left, got = int(self.headers.get("Content-Length") or 0), 0
        while left > 0:
            block = self.rfile.read(min(65536, left))
            if not block:
                break
            got += len(block)
            left -= len(block)
        Sink.received.append(got)
        body = json.dumps({"id": "file_x", "type": "file", "filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": got,
                           "created_at": "2026-01-01T00:00:00Z", "downloadable": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Memory(unittest.TestCase):
    def test_a_forty_megabyte_upload_goes_through_every_step_without_being_held(self):
        """落盘 → 原件检查 → 交甲骨文的请求体 → 交引擎（真实 HTTP）→ 读页数，一份 40MB 进程内存峰值只涨几 MB。"""
        from pearnly_core.assistant.engines import anthropic_managed_agents as engine
        from pearnly_core.assistant import files
        from pearnly_core.originals import oracle

        import anthropic
        import frappe

        workdir = Path(tempfile.mkdtemp())
        source = _pdf(workdir / "big.pdf", pages=40, side=580)  # 约 40MB
        size = os.path.getsize(source)
        self.assertGreater(size, 38 * MIB)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        adapter = engine.Adapter.__new__(engine.Adapter)
        adapter.api = anthropic.Anthropic(api_key="x", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=2)
        import pymupdf  # noqa: F401  库本身第一次导入的开销不算在上传头上
        import pypdf  # noqa: F401
        from PIL import Image  # noqa: F401

        gc.collect()
        peak, stop = [_rss_mib()], threading.Event()

        def sample():  # 当前匿名内存的峰值（不用 ru_maxrss：前面造 40MB 测试文件的峰值会盖住后面）
            while not stop.is_set():
                peak[0] = max(peak[0], _rss_mib())
                stop.wait(0.005)

        before = peak[0]
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        with open(source, "rb") as upload, mock.patch.object(files, "_folder", lambda name, origin=files.USER: str(workdir / "kept")), \
                mock.patch.object(frappe, "local", types.SimpleNamespace(request=None)):
            kept = files._keep(types.SimpleNamespace(name="CCS-1"), "upl_1", "big.pdf", upload)
            meta = checks.inspect("big.pdf", kept)
            body = oracle.JsonUpload({"object": "t/a.pdf", "filename": "big.pdf"}, kept)
            for _ in iter(lambda: body.read(65536), b""):
                pass
            engine_id = adapter.upload("big.pdf", kept, "application/pdf")
            pages = files._pdf_pages(kept)
        stop.set()
        sampler.join()
        grown = max(peak[0], _rss_mib()) - before
        server.shutdown()
        self.assertEqual((meta["page_count"], pages, engine_id, Sink.received[-1] > size), (40, 40, "file_x", True))
        self.assertEqual(Path(kept.path).read_bytes()[:64], Path(source).read_bytes()[:64])
        self.assertLess(grown, 12, "原来整条路上一直占着整份 40MB（读成 bytes）")


if __name__ == "__main__":
    unittest.main()
