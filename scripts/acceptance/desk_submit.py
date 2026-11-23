"""验收用的浏览器一段（L3 跨端场景「网页真点提交」，核心集第 1 条）：登录本机 Mac 实验栈的站点 → 打开一张单的 Desk 页 → 点页头主按钮
（草稿页的主按钮就是「提交」，按 Frappe 原生 `page.btn_primary` 点，不按文字认，四种语言一样）→ 等它跳到提交后的那张单 → 交回 JSON。

    <装了 Playwright 的 python> scripts/acceptance/desk_submit.py '<JSON {base, user, route, doc, shot}>'

- 密码不进参数：从 `MACLAB_CONFIG_DIR/<产品>.env` 的 MACLAB_USER_PASSWORD 读（本机测试栈的测试账号，`scripts/maclab.sh` 生成，600），不打印。
- 登录用 Frappe 原生 `/api/method/login`（Mac 栈没有统一身份），会话 Cookie 留在这个浏览器上下文里。
- 点了之后出了原生弹窗（写进 Express 前要人定的事，`express_preflight`）不替人答：交回弹窗标题，由场景判「没测到」。
- 点了之后按服务端这张单的状态等（`api_purchase_document`；提交草稿后页面回到同一个号，不能按换页判）。
- 末行打印 ACC {json}：clicked、status（点完后这张单的状态）、dialog、route、msgs（页面上出现过的 msgprint / alert 文字）、shot。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def password(product: str) -> str:
    folder = Path(os.environ.get("MACLAB_CONFIG_DIR") or Path.home() / ".config" / "pearnly-maclab")
    for line in (folder / f"{product}.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("MACLAB_USER_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no MACLAB_USER_PASSWORD")


WAIT_PAGE = """async ([name]) => {
  const until = async (f, ms) => { const end = Date.now() + ms; while (Date.now() < end) { try { const v = f(); if (v) return v; } catch (e) {} await new Promise(r => setTimeout(r, 250)); } return null; };
  const page = await until(() => frappe.pages['pearnly-purchase']?.page?.btn_primary?.is(':visible') && $.active === 0 && frappe.pages['pearnly-purchase'].page, 30000);
  return {ready: !!page, route: frappe.get_route(), primary: page ? page.btn_primary.text().trim() : ''};
}"""
CLICK = """async ([name]) => {
  const until = async (f, ms) => { const end = Date.now() + ms; while (Date.now() < end) { try { const v = await f(); if (v) return v; } catch (e) {} await new Promise(r => setTimeout(r, 500)); } return null; };
  const msgs = new Set();
  const watch = setInterval(() => document.querySelectorAll('.msgprint, .alert-message').forEach(e => { const t = e.innerText.trim(); if (t) msgs.add(t); }), 200);
  const status = async () => (await frappe.call({method: 'pearnly_erp_portal.purchasing.api.api_purchase_document', args: {name}})).message?.status;
  const page = frappe.pages['pearnly-purchase'].page;
  page.btn_primary.click();
  // 提交草稿后页面回到同一张单（同一个号），所以按服务端这张单的状态等，不按换没换页
  const done = await until(async () => { const s = await status(); return s && s !== 'draft' ? s : null; }, 90000);
  clearInterval(watch);
  const dialog = !done && window.cur_dialog && cur_dialog.$wrapper.is(':visible') ? (cur_dialog.title || 'dialog') : '';
  return {clicked: true, status: done || await status(), route: frappe.get_route(), dialog, msgs: [...msgs]};
}"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    p = json.loads(sys.argv[1])
    out = {"ok": False}
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            login = context.request.post(p["base"] + "/api/method/login", form={"usr": p["user"], "pwd": password(p.get("product", "erp"))})
            if login.status != 200:
                raise SystemExit(f"login {login.status}")
            page.goto(p["base"] + "/desk/" + p["route"], wait_until="domcontentloaded", timeout=60000)
            out["page"] = page.evaluate(WAIT_PAGE, [p["doc"]])
            if not out["page"]["ready"]:
                raise SystemExit("the page or its primary button did not come up")
            out.update(page.evaluate(CLICK, [p["doc"]]))
            out["ok"] = True
        except SystemExit as stop:
            out["why"] = str(stop)
        except Exception as error:  # noqa: BLE001 —— 交回原因，由场景判
            out["why"] = f"{type(error).__name__}: {str(error)[:300]}"
        finally:
            if p.get("shot"):
                try:
                    page.screenshot(path=p["shot"])
                    out["shot"] = p["shot"]
                except Exception:  # noqa: BLE001
                    pass
            browser.close()
    print("ACC " + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
