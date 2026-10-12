# -*- coding: utf-8 -*-
"""Tomo 窗口的地址规则（纯函数，不碰 Qt，单测直接测）。

- 窗口里只开自己的 https 页面（fatcc.com 入口、auth.pearnly.com 登录、各公司 <租户>.pearnly.com）；
  别的网址交给系统浏览器，本地文件、data: 等一律不在窗口里打开。
- 下次打开回到上次停留的对话页（/assistant），没有就从入口 fatcc.com 进。
- 测试时用环境变量 PEARNLY_TOMO_ENTRY 换入口（例如 https://testai.pearnly.com），同样只认 Pearnly 的 https 地址。
- 托盘的对话提醒点开那段对话：``https://<公司站点>/assistant?c=<对话>``，没有对话就是定时任务页 ``?view=tasks``
  （与共用层 assistant/contracts.deep_link 同一形状）；对话页已开着就交给页面的 window.tomo.open，不重新加载。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

ENTRY = "https://fatcc.com"
OWN_DOMAINS = ("pearnly.com", "fatcc.com")   # 入口 fatcc.com（2026-10-10 起），登录与各公司站点在 pearnly.com
CHAT_PATH = "/assistant"
# 对话编号的形状（与服务端 pearnly_core/assistant/contracts.OPAQUE_ID 一致）
SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$")


def is_own(url: str) -> bool:
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == d or host.endswith("." + d) for d in OWN_DOMAINS)


def is_chat(url: str) -> bool:
    if not is_own(url):
        return False
    path = urlsplit(url).path
    return path == CHAT_PATH or path.startswith(CHAT_PATH + "/")


def entry_url(env=None) -> str:
    value = str((os.environ if env is None else env).get("PEARNLY_TOMO_ENTRY") or "").strip().rstrip("/")
    return value if is_own(value) else ENTRY


def resume_url(saved: str, env=None) -> str:
    """上次停留的对话页；入口换了（测试 / 正式）就不沿用另一边的公司。"""
    entry = entry_url(env)
    if not is_chat(saved):
        return entry
    testing = urlsplit(entry).hostname != urlsplit(ENTRY).hostname
    saved_testing = (urlsplit(saved).hostname or "").startswith("test")
    return saved if testing == saved_testing else entry


def route(url: str) -> str:
    """顶层导航去哪：'load' 窗口里开、'external' 交给系统浏览器、'block' 不开。"""
    if is_own(url):
        return "load"
    if urlsplit(str(url or "")).scheme in ("http", "https", "mailto"):
        return "external"
    return "block"


def ask_script(text: str) -> str:
    """把快捷输入条里的文字交给对话页（frontend/assistant/src/lib/desktop.ts 的 window.tomo.ask）；
    页面刚打开时 React 还没挂好，最多等 10 秒。"""
    return ("(function f(n){if(window.tomo){window.tomo.ask(" + json.dumps(str(text), ensure_ascii=False) + ");return}"
            "if(n>0)setTimeout(function(){f(n-1)},250)})(40)")


def session_of(value: str) -> str:
    """合形状的对话编号；不合形状的当作没有（打开定时任务页）。"""
    text = str(value or "")
    return text if SESSION.match(text) else ""


def same_site(a: str, b: str) -> bool:
    try:
        left, right = urlsplit(str(a or "")), urlsplit(str(b or ""))
    except ValueError:
        return False
    return bool(left.hostname) and (left.hostname, left.port) == (right.hostname, right.port)


def conversation_url(site: str, session: str = "") -> str:
    """提醒点开的地址：``site`` 是连接的服务器（自家 https 站点），对话编号合形状就打开那段对话，否则定时任务页；
    ``site`` 不是自家 https 站点返回空串（托盘就只打开窗口）。"""
    if not is_own(site):
        return ""
    parts = urlsplit(str(site))
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    target = session_of(session)
    query = f"c={quote(target, safe='')}" if target else "view=tasks"
    return f"https://{host}{CHAT_PATH}?{query}"


def open_script(session: str, url: str) -> str:
    """把「打开这段对话」交给已开着的对话页（frontend/assistant/src/lib/desktop.ts 的 window.tomo.open，空串 = 定时任务页）；
    旧版页面没有 open 就直接打开地址。页面刚打开时 React 还没挂好，最多等 10 秒，等不到也直接打开地址。"""
    target, address = json.dumps(session_of(session)), json.dumps(str(url or ""))
    return ("(function f(n){var t=window.tomo;if(t&&t.open){t.open(" + target + ");return}"
            "if(t||n<=0){location.href=" + address + ";return}"
            "setTimeout(function(){f(n-1)},250)})(40)")


def unique_path(folder: Path, name: str) -> Path:
    """下载存到「下载」文件夹；同名不覆盖，依次加 (1)、(2)…；名字里不安全的字符换掉。"""
    clean = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in str(name or "")).strip(" .") or "download"
    stem, dot, ext = clean.rpartition(".")
    if not dot:
        stem, ext = clean, ""
    candidate = folder / clean
    n = 1
    while candidate.exists():
        candidate = folder / (f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})")
        n += 1
    return candidate
