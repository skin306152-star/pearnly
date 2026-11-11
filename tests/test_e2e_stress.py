"""线上压测脚本 scripts/e2e/stress.py 的离线测试（docs/work/2026-11-08-prod-stress-100.md）：
不连网络。纯函数（模型用量折算、工具调用、内部名词、估算）、跳过规则（明天的格、缺文件、超预算）；
本机装了 Playwright 时再用真 Chromium 接一个假的 CC 页面与假接口，把一格从放文件、发话、截图、读事件到停下走一遍。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
import run as e2e  # noqa: E402
import stress  # noqa: E402


class PureTest(unittest.TestCase):
    def test_usage_usd_sums_model_requests(self):
        events = [{"type": "span.model_request_end", "model_usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
                  {"type": "span.model_request_end", "model_usage": {"cache_read_input_tokens": 2_000_000, "output_tokens": 40_000}},
                  {"type": "agent.message"}]
        self.assertEqual(stress.usage_usd(events), round(5.0 + 1.0 + 1.0, 4))

    def test_tool_calls_take_status_from_site_runs(self):
        events = [{"type": "agent.custom_tool_use", "id": "a", "name": "cowork_recognize_attachments", "processed_at": "t1"},
                  {"type": "agent.custom_tool_use", "id": "b", "name": "cowork_push_documents", "processed_at": "t2"},
                  {"type": "pearnly.tool_run", "tool_use_id": "b", "status": "failed"},
                  {"type": "user.custom_tool_result", "tool_use_id": "a", "is_error": False}]
        calls = stress.tool_calls(events)
        self.assertEqual([c["name"] for c in calls], ["cowork_recognize_attachments", "cowork_push_documents"])
        self.assertEqual(calls[1]["status"], "failed")
        self.assertFalse(calls[0]["error"])

    def test_internal_words(self):
        seen = {m.group(0) for m in stress.INTERNAL.finditer("ใช้ Express operator แล้ว · CWH-24fbf585ab · 已识别 24 张")}
        self.assertEqual(seen, {"operator", "CWH-24fbf585ab"})
        self.assertFalse(list(stress.INTERNAL.finditer("ระบบกำลังอ่านเอกสาร 12 หน้า · 识别中 · 共 12 页")))

    def test_estimate_counts_lanes(self):
        s = stress.BY_ID["S6"]
        self.assertEqual(stress.estimate(s), round(s.pages * stress.OCR_PER_PAGE + s.cc_cap * s.lanes, 2))

    def test_tomorrow_cells_click_and_tonight_cells_do_not(self):
        for s in stress.SCENARIOS:
            if s.tonight:
                self.assertEqual(s.click, "", f"{s.id} 今晚的格不能点推送 / 开始")
            if s.mode == "operator":
                self.assertTrue(s.company, f"{s.id} 代录新建页没选公司发不出")


class SkipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (stress.SPENT_FILE, stress.LEDGER, e2e.load_accounts, e2e.check_playwright, stress.run_one)
        stress.SPENT_FILE, stress.LEDGER = self.tmp / "spent.json", self.tmp / "ledger.md"
        e2e.load_accounts = lambda: {}
        e2e.check_playwright = lambda: None
        self.ran = []
        stress.run_one = lambda s, *a, **k: self.ran.append(s.id) or {"cc_usd": 0.1, "tools": [{"name": "cowork_recognize_attachments"}] if s.id == "S4" else []}

    def tearDown(self):
        stress.SPENT_FILE, stress.LEDGER, e2e.load_accounts, e2e.check_playwright, stress.run_one = self.saved

    def run_main(self, *argv):
        return stress.main(list(argv) + ["--files", str(self.tmp), "--out", str(self.tmp / "out")])

    def test_tomorrow_cell_needs_flag(self):
        self.assertEqual(self.run_main("--scenario", "S8"), 3)
        self.assertEqual(self.ran, [])

    def test_missing_files_skip(self):
        self.assertEqual(self.run_main("--scenario", "S4"), 3)
        self.assertEqual(self.ran, [])

    def test_budget_skip_counts_spent(self):
        for name in stress.BY_ID["S4"].files:
            (self.tmp / name).write_bytes(b"%PDF-1.4")
        stress.add_spent(24.9)
        self.assertEqual(self.run_main("--scenario", "S4", "--budget", "25"), 3)
        self.assertEqual(self.ran, [])

    def test_runs_and_books_spend(self):
        for name in stress.BY_ID["S4"].files:
            (self.tmp / name).write_bytes(b"%PDF-1.4")
        self.assertEqual(self.run_main("--scenario", "S4", "--budget", "25"), 0)
        self.assertEqual(self.ran, ["S4"])
        self.assertAlmostEqual(stress.spent(), 0.1 + stress.BY_ID["S4"].pages * stress.OCR_PER_PAGE)
        self.assertIn("压测 S4", stress.LEDGER.read_text(encoding="utf-8"))

    def test_write_cell_needs_company(self):
        for name in stress.BY_ID["S8"].files:
            (self.tmp / name).write_bytes(b"%PDF-1.4")
        saved = os.environ.pop("PEARNLY_STRESS_WRITE_COMPANY", None)
        try:
            self.assertEqual(self.run_main("--scenario", "S8", "--allow-tomorrow"), 3)
            self.assertEqual(self.ran, [])
            os.environ["PEARNLY_STRESS_WRITE_COMPANY"] = "TEST"
            self.assertEqual(self.run_main("--scenario", "S8", "--allow-tomorrow", "--budget", "32"), 0)
            self.assertEqual(self.ran, ["S8"])
        finally:
            os.environ.pop("PEARNLY_STRESS_WRITE_COMPANY", None)
            if saved is not None:
                os.environ["PEARNLY_STRESS_WRITE_COMPANY"] = saved

    def test_month_cap_stops_new_cells(self):
        for name in stress.BY_ID["S4"].files:
            (self.tmp / name).write_bytes(b"%PDF-1.4")
        stress.LEDGER.write_text("| x | 别的窗口 | … | 0 | 1 | **11 月累计约 119.5** |\n", encoding="utf-8")
        self.assertEqual(self.run_main("--scenario", "S4", "--budget", "32"), 3)
        self.assertEqual(self.ran, [])
        self.assertEqual(self.run_main("--scenario", "S4", "--budget", "32", "--month-cap", "200"), 0)

    def test_write_cells_resolve_and_answer(self):
        for s in stress.SCENARIOS:
            if not s.tonight:
                self.assertTrue(s.company in (stress.WRITE, stress.ERP_WRITE), f"{s.id} 写入格的公司要等主会话定")
                self.assertTrue(s.auto or s.probe_real, f"{s.id} 写入格要替用户答卡")
        self.assertTrue(stress.BY_ID["S8a"].probe_real)
        self.assertFalse(stress.BY_ID["S8a"].auto, "P0 实证格不替用户批准推送")

    def test_rejected_upload_not_charged(self):
        for name in stress.BY_ID["S3"].files:
            (self.tmp / name).write_bytes(b"%PDF-1.4")
        self.assertEqual(self.run_main("--scenario", "S3", "--budget", "25"), 0)
        self.assertAlmostEqual(stress.spent(), 0.1)   # 只算模型费：没调识别


# 假 CC 页面：附件钮、输入框、发送 / 停止、状态行、助手回复、提问卡；接口：侧栏列表与会话事件
FAKE = """<!doctype html><meta charset=utf-8><body>
<div data-slot="aui_thread-viewport"><div id="log"></div></div>
<div class="aui-composer-attachments" id="atts"></div>
<button class="cc-attach">+</button><textarea class="aui-composer-input"></textarea>
<button class="aui-composer-send" id="send">send</button><div data-slot="cc-run-indicator" id="line"></div>
<script>
window.csrf_token='x'; window.__sent=0;
document.querySelector('.cc-attach').onclick=()=>{const i=document.createElement('input'); i.type='file'; i.multiple=true;
  i.onchange=()=>{for(const f of i.files){const d=document.createElement('div'); d.className='aui-attachment-root'; d.textContent=f.name; atts.append(d);}};
  i.click();};
