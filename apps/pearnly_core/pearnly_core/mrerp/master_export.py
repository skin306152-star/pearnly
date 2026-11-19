"""整读一份 MR.ERP 账套的主档（只读、纯网络，不碰数据库；多产品共用）：读成与 Express 主档同一形状，交 ERP 同一个落库。

读法（MR.ERP 源码 `/Users/skin/mrerp-source/src` 核对，11-14）：
- 客户 / 供应商 / 物料：MR.ERP 自带的「导出」页（`exparmas` / `expapmas` / `expstkmas` 的 `component/expdata.php`），POST
  编码范围（首末编码取同模块列表接口 `component/bshlistboxdata.php` 的第一条与最后一条，那里按编码 ``ORDER BY``，与导出的
  ``BETWEEN`` 同一库排序），回 xlsx。这几页只查会话里的公司与年度，不改会话里的当前菜单，可以和推送共用一个会话。
- 单位名：物料主档列表接口（`stkmas`）的单位表。
- 仓库与按仓结存：报表「สินค้าคงเหลือ:แยกคลัง」（`rpstkmivtrbalstore`，菜单 55，Excel 版报表 241，经
  `component/php/showreportfile.php` 出 xlsx）。报表按会话里的当前菜单找文件，所以先打开一次报表页（带 ``idmenu``）。
  报表只列有过库存流水的物料；没列到的物料结存当 0（同 Express 没有 STLOC 行）。

输出（同 `pearnly_erp_portal.express.contracts` 读的 Express 主档）：
``{customers, suppliers, products, units, warehouses, stock, settings}``（``settings`` 只有 ``mainloc``），各行 ``code`` 必有；不交 ``mark``（MR.ERP 没有文件
标记可看，ERP 一侧按定时整读比对）。物料的 ``balance`` 不交（只有各仓结存一个来源，不做 Express 那种全仓 / 各仓互核）。
"""
from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional
from xml.etree import ElementTree

from pearnly_core.mrerp import masters as rules
from pearnly_core.mrerp.session import SessionError

logger = logging.getLogger(__name__)

LOCK_WAIT_SECONDS = 120
TIMEOUT_SECONDS = 120  # 导出 / 报表一次出整本，比普通业务请求慢
# 种类 → (模块, 列表接口里的编码键, 导出页范围参数前缀)
EXPORTS = {
    "customers": ("exparmas", "txtarcodebegin", "txtarcode"),
    "suppliers": ("expapmas", "txtapcodebegin", "txtapcode"),
    "products": ("expstkmas", "txtstkcodebegin", "txtstkcode"),
}
STOCK_MODULE = "rpstkmivtrbalstore"
STOCK_MENU = 55
STOCK_REPORT = 241
UNIT_LIST = "stkmas/component/bshlistboxdata.php"
UNIT_KEYS = ("txtunit_usell", "txtunit_ubuy", "txtunit_ustd", "txtunit_ucc")
XLSX_MAX_BYTES = 64 * 1024 * 1024
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_STORE_ROW = re.compile(r"^\s*คลัง\s+(\S+)\s*(.*)$")
_SERVICE = ("บริการ",)  # 物料类型名（导出已去掉「สินค้า :」前缀）以它开头的是服务，不进库存


class ExportError(Exception):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(f"{code}: {detail}"[:300])


# ── xlsx（只读第一张表；PHPExcel Excel2007 输出）────────────────────────────


def _col(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch.upper()) - 64)
    return index - 1


