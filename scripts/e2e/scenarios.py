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

import json
import re
from dataclasses import dataclass
from pathlib import Path

I18N = Path(__file__).resolve().parents[2] / "frontend" / "assistant" / "src" / "i18n.ts"


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
        "([sel, texts]) => [...document.querySelectorAll(sel)].find(e => e.getClientRects().length && texts.includes(e.innerText.trim()))",
        arg=[selector, texts], timeout=NAV_TIMEOUT)
    return handle.as_element()


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


STEPS: dict = {}
for _product in ("erp", "cowork"):
    STEPS[f"e2e.{_product}.login"] = login_step
    STEPS[f"e2e.{_product}.switch-site"] = switch_site_step
    STEPS[f"e2e.{_product}.logout"] = logout_step
