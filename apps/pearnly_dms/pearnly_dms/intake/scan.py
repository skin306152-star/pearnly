# -*- coding: utf-8 -*-
"""LINE 里「扫身份证」：LIFF 扫描页把照片直接传到本站点，再走同一条身份证识别。

为什么不只靠在聊天里发照片：照片发进 LINE 后，服务器要去 LINE 日本的下载服务器取回来，
新加坡机房到那里绕美国，单这一步约 3 秒（2026-10-03 生产计时）。扫描页直传本站点，这一步就没有了；
识别本身（模型、提示词、扣费、复核规则）一字不改，走的仍是 `flow.process_image`。

流程（用户 2026-10-04 定：提示发身份证的那几条文字下加一个快捷按钮，不点照旧发照片）：

1. 提示发身份证的文字带一个快捷按钮，打开 LIFF 扫描页（票据模式 `scan`）；
2. 页面上传照片 → `store_upload`：只在收料态（`collecting`）收，照片存站点私有目录，
   会话的 `pending_image` 记成 `scan:<id>`（与聊天里发照片同一个"以最后一张为准"的规则）；
3. 页面替业务员在聊天里发一句纯文字「… #<短码>」（`line.page_reply`，不加图标）→ webhook 收到这条文字
   （带免费回覆令牌）→ `handle_code` 开始识别；LIFF 没有发消息权限（`chat_message.write`）时页面改调 `start`，
   结果只能走推播；
4. 识别任务读照片走 `read`（代替从 LINE 下载），之后与聊天里发照片完全相同。

照片不进不淘汰的状态 Redis（那里只放小键）：存在站点私有目录，Redis 只记归属与过期。
"""

from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

import frappe

from pearnly_dms.line import cards

PREFIX = "scan:"
CODE_LENGTH = 6
# 识别排队最长 30 分钟（`max_age`）：「只开始一次」的标记留一小时。
TTL_SECONDS = 60 * 60
# 原件与归属留 7 天：订车会把它作为「身份证复印件」附进 DMS（可能在建档之后过一阵才订、失败还要补传），
# 和聊天里发的照片一样能取到（2026-10-05 测试机：识别后即删，订车附件失败）。
_KEEP_SECONDS = 7 * 24 * 3600
_STALE_SECONDS = _KEEP_SECONDS
_KEY = "pearnly:dms:id-scan:"
_STARTED = "pearnly:dms:id-scan-started:"
_ID = re.compile(r"[0-9a-f]{16}")


class ScanError(Exception):
    """上传被拒；`code` 是页面按码取话术的稳定代码（`dms_booking.*`）。"""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _state():
    from pearnly_core.execution import state

    return state.store()


def _directory() -> Path:
    return Path(frappe.get_site_path("private", "files", "dms-id-scan"))


def _image_type(content: bytes) -> str:
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return ""


def link() -> str:
    """快捷按钮打开的地址；站点没配 LIFF 就返回空串（调用方不给按钮）。"""
    from pearnly_core import line

    liff = line.liff_id()
    return f"https://liff.line.me/{liff}?editor=scan" if liff else ""


def prompt(text: str) -> dict:
    """提示发身份证的文字，带「扫身份证」快捷按钮；没有 LIFF 就是原来的纯文字。"""
    message = {"type": "text", "text": str(text)}
    url = link()
    if url:
        label = str(cards.BTN_SCAN_CARD)[:20]
        message["quickReply"] = {"items": [
            {"type": "action", "action": {"type": "uri", "label": label, "uri": url}},
        ]}
    return message


def marker_text(code: str) -> str:
    """页面替业务员发回聊天的那句话（按收件人语言，纯文字）；`line.page_reply.route` 认它。"""
    from pearnly_dms.line import page_reply

    return page_reply.marker("scan", code)


def _binding(line_user_id: str) -> Optional[dict]:
    from pearnly_dms.line import identity

    live = identity.current(line_user_id)
    if not live:
        return None
    return {
        "line_user_id": line_user_id,
        "user_id": live["user"],
        "tenant_id": frappe.local.site,
        "binding_revision": str(live.get("binding_revision") or ""),
    }


def _sweep(directory: Path) -> None:
    """顺手删掉 7 天前留下的照片（有界：只看这一个目录）。"""
    cutoff = time.time() - _STALE_SECONDS
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries[:200]:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
        except OSError:
            continue


