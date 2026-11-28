"""MR.ERP 一位客户此刻的未收单（只读；ERP 收款直写列未收发票用，2026-11-17）：解析是纯函数，读取走共用会话入口。

来源（MR.ERP 源码只读核实）：收款新单「选单」页 ``farev/selbill.php?id=<客户内部号>`` 按客户列四个页签（`selbill.php`）——
รับเงินล่วงหน้า（预收，``tab-arad``）、ขายเชื่อ（赊销，``tab-artran``，期初应收也记在这里）、เพิ่มหนี้（增债，``tab-arsd``）、
ลดหนี้（减债，``tab-arsc``）；每页签一行一张 ``balnetfarev > 0`` 的单：选框、单号、日期、到期日（``dd/mm/佛历年``）、金额、未收余额。
客户内部号 = 客户主档接口（``armas/component/bshlistboxdata.php``）``inpdupdata`` 里那一行的 id（按客户码一字不差找）。

收**赊销、增债与减债**三类（减债由 ``OFFSETS_RELEASED`` 控制，本批不列）：减债单在收款导入核销表里是扣减（``importpc.php`` billtype 4 从合计里减，金额照写正数），读进快照
记 ``5``（同 Express 减项的 RECTYP，快照里 5 一律是抵减单，`collection.contracts.is_offset`；退货 / 减项进收款 11-28）。预收不列
（导入里 billtype 1 是加进合计，与 Express 定金方向相反，语义待核，主会话 11-28 定不做）。
"""
from __future__ import annotations

import html as _html
import logging
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OFFSET_TAB = "arsc"
# 减债进收款先不放：待 MR.ERP 测试账套有原生减债单实写验证后放开（菜单 124 要有 MR.ERP 管理员关联账簿并开一张减债单；2026-11-28 主会话定）。
# 解析与核销表扣减的代码和离线测试保留，放开只改这一个常量；不做租户开关、不进站点配置。
OFFSETS_RELEASED = False
_ALL_TABS = {"artran": "2", "arsd": "3", OFFSET_TAB: "5"}  # 页签 → 记进快照的类型（赊销、增债同 importpc 的 billtype；减债记 5 = 抵减单）
TABS = {name: kind for name, kind in _ALL_TABS.items() if OFFSETS_RELEASED or name != OFFSET_TAB}
SKIPPED_TABS = ("arad",) if OFFSETS_RELEASED else ("arad", OFFSET_TAB)
ROWS_MAX = 2000
READ_FAILED = "ERR_MRERP_RECEIVABLES_UNREADABLE"
LOCK_WAIT_SECONDS = 60

_TAB = re.compile(r'<div\s+id=["\']tab-(arad|artran|arsd|arsc)["\'][^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL)
_ROW = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_SPAN = re.compile(r"<span\b[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _text(fragment: str) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", fragment or "")).split())


def _iso(value: str) -> str:
    """``dd/mm/佛历年`` → ``YYYY-MM-DD``；认不出回空串。"""
    found = _DAY.match(value or "")
    if not found:
        return ""
    day, month, year = (int(part) for part in found.groups())
    year = year - 543 if year > 2400 else year
    return f"{year:04d}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else ""


def _amount(value: str) -> str:
    text = str(value or "").replace(",", "").strip()
    return text if re.fullmatch(r"-?\d+(\.\d+)?", text) else ""


def parse_bills(page: str) -> Optional[List[Dict[str, str]]]:
    """选单页 → ``[{docnum, rectyp, docdat, net, received, remaining}]``（赊销、增债、减债，余额 > 0）；页上没有这几个页签（掉线、被踢回
    登录页、没有权限）回 None。"""
    tabs = _TAB.findall(page or "")
    if not any(name == "artran" for name, _body in tabs):
        return None
    out: List[Dict[str, str]] = []
    for name, body in tabs:
        if name not in TABS:
            continue
        for row in _ROW.findall(body):
            if "checkbox" not in row:
                continue  # 表头那一行、No Data
            cells = [_text(cell) for cell in _SPAN.findall(row)]
            if len(cells) < 6 or not cells[1]:
                continue
            net, remaining = _amount(cells[4]), _amount(cells[5])
            if not net or not remaining or Decimal(remaining) <= 0:
                continue
            received = format(max(Decimal("0.00"), Decimal(net) - Decimal(remaining)).quantize(Decimal("0.01")), "f")
            out.append({"docnum": cells[1], "rectyp": TABS[name], "docdat": _iso(cells[2]), "net": net,
                        "received": received, "remaining": remaining})
    return out[:ROWS_MAX]


