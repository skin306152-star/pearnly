# -*- coding: utf-8 -*-
"""「LINE 开通手册」页面的正文（管理后台左侧菜单，用户 2026-09-30）。

正文是给运维看的操作文档（按语言一份 HTML，目前只有中文），不是界面文案：
界面上的标题、菜单、输入框标签照常走翻译包。截图随代码放在 `line_guide_content/img/`，
密钥与令牌已打码。管理后台不提供本 App 的静态文件（`/assets/pearnly_control/...` 是 404），
所以图片在这里转成 data URI 一起返回。
"""

import base64
import re
from pathlib import Path

import frappe

CONTENT = Path(__file__).with_name("line_guide_content")
_IMG = re.compile(r'src="img/([a-z0-9_]+\.jpg)"')


def _embed(match) -> str:
    path = CONTENT / "img" / match.group(1)
    if not path.is_file():
        return 'src=""'
    return 'src="data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode() + '"'


def render(lang: str = "") -> str:
    """指定语言的正文；没有这门语言就给中文。"""
    lang = (lang or "").split("-")[0]
    path = CONTENT / f"{lang}.html"
    if not path.is_file():
        path = CONTENT / "zh.html"
    return _IMG.sub(_embed, path.read_text(encoding="utf-8"))


@frappe.whitelist(methods=["GET"])
def content() -> dict:
    frappe.only_for("System Manager")
    return {"html": render(frappe.local.lang)}
