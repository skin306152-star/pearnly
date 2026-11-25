# -*- coding: utf-8 -*-
r"""桥与云端那份 CC 之间 ``ctl\`` 里的状态 / 命令文件（第二阶段评估 4.1、4.2）：

    members.json     桥 → 云端  这台云端电脑在哪些站点登记、走哪种通道（不含令牌）
    status.json      云端 → 桥  云端 CC 的版本、成员是否就绪、找到的数据目录与账套数
    rescan.json      桥 → 云端  「重新查找」
    add-place.json   桥 → 云端  手填一个数据目录
    uninstall.json   桥 → 云端  「断开」时请云端删我们装的东西（段 ③，``cloud_self.handle_uninstall``）
    uninstalled.json 云端 → 桥  上一条的回执（同一编号：``{code, dir, link}``）

全部用**当前隧道会话的密钥**（``tunnel_protocol.Session.key``，桥每次起会话生成、经 ``session.json`` 交给云端、只在内存）加密：
AES-256-GCM，密钥由会话密钥经 HKDF-SHA256 按文件种类分别派生，96 位随机 nonce，附加数据是「标签 | 种类 | 会话号 | 编号」。
换了会话、改了种类、动过密文都解不开；会话号对不上的直接当过期。命令文件（重新查找、添加）带 128 位随机编号，读的一方记已见编号，
见过的不再做（防重放）；状态文件（成员、状态）是整份当前状态，后写的盖前写的，不需要编号去重。

文件写法沿用 ``cred_handoff``：先写 ``.part`` 再改名，读的一方读后删除并回读确认已不在（:func:`cred_handoff.take`）。
本模块只有编解码与校验，不做 IO，Mac 上离线测。内容里不放令牌、密码、设备密钥；日志只记种类与原因码。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from typing import Any, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1

MEMBERS_FILE = "members.json"
STATUS_FILE = "status.json"
RESCAN_FILE = "rescan.json"
ADD_PLACE_FILE = "add-place.json"
UNINSTALL_FILE = "uninstall.json"
UNINSTALLED_FILE = "uninstalled.json"
KINDS = {"members": MEMBERS_FILE, "status": STATUS_FILE, "rescan": RESCAN_FILE, "add_place": ADD_PLACE_FILE,
         "uninstall": UNINSTALL_FILE, "uninstalled": UNINSTALLED_FILE}
FILES = tuple(KINDS.values())

# 原因码
WRONG_EPOCH = "wrong_epoch"
TAMPERED = "tampered"
MALFORMED = "malformed"

_LABEL = b"pearnly-cc/cloud-ctl/v1"
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_MAX_FILE = 256 * 1024
_NONCE = 12


class CtlError(Exception):
    """文件不对（``code``：会话号不对 / 解不开 / 格式不对）。文本只有原因码。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _key(session_key: bytes, kind: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=_LABEL + b"|" + kind.encode("ascii")).derive(bytes(session_key))


def _aad(kind: str, epoch: str, item_id: str) -> bytes:
    return b"|".join((_LABEL, kind.encode("ascii"), epoch.encode("ascii"), item_id.encode("ascii")))


def new_id() -> str:
    return secrets.token_hex(16)


def seal(session, kind: str, payload: dict, *, item_id: Optional[str] = None) -> bytes:
    """``kind`` 种类的文件内容（``KINDS`` 的键）；``payload`` 是要交的 JSON 对象。"""
    if kind not in KINDS or not isinstance(payload, dict):
        raise CtlError(MALFORMED)
    item_id = item_id or new_id()
    nonce = secrets.token_bytes(_NONCE)
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sealed = AESGCM(_key(session.key, kind)).encrypt(nonce, plain, _aad(kind, session.epoch, item_id))
    head = {"v": VERSION, "kind": kind, "epoch": session.epoch, "id": item_id,
            "nonce": base64.b64encode(nonce).decode("ascii"), "ct": base64.b64encode(sealed).decode("ascii")}
    return (json.dumps(head, separators=(",", ":")) + "\n").encode("ascii")


