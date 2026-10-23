"""CC 桌面版共用机器通道的纯约定（多产品共用，ADR-025）：令牌、设备密钥、账套行、在线判据。不连库、不依赖框架、不做 IO。

- 令牌 ``pcc_<电脑编号>_<随机串>``：与 Cowork 的 ``exp_`` 互不相认（两套机器钩子在同一站点上各认各的），CC 桌面版按前缀
  认出这是共用通道的成员（方法路径 ``pearnly_core.cc_desktop.machine.*``）。库里只存 sha256。
- 设备密钥：CC 桌面版本机的随机串（DPAPI 保护），与 Cowork 同一把；站点里只存 ``sha256(["pcc-device", 站点, 密钥])``，
  所以同一台电脑在 Cowork 站点和 ERP 站点上是互不相干的两行。
- 账套行：心跳报的账套名单（与 Cowork 同一份上报，经共用的 `wire.report_rules.account_sets` 净化）→ 每份一行，键是
  ``book_key``：报了文件号（``uid``，1.7.0 起）按文件号，没报按这台电脑的路径；取 sha256 前 40 位，不透明、定长好建索引。
  同一份账套两台电脑报的文件号相同就是同一把站点内写者锁；不同站点之间的互斥在电脑那一侧（账套目录里的锁文件）。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any, List, Mapping, Optional

from pearnly_core.cc_desktop.wire import report_rules
from pearnly_core.express_rules.targets.identity import normalize_express_account_key

TOKEN_PREFIX = "pcc_"
MAX_TOKEN_LENGTH = 1024
DEVICE_PREFIX = "PCD-"
_DEVICE = re.compile(r"^PCD-[0-9a-f]{20}$")
_SECRET = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_DEVICE_KEY = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 心跳 / 领单 / 领请求这么久之内来过才算在线（CC 桌面版每 60 秒一次心跳，连续 3 次失败才算离线）
ONLINE_SECONDS = 180
# 一台电脑最多登记这么多份账套（事务所的数据根里常有几百份；多了截掉，按「能写的在前」）
MAX_BOOKS = 2000
MAPPING_KEYS = ("revenue_acc", "ar_acc", "vat_output_acc", "fallback_acc", "ap_acc", "vat_input_acc")


class ChannelError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def text(value: Any, *, maximum: int = 140, optional: bool = False) -> str:
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ChannelError("cc_desktop.request_invalid")
    return value.strip()


def wire_flag(value: Any) -> Any:
    """HTTP 表单把布尔值送成字符串：只认这四种确切写法。"""
    return {"1": True, "true": True, "0": False, "false": False}.get(value, value) if isinstance(value, str) else value


def flag(value: Any) -> bool:
    value = wire_flag(value)
    if value in (None, ""):
        return False
    if type(value) is not bool:
        raise ChannelError("cc_desktop.request_invalid")
    return value


def fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── 电脑与令牌 ────────────────────────────────────────────────────────────────


def device_key(value: Any) -> str:
    if not isinstance(value, str) or not _DEVICE_KEY.fullmatch(value):
        raise ChannelError("cc_desktop.request_invalid")
    return value


def device_digest(site: str, key: str) -> str:
    return fingerprint(["pcc-device", text(site), device_key(key)])


def new_device_name() -> str:
    return DEVICE_PREFIX + secrets.token_hex(10)


def new_token(device: str) -> str:
    if not _DEVICE.fullmatch(str(device or "")):
        raise ChannelError("cc_desktop.request_invalid")
    return f"{TOKEN_PREFIX}{device}_{secrets.token_urlsafe(32)}"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_token(token: Any) -> Optional[tuple]:
    """``pcc_<电脑>_<随机串>`` → (电脑编号, 摘要)；形状不对回 None。"""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH or not token.startswith(TOKEN_PREFIX):
        return None
    parts = token[len(TOKEN_PREFIX):].split("_", 1)
    if len(parts) != 2 or not _DEVICE.fullmatch(parts[0]) or not _SECRET.fullmatch(parts[1]):
        return None
    return parts[0], token_digest(token)


def digest_matches(stored: Any, supplied: Any) -> bool:
    import hmac

    return (isinstance(stored, str) and isinstance(supplied, str) and bool(_DIGEST.fullmatch(stored))
            and bool(_DIGEST.fullmatch(supplied)) and hmac.compare_digest(stored, supplied))


def users(raw: Any) -> List[str]:
    """电脑行上「在这台电脑上登录过 CC 的人」（JSON 列表）；坏值当没有。"""
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
    except ValueError:
        return []
    return sorted({str(item) for item in value if isinstance(item, str) and item}) if isinstance(value, list) else []


def with_user(raw: Any, user: str) -> str:
    return json.dumps(sorted({*users(raw), user}), ensure_ascii=False)


def online(last_seen: Optional[datetime], now: datetime) -> bool:
    return last_seen is not None and abs((now - last_seen).total_seconds()) <= ONLINE_SECONDS


# ── 账套行 ────────────────────────────────────────────────────────────────────


def book_identity(path: Any, uid: Any = "") -> str:
    uid = str(uid or "").strip().lower()
    return f"uid:{uid}" if uid else f"path:{normalize_express_account_key(path)}"


def book_key(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]


def _day(value: Any) -> Optional[str]:
    value = str(value or "")[:10]
    if not _ISO_DAY.fullmatch(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _mapping(value: Any) -> dict:
    if not isinstance(value, Mapping):
        return {}
    return {key: str(value.get(key) or "").strip()[:40] for key in MAPPING_KEYS if str(value.get(key) or "").strip()}


def books(raw: Any) -> List[dict]:
    """心跳报的账套名单 → 这台电脑的账套行（不含电脑编号）。路径空、同一键重复的丢掉；最多 :data:`MAX_BOOKS` 份，能写的在前。"""
    rows, seen = [], set()
    for item in report_rules.account_sets(raw):
        attributes = item.get("attributes") or {}
        path = str(attributes.get("path") or item.get("source_id") or "").strip()
        if not path:
            continue
        identity = book_identity(path, attributes.get("uid"))
        key = book_key(identity)
        if key in seen:
            continue
        seen.add(key)
        try:
            row = max(0, int(attributes.get("row") or 0))
        except (TypeError, ValueError):
            row = 0
        rows.append({
            "book_key": key, "identity": identity[:1000], "path": path[:1000],
            "root": str(attributes.get("root") or "").strip()[:1000], "row": row,
            "uid": str(attributes.get("uid") or "").strip()[:200],
            "label": str(item.get("label") or "")[:200], "company": str(attributes.get("company") or "")[:200],
            "tax_id": re.sub(r"\D", "", str(attributes.get("tax_id") or ""))[:20],
            "branch": re.sub(r"\D", "", str(attributes.get("branch") or ""))[:10],
            "period_start": _day(attributes.get("period_start")), "period_end": _day(attributes.get("period_end")),
            "next_end": _day(attributes.get("next_end")), "writable": attributes.get("writable") is True,
            "mapping": json.dumps(_mapping(attributes.get("mapping")), ensure_ascii=False, sort_keys=True),
        })
    rows.sort(key=lambda row: (not row["writable"],))
    return rows[:MAX_BOOKS]


def same_book(stored: Mapping[str, Any], fresh: Mapping[str, Any]) -> bool:
    """存着的行与这次报的一样（不用重写）。"""
    keys = ("identity", "path", "root", "row", "uid", "label", "company", "tax_id", "branch", "period_start",
            "period_end", "next_end", "writable", "mapping")

    def norm(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()[:10]
        if isinstance(value, bool):
            return int(value)
        return "" if value is None else value

    return all(norm(stored.get(key)) == norm(fresh.get(key)) for key in keys)


def expires(now: datetime, seconds: int) -> datetime:
    return now + timedelta(seconds=seconds)


__all__ = ["ChannelError", "MAPPING_KEYS", "MAX_BOOKS", "ONLINE_SECONDS", "TOKEN_PREFIX", "book_identity", "book_key",
           "books", "device_digest", "device_key", "digest_matches", "expires", "fingerprint", "flag",
           "new_device_name", "new_token", "online", "parse_token", "same_book", "text", "token_digest", "users",
           "wire_flag", "with_user"]
