#!/usr/bin/env python3
"""线上压测：多票连传（11-08 事故后用户要求「压测 100 张连票、各场景压出来、走线上」；方案见 docs/work/2026-11-08-prod-stress-100.md）。

部署回读通过后在本机跑：用 e2e 账号（同 run.py，凭据 ~/.config/pearnly-e2e/accounts.env，600）经 fatcc.com + Keycloak 真登录生产上的
测试租户（testcowork / testerp），在 CC 网页里像普通用户一样放文件、说一句话，真模型、真识别。正式租户一个都不登录。

    python3 scripts/e2e/stress.py --list                                   # 列出场景与估算花费
    python3 scripts/e2e/stress.py --files DIR --scenario S3 --budget 25    # 跑一格（DIR 放场景要的文件）
    python3 scripts/e2e/stress.py --files DIR --scenario S6 --budget 25    # 并发格：各路各开一个进程同时跑

每格记：用户看到什么（每 10 秒截图 + 状态行 + 助手最后一段话）、每一步工具调用（对话页同一个事件接口）、模型用量折算的花费、
提问卡片数、界面上有没有内部名词、多少秒、与预期是否一致。默认**不点**推送 / 开始（`click` 字段写明的才点），免得写进 Express。
花费硬上限：开跑前「已花 + 本格估算」超过 --budget 就不跑；跑着时模型用量折算超过本格上限就停（点停止）。识别费用按页估算，
真实数在收尾时一次只读 SSH 读识别记录的检查点核对（不在这里轮询生产）。每格结果追加到台账（--ledger）。

退出码：0 全部跑完且与预期一致；1 有不一致；3 有没跑（超预算 / 缺文件 / 前置自检不过）；2 用法不对。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as e2e  # noqa: E402  凭据、前置自检、浏览器同一份
import scenarios  # noqa: E402  登录、cc() 页面帮手同一份

LEDGER = Path(os.environ.get("PEARNLY_STRESS_LEDGER", Path.home() / "pearnly-devtools" / "test-window" / "round12" / "ocr-spend.md"))
SPENT_FILE = Path(os.environ.get("PEARNLY_STRESS_SPENT", Path.home() / "pearnly-devtools" / "e2e-runs" / "stress-spent.json"))
# Opus 5.5 每百万 token 美元（输入 / 输出 / 写缓存 / 读缓存），CC 引擎所用模型；用量从会话事件 span.model_request_end 取
PRICE = {"input_tokens": 5.0, "output_tokens": 25.0, "cache_creation_input_tokens": 6.25, "cache_read_input_tokens": 0.5}
# 识别按页估：多页扫描件分段读 Haiku+Opus，11-07/11-08 BJC 12 页实数约 0.47（≈0.04/页）；单页小票约 0.01
OCR_PER_PAGE = 0.04
# 用户界面上不该出现的内部名词（11-08 事故：「Express operator」「CWH-哈希」）
INTERNAL = re.compile(r"\boperator\b|CWH-[0-9a-f]{6,}|wrong_mode|choice_needed|tool_use|toolu_|Traceback|\bNone\b|undefined|\bnull\b", re.I)
SHOT_EVERY = 10   # 秒


@dataclass
class Scenario:
    id: str
    title: str
    product: str                 # cowork / erp
    run: str                     # desktop / mobile
    mode: str                    # chat / operator
    say: str                     # 用户一句话
    files: list                  # 相对 --files 目录
    pages: int                   # 总页数（估识别费）
    expect: str                  # 预期（报告里对照）
    company: str = ""            # 选择公司弹窗里点的公司名（空 = 不选，用下巴默认；"*" = 弹窗里第一家已建的）
    follow: list = field(default_factory=list)   # [(第几秒, 一句话)]：中途发消息 / 问进度
    click: str = ""              # 要点的按钮（"" 不点；"start" 点开始卡的开始；"stop" 点停止）
    minutes: int = 20            # 本格最长跑多久
    cc_cap: float = 3.0          # 本格模型费上限（美元），超了点停止
    lanes: int = 1               # 并发路数（>1 时各路一个进程）
    tonight: bool = True         # 今晚（不写账）能跑；False = 要 accserver 在线 / 修复上线后
    charged: bool = True         # 预期会扣识别费（超上限读前拒的格估算时照算、记账时按 False 不计）


SINCERE_MULTI = ["bigc-jun-soi.pdf", "bigc-jun-plab.pdf", "bigc-jul-soi.pdf", "bigc-jul-plab.pdf",
                 "void-jun-plab.pdf", "void-jun-soi.pdf", "void-jul-plab.pdf"]   # 27+15+23+25+5+1+6 = 102 页

SCENARIOS = [
    Scenario("S3", "单个 PDF 53 页（超过 50 页上限）", "cowork", "desktop", "chat",
             "บันทึกเอกสารนี้เข้า Express ให้หน่อย", ["bigc-aug-53p.pdf"], 53,
             "识别前就拒、说清超过 50 页、给能做的下一步（拆分）；不扣识别费；不露内部名词", minutes=6, cc_cap=0.6, charged=False),
    Scenario("S4", "ERP 直写对话放 PDF 说录进 Express（线上 ERP 对话还没识别工具）", "erp", "desktop", "chat",
             "把这份 PDF 录进 Express", ["bigc-jun-plab.pdf"], 0,
             "如实说现在能做什么、不编造已录入；不露内部名词", minutes=6, cc_cap=0.6),
    Scenario("S2", "只看内容：50 页单 PDF", "cowork", "mobile", "chat",
             "这份 PDF 里有几张票？每张的买方和合计是多少？只看内容，不要录进 Express", ["bigc-aug-50p.pdf"], 50,
             "读出约 50 张、按买方分店列出合计；不调推送；有进度；不露内部名词", minutes=25, cc_cap=2.0),
    Scenario("S1", "直写：多文件 102 页说录进 Express（事故形状：同税号 Big C 多分店、从没记过对应）", "cowork", "desktop", "chat",
             "บันทึกเอกสารทั้งหมดนี้เข้า Express", SINCERE_MULTI, 102,
             "识别完自动继续；同一买方只问一次（不是逐张问）；作废票单独说；到开始前卡片 / 推送确认为止（不点）；不露内部名词",
             follow=[(240, "ตอนนี้ถึงไหนแล้ว")], minutes=35, cc_cap=3.0),
    Scenario("S7", "日文抽查：5 页", "cowork", "desktop", "chat",
             "このPDFをExpressに入力してください", ["void-jun-plab.pdf"], 5,
             "日文回复；作废票说清；到确认为止（不点）；不露内部名词", minutes=10, cc_cap=0.8),
    Scenario("S5a", "代录基线（Cowork 电脑 泰文）：24 页 PDF 说录进 Express", "cowork", "desktop", "operator",
             "บันทึกไฟล์ PDF นี้เข้า Express", ["bigc-aug-soi.pdf"], 25,
             "开始前卡片一次问完同一买方；不调直写推送；状态不卡「准备中」；不露内部名词（修复前预期复现事故）", company="*",
             minutes=15, cc_cap=1.2),
    Scenario("S5b", "代录基线（ERP 手机 中文）：24 页 PDF 说录进 Express", "erp", "mobile", "operator",
             "把这份 PDF 录进 Express", ["bigc-aug-soi.pdf"], 25,
             "同 S5a（ERP 代录）", company="*", minutes=15, cc_cap=1.2),
    Scenario("S6", "并发：每路一个 10 页混合 PDF「识别并录入」（两租户各 3 路同时）", "both", "desktop", "chat",
             "识别这份 PDF 并录进 Express", ["mix-10p.pdf"], 60,
             "各路都出结果、没有 500 / 卡死；记每路耗时与排队；不点推送", minutes=20, cc_cap=0.8, lanes=6),
    Scenario("S8", "直写真推送（明天，accserver 在线、TEST 写前快照）", "cowork", "desktop", "chat",
             "บันทึกเอกสารทั้งหมดนี้เข้า Express", SINCERE_MULTI, 102,
             "推送进 TEST、回读一致；同税号分店选一次；重复 / 作废票不重复写", click="start", minutes=45, cc_cap=3.0, tonight=False),
    Scenario("S9", "代录全量（明天，修复上线后）：开始前一次问完 → 开始 → 中途问进度 / 发消息 → 停止", "cowork", "desktop", "operator",
             "บันทึกเอกสารทั้งหมดนี้เข้า Express", SINCERE_MULTI, 102,
             "开始前一次问完；录的过程中问进度有答；停止后状态说清", follow=[(180, "ถึงไหนแล้ว"), (360, "หยุดก่อน")],
             company="*", click="start", minutes=45, cc_cap=3.0, tonight=False),
]
BY_ID = {s.id: s for s in SCENARIOS}


def estimate(s: Scenario) -> float:
    return round(s.pages * OCR_PER_PAGE + s.cc_cap * max(1, s.lanes), 2)


def spent() -> float:
    try:
        return float(json.loads(SPENT_FILE.read_text(encoding="utf-8")).get("usd", 0))
    except (OSError, ValueError):
        return 0.0


def add_spent(usd: float):
    SPENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPENT_FILE.write_text(json.dumps({"usd": round(spent() + usd, 4), "at": datetime.datetime.now().isoformat()}), encoding="utf-8")


def ledger(line: str):
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


def usage_usd(events: list) -> float:
    total = 0.0
    for event in events:
        if event.get("type") == "span.model_request_end":
            usage = event.get("model_usage") or {}
            total += sum(int(usage.get(k) or 0) * price for k, price in PRICE.items()) / 1_000_000
    return round(total, 4)


def tool_calls(events: list) -> list:
    """事件里的工具调用（名字、本站执行状态：本站记录事件 `tool_use_id` + `status`，引擎结果里的 is_error），按出现顺序。"""
    out, by_id = [], {}
    for event in events:
        kind = event.get("type") or ""
        if kind.endswith("tool_use") and event.get("name"):
            call = {"at": event.get("processed_at"), "name": event["name"]}
            out.append(call)
            if event.get("id"):
                by_id[event["id"]] = call
        elif kind.endswith("tool_result"):
            call = by_id.get(event.get("tool_use_id")) or (out[-1] if out else None)
            if call is not None:
                call["error"] = bool(event.get("is_error"))
        elif event.get("tool_use_id") and event.get("status") and event.get("tool_use_id") in by_id:
            by_id[event["tool_use_id"]]["status"] = event["status"]
    return out


# 页面上读此刻的样子：状态行、发送 / 停止、助手最后一段话、提问卡片、开始前卡片、整页可见文字里的内部名词
LOOK = """
const text=e=>(e?.innerText||'').trim();
const last=[...document.querySelectorAll('[data-slot="aui_assistant-message-content"]')].pop();
// 等人答的卡：通用提问卡（.cc-askq，翻页时一题一页）、批准条（cc-ask-sheet）、代录每张单的选择卡（cc-op-reason 每张一个）
const asks=[...document.querySelectorAll('.cc-askq, [data-slot="cc-ask-sheet"], [data-slot="cc-op-reason"]')].filter(seen);
const pager=[...document.querySelectorAll('[data-slot="cc-askq-pager"]')].filter(seen).map(e=>text(e)).slice(0, 3);
return {line: text(q('[data-slot="cc-run-indicator"]')), send: !!q('button.aui-composer-send'), stop: !!q('button.aui-composer-cancel'),
  reply: text(last).slice(-600), asks: asks.length, pager, ask_text: asks.slice(0, 3).map(e => text(e).slice(0, 160)),
  start_card: !!q('[data-slot="cc-op-target"], [data-slot="cc-op-before-ask"]'),
  page_text: text(q('[data-slot="aui_thread-viewport"]') || document.body).slice(-20000)};