def xlsx_rows(data: bytes) -> List[List[str]]:
    """xlsx 第一张表 → 行（每格文字；数字照原样的文字，如 ``1250.5``）。不是 xlsx（掉线时回的弹窗脚本等）抛 ``ExportError``。"""
    if not data or len(data) > XLSX_MAX_BYTES or not data.startswith(b"PK"):
        raise ExportError(rules.UNREADABLE, "not an xlsx")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            names = set(book.namelist())
            shared = []
            if "xl/sharedStrings.xml" in names:
                for item in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                    shared.append("".join(node.text or "" for node in item.iter(f"{{{_NS['m']}}}t")))
            sheet = "xl/worksheets/sheet1.xml"
            if sheet not in names:
                sheet = sorted(name for name in names if name.startswith("xl/worksheets/sheet"))[0]
            root = ElementTree.fromstring(book.read(sheet))
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, IndexError) as exc:
        raise ExportError(rules.UNREADABLE, f"xlsx: {type(exc).__name__}") from None
    out = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells = {}
        for cell in row.findall("m:c", _NS):
            kind, value = cell.get("t"), cell.find("m:v", _NS)
            if kind == "inlineStr":
                text = "".join(node.text or "" for node in cell.iter(f"{{{_NS['m']}}}t"))
            elif value is None:
                text = ""
            elif kind == "s":
                text = shared[int(value.text)] if (value.text or "").isdigit() and int(value.text) < len(shared) else ""
            else:
                text = value.text or ""
            cells[_col(cell.get("r") or "A")] = text
        if cells:
            out.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return out


def _cell(row: list, index: int) -> str:
    return str(row[index]).strip() if index < len(row) and row[index] is not None else ""


def _number(text: str) -> Decimal:
    try:
        return Decimal(str(text or "0").replace(",", "").strip() or "0")
    except InvalidOperation:
        return Decimal(0)


# ── 各表 → Express 主档同形状 ────────────────────────────────────────────────


def _named(prefix: str, name: str) -> str:
    prefix = prefix.strip()
    return f"{prefix} {name}".strip() if prefix and prefix not in ("-", ".") and not name.startswith(prefix) else name


def _address(row: list, first: int) -> str:
    lines = [_cell(row, i) for i in range(first, first + 4)]
    zipcode = _cell(row, first + 4)
    text = " ".join(part for part in lines if part and part != "-")
    return f"{text} {zipcode}".strip() if zipcode and zipcode != "-" else text


def parties(rows: List[list], *, customer: bool) -> List[dict]:
    """导出表（第一行是表头）→ ``[{code, name, tax_id, branch, address, phone}]``。
    客户 25 列：编码 0、称谓 2、名称 3、地址 4–7、邮编 8、电话 9、税号 23、分店 24；供应商 22 列：税号 20、分店 21。"""
    tax_at, branch_at = (23, 24) if customer else (20, 21)
    out = []
    for row in rows[1:]:
        code = _cell(row, 0)
        if not code:
            continue
        out.append({"code": code, "name": _named(_cell(row, 2), _cell(row, 3)) or code,
                    "tax_id": _cell(row, tax_at), "branch": _cell(row, branch_at),
                    "address": _address(row, 4), "phone": _cell(row, 9).strip("-").strip()})
    return out


def products(rows: List[list]) -> List[dict]:
    """物料导出表 → ``[{code, name, kind, unit, sale_unit, sale_factor, purchase_unit, purchase_factor, prices, group}]``。
    列：类型名 0、分类 1、编码 2、名称 3、售价 8、基本单位 11、进货单位 12 / 换算 13、销售单位 14 / 换算 15。"""
    out = []
    for row in rows[1:]:
        code = _cell(row, 2)
        if not code:
            continue
        price = _number(_cell(row, 8))
        out.append({"code": code, "name": _cell(row, 3) or code,
                    "kind": "service" if _cell(row, 0).startswith(_SERVICE) else "stock",
                    "unit": _cell(row, 11), "purchase_unit": _cell(row, 12), "purchase_factor": _cell(row, 13) or "1",
                    "sale_unit": _cell(row, 14), "sale_factor": _cell(row, 15) or "1",
                    "prices": [str(price)] if price > 0 else [], "group": _cell(row, 1), "barcode": ""})
    return out