def store_upload(line_user_id: str, content: bytes) -> dict:
    """收下扫描页的照片，登记成这位业务员当前要识别的图片。返回页面要发回聊天的那句话。"""
    from pearnly_core import line_session as store
    from pearnly_dms.intake import flow
    from pearnly_dms.ocr.ocr_service import MAX_ID_CARD_MB

    if not content:
        raise ScanError("dms_scan.empty")
    if len(content) > MAX_ID_CARD_MB * 1024 * 1024:
        raise ScanError("dms_scan.too_large")
    if not _image_type(content):
        raise ScanError("dms_scan.not_image")
    binding = _binding(line_user_id)
    if not binding:
        raise ScanError("dms_booking.not_bound", 403)
    if (store.get_session(line_user_id) or {}).get("state") != "collecting":
        raise ScanError("dms_booking.expired", 409)

    scan_id = secrets.token_hex(8)
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    _sweep(directory)
    path = directory / scan_id
    path.write_bytes(content)
    _state().set_value(_KEY + scan_id, {"line_user_id": line_user_id, "size": len(content)},
                       expires_in_sec=_KEEP_SECONDS)
    flow.merge_session(binding, line_user_id, {"pending_image": PREFIX + scan_id}, keep=("phone", "mode"))
    code = scan_id[:CODE_LENGTH]
    return {"code": code, "marker": marker_text(code)}


def _pending(line_user_id: str, code: str = "") -> str:
    """当前等着识别的扫描图（`scan:<id>`）；短码对不上或不在收料态就是空串。"""
    from pearnly_core import line_session as store

    sess = store.get_session(line_user_id) or {}
    pending = str((sess.get("payload") or {}).get("pending_image") or "")
    if sess.get("state") != "collecting" or not pending.startswith(PREFIX):
        return ""
    scan_id = pending[len(PREFIX):]
    if not _ID.fullmatch(scan_id) or (code and not scan_id.startswith(code)):
        return ""
    return pending


def _begin(line_user_id: str, pending: str, reply_token: str) -> bool:
    """开始识别（每张扫描图只开始一次：自动发的那句话与页面的回落调用可能都会到）。"""
    from pearnly_dms.intake import flow

    binding = _binding(line_user_id)
    if not binding:
        return False
    client = _state()
    if not client.set(client.make_key(_STARTED + pending[len(PREFIX):]), "1", ex=TTL_SECONDS, nx=True):
        return True
    flow.handle_image(binding, line_user_id, pending, reply_token)
    return True


def handle_code(line_user_id: str, reply_token: str, code: str) -> bool:
    """聊天里收到扫描页发的那句话（`line.page_reply.route` 已认出短码）→ 用它的免费回覆令牌开始识别。"""
    pending = _pending(line_user_id, code)
    if not pending:
        from pearnly_core import line

        line.reply(reply_token, [{"type": "text", "text": str(cards.TXT_EXPIRED)}])
        return True
    _begin(line_user_id, pending, reply_token)
    return True


def start(line_user_id: str, code: str) -> bool:
    """扫描页发不了消息时的回落：直接开始识别（没有令牌，结果走推播）。"""
    pending = _pending(line_user_id, str(code or ""))
    if not pending:
        raise ScanError("dms_booking.expired", 409)
    return _begin(line_user_id, pending, "")


def read(message_id: str, line_user_id: str) -> bytes:
    """识别任务取扫描图（代替从 LINE 下载），只给上传者。原件留着给订车附件用（见 `content`）。"""
    scan_id = str(message_id or "")[len(PREFIX):]
    if not _ID.fullmatch(scan_id):
        return b""
    meta = _state().get_value(_KEY + scan_id)
    if not isinstance(meta, dict) or meta.get("line_user_id") != line_user_id:
        return b""
    return content(message_id)


def content(message_id: str) -> bytes:
    """订车附件取「身份证复印件」：`scan:<id>` 是服务端随机生成、只在本人草稿里的编号。取不到返回空。"""
    scan_id = str(message_id or "")[len(PREFIX):]
    if not str(message_id or "").startswith(PREFIX) or not _ID.fullmatch(scan_id):
        return b""
    try:
        return (_directory() / scan_id).read_bytes()
    except OSError:
        return b""


__all__ = ["PREFIX", "ScanError", "content", "handle_code", "link", "marker_text", "prompt",
           "read", "start", "store_upload"]