"""


def pick_first(page) -> str:
    """新建页下巴点「公司」，选择公司弹窗里点第一家已建的公司（同 scenarios.pick_company_step），返回公司名。"""
    page.locator('[data-slot="cc-chin-company"]:visible').first.click()
    built = '[data-slot="cc-company-picker"] [data-slot="cc-candidates"] .cc-list-row[data-built]'
    first = page.locator(built).first
    first.wait_for(timeout=scenarios.NAV_TIMEOUT)
    title = first.inner_text().strip().splitlines()[0].strip()
    first.click()
    page.locator('[data-slot="cc-company-picker"]').wait_for(state="hidden", timeout=scenarios.NAV_TIMEOUT)
    return title


def attach(page, paths: list):
    """点输入框左边的附件钮，用文件选择器放进这些文件（同用户在系统对话框里多选）。"""
    with page.expect_file_chooser(timeout=20_000) as chooser:
        page.locator("button.cc-attach:visible").first.click()
    chooser.value.set_files([str(p) for p in paths])
    page.wait_for_function("n => document.querySelectorAll('.aui-attachment-root, [data-slot*=attachment]').length >= n",
                           arg=len(paths), timeout=120_000)


def login_cc(page, s: Scenario, product: str, accounts: dict):
    """真登录：未登录开测试租户 /assistant → CC 登录页 → e2e 账号（同 run.py 的场景，登录被拒不重试）。"""
    ctx = scenarios.Context(product=product, run=s.run, sites=e2e.SITES, entry=e2e.ENTRY, accounts=accounts)
    scenarios.start(page, ctx)


def run_one(s: Scenario, product: str, files_dir: Path, out_dir: Path, accounts: dict, lane: int = 0,
            login=login_cc, hook=None, shot_every: int = SHOT_EVERY, idle_after: int = 40) -> dict:
    """跑一格。``login`` / ``hook``（建好页面后先调，自测用来接假接口）可换，线上用默认的真登录。"""
    from playwright.sync_api import sync_playwright

    tag = f"{s.id}-{product}-{lane}-{time.strftime('%H%M%S')}"
    shots = out_dir / tag
    shots.mkdir(parents=True, exist_ok=True)
    result = {"scenario": s.id, "product": product, "lane": lane, "title": s.title, "expect": s.expect, "trail": [],
              "tools": [], "cc_usd": 0.0, "ocr_pages": s.pages // max(1, s.lanes) if s.lanes > 1 else s.pages}
    driver = sync_playwright().start()
    browser = driver.chromium.launch(headless=True)
    options = {**driver.devices["Pixel 7"], "viewport": e2e.VIEWPORTS["mobile"]} if s.run == "mobile" else {"viewport": e2e.VIEWPORTS["desktop"]}
    context = browser.new_context(**options)
    page = context.new_page()
    if hook:
        hook(page)
    t0 = time.time()
    sid = ""
    try:
        login(page, s, product, accounts)
        listing = "operator" if s.mode == "operator" else "chat"
        before = {x["id"] for x in scenarios.cc(page, f"return await listed('{listing}');")}
        if s.mode == "operator":
            scenarios.expect(scenarios.cc(page, "return await toMode('operator');"), "切不到代录模式")
        if s.company == "*":
            result["company"] = pick_first(page)
        elif s.company:
            result["company"] = s.company
            scenarios.expect(scenarios.cc(page, f"return await pickCompany({json.dumps(s.company)});"), f"选不到公司 {s.company}")
        attach(page, [files_dir / name for name in s.files])
        page.screenshot(path=str(shots / "00-attached.png"))
        scenarios.expect(scenarios.cc(page, f"return await say({json.dumps(s.say)});"), "输入框发不出去")
        result["sent_at"] = round(time.time() - t0, 1)
        follow = list(s.follow)
        idle_since = None
        k = 0
        while time.time() - t0 < s.minutes * 60:
            page.wait_for_timeout(shot_every * 1000)
            k += 1
            now = round(time.time() - t0)
            look = scenarios.cc(page, LOOK)
            if not sid:
                fresh = [x["id"] for x in scenarios.cc(page, f"return await listed('{listing}');") if x["id"] not in before]
                sid = fresh[0] if fresh else ""
            events = scenarios.cc(page, f"return ((await call('pearnly_core.assistant.api.events',{{session_id:{json.dumps(sid)}}}))||{{}}).events||[];") if sid else []
            result["cc_usd"] = usage_usd(events)
            result["tools"] = tool_calls(events)
            bad = sorted(set(m.group(0) for m in INTERNAL.finditer(look.pop("page_text"))))
            row = {"s": now, **{k2: look[k2] for k2 in ("line", "send", "stop", "asks", "pager", "start_card")}, "internal": bad,
                   "reply": look["reply"][-200:], "ask_text": look["ask_text"]}
            if not result["trail"] or {k2: v for k2, v in row.items() if k2 != "s"} != {k2: v for k2, v in result["trail"][-1].items() if k2 != "s"}:
                result["trail"].append(row)
            page.screenshot(path=str(shots / f"{k:03d}-{now}s.png"))
            if result["cc_usd"] > s.cc_cap:
                result["stopped"] = f"模型费 {result['cc_usd']} 超本格上限 {s.cc_cap}，点停止"
                if page.locator("button.aui-composer-cancel").count():
                    page.locator("button.aui-composer-cancel").first.click()
                break
            if follow and now >= follow[0][0] and look["send"]:
                _, words = follow.pop(0)
                result.setdefault("followed", []).append({"s": now, "say": words, "sent": scenarios.cc(page, f"return await say({json.dumps(words)});")})
                idle_since = None
                continue
            # 停下来等人（有提问卡 / 开始卡）或空闲 40 秒：这一格看完
            waiting_user = look["send"] and not look["stop"] and (look["asks"] or look["start_card"])
            idle = look["send"] and not look["stop"] and not look["line"]
            if waiting_user or idle:
                idle_since = idle_since or now
                if now - idle_since >= idle_after and not follow:
                    break
            else:
                idle_since = None
        result["seconds"] = round(time.time() - t0, 1)
        result["session"] = sid
        page.screenshot(path=str(shots / "zz-final.png"), full_page=True)
        last = result["trail"][-1] if result["trail"] else {}
        result["final"] = last
        result["internal_seen"] = sorted({w for row in result["trail"] for w in row["internal"]})
        result["max_asks"] = max([row["asks"] for row in result["trail"]] or [0])
        result["direct_push_in_operator"] = s.mode == "operator" and any("push" in t["name"] for t in result["tools"])
    except Exception as error:  # noqa: BLE001  一格出错记下来，不中断别的格
        result["error"] = f"{type(error).__name__}: {str(error)[:300]}"
        result["seconds"] = round(time.time() - t0, 1)
        result["session"] = sid
        try:
            page.screenshot(path=str(shots / "zz-error.png"))
        except Exception:  # noqa: BLE001
            pass
    finally:
        context.close()
        browser.close()
        driver.stop()
    result["shots"] = str(shots)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--files", type=Path)
    parser.add_argument("--budget", type=float, default=25.0, help="本轮总上限（美元），含已花")
    parser.add_argument("--allow-tomorrow", action="store_true", help="允许跑要写 Express / 修复后才跑的格")
    parser.add_argument("--lane", type=int, default=-1, help=argparse.SUPPRESS)   # 并发格的子进程
    parser.add_argument("--lane-product", default="", help=argparse.SUPPRESS)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.list:
        for s in SCENARIOS:
            print(f"{s.id}\t{'今晚' if s.tonight else '明天'}\t估 {estimate(s)} 美元\t{s.title}")
        print(f"本轮已记花费 {spent()} 美元（{SPENT_FILE}）")
        return 0
    if not args.scenario or not args.files:
        parser.error("给 --scenario 与 --files")
    accounts = e2e.load_accounts()
    out_dir = args.out or (e2e.RUNS_DIR / ("stress-" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S")))
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.lane >= 0:   # 并发格的一路
        s = BY_ID[args.scenario[0]]
        print(json.dumps(run_one(s, args.lane_product, args.files, out_dir, accounts, args.lane), ensure_ascii=False))
        return 0

    e2e.check_playwright()
    results, code = [], 0
    for sid in args.scenario:
        s = BY_ID[sid]
        if not s.tonight and not args.allow_tomorrow:
            results.append({"scenario": sid, "skipped": "要写 Express / 修复上线后才跑（加 --allow-tomorrow）"})
            code = max(code, 3)
            continue
        missing = [n for n in s.files if not (args.files / n).exists()]
        if missing:
            results.append({"scenario": sid, "skipped": "缺文件：" + "、".join(missing)})
            code = max(code, 3)
            continue
        if spent() + estimate(s) > args.budget:
            results.append({"scenario": sid, "skipped": f"超预算：已花 {spent()} + 估 {estimate(s)} > {args.budget}"})
            code = max(code, 3)
            continue
        if s.lanes > 1:
            products = (["cowork", "erp"] * s.lanes)[:s.lanes] if s.product == "both" else [s.product] * s.lanes
            procs = [subprocess.Popen([sys.executable, __file__, "--scenario", sid, "--files", str(args.files), "--out", str(out_dir),
                                       "--lane", str(i), "--lane-product", p], stdout=subprocess.PIPE, text=True)
                     for i, p in enumerate(products)]
            lanes = []
            for proc in procs:
                out, _ = proc.communicate()
                try:
                    lanes.append(json.loads(out.strip().splitlines()[-1]))
                except (ValueError, IndexError):
                    lanes.append({"error": "这一路没有输出", "raw": (out or "")[-300:]})
            usd = round(sum(x.get("cc_usd", 0) for x in lanes) + s.pages * OCR_PER_PAGE, 3)
            results.append({"scenario": sid, "lanes": lanes, "usd_est": usd})
        else:
            r = run_one(s, s.product, args.files, out_dir, accounts)
            usd = round(r.get("cc_usd", 0) + (s.pages * OCR_PER_PAGE if s.charged or any("recogn" in t["name"] for t in r.get("tools", [])) else 0), 3)
            r["usd_est"] = usd
            results.append(r)
        add_spent(usd)
        ledger(f"| {time.strftime('%m-%d %H:%M')} | 线上压测窗口 | 压测 {sid}：{s.title}（testcowork/testerp，e2e 账号，生产密钥） | {s.pages} | "
               f"估 {usd}（模型按用量 + 识别按页 {OCR_PER_PAGE}） | 本轮累计约 {spent()} |")
        (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        if any(x.get("error") for x in ([results[-1]] + results[-1].get("lanes", []))):
            code = max(code, 1)
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps([{k: v for k, v in r.items() if k not in ("trail", "lanes")} for r in results], ensure_ascii=False, indent=1))
    print(f"结果：{out_dir / 'results.json'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