def customer_id(payload: Any, code: Any) -> str:
    """客户主档接口的回应 → 这位客户（按客户码一字不差）的内部号；没有回空串。"""
    want = str(code or "").strip()
    for row in (payload or {}).get("inpdupdata") or [] if isinstance(payload, dict) else []:
        if isinstance(row, list) and len(row) >= 2 and str(row[1] or "").strip() == want and str(row[0]).isdigit():
            return str(row[0])
    return ""


def read(lease: Dict[str, str], account_set_key: str, customer: str, *, menu: str = "") -> Dict[str, Any]:
    """登录（共用入口 `mrerp.account.use`：同账号锁、被踢标记、接上缓存会话；后台读不算人点的）→ 选账套 → 这位客户的未收单。

    → ``{"items": [...] | None, "menus": {"impfarev": 选项}, "error": 码 | None}``。``menu``：收款菜单号（选过的或唯一已关联的；
    选单页按会话里的菜单出页头）；没给时照读到的导入页定，定不下来也照读（选单页不分菜单）。客户在 MR.ERP 里没有 → 空列表。
    永不抛。"""
    from pearnly_core.session_lock import SessionLockUnavailable
    from pearnly_core.mrerp import account as mrerp_account
    from pearnly_core.mrerp import menus as mrerp_menus
    from pearnly_core.mrerp.master_reader import MasterReader
    from pearnly_core.mrerp.session import SessionError

    comidyear, _, seldb = str(account_set_key or "").partition(":")
    if not lease.get("username") or not lease.get("password"):
        return {"items": None, "menus": {}, "error": "ERR_NO_CREDS"}
    observed: Dict[str, Any] = {}
    page_path = f"{mrerp_menus.RECEIPT_PAGE}/formupload.php?idmenu={mrerp_menus.ALL_PAGES[mrerp_menus.RECEIPT_PAGE]}"
    try:
        with mrerp_account.use(lease["system_url"], lease["username"], lease["password"], manual=False,
                               lock_wait=LOCK_WAIT_SECONDS) as session:
            session.select(comidyear, seldb, probe=page_path)
            home = {"Referer": f"{session.base}/login/mainmenu.php"}
            options = mrerp_menus.parse_options(session.request("GET", page_path, headers=home).text or "")
            if options is not None:
                observed[mrerp_menus.RECEIPT_PAGE] = options
            ident = customer_id(MasterReader(session.request, session.base).listbox("armas"), customer)
            if not ident:
                return {"items": [], "menus": observed, "error": None}
            chosen, _reason = mrerp_menus.resolve(options, menu) if options is not None else (None, None)
            idmenu = (chosen or {}).get("idmenu") or str(menu or "").strip()
            if idmenu:
                session.request("GET", f"farev/allview.php?idmenu={idmenu}", headers=home)
            page = session.request("GET", f"farev/selbill.php?id={ident}",
                                   headers={"Referer": f"{session.base}/farev/selarmas.php"})
            items = parse_bills(page.text or "")
            return {"items": items, "menus": observed, "error": None if items is not None else READ_FAILED}
    except mrerp_account.Marked:
        return {"items": None, "menus": observed, "error": mrerp_account.IN_USE}
    except SessionLockUnavailable:
        return {"items": None, "menus": observed, "error": "ERR_MRERP_ACCOUNT_IN_USE"}
    except SessionError as error:
        logger.info("MR.ERP receivables read failed: %s", error.code)
        return {"items": None, "menus": observed, "error": error.code}
    except Exception:  # noqa: BLE001 — 契约：读不到只落错误码
        logger.exception("MR.ERP receivables read raised")
        return {"items": None, "menus": observed, "error": "ERR_UNEXPECTED"}


__all__ = ["OFFSETS_RELEASED", "OFFSET_TAB", "READ_FAILED", "SKIPPED_TABS", "TABS", "customer_id", "parse_bills", "read"]
