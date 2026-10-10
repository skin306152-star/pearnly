# -*- coding: utf-8 -*-
"""Tomo 窗口的地址规则（纯函数，不碰 Qt，单测直接测）。

- 窗口里只开自己的 https 页面（fatcc.com 入口、auth.pearnly.com 登录、各公司 <租户>.pearnly.com）；
  别的网址交给系统浏览器，本地文件、data: 等一律不在窗口里打开。
- 下次打开回到上次停留的对话页（/assistant），没有就从入口 fatcc.com 进。
- 测试时用环境变量 PEARNLY_TOMO_ENTRY 换入口（例如 https://testai.pearnly.com），同样只认 Pearnly 的 https 地址。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ENTRY = "https://fatcc.com"
OWN_DOMAINS = ("pearnly.com", "fatcc.com")   # 入口 fatcc.com（2026-10-10 起），登录与各公司站点在 pearnly.com
CHAT_PATH = "/assistant"


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
