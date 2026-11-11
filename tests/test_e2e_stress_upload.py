"""40MB 并发上传压测脚本 scripts/e2e/stress_upload.py 的离线测试（docs/work/2026-11-11-prod-stress-round2.md）：
不连网络。合成 PDF 合法、页数与大小对；上传请求里认出会话编号；汇总认出失败与丢件；
本机装了 Playwright 时（STRESS_BROWSER_TEST=1）用真 Chromium 接假页面：几页同时放附件、核大小、走删除。"""
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
import stress_upload as up  # noqa: E402


class PureTest(unittest.TestCase):
    def test_make_pdf_pages_and_size(self):
        path = Path(tempfile.mkdtemp()) / "big.pdf"
        size = up.make_pdf(path, pages=4, target=400_000)
        data = path.read_bytes()
        self.assertEqual(size, len(data))
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertEqual(len(re.findall(rb"/Type /Page /Parent", data)), 4)
        self.assertIn(b"/Count 4", data)
        self.assertLess(abs(size - 400_000), 40_000)
        # xref 里每个对象的偏移都指向「N 0 obj」
        xref = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
        offsets = [int(line[:10]) for line in data[xref:].split(b"\n")[3:] if re.match(rb"^\d{10} 00000 n", line)]
        for n, offset in enumerate(offsets, 1):
            self.assertTrue(data[offset:].startswith(b"%d 0 obj" % n))

    def test_default_target_fits_limit(self):
        self.assertLess(up.TARGET_BYTES + 64 * 1024, up.MAX_BYTES)

    def test_session_of_multipart(self):
        body = (b'------b\r\nContent-Disposition: form-data; name="session_id"\r\n\r\nses_abc123\r\n'
                b'------b\r\nContent-Disposition: form-data; name="file"; filename="x.pdf"\r\n\r\n%PDF')
        self.assertEqual(up.session_of(body), "ses_abc123")
        self.assertEqual(up.session_of(b"nothing"), "")
        self.assertEqual(up.session_of(None), "")

    def test_summarize_counts_lost_and_failed(self):
        lanes = [{"lane": 0, "status": 200, "size_ok": True, "seconds": 10.0},
                 {"lane": 1, "status": 200, "size_ok": False, "seconds": 12.0},
                 {"lane": 2, "status": 413, "seconds": 3.0},
                 {"lane": 3, "error": "TimeoutError: x"}]
        s = up.summarize(lanes)
        self.assertEqual((s["n"], s["ok"], s["failed"]), (4, 1, 3))
        self.assertEqual(s["lost"], [1])
        self.assertEqual(s["max"], 12.0)
        self.assertIn("413", s["statuses"])

    def test_sampler_script_parses_and_is_read_only(self):
        script = ROOT / "scripts" / "e2e" / "stress_upload_sampler.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        text = script.read_text(encoding="utf-8")
        for word in ("docker exec", "docker restart", "docker rm", "mariadb", " rm ", "sudo", "> /"):
            self.assertNotIn(word, text, f"采样脚本只读：不该有 {word!r}")


# 假 CC 页：附件钮选文件后像真页面一样 POST multipart（session_id + file）；接口按会话记下收到的大小
FAKE = """<!doctype html><meta charset=utf-8><body><button class="cc-attach">+</button><script>
window.csrf_token='x';
document.querySelector('.cc-attach').onclick=()=>{const i=document.createElement('input'); i.type='file';
  i.onchange=async()=>{const f=new FormData(); f.append('session_id','ses_'+Math.random().toString(36).slice(2,8)); f.append('file', i.files[0]);
    await fetch('/api/method/pearnly_core.assistant.api.upload',{method:'POST',body:f});}; i.click();};
</script>"""


@unittest.skipUnless(os.environ.get("STRESS_BROWSER_TEST") == "1", "要本机 Playwright（STRESS_BROWSER_TEST=1）")
class FakeWaveTest(unittest.TestCase):
    def test_wave_uploads_checks_and_deletes(self):
        tmp = Path(tempfile.mkdtemp())
        file = tmp / "big.pdf"
        size = up.make_pdf(file, pages=3, target=300_000)
        got, deleted = {}, []

        async def hook(page):
            async def handle(route):
                req, url = route.request, route.request.url
                if url.startswith("https://fake.test/assistant"):
                    return await route.fulfill(status=200, content_type="text/html", body=FAKE)
                if up.UPLOAD in url:
                    sid = up.session_of(req.post_data_buffer)
                    # 第一份当作丢件：服务端记的大小不对
                    got[sid] = size if got else 1
                    await asyncio.sleep(0.5)
                    body = {"fileId": "upl_1", "size_bytes": got[sid]}
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"message": body}))
                if "session_files" in url:
                    sid = re.search(r"session_id=([^&]+)", url).group(1)
                    return await route.fulfill(status=200, content_type="application/json",
                                               body=json.dumps({"message": [{"kind": "upload", "size_bytes": got.get(sid)}]}))
                if "api.delete" in url:
                    deleted.append(req.post_data)
                if "api.listing" in url:
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"message": {"sessions": []}}))
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"message": None}))
            await page.route("https://fake.test/**", handle)

        lanes = asyncio.run(up.wave(3, "https://fake.test/assistant", file, None, hook=hook))
        s = up.summarize(lanes)
        self.assertEqual(s["n"], 3, lanes)
        self.assertTrue(all(x.get("status") == 200 and x.get("session", "").startswith("ses_") for x in lanes), lanes)
        self.assertEqual(s["ok"], 2, lanes)
        self.assertEqual(len(s["lost"]), 1)
        self.assertEqual(len(deleted), 3)
        self.assertTrue(all(x.get("deleted") for x in lanes))
        self.assertTrue(all("page" not in x and "context" not in x for x in lanes))


if __name__ == "__main__":
    unittest.main()