def stock(rows: List[list]) -> tuple:
    """按仓结存报表 → (``[{code, name}]`` 报表里出现的仓, ``[{code, loccod, qty}]``)。
    报表格式（报表 241）：物料行 A 编码 / B 名称 / C 单位；其下每仓一行，A 是「     คลัง 仓码 仓名」、D 数量；
    「รวม…」是合计行。"""
    warehouses, out, current = {}, [], ""
    for row in rows:
        first = _cell(row, 0)
        if not first or first.startswith("รวม"):
            continue
        match = _STORE_ROW.match(str(row[0]))
        if match and current:
            loccod = match.group(1)
            warehouses.setdefault(loccod, match.group(2).strip() or loccod)
            out.append({"code": current, "loccod": loccod, "qty": str(_number(_cell(row, 3)))})
        elif len(row) >= 3 and _cell(row, 1) and not match:
            current = first
    return [{"code": code, "name": name} for code, name in warehouses.items()], out


def units(listbox: Optional[dict]) -> List[dict]:
    seen, out = set(), []
    for key in UNIT_KEYS:
        for item in (listbox or {}).get(key) or []:
            if isinstance(item, list) and len(item) >= 3 and str(item[1]).strip() not in seen:
                seen.add(str(item[1]).strip())
                out.append({"code": str(item[1]).strip(), "name": str(item[2]).strip() or str(item[1]).strip()})
    return out


# ── 网络 ─────────────────────────────────────────────────────────────────────


NEGATIVE_PAGE = "stktraniss/allform.php"


class _Reader:
    def __init__(self, session):
        self.session = session
        self.requests = 0
        self.bytes = 0

    def _call(self, method, path, **kwargs):
        kwargs.setdefault("headers", {"Referer": f"{self.session.base}/login/mainmenu.php"})
        kwargs.setdefault("timeout", TIMEOUT_SECONDS)
        self.requests += 1
        response = self.session.request(method, path, **kwargs)
        self.bytes += len(response.content or b"")
        return response

    def listbox(self, path):
        response = self._call("POST", path, data={},
                              headers={"X-Requested-With": "XMLHttpRequest", "Origin": self.session.base,
                                       "Referer": f"{self.session.base}/login/mainmenu.php"})
        found = rules.parse_listbox(response.text or "")
        if found is None:
            raise ExportError(rules.UNREADABLE, path)
        return found

    def export(self, module, code_key, prefix):
        codes = [str(item[1]).strip() for item in self.listbox(f"{module}/component/bshlistboxdata.php").get(code_key) or []
                 if isinstance(item, list) and len(item) >= 2 and str(item[1]).strip()]
        if not codes:
            return [], 0
        response = self._call("POST", f"{module}/component/expdata.php",
                              data={f"{prefix}begin": codes[0], f"{prefix}end": codes[-1]})
        return xlsx_rows(response.content or b""), len(codes)

    def stock_negative(self) -> str:
        """这份账套「ตั้งค่าระบบสินค้า」的 อนุญาตให้สินค้าติดลบ：``1`` 允许 / ``2`` 不允许 / 空串没设置或读不到。出库新单网页每次现查
        ``tbl_stksetup.negative`` 写进 ``var ssunegative``（`stktraniss/allform.php`）。读不到不当错误（只影响出库提交前拦不拦）。"""
        try:
            page = self._call("GET", NEGATIVE_PAGE)
        except Exception:  # noqa: BLE001 — 只是一项设置
            return ""
        return rules.parse_stock_negative(page.text or "")

    def stock(self, today: date):
        lists = self.listbox(f"{STOCK_MODULE}/component/bshlistboxdata.php")
        stores = [item for item in lists.get("txtstorebegin") or [] if isinstance(item, list) and len(item) >= 3]
        items = [item for item in lists.get("txtstkmasbegin") or [] if isinstance(item, list) and len(item) >= 3]
        warehouses = [{"code": str(item[1]).strip(), "name": str(item[2]).strip()} for item in stores]
        if not stores or not items:
            return warehouses, []
        page = self._call("GET", f"{STOCK_MODULE}/{STOCK_MODULE}form.php", params={"idmenu": STOCK_MENU})
        types = re.findall(r'<select name="selstktypebegin".*?</select>', page.text or "", re.S)
        ids = [int(v) for v in re.findall(r'<option value="(\d+)"', types[0])] if types else []
        if not ids:
            raise ExportError(rules.UNREADABLE, "stock report types")
        response = self._call("POST", "component/php/showreportfile.php", data={
            "idreport": STOCK_REPORT,
            "txtstorebegin": stores[0][1], "txtstoreend": stores[-1][1],
            "storebeginval": stores[0][0], "storeendval": stores[-1][0],
            "txtstkmasbegin": items[0][1], "txtstkmasend": items[-1][1],
            "stkmasbeginval": items[0][0], "stkmasendval": items[-1][0],
            "txtstkmasdate": f"{today.day:02d}/{today.month:02d}/{today.year + 543}",
            "selstktypebegin": min(ids), "selstktypeend": max(ids)},
            headers={"Referer": f"{self.session.base}/{STOCK_MODULE}/{STOCK_MODULE}form.php?idmenu={STOCK_MENU}"})
        seen, rows = stock(xlsx_rows(response.content or b""))
        known = {row["code"] for row in warehouses}
        warehouses += [row for row in seen if row["code"] not in known]
        return warehouses, rows


