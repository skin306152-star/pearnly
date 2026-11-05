"""登录后端到端的场景步骤（登记 tests/e2e/registry.toml，执行器 scripts/e2e/run.py）。

每个场景一个函数 `step(page, ctx)`，登记在 `STEPS`；没登记的场景执行器记「没测到：场景步骤还没写」。
步骤只断言业务结果（落在哪个站、页面上出现了什么、服务端回读的状态），不只看能不能打开：
- 断言不对 → raise Failed（人话写清哪一步、期望什么、实际什么）；
- Keycloak 拒绝登录 → raise LoginRejected（执行器不重试，防锁号）；
- 页面导航超时 → raise NavigationTimeout（执行器把这一格重跑一次）；
- 这一格在此刻测不了 → raise NotTested（说原因）。
密码只经 `ctx.accounts[...]` 传给 `page.fill`，不拼进地址、不打印。

步骤要等 e2e 账号建好（方案 §12 第 4 问，主会话问用户中）后对着生产测试租户逐个调通；跑通一个就在登记表里写上 since。
"""
from __future__ import annotations

import importlib.util
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
I18N = ROOT / "frontend" / "assistant" / "src" / "i18n.ts"
REPLY_TIMEOUT = 90_000


def _cc_js() -> str:
    """CC 对话页的页面帮手（say / mode / toMode / pickCompany / rows……），与流程用例同一份 scripts/flows/cc_lib.py。"""
    spec = importlib.util.spec_from_file_location("cc_lib", ROOT / "scripts" / "flows" / "cc_lib.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.JS


def cc(page, body: str):
    return page.evaluate("async () => {" + _cc_js() + body + "}")


class NotTested(Exception):
    """这一格测不了（前置自检不过、步骤还没写）：不算通过也不算不通过。"""


class LoginRejected(Exception):
    """Keycloak 拒绝登录：不重试，免得把 e2e 账号锁住。"""


class NavigationTimeout(Exception):
    """页面导航超时：这一格重跑一次。"""


class Failed(AssertionError):
    """断言的业务结果不对。"""


@dataclass
class Context:
    product: str          # erp / cowork
    run: str              # desktop / mobile / iphone
    sites: dict           # erp / erp-second / cowork → 测试租户域名
    entry: str            # CC 入口 https://fatcc.com
    accounts: dict        # erp / cowork → Account（repr 不带密码）

    @property
    def site(self) -> str:
        return self.sites[self.product]


# ---------------------------------------------------------------- 共用步骤

NAV_TIMEOUT = 45_000
LOGIN_TIMEOUT = 60_000
ENTRY_HOST = "fatcc.com"
AUTH = "https://auth.pearnly.com/realms/"


def watch(page) -> list:
    """记下每一跳主导航（状态、地址），断言用：哪一跳 4xx / 5xx、有没有停在 Pearnly 登录页。"""
    hops = []
    page.on("response", lambda r: hops.append((r.status, r.url)) if r.request.is_navigation_request()
            and r.frame == page.main_frame else None)
    return hops


def goto(page, url: str):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        return page.goto(url, timeout=NAV_TIMEOUT)
    except PlaywrightTimeout as error:
        raise NavigationTimeout(f"打开 {url.split('?')[0]} 超时") from error


def pearnly_login_pages(hops, sites) -> list:
    """停在页面上的 Pearnly 登录页：站点自己的客户端（client_id = 站点名）在 auth.pearnly.com 回了 200（静默确认是 302）。"""
    return [url for status, url in hops if status == 200 and url.startswith(AUTH) and "/protocol/openid-connect/auth" in url
            and any(f"client_id={site}" in url for site in sites)]


def bad_hops(hops) -> list:
    return [f"{status} {url.split('?')[0]}" for status, url in hops if status >= 400]


def on_entry(page) -> bool:
    return page.url.startswith(f"https://{ENTRY_HOST}/")


def login(page, account, expect_site: str):
    """在 CC 登录框（fatcc.com）登一个 e2e 账号，等落到 expect_site 的 /assistant。登录被拒 → LoginRejected（不重试）。"""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    if not on_entry(page):
        raise Failed(f"应在 CC 登录页（{ENTRY_HOST}），实际在 {page.url.split('?')[0]}")
    page.fill("#entry input[name=login]", account.user)
    page.fill("#entry input[name=password]", account.password)
    remember = page.locator("#entry input[name=remember]")
    if remember.count() and not remember.is_checked():
        remember.check()
    page.click("#go")
    try:
        page.wait_for_url(f"https://{expect_site}/assistant**", timeout=LOGIN_TIMEOUT)
    except PlaywrightTimeout:
        if page.url.startswith(AUTH) and page.locator("#kc-form-login").count():
            raise LoginRejected(f"{account.user} 登录被拒（停在 {page.url.split('?')[0]}）")
        raise Failed(f"{account.user} 登录后应落到 {expect_site}/assistant，实际在 {page.url.split('?')[0]}")
    ready(page)


def ready(page):
    """CC 外壳出来了：输入框在（电脑与手机都有）。"""
    page.wait_for_selector("textarea.aui-composer-input", state="attached", timeout=NAV_TIMEOUT)


def current_user(page) -> str:
    return page.evaluate("fetch('/api/method/frappe.auth.get_logged_user').then(r => r.json()).then(j => j.message || '')")


def expect(condition, message: str):
    if not condition:
        raise Failed(message)


def words(key: str) -> list[str]:
    """CC 界面一个词条在各语言里的文字（从 frontend/assistant/src/i18n.ts 现取，不在这里写死；账号用哪种语言都认）。"""
    found = re.findall(r"\b" + re.escape(key) + r': "([^"]+)"', I18N.read_text(encoding="utf-8"))
    if not found:
        raise Failed(f"i18n.ts 里没有词条 {key}")
    return sorted(set(found))


def by_words(page, selector: str, key: str):
    """selector 里文字等于词条（任一语言）且看得见的那个元素。"""
    texts = words(key)
    handle = page.wait_for_function(
        "([sel, texts]) => [...document.querySelectorAll(sel)].find(e => e.getClientRects().length"
        " && texts.includes(e.innerText.trim().split('\\n')[0].trim()))",   # 菜单项后面可能带快捷键（如「ลบ D」）
        arg=[selector, texts], timeout=NAV_TIMEOUT)
    return handle.as_element()


def open_nav(page, ctx, slot: str):
    """侧栏导航里的一项（手机先开侧栏抽屉）。"""
    if ctx.run != "desktop":
        page.click('[data-slot="cc-nav-toggle"]')
        page.wait_for_timeout(400)   # 抽屉滑出的动画
    page.click(f'[data-slot="{slot}"]')


def open_user_menu(page, ctx):
    """左下角用户菜单（手机先开侧栏抽屉）。"""
    if ctx.run != "desktop":
        page.click('[data-slot="cc-nav-toggle"]')
        page.click('[data-slot="cc-drawer-foot"] button.cc-avatar')   # 抽屉底部的头像就是用户菜单
    else:
        page.click('[data-slot="cc-user-menu"]')


def start(page, ctx, product=None):
    """未登录打开本站 /assistant，必须先到 CC 登录页（带 ?site=本站），再登这个产品的 e2e 账号。返回导航记录。"""
    product = product or ctx.product
    site = ctx.sites[product]
    hops = watch(page)
    goto(page, f"https://{site}/assistant")
    expect(on_entry(page) and f"site={site}" in page.url,
           f"未登录打开 {site}/assistant 应转到 CC 登录页并带 ?site=，实际在 {page.url.split('#')[0]}")
    login(page, ctx.accounts[product], site)
    return hops


def no_trouble(hops, sites):
    shown, bad = pearnly_login_pages(hops, sites), bad_hops(hops)
    expect(not shown, "出现了 Pearnly 登录页：" + "、".join(url.split("?")[0] + "?" + url.split("?")[1][:60] for url in shown[:2]))
    expect(not bad, "导航出错：" + "、".join(bad))


# ---------------------------------------------------------------- 场景

def login_step(page, ctx):
    hops = start(page, ctx)
    user = current_user(page)
    expect(user.startswith(ctx.accounts[ctx.product].user + "."), f"当前用户应是 {ctx.accounts[ctx.product].user}，实际是 {user or '访客'}")
    no_trouble(hops, [ctx.site])


def switch_site_step(page, ctx):
    """浏览器里切换站点 = 打开另一站的 /assistant（同 CC 的 openSite）。e2e 账号只是本站成员：
    同池非成员站、跨池站都应落到 CC 登录页（fatcc.com），不能是 Pearnly 登录页（11-03 事故 2）；在那里登另一个池的账号，落到那一站。"""
    hops = start(page, ctx)
    other = "cowork" if ctx.product == "erp" else "erp"
    targets = ([ctx.sites["erp-second"]] if ctx.product == "erp" else []) + [ctx.sites[other]]
    for target in targets:
        goto(page, f"https://{target}/assistant")
        page.wait_for_load_state("load")
        expect(on_entry(page) and page.locator("#entry").count(),
               f"已登录 {ctx.site} 再开 {target}/assistant 应到 CC 登录页，实际在 {page.url.split('?')[0]}")
    login(page, ctx.accounts[other], ctx.sites[other])
    user = current_user(page)
    expect(user.startswith(ctx.accounts[other].user + "."), f"在 CC 登录页登 {ctx.accounts[other].user} 后当前用户是 {user or '访客'}")
    no_trouble(hops, [ctx.site] + targets)


def logout_step(page, ctx):
    hops = start(page, ctx)
    open_user_menu(page, ctx)
    by_words(page, "[role=menuitem]", "logout").click()
    page.wait_for_url(f"https://{ENTRY_HOST}/**", timeout=NAV_TIMEOUT)
    confirm = [url for status, url in hops if status == 200 and url.startswith(AUTH) and "/logout" in url]
    expect(not confirm, "退出时出现了 Keycloak「确定要注销吗」页")
    goto(page, f"https://{ctx.site}/assistant")
    page.wait_for_load_state("load")
    expect(on_entry(page) and page.locator("#entry").count(),
           f"退出后再开 {ctx.site}/assistant 应要重新登录，实际在 {page.url.split('?')[0]}")
    expect(current_user(page) in ("", "Guest") if not on_entry(page) else True, "退出后站点还认得这个人")
    no_trouble(hops, [ctx.site])


def add_company_step(page, ctx):
    """侧栏「公司」→ 添加公司：在本页弹窗（一次都不开新窗口，11-03 事故 3）；候选由服务端搜索；点一家「已添加」的打开那家，不新建公司。"""
    hops = start(page, ctx)
    opened = []
    page.context.on("page", lambda other: opened.append(other.url))
    open_nav(page, ctx, "cc-nav-companies")
    by_words(page, "button", "addCompany").click()
    dialog = page.locator('[data-slot="cc-add-company"]')
    dialog.wait_for(state="visible", timeout=NAV_TIMEOUT)
    added = words("added")
    candidates = '[data-slot="cc-add-company"] [data-slot="cc-candidates"] button'
    page.wait_for_function("sel => document.querySelectorAll(sel).length > 0", arg=candidates, timeout=NAV_TIMEOUT)
    rows = [text for text in page.locator(candidates).all_inner_texts()]
    picked = next((text for text in rows if any(word in text for word in added)), None)
    expect(picked, f"候选里没有「已添加」的公司（共 {len(rows)} 个候选）")
    title = picked.strip().splitlines()[0].strip()
    box = dialog.locator("input").first
    if box.count():
        box.fill(title[:12])
        page.wait_for_function("([sel, t]) => [...document.querySelectorAll(sel)].some(e => e.innerText.includes(t))",
                               arg=[candidates, title], timeout=NAV_TIMEOUT)
    page.locator(candidates, has_text=title).first.click()
    dialog.wait_for(state="hidden", timeout=NAV_TIMEOUT)
    page.wait_for_function("t => document.body.innerText.includes(t)", arg=title, timeout=NAV_TIMEOUT)
    expect(not opened, f"添加公司开了新窗口：{opened[0] if opened else ''}")
    expect(page.url.startswith(f"https://{ctx.site}/assistant"), f"点已添加的公司后离开了本站 CC：{page.url.split('?')[0]}")
    no_trouble(hops, [ctx.site])


ROW = '[data-slot="aui_thread-list-item"]'
# 界面删除对话用的同一个接口（assistant.api.delete，POST，带 CSRF），回 [状态码, 正文]
DELETE = """async id => { const r = await fetch('/api/method/pearnly_core.assistant.api.delete', {method: 'POST',
  headers: {'X-Frappe-CSRF-Token': window.csrf_token || '', 'Content-Type': 'application/x-www-form-urlencoded'},
  body: new URLSearchParams({session_id: id})}); return [r.status, (await r.text()).slice(0, 300)]; }"""


def delete_chat(page, ctx, row_id: str):
    """走产品的删除：侧栏这条对话的「更多」→ 删除 → 确认；之后侧栏里没有它。对话标题会被模型改写，所以按行编号认。"""
    if ctx.run != "desktop":
        page.click('[data-slot="cc-nav-toggle"]')
        page.wait_for_timeout(400)
    row = page.locator(f'{ROW}[data-row-id="{row_id}"]')
    row.hover()
    row.locator('[data-slot="aui_thread-list-item-more"]').click()
    by_words(page, "[role=menuitem]", "delete").click()
    page.click('[data-slot="cc-delete-chats-yes"]')
    row.wait_for(state="detached", timeout=NAV_TIMEOUT)


def chat_one_step(page, ctx):
    """新对话发一句固定的话：90 秒内有助手回复、没有出错卡片，侧栏出现这条对话；跑完走产品的删除把它删掉。"""
    hops = start(page, ctx)
    title = f"e2e {time.strftime('%H%M%S')} ตอบคำว่า OK คำเดียว"
    before = {item["id"] for item in cc(page, "return await listed('chat');")}
    expect(cc(page, f"return await say({json.dumps(title)});"), "输入框发不出去（发送钮不可用）")
    reply = page.locator('[data-slot="aui_assistant-message-content"]').last
    try:
        page.wait_for_function("() => { const all = document.querySelectorAll('[data-slot=\"aui_assistant-message-content\"]');"
                               " return all.length && all[all.length - 1].innerText.trim().length > 0; }", timeout=REPLY_TIMEOUT)
    except Exception as error:  # noqa: BLE001  超时就是没回复
        raise Failed(f"{REPLY_TIMEOUT // 1000} 秒内没有助手回复") from error
    errors = page.locator('[data-slot="aui_thread-viewport"] [data-slot*="error"]')
    expect(not errors.count() or not errors.first.is_visible(), f"对话里出现了出错卡片：{errors.first.inner_text()[:80] if errors.count() else ''}")
    expect(reply.inner_text().strip(), "助手回复是空的")
    new_id = ""
    for _ in range(60):   # 侧栏同一个接口（assistant.api.listing）里多出来的那一条就是这次的对话
        fresh = [item["id"] for item in cc(page, "return await listed('chat');") if item["id"] not in before]
        if fresh:
            new_id = fresh[0]
            break
        page.wait_for_timeout(500)
    expect(new_id, "侧栏接口里没有出现这条对话")
    if ctx.run == "desktop":
        # 电脑：走界面「更多 → 删除 → 确认」（当前行在页面上可能还是本地临时编号，按页面上的认）
        delete_chat(page, ctx, page.locator(f"{ROW}[data-active]").get_attribute("data-row-id"))
    else:
        # 手机的删除是长按出菜单（触摸事件，WebKit 上模拟不稳）：调界面删除用的同一个接口清理，界面那条路由电脑宽度覆盖。
        # 回复刚出来时服务端会话可能还在收尾（running），这时删不掉：等侧栏接口里它不再是运行中再删
        item = {}
        for _ in range(60):
            item = next((s for s in cc(page, "return await listed('chat');") if s["id"] == new_id), {})
            # 回复后服务端还在给这条对话起标题，这期间删会锁等待超时回 500（11-05 实测）：等标题不再是发的那句话、也不在运行中
            if item.get("status") not in ("queued", "running", "rescheduling") and item.get("title") and item["title"] != title:
                break
            page.wait_for_timeout(500)
        deleted = page.evaluate(DELETE, new_id)
        expect(deleted[0] == 200, f"删除接口回 {deleted[0]}：{deleted[1][:120]}（删时状态 {item.get('status')}）")
    gone = False
    for _ in range(40):   # 删除落库可能晚一点：最多等 20 秒
        gone = cc(page, f"const id = {json.dumps(new_id)}; return !(await listed('chat')).some(s => s.id === id);")
        if gone:
            break
        page.wait_for_timeout(500)
    expect(gone, f"删掉后侧栏接口里还有这条对话 {new_id}")
    no_trouble(hops, [ctx.site])


STEPS: dict = {}
for _product in ("erp", "cowork"):
    STEPS[f"e2e.{_product}.login"] = login_step
    STEPS[f"e2e.{_product}.switch-site"] = switch_site_step
    STEPS[f"e2e.{_product}.logout"] = logout_step
    STEPS[f"e2e.{_product}.add-company"] = add_company_step
    STEPS[f"e2e.{_product}.chat-one"] = chat_one_step
