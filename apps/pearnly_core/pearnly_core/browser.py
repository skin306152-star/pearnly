# -*- coding: utf-8 -*-
"""外部系统的浏览器会话（Playwright）。

**为什么共用层要有这个**：MR.ERP 那类系统没有开放 API，老产品的铁律是
"一律走服务端 Playwright，不做抓包重放"。DMS 现在要用，Cowork/ERP 以后也要用，
所以浏览器这层放共用层，谁用谁 import。

照搬老产品的 `services/erp/_browser.py`（那边已经拿真系统跑通过），
行为一字未改，只把"为什么"写成中文：

· **`--no-sandbox`**：容器里跑 chromium 要它，否则起不来；
· **`--disable-dev-shm-usage`**：本机容器 `/dev/shm` 只有 64M（实测），
  chromium 的 IPC 会往那儿放，不加这个标志会直接崩；
· **`--disable-gpu`**：容器没有显示器，不加会卡在等 GPU 进程；
· **`sdpt()` 补丁**：MR.ERP 自己的导航函数会把 `target="_blank"` 弹窗变新标签页，
  Playwright 就看不见它了。补丁把它改成同标签页跳转 → 导航可观测；
· **对话框自动接受 + 留档**：DMS 用 JS 弹窗报"重复登录"这类事，
  不接会把流程卡死；接了但**把文字存下来**，错误分类才能说清是什么原因。

⚠️ 凭据不许进日志：`redact_strings` 里的子串在任何 handler 拿到之前就被替换成
`***`，所以哪怕别处手滑把密码拼进日志，也漏不出去。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

logger = logging.getLogger(__name__)


# 把 MR.ERP 自带的 sdpt()（会开新标签页的导航函数）改成同标签页跳转。
# 不改的话 Playwright 看不到导航，登录成功也会被判成"没动"。
SDPT_INIT_SCRIPT = """
(function() {
    var patched = false;
    function tryPatch() {
        if (patched) return;
        if (typeof window.sdpt === 'function') {
            var orig = window.sdpt;
            window.sdpt = function(objval, actionval, targetval) {
                console.log('SDPT_PATCHED', actionval);
                return orig(objval, actionval, '_self');
            };
            patched = true;
        }
    }
    if (document.readyState === 'complete') tryPatch();
    else {
        window.addEventListener('load', tryPatch);
        document.addEventListener('DOMContentLoaded', tryPatch);
    }
    setTimeout(tryPatch, 200);
    setTimeout(tryPatch, 800);
})();
"""


class _RedactFilter(logging.Filter):
    """在日志记录到达任何 handler 之前，把凭据子串换成 `***`。"""

    def __init__(self, redactions: Optional[List[str]] = None):
        super().__init__()
        self.redactions: List[str] = [r for r in (redactions or []) if r]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.redactions:
            return True
        try:
            msg = record.getMessage()
        except Exception:
            return True
        replaced = msg
        changed = False
        for token in self.redactions:
            if token and token in replaced:
                replaced = replaced.replace(token, "***")
                changed = True
        if changed:
            record.msg = replaced
            record.args = None
        return True


def install_redact_filter(log: logging.Logger, secrets: List[str]) -> _RedactFilter:
    """挂上脱敏过滤器。幂等：重复调用是替换而不是叠加。"""
    for existing in list(log.filters):
        if isinstance(existing, _RedactFilter):
            log.removeFilter(existing)
    f = _RedactFilter(secrets)
    log.addFilter(f)
    return f


@dataclass
class BrowserSession:
    """一次浏览器会话：一个 Playwright + Browser + context + page。

    用法：

        with BrowserSession(headless=True, redact_strings=[password]) as bs:
            bs.page.goto(...)
            bs.screenshot("login-ok")
    """

    headless: bool = True
    viewport_width: int = 1440
    viewport_height: int = 900
    locale: str = "th-TH"
    slow_mo_ms: int = 0
    screenshot_dir: Optional[Path] = None
    redact_strings: List[str] = field(default_factory=list)

    # 下面这些在 __enter__ 里填
    _pw: Optional[Playwright] = None
    _browser: Optional[Browser] = None
    _ctx: Optional[BrowserContext] = None
    _page: Optional[Page] = None
    _dialogs: List[str] = field(default_factory=list)
    _shot_count: int = 0

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserSession 必须在 `with` 块里用")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._ctx is None:
            raise RuntimeError("BrowserSession 必须在 `with` 块里用")
        return self._ctx

    @property
    def dialogs(self) -> List[str]:
        return list(self._dialogs)

    @property
    def last_dialog(self) -> str:
        return self._dialogs[-1] if self._dialogs else ""

    def __enter__(self) -> "BrowserSession":
        install_redact_filter(logger, self.redact_strings)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        self._ctx = self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            locale=self.locale,
            accept_downloads=True,
        )
        self._ctx.add_init_script(SDPT_INIT_SCRIPT)
        self._page = self._ctx.new_page()
        self._page.on("dialog", self._on_dialog)
        if self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for closer in (self._ctx, self._browser):
            if closer is not None:
                try:
                    closer.close()
                except Exception:
                    pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._ctx = self._browser = self._page = self._pw = None

    def new_isolated_page(self, *, extra_secrets: Optional[List[str]] = None):
        """在**同一个**已运行的浏览器上再开一个隔离 context + page。

        两个用途：

        1. **第二套凭据**（比如"有修改权限的账号"）登录时 cookie 不能和主会话串；
           context 级隔离就是保证这件事。
        2. **绝不能**在同线程再起第二个 `sync_playwright()` —— 那会死锁
          （报 `Sync API inside the asyncio loop`）。

        返回 `(context, page)`，调用方负责关掉返回的 context。
        """
        if self._browser is None:
            raise RuntimeError("BrowserSession 必须在 `with` 块里用")
        if extra_secrets:
            install_redact_filter(
                logger, list(self.redact_strings) + [s for s in extra_secrets if s]
            )
        ctx = self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            locale=self.locale,
            accept_downloads=True,
        )
        ctx.add_init_script(SDPT_INIT_SCRIPT)
        page = ctx.new_page()
        page.on("dialog", self._on_dialog)
        return ctx, page

    def _on_dialog(self, dialog) -> None:
        msg = (dialog.message or "")[:500]
        self._dialogs.append(f"[{dialog.type}] {msg}")
        logger.info("dialog (%s): %s", dialog.type, msg)
        try:
            dialog.accept()
        except Exception:
            pass

    def screenshot(self, name: str, scenario: str = "") -> Optional[Path]:
        if not self.screenshot_dir or self._page is None:
            return None
        self._shot_count += 1
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") or "shot"
        ts = time.strftime("%H%M%S")
        fname = f"{self._shot_count:02d}-{ts}-{slug}.png"
        path = self.screenshot_dir / fname
        try:
            self._page.screenshot(path=str(path), full_page=True)
        except Exception as e:
            logger.warning("screenshot %s failed: %s", fname, e)
            return None
        if scenario:
            logger.info("shot %s | %s", fname, scenario)
        return path