document.addEventListener('click', e=>{ if(e.target.id!=='send') return; window.__sent++;
  const b=e.target; b.className='aui-composer-cancel'; line.textContent='识别中 · 共 5 页';
  setTimeout(()=>{ line.textContent=''; b.className='aui-composer-send';
    const m=document.createElement('div'); m.setAttribute('data-slot','aui_assistant-message-content');
    m.textContent='已读 5 张，Express operator 需要选客户'; log.append(m);
    const a=document.createElement('div'); a.className='cc-askq'; a.textContent='BIG C / BIG C-001 / BIG C-002'; log.append(a);
  }, 2500); });
</script>"""
# 写入格：发话后出一张提问卡（两项，要选一项再点下一步），答完出旧版开始卡，点了出「录吧」快捷回复（一句话下令），点了助手说开始录了
FAKE_WRITE = FAKE.replace("""    const a=document.createElement('div'); a.className='cc-askq'; a.textContent='BIG C / BIG C-001 / BIG C-002'; log.append(a);""",
"""    const a=document.createElement('div'); a.className='cc-askq';
    a.innerHTML='<div data-slot="cc-askq-question">ลูกค้าคือใคร</div><button data-slot="cc-askq-option" aria-checked="false">BIG C-001</button>'
      +'<button data-slot="cc-askq-option" aria-checked="false">BIG C-002</button><button data-slot="cc-askq-next" disabled>ถัดไป</button>';
    log.append(a);
    a.querySelectorAll('[data-slot=cc-askq-option]').forEach(o=>o.onclick=()=>{o.setAttribute('aria-checked','true'); a.querySelector('[data-slot=cc-askq-next]').disabled=false;});
    a.querySelector('[data-slot=cc-askq-next]').onclick=()=>{a.remove();
      const c=document.createElement('div'); c.innerHTML='<span data-slot="cc-op-target">บันทึกลง Express ของ TEST</span><button data-slot="cc-op-start">เริ่ม</button>';
      log.append(c); c.querySelector('button').onclick=()=>{c.remove();
        const r=document.createElement('div'); r.setAttribute('data-slot','cc-quick-replies'); r.innerHTML='<button>บันทึกเลย</button>';
        document.body.append(r); r.querySelector('button').onclick=()=>{r.remove(); const m=document.createElement('div');
          m.setAttribute('data-slot','aui_assistant-message-content'); m.textContent='เริ่มบันทึกแล้ว'; log.append(m);};};};""")
EVENTS = [{"type": "agent.custom_tool_use", "id": "u1", "name": "cowork_recognize_attachments", "processed_at": "t"},
          {"type": "pearnly.tool_run", "tool_use_id": "u1", "status": "completed"},
          {"type": "span.model_request_end", "model_usage": {"input_tokens": 10000, "output_tokens": 1000}}]


def fake_api(page, html=None):
    html = html or FAKE

    def handle(route):
        url = route.request.url
        if "/api/method/pearnly_core.assistant.api.listing" in url:
            sent = page.evaluate("window.__sent || 0")
            body = {"message": {"sessions": [{"id": "s1", "status": "idle"}] if sent else []}}
        elif "/api/method/pearnly_core.assistant.api.events" in url:
            body = {"message": {"events": EVENTS}}
        elif url.startswith("https://fake.test/assistant"):
            return route.fulfill(status=200, content_type="text/html", body=html)
        else:
            body = {"message": None}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    page.route("https://fake.test/**", handle)


try:
    import playwright  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False


@unittest.skipUnless(HAVE_PLAYWRIGHT and os.environ.get("STRESS_BROWSER_TEST") == "1", "要本机 Playwright（STRESS_BROWSER_TEST=1）")
class FakePageTest(unittest.TestCase):
    def test_one_cell_against_fake_cc(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.pdf").write_bytes(b"%PDF-1.4 fake")
        (tmp / "b.pdf").write_bytes(b"%PDF-1.4 fake")
        cell = stress.Scenario("T1", "自测", "cowork", "desktop", "chat", "录进 Express", ["a.pdf", "b.pdf"], 5, "自测")
        cell.minutes = 1
        r = stress.run_one(cell, "cowork", tmp, tmp / "out", {}, login=lambda page, *a: page.goto("https://fake.test/assistant"),
                           hook=fake_api, shot_every=1, idle_after=3)
        self.assertNotIn("error", r, r.get("error"))
        self.assertEqual(r["session"], "s1")
        self.assertEqual([t["name"] for t in r["tools"]], ["cowork_recognize_attachments"])
        self.assertEqual(r["tools"][0]["status"], "completed")
        self.assertAlmostEqual(r["cc_usd"], round(10000 * 5 / 1e6 + 1000 * 25 / 1e6, 4))
        self.assertIn("operator", r["internal_seen"])
        self.assertEqual(r["max_asks"], 1)
        self.assertTrue(any(row["line"] == "识别中 · 共 5 页" and row["stop"] for row in r["trail"]))
        self.assertTrue(list(Path(r["shots"]).glob("*.png")))


    def test_write_cell_answers_cards_and_starts(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.pdf").write_bytes(b"%PDF-1.4 fake")
        cell = stress.Scenario("T2", "自测写入", "cowork", "desktop", "chat", "录进 Express", ["a.pdf"], 5, "自测",
                               click="start", auto=True, minutes=1)
        r = stress.run_one(cell, "cowork", tmp, tmp / "out", {}, login=lambda page, *a: page.goto("https://fake.test/assistant"),
                           hook=lambda page: fake_api(page, FAKE_WRITE), shot_every=1, idle_after=3)
        self.assertNotIn("error", r, r.get("error"))
        did = [a["did"].split(":")[0] for a in r.get("acted", [])]
        self.assertEqual(did, ["pick", "next", "start", "record"], r.get("acted"))
        self.assertIn("BIG C-001", r["acted"][0]["did"])
        self.assertIn("TEST", r["acted"][2]["did"])


if __name__ == "__main__":
    unittest.main()