def open_(data, session, kind: str) -> tuple:
    """解开 ``kind`` 种类的文件：回 ``(编号, payload)``。会话号不是当前的抛 ``wrong_epoch``，解不开 ``tampered``。"""
    if kind not in KINDS:
        raise CtlError(MALFORMED)
    try:
        raw = bytes(data)
        if len(raw) > _MAX_FILE:
            raise CtlError(MALFORMED)
        head = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        raise CtlError(MALFORMED) from None
    if not isinstance(head, dict) or head.get("v") != VERSION or head.get("kind") != kind:
        raise CtlError(MALFORMED)
    epoch, item_id = head.get("epoch"), head.get("id")
    if not isinstance(epoch, str) or not _HEX32.match(epoch) or not isinstance(item_id, str) or not _HEX32.match(item_id):
        raise CtlError(MALFORMED)
    if epoch != session.epoch:
        raise CtlError(WRONG_EPOCH)
    try:
        nonce = base64.b64decode(str(head.get("nonce") or "").encode("ascii"), validate=True)
        sealed = base64.b64decode(str(head.get("ct") or "").encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise CtlError(MALFORMED) from None
    if len(nonce) != _NONCE:
        raise CtlError(MALFORMED)
    try:
        plain = AESGCM(_key(session.key, kind)).decrypt(nonce, sealed, _aad(kind, epoch, item_id))
    except InvalidTag:
        raise CtlError(TAMPERED) from None
    try:
        payload = json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise CtlError(MALFORMED) from None
    if not isinstance(payload, dict):
        raise CtlError(MALFORMED)
    return item_id, payload


# ── 成员（members.json） ──────────────────────────────────────────────────────

CHANNELS = ("cowork", "core")
_SITE = re.compile(r"^[a-z0-9.:-]{1,253}$")
_ENDPOINT = re.compile(r"^[A-Za-z0-9-]{0,64}$")


def member_entry(site: str, channel: str, endpoint: str = "") -> dict:
    """一条成员：站点（域名）、通道（cowork / core）、端点号（令牌里 ``<前缀><端点号>_<秘密>`` 的中段，不是秘密）。"""
    site = str(site or "").strip().lower().rstrip(".")
    if not _SITE.match(site) or channel not in CHANNELS or not _ENDPOINT.match(str(endpoint or "")):
        raise CtlError(MALFORMED)
    return {"site": site, "channel": channel, "endpoint": str(endpoint or "")}


def endpoint_of(token: str) -> str:
    """令牌 ``<前缀><端点号>_<秘密>`` 里的端点号；形状不认识（含别的字符、太长）回空串——端点号只为保持占位令牌的形状，
    缺了不影响路由，不能因此把整个站点漏下发。"""
    parts = str(token or "").split("_", 2)
    endpoint = parts[1] if len(parts) == 3 else ""
    return endpoint if _ENDPOINT.match(endpoint) else ""


def members_of(payload: Any) -> list:
    """``members.json`` 的 payload → 规范化的成员列表（同一站点只留第一条）；不合规抛 ``malformed``。"""
    items = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(items, list) or len(items) > 16:
        raise CtlError(MALFORMED)
    found, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise CtlError(MALFORMED)
        entry = member_entry(item.get("site"), item.get("channel"), item.get("endpoint") or "")
        if entry["site"] not in seen:
            seen.add(entry["site"])
            found.append(entry)
    return found


__all__ = ["ADD_PLACE_FILE", "CHANNELS", "CtlError", "FILES", "KINDS", "MALFORMED", "MEMBERS_FILE", "RESCAN_FILE",
           "STATUS_FILE", "TAMPERED", "UNINSTALLED_FILE", "UNINSTALL_FILE", "WRONG_EPOCH", "endpoint_of", "member_entry", "members_of", "new_id", "open_", "seal"]
