"""CC 桌面端的按需请求（2026-10 CC 闭环）：服务端请某台电脑上的 CC 桌面端读一样东西交回来——Express 账套
（``express_read``）、已授权文件夹里的文件清单（``folder_list``）或其中一份文件（``folder_fetch``）。纯规则，不连库、
不依赖框架。

与推送队列（lease / ack）完全分开：自己一张表（Cowork Companion Request）、两条机器方法（``request_api``，登记在
``http_auth.REQUEST_METHODS``），小助手另起一条轮询线程领取；读得再慢也不挡推送、不碰推送的围栏、写者锁与看门狗。
状态：requested（等电脑来领）→ leased（电脑在做，带一次性的 handle）→ done / failed；没人来领或做到一半超时算
expired。结果字节分块交回（每块 ``CHUNK_BYTES``，base64 后仍在租户站点 nginx 30m 与 Frappe 25MB 请求上限之内），
存在站点私有目录，取走即删。时间一律存 UTC。

加一种请求（kind）：
1. 这里与小助手 ``companion/src/companion/request_contract.py`` 的 ``KINDS``、``MAX_BYTES``、``LEASE_SECONDS`` 各加一项
   （``tests/test_companion_contract.py`` 逐值比对）；只有 Express 连接能做的（读账套）不进 ``FOLDER_KINDS``；
2. 发起它的业务域自己校验参数（这里只管是不是一个不大的 JSON 对象），调
   ``request_service.create(kind, connection, params, request_key)``，再用 ``status`` / ``result`` / ``drop`` 取结果；
3. 小助手在 ``request_worker.HANDLERS`` 登记处理函数 ``fn(cfg, params) -> Result``；结果字节的格式由这一种请求自己定
   （JSON 一律 gzip 后交），处理不了回 ``RequestFailed(code)``。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

MIB = 1024 * 1024
KINDS = ("express_read", "folder_list", "folder_fetch")
FOLDER_KINDS = ("folder_list", "folder_fetch")
# 哪种连接的电脑能做哪几种：Express 的电脑都能做；MR.ERP 的小助手成员只读文件夹。
ADAPTER_KINDS = {"express": KINDS, "mrerp": FOLDER_KINDS}
STATUSES = ("requested", "leased", "done", "failed", "expired")
OPEN = ("requested", "leased")
CHUNK_BYTES = 4 * MIB
MAX_BYTES = {"express_read": 50 * MIB, "folder_list": 2 * MIB, "folder_fetch": 30 * MIB}
LEASE_SECONDS = {"express_read": 900, "folder_list": 180, "folder_fetch": 600}
# 这么久没有电脑来领就算过期（code not_claimed）；领了以后每交一块续一次租约。
CLAIM_SECONDS = 600
# 小助手空闲时每 POLL_SECONDS 来领一次：SEEN_SECONDS 之内来过才算此刻在线。
POLL_SECONDS = 15
SEEN_SECONDS = 90
# 旧请求行（含没取走的结果）在同一连接下一次新建请求时顺手删掉，不另开定时任务。
KEEP_SECONDS = 7 * 86400
# 同一个人在同一条连接上同时等着的请求最多这么多（防止一次排上几百个读文件）。
OPEN_PER_ACTOR = 20
# 读账套要点名推送过的单据号（最多 2000 个，`readback.contracts.DOCNUMS_MAX`），参数要放得下。
PARAMS_MAX = 64 * 1024
META_MAX = 4 * 1024
MIN_COMPANION = "1.5.0"
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,140}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class ContractError(ValueError):
    def __init__(self, code: str, **extra: Any):
        self.code = code
        self.extra = extra
        super().__init__(code)


def kind(value: Any) -> str:
    if value not in KINDS:
        raise ContractError("companion.on_demand_invalid")
    return value


def request_key(value: Any) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ContractError("companion.on_demand_invalid")
    return value


def params(value: Any) -> Dict[str, Any]:
    """请求参数：一个不大的 JSON 对象（各业务域自己校验内容）。"""
    if not isinstance(value, dict):
        raise ContractError("companion.on_demand_invalid")
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ContractError("companion.on_demand_invalid") from error
    if len(text.encode("utf-8")) > PARAMS_MAX:
        raise ContractError("companion.on_demand_invalid")
    return json.loads(text)


def allowed_kinds(adapter: Any, declared: List[str]) -> List[str]:
    """小助手声明能做的，再按连接的 ERP 筛（MR.ERP 的成员不领读账套）。"""
    usable = ADAPTER_KINDS.get(str(adapter or ""), ())
    return [item for item in declared if item in usable]


def next_request(body: Any) -> Dict[str, Any]:
    """小助手来领：{version, kinds, device}。新版小助手声明了本服务端还不认识的种类就略过那几种，不整个拒绝。"""
    if not isinstance(body, dict):
        raise ContractError("companion.on_demand_invalid")
    version, device, declared = body.get("version"), body.get("device"), body.get("kinds")
    if not isinstance(version, str) or len(version) > 40 or not isinstance(declared, list) or len(declared) > 20:
        raise ContractError("companion.on_demand_invalid")
    if device is not None and (not isinstance(device, str) or len(device) > 60):
        raise ContractError("companion.on_demand_invalid")
    kinds = []
    for item in declared:
        if item in KINDS and item not in kinds:
            kinds.append(item)
    return {"version": version.strip(), "kinds": kinds, "device": str(device or "").strip()}


def _b64_limit(size: int) -> int:
    return 4 * ((size + 2) // 3)


def _meta(value: Any) -> Dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ContractError("companion.on_demand_invalid")
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ContractError("companion.on_demand_invalid") from error
    if len(text.encode("utf-8")) > META_MAX:
        raise ContractError("companion.on_demand_invalid")
    return json.loads(text)


def complete_request(body: Any) -> Dict[str, Any]:
    """小助手交结果。失败：{request, handle, ok: false, code}；成功：一块一块交 {request, handle, ok: true, offset,
    content(base64), final}，最后一块另带 {size, sha256, meta}。"""
    if not isinstance(body, dict):
        raise ContractError("companion.on_demand_invalid")
    request, handle, ok = body.get("request"), body.get("handle"), body.get("ok")
    if not isinstance(request, str) or not _HEX32.fullmatch(request) or not isinstance(handle, str) \
            or not _HEX32.fullmatch(handle) or not isinstance(ok, bool):
        raise ContractError("companion.on_demand_invalid")
    if not ok:
        code = body.get("code")
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            raise ContractError("companion.on_demand_invalid")
        return {"request": request, "handle": handle, "ok": False, "code": code}
    offset, encoded, final = body.get("offset"), body.get("content"), body.get("final")
    if type(offset) is not int or offset < 0 or not isinstance(encoded, str) \
            or len(encoded) > _b64_limit(CHUNK_BYTES) or not isinstance(final, bool):
        raise ContractError("companion.on_demand_invalid")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ContractError("companion.on_demand_invalid") from error
    out = {"request": request, "handle": handle, "ok": True, "offset": offset, "content": content, "final": final}
    if final:
        size, digest = body.get("size"), body.get("sha256")
        if type(size) is not int or size < 0 or not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            raise ContractError("companion.on_demand_invalid")
        out.update(size=size, sha256=digest, meta=_meta(body.get("meta")))
    return out


def effective(status: str, code: Optional[str], requested_at: Optional[datetime],
              lease_expires_at: Optional[datetime], now: datetime) -> Tuple[str, str]:
    """此刻的状态：等太久没人领、领了超时未交的，不必等到下一次有人来领才落库，读的时候就按过期算。"""
    if status == "requested" and requested_at and now - requested_at > timedelta(seconds=CLAIM_SECONDS):
        return "expired", "not_claimed"
    if status == "leased" and (not lease_expires_at or lease_expires_at <= now):
        return "expired", "lease_expired"
    return status, code or ""


def version_tuple(value: Any) -> tuple:
    try:
        return tuple(int(part) for part in str(value or "").strip().split("."))
    except ValueError:
        return (0,)


def supports(version: Any, minimum: str = MIN_COMPANION) -> bool:
    """这个版本的 CC 桌面端会来领按需请求。"""
    return version_tuple(version) >= version_tuple(minimum)


def seen_recently(at: Optional[datetime], now: datetime) -> bool:
    return at is not None and abs((now - at).total_seconds()) <= SEEN_SECONDS
