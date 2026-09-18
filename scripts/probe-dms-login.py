#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：用解出来的 `dmstest` 真登一次 MR.ERP DMS，确认凭据和网络是通的。

**这是探针，不是客户端**。目的只有一个：在搬那 2500 行 DMS 客户端之前，
先证明"这套账号从本机真能登进去"。搬完客户端这脚本就可以退休。

登录细节照抄老产品 `services/erp/mrerp_dms_adapter.py:_perform_login`：

· 选择器 `#txtusers` / `#txtpasswords` / `#btnlogin`；
· 成功判据 = **离开 `index.php`**；
· DMS 的判定在 `checklogin.php` 响应里（`lct::<status>::<grant>`），
  不是原生 alert —— 所以直接读响应体，这是唯一可靠信号；
· `index.js` 把点击处理绑在 `window.load` 上，早点是静默无操作 → 要多点几次。

⚠️ 老产品文档：**`dmstest` 只允许一个活跃登录**。跑之前确认没有别的会话占着。

用法（需要 playwright；用老产品的 venv 最省事）：
    <老产品>/venv/bin/python scripts/probe-dms-login.py

密码从 `~/.config/pearnly/credentials/legacy-endpoints.json` 读，**不打印**。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CREDS = Path.home() / ".config" / "pearnly" / "credentials" / "legacy-endpoints.json"


def load_dms_creds(path: Path, username: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for e in data.get("endpoints") or []:
        if e.get("adapter") == "mrerp_dms" and e.get("username") == username and e.get("password"):
            return e
    raise SystemExit("在 %s 里找不到 adapter=mrerp_dms 且 username=%s 的连接" % (path, username))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", type=Path, default=DEFAULT_CREDS)
    ap.add_argument("--username", default="dmstest")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--headed", dest="headless", action="store_false",
                    help="弹出真窗口看过程（排查用）")
    ap.add_argument("--shot", type=Path, default=None, help="成功后截图存这里")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺 playwright —— 用老产品的 venv 跑：<老产品>/venv/bin/python", file=sys.stderr)
        return 1

    e = load_dms_creds(args.creds, args.username)
    url = (e.get("config_plain") or {}).get("system_url") or ""
    if not url:
        raise SystemExit("这条连接没有 system_url")

    def mask(v: str) -> str:
        return "%s***(%d位)" % (v[:3], len(v)) if v else "(空)"

    print("登录地址: %s" % url)
    print("账号    : %s" % mask(e["username"]))

    checklogin: list[str] = []
    dialogs: list[str] = []
    result = {"ok": False, "why": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("dialog", lambda d: (dialogs.append(d.message or ""), d.dismiss()))

        def on_resp(resp):
            if "checklogin.php" in resp.url:
                try:
                    checklogin.append((resp.text() or "").strip()[:200])
                except Exception:
                    pass

        page.on("response", on_resp)

        try:
            page.goto(url, wait_until="load", timeout=45_000)
            page.wait_for_selector("#btnlogin", state="visible", timeout=45_000)
            page.fill("#txtusers", e["username"])
            page.fill("#txtpasswords", e["password"])
            for _ in range(3):
                try:
                    page.click("#btnlogin", timeout=5_000)
                except Exception:
                    page.wait_for_timeout(600)
                    continue
                page.wait_for_timeout(3_000)
                if "index.php" not in page.url:
                    result["ok"] = True
                    break
            result["url_after"] = page.url
            result["title_after"] = page.title()
            if args.shot and result["ok"]:
                page.screenshot(path=str(args.shot), full_page=False)
        except Exception as exc:  # noqa: BLE001 — 探针就是要如实报出任何错
            result["why"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        finally:
            browser.close()

    # checklogin 的响应体是**状态码**不是凭据（`lct::<status>::<grant>`），可以打
    print("checklogin 响应: %s" % (checklogin[-1] if checklogin else "(没抓到)"))
    print("弹窗           : %s" % (dialogs if dialogs else "(无)"))
    print("落点 URL       : %s" % result.get("url_after", "-"))
    print("落点标题       : %s" % result.get("title_after", "-"))
    if result["ok"]:
        print("RESULT: 登录成功（已离开 index.php）")
        return 0
    print("RESULT: 登录失败 —— %s" % (result["why"] or "停在 index.php，没进认证区"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
