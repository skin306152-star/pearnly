#!/usr/bin/env python3
"""线上压测：多份 40MB 大文件同时上传（第二轮加的一项，docs/work/2026-11-11-prod-stress-round2.md）。

C 线 11-11 估算：40MB 一份在网页进程里瞬时多约 130MB，Cowork / ERP 网页约 11–15 份同时到内存上限。这里用实测回答：
同时传 N 份时变不变慢、有没有 413 / 502 / 超时、有没有丢件（服务端会话文件里大小对不上）、生产网页容器内存与 swap 怎么走。

做法：每个产品登录一次（同 stress.py 并发格，Keycloak 对同账号 1 秒内连续登录按失败算），N 个标签页各开一段新对话，
等 N 页都就绪后同时用附件钮放进同一份合成 PDF（本机生成的噪点图，没有任何票面信息）。**只放附件、不发消息**：
不调模型、不识别，费用为 0（引擎收文件不收费）。每份记：从放文件到上传接口回来的秒数、状态码、服务端回的大小；
传完逐段读会话文件核大小，再走正式删除接口删掉这些对话并读回。

    python3 scripts/e2e/stress_upload.py --make-file DIR/big.pdf                 # 生成约 39MB / 40 页合成 PDF（不连网）
    python3 scripts/e2e/stress_upload.py --product cowork --file DIR/big.pdf --waves 5,10,15
生产那头的内存采样另起一次 SSH 长连接（scripts/e2e/stress_upload_sampler.sh），与这里同时跑。

退出码：0 全部传上且没丢；1 有失败 / 丢件；3 没跑（登录被拒 / 文件不对）；2 用法不对。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as e2e  # noqa: E402
import scenarios  # noqa: E402
import stress  # noqa: E402  登录一次存状态、台账同一份

UPLOAD = "pearnly_core.assistant.api.upload"
MAX_BYTES = 40 * 1024 * 1024          # pearnly_core.assistant.contracts.UPLOAD_MAX_BYTES
TARGET_BYTES = 39 * 1024 * 1024       # 上限内最大的一档，留 1MiB 给 multipart 头
UPLOAD_TIMEOUT = 300_000
PAUSE_BETWEEN = 45                    # 两波之间等网页进程回落（秒）


def make_pdf(path: Path, pages: int = 40, target: int = TARGET_BYTES) -> int:
    """每页一张不压缩的 RGB 噪点图（随机字节压不小，大小可控）；合法 PDF，页数照实。回文件字节数。"""
    side = int(((target / pages) / 3) ** 0.5)
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for i in range(pages):
        img_no, content_no, page_no = len(objs) + 1, len(objs) + 2, len(objs) + 3
        raw = os.urandom(side * side * 3)
        objs.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 /Length %d >>\nstream\n"
                    % (side, side, len(raw)) + raw + b"\nendstream")
        draw = b"q 595 0 0 595 0 123 cm /Im0 Do Q BT /F1 18 Tf 40 60 Td (STRESS %d / %d) Tj ET" % (i + 1, pages)
        objs.append(b"<< /Length %d >>\nstream\n" % len(draw) + draw + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                    b"/Resources << /XObject << /Im0 %d 0 R >> /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"
                    % (content_no, img_no))
        kids.append(page_no)
    objs[1] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (pages, b" ".join(b"%d 0 R" % k for k in kids))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return len(out)


def session_of(body: bytes | None) -> str:
    """上传请求（multipart）里的 session_id 字段；只看头 8KB（字段在文件前面）。"""
    head = (body or b"")[:8192]
    mark = head.find(b'name="session_id"')
    if mark < 0:
        return ""
    start = head.find(b"\r\n\r\n", mark)
    end = head.find(b"\r\n", start + 4)
    return head[start + 4:end].decode("ascii", "replace").strip() if start >= 0 and end > start else ""


def summarize(lanes: list) -> dict:
    ok = [x for x in lanes if x.get("status") == 200 and x.get("size_ok")]
    secs = sorted(x["seconds"] for x in lanes if "seconds" in x)
    return {"n": len(lanes), "ok": len(ok), "failed": len(lanes) - len(ok),
            "statuses": sorted({str(x.get("status", x.get("error", "?")))[:40] for x in lanes}),
            "p50": round(statistics.median(secs), 1) if secs else None, "max": secs[-1] if secs else None,
            "lost": [x["lane"] for x in lanes if x.get("status") == 200 and not x.get("size_ok")]}


RECORD_SESSION = """(() => { for (const name of ['append', 'set']) { const orig = FormData.prototype[name];
  FormData.prototype[name] = function (k, v, ...rest) { if (k === 'session_id') (window.__ups = window.__ups || []).push(String(v));
    return orig.call(this, k, v, ...rest); }; } })();"""


async def _js(page, body: str):
    return await page.evaluate("async () => {" + scenarios._cc_js() + body + "}")


async def lane(browser, i: int, url: str, file: Path, size: int, gate: asyncio.Event, ready: list, state: Path | None, hook=None) -> dict:
    out = {"lane": i}
    context = await browser.new_context(**({"storage_state": str(state)} if state else {}), viewport=e2e.VIEWPORTS["desktop"])
    page = await context.new_page()
    try:
        if hook:
            await hook(page)
        # 兜底认会话：请求体里不一定带得出（Playwright 对文件上传的 postData 可能不全），页面里记下表单的 session_id
        await page.add_init_script(RECORD_SESSION)
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        attach = page.locator("button.cc-attach:visible").first
        await attach.wait_for(timeout=90_000)
        ready.append(i)
        await gate.wait()                                # 所有页都就绪后一起放
        t0 = time.time()
        async with page.expect_response(lambda r: UPLOAD in r.url, timeout=UPLOAD_TIMEOUT) as answer:
            async with page.expect_file_chooser(timeout=20_000) as chooser:
                await attach.click()
            await (await chooser.value).set_files(str(file))
        response = await answer.value
        out["seconds"] = round(time.time() - t0, 1)
        out["status"] = response.status
        try:
            out["session"] = session_of(response.request.post_data_buffer)
        except Exception:  # noqa: BLE001
            out["session"] = ""
        out["session"] = out["session"] or ((await page.evaluate("window.__ups || []")) or [""])[-1]
        try:
            body = (await response.json()).get("message") or {}
        except Exception:  # noqa: BLE001  网关回的 HTML（413 / 502）
            body = {"text": (await response.text())[:200]}
        out["reply"] = {k: body.get(k) for k in ("fileId", "size_bytes", "pages", "text", "exc_type") if body.get(k) is not None}
        if out["session"]:
            files = await _js(page, f"return await call('pearnly_core.assistant.api.session_files',{{session_id:{json.dumps(out['session'])}}});") or []
            sizes = [f.get("size_bytes") or f.get("size") for f in files if isinstance(f, dict)]
            out["server_sizes"] = sizes
            out["size_ok"] = size in sizes
    except Exception as error:  # noqa: BLE001  一路出错记下来，别的路照跑
        out["error"] = f"{type(error).__name__}: {str(error)[:200]}"
    finally:
        out["page"] = page
        out["context"] = context
    return out


async def cleanup(lanes: list) -> None:
    """正式删除接口删本波建的对话，读回侧栏里没有；关页面。"""
    for x in lanes:
        page, context = x.pop("page", None), x.pop("context", None)
        try:
            if page and x.get("session"):
                sid = json.dumps(x["session"])
                await _js(page, f"await call('pearnly_core.assistant.api.delete',{{session_id:{sid}}},true); return true;")
                x["deleted"] = not await _js(page, f"return (await listed('chat')).some(s=>s.id==={sid});")
        except Exception as error:  # noqa: BLE001
            x["delete_error"] = f"{type(error).__name__}: {str(error)[:120]}"
        finally:
            if context:
                await context.close()


async def wave(n: int, url: str, file: Path, state: Path | None, hook=None) -> list:
    from playwright.async_api import async_playwright

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        gate, ready = asyncio.Event(), []
        tasks = [asyncio.create_task(lane(browser, i, url, file, file.stat().st_size, gate, ready, state, hook)) for i in range(n)]
        for _ in range(180):                            # 最多等 90 秒全部就绪；没就绪的照样放（记在结果里）
            if len(ready) >= n or all(t.done() for t in tasks):
                break
            await asyncio.sleep(0.5)
        started = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
        gate.set()
        lanes = list(await asyncio.gather(*tasks))
        await cleanup(lanes)
        await browser.close()
    for x in lanes:
        x["released_utc"] = started
    return lanes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--make-file", type=Path, help="生成合成 PDF 到这个路径后退出")
    parser.add_argument("--pages", type=int, default=40)
    parser.add_argument("--product", choices=["cowork", "erp"])
    parser.add_argument("--file", type=Path)
    parser.add_argument("--waves", default="5,10,15")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.make_file:
        size = make_pdf(args.make_file, args.pages)
        print(f"{args.make_file}\t{size} 字节\t{args.pages} 页")
        return 0
    if not args.product or not args.file:
        parser.error("给 --product 与 --file（或 --make-file）")
    size = args.file.stat().st_size if args.file.exists() else 0
    if not size or size > MAX_BYTES:
        print(f"文件不对：{args.file}（{size} 字节，上限 {MAX_BYTES}）")
        return 3
    waves = [int(x) for x in args.waves.split(",") if x.strip()]
    if any(n < 1 or n > 20 for n in waves):
        parser.error("每波 1–20 份")
    e2e.check_playwright()
    accounts = e2e.load_accounts()
    out_dir = args.out or (e2e.RUNS_DIR / ("stress-upload-" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S")))
    out_dir.mkdir(parents=True, exist_ok=True)
    state = out_dir / f".login-{args.product}.json"
    try:
        stress.save_login(args.product, accounts, state)
    except scenarios.LoginRejected as error:
        state.unlink(missing_ok=True)
        print(f"登录被拒，不重试：{error}")
        return 3
    url = f"https://{e2e.SITES[args.product]}/assistant"
    report, code = [], 0
    try:
        for k, n in enumerate(waves):
            if k:
                time.sleep(PAUSE_BETWEEN)
            lanes = asyncio.run(wave(n, url, args.file, state))
            row = {"product": args.product, "wave": n, "size": size, **summarize(lanes), "lanes": lanes}
            report.append(row)
            print(json.dumps({k2: v for k2, v in row.items() if k2 != "lanes"}, ensure_ascii=False))
            (out_dir / "upload-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            if row["failed"]:
                code = 1
    finally:
        state.unlink(missing_ok=True)                    # 登录状态里有会话 Cookie：用完就删
    stress.ledger(f"| {time.strftime('%m-%d %H:%M')} | 线上压测窗口（第二轮） | 40MB 并发上传 {args.product}：每波 {args.waves} 份同时放附件、不发消息"
                  f"（不调模型不识别） | 0 | 0 | **{time.localtime().tm_mon} 月累计约 {stress.month_total()}** |")
    print(f"结果：{out_dir / 'upload-results.json'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