def read_session(session, today: Optional[date] = None) -> tuple:
    """在已选好账套的会话里整读 → (主档, 统计 ``{requests, bytes, seconds, listed}``)。读不到抛 ``ExportError`` / ``SessionError``。"""
    started = time.monotonic()
    reader = _Reader(session)
    facts, listed = {}, {}
    for kind, (module, code_key, prefix) in EXPORTS.items():
        rows, listed[kind] = reader.export(module, code_key, prefix)
        facts[kind] = products(rows) if kind == "products" else parties(rows, customer=kind == "customers")
    facts["units"] = units(reader.listbox(UNIT_LIST))
    facts["warehouses"], facts["stock"] = reader.stock(today or date.today())
    # 主仓：MR.ERP 网页新建单据默认取编码排第一的仓（`artran/component/javascript/allform.js`，见 `masters.py` 文件头）；
    # ERP 建公司时自带的那一个没编码的仓第一次落库认作它（同 Express 的 mainloc）
    facts["settings"] = {"mainloc": facts["warehouses"][0]["code"]} if facts["warehouses"] else {}
    negative = reader.stock_negative()  # 照 MR.ERP 出库网页拦不拦负库存（ERP 出库单提交前，`erp mrerp.stock.check_negative`）
    if negative:
        facts["settings"]["stock_negative"] = negative
    stats = {"requests": reader.requests, "bytes": reader.bytes, "seconds": round(time.monotonic() - started, 2),
             "listed": listed, "counts": {key: len(value) for key, value in facts.items() if isinstance(value, list)}}
    return facts, stats


def read(lease, account_set_key, *, manual) -> tuple:
    """→ (主档 | None, 错误码 | None, 统计)。会话从共用入口拿（`account.use`：同账号锁、被踢标记、接上缓存会话）。
    ``manual``：人在明确的入口点出来的这一次（从连接添加、公司页选账套）先清被踢标记、照常登录；定时、写单后的不清，账号被踢过
    时不登录、回 ``ERR_ACCOUNT_IN_USE``。由调用入口显式给。"""
    # 账号锁与会话入口只在真读时载入（解析部分在离线测试里不需要框架）
    from pearnly_core.session_lock import SessionLockUnavailable
    from pearnly_core.mrerp import account as mrerp_account

    comidyear, _, seldb = str(account_set_key or "").partition(":")
    if not lease.get("username") or not lease.get("password"):
        return None, "ERR_NO_CREDS", {}
    try:
        with mrerp_account.use(lease["system_url"], lease["username"], lease["password"], manual=manual,
                               lock_wait=LOCK_WAIT_SECONDS) as session:
            session.select(comidyear, seldb)
            facts, stats = read_session(session)
            return facts, None, stats
    except SessionLockUnavailable:
        return None, "ERR_TECHNICAL", {}
    except (SessionError, ExportError) as exc:
        logger.info("MR.ERP master export failed: %s", exc)
        return None, exc.code, {}
    except Exception:  # noqa: BLE001 — 契约：读取失败只落错误码
        logger.exception("MR.ERP master export raised")
        return None, "ERR_UNEXPECTED", {}


__all__ = ["ExportError", "parties", "products", "read", "read_session", "stock", "units", "xlsx_rows"]
