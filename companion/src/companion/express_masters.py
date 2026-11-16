# -*- coding: utf-8 -*-
"""按需请求 ``express_masters``（2026-10 ERP 前台）：只读读出一本 Express 账套的整套主档与各仓结存，交回服务端给 ERP 做
主档与库存镜像（Express 是唯一账本，ERP 只做前台）。

- 账套判据、复制到本机再读、不碰「等账套」、不拿写者锁、Express 开着照读：与 ``express_read`` 同一套（``resolve`` /
  ``snapshot``），只复制这里要读的几张表；复制途中有表变了整份重读，读满次数仍在变就照交，并在 meta 报 ``snapshot_changed``。
- 参数只收 ``{account_set, account_root}``（与 ``account_history`` 去掉 ``since`` 相同），多一个键回 ``params_invalid``。
- 读什么（软删的行一律跳过，同 ``catalog_probe``）：
  - settings：ISINFO 的主仓 MAINLOC、允许负库存 NEGALLOW、成本法 COSMTD、用不用部门 USEDEP；
  - customers / suppliers：ARMAS / APMAS 的代码、名称、税号、分店号（ORGNUM 归一 5 位）、地址（ADDR01–03 与邮编用空格接起来）、
    电话 TELNUM、付款期天数 PAYTRM；供应商另带赊购单服务品进项税口径串 ``srv``（读账套原表 APTRN，``service_vat``，10-29），
    客户另带赊销单服务品销项税口径串 ``srv``（ARTRN，11-12 晚）；
  - products：STMAS 的代码、名称、类别（STKTYP = 0 记 stock，其余 non_stock，同 ``catalog_probe``）、商品组 STKGRP、基本单位
    QUCOD、销售单位 SQUCOD 与换算 SFACTOR、采购单位 PQUCOD 与换算 PFACTOR、售价 SELLPR1–5、条码 BARCOD、税码 VATCOD、
    该品允许负库存 NEGALLOW、全仓总结存 TOTBAL（``balance``，出入库第一期起；11-07 前误读 MREMBAL）；
  - units / warehouses / departments：代码表 ISTAB 里 TABTYP = 20 / 21 / 50 的行（单位、仓库、部门），名称取 TYPDES，空则 SHORTNAM；
  - stock：各仓结存 STLOC 的 STKCOD / LOCCOD / LOCBAL / LOCVAL，只给数量或金额非 0 的行。
- 数字一律字符串化的 Decimal（DBF 浮点 → repr → Decimal 再按位数四舍五入，不带 float 误差）：金额（LOCVAL）两位小数；数量、
  换算、售价最多 6 位小数并去掉末尾的 0；付款期是整数。名称 cp874 解码、坏字节替换（同 ``catalog_probe``）。字段缺的给空串 / "0"，
  不省键。表缺的给空列表；ISINFO 与 STMAS 都没有才回 ``account_tables_missing``（那不是一份 Express 账套）。
- facts（1.11.0 起，``RESULT_VERSION`` 2，ADR-025：ERP 直连后由 ERP 自己准备写账载荷）：与心跳按账套上报同形状的科目表
  ``accounts``（GLACC 可记账、启用的科目，``account_probe.probe_chart_of_accounts``）、``mapping``（ISINFO 的 6 个默认科目，
  ``probe_default_accounts``）、``catalog.isacc_candidates``（能选的存货科目组，最多 20 个，同 ``catalog_probe.catalog_payload``）。
  各块读坏了给空，不挡主档。
- 读出来的单行坏了（坏字节以外的异常）跳过并按表计数，放在 meta ``bad_rows``，服务端自己决定要不要信这一份。
- 结果是 gzip 后的 JSON（``RESULT_VERSION``），超过 ``MAX_BYTES["express_masters"]`` 回 ``too_large``，不截断。
- 1.12.0：settings 另带账套设置里的地址（ISINFO ADDR01–03 与邮编）、电话 TELNUM（ERP 按账套自动建的公司用）；结果顶层另带
  ``mark``（:func:`mark`：主档几张表的大小与修改时间），ERP 记下它，之后用很轻的按需请求 ``express_marks``（:func:`read_marks`，
  只看文件属性、不读内容）比一比，变了才再请整套读（10-23：Express 主档改了 ERP 自动跟上，又不让办公室电脑反复整套读）。
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from . import express_read
from .logger import get_logger
from .request_contract import MAX_BYTES, RequestFailed, Result

log = get_logger(__name__)

RESULT_VERSION = 2
TABLES = ("ISINFO", "ARMAS", "APMAS", "STMAS", "ISTAB", "STLOC", "GLACC", "ISACC", "ISRUN", "OESLM", "BKMAS")
MAX_STOCK_GROUPS = 20
# 至少要有其中一张才是一份 Express 账套
REQUIRED_ANY = ("ISINFO", "STMAS")
PARAM_KEYS = ("account_set", "account_root")
# ISTAB 代码表的段：单位、仓库、部门
UNIT_TABTYP = "20"
WAREHOUSE_TABTYP = "21"
DEPARTMENT_TABTYP = "50"
PRICE_SLOTS = ("SELLPR1", "SELLPR2", "SELLPR3", "SELLPR4", "SELLPR5")
SETTINGS = {"mainloc": "MAINLOC", "negallow": "NEGALLOW", "cosmtd": "COSMTD", "usedep": "USEDEP"}
# 主档变没变看这几张表（大小 + 修改时间）：客户、供应商、商品、代码表（单位 / 仓库 / 部门）、各仓结存、账套设置、销售员、银行主档
# （ISRUN 不进：每写一张单计数器就变，进了会让每张单都触发整套重读；收款类型改了随下一次别的主档变化一起读到）
MARK_TABLES = ("ARMAS", "APMAS", "STMAS", "ISTAB", "STLOC", "ISINFO", "OESLM", "BKMAS")
# 未收清发票（按需请求 ``express_ar_open``）变没变看单据表 ARTRN：``express_marks`` 另报 ``ar_mark``，ERP 变了才重读快照
AR_MARK_TABLES = ("ARTRN",)
CENT = Decimal("0.01")
FINE = Decimal("0.000001")


def _params(params: dict) -> tuple:
    if not isinstance(params, dict) or set(params) - set(PARAM_KEYS):
        raise RequestFailed("params_invalid")
    values = [params.get(key) for key in PARAM_KEYS]
    if any(value is not None and not isinstance(value, str) for value in values):
        raise RequestFailed("params_invalid")
    return tuple(values)


def _decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    try:
        number = Decimal(repr(value) if isinstance(value, float) else str(value).strip() or "0")
    except InvalidOperation:
        raise ValueError("number") from None
    if not number.is_finite():
        raise ValueError("number")
    return number


def money(value) -> str:
    """金额：两位小数的字符串。"""
    return f"{_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def number(value) -> str:
    """数量 / 换算 / 售价：最多 6 位小数、去掉末尾的 0 的字符串（不用科学记数）。"""
    text = f"{_decimal(value).quantize(FINE, rounding=ROUND_HALF_UP):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def whole(value) -> str:
    return str(int(_decimal(value).to_integral_value(rounding=ROUND_HALF_UP)))


def _code(value) -> str:
    """编码照 Express 原样（只去两头的空白）：泰文编码里常带不换行空格（\xa0），写账时 CC 按原样比对编码，换成普通空格
    就认不出了（10-23 验收：冰块商品写账失败）。名称、地址仍用 ``express_read._text``（不换行空格当空格，便于显示与搜索）。"""
    return "" if value is None else str(value).strip()


def _address(rec, columns) -> str:
    get, text = express_read._get, express_read._text
    parts = [text(get(rec, columns, name)) for name in ("ADDR01", "ADDR02", "ADDR03", "ZIPCOD")]
    return " ".join(part for part in parts if part)


def _party(code_field: str, name_field: str):
    get, text = express_read._get, express_read._text

    def pick(rec, columns, _gone):
        code, name = _code(get(rec, columns, code_field)), text(get(rec, columns, name_field))
        if not code and not name:
            return None
        # prename：Express 的 PRENAM（称谓 / 法人前缀，name 是不带它的核心名）；服务端拼成显示名（1.12.22 起，旧服务端不看）
        return {"code": code, "name": name, "prename": text(get(rec, columns, "PRENAM")), "tax_id": text(get(rec, columns, "TAXID")),
                "branch": express_read._branch(rec, columns), "address": _address(rec, columns),
                "phone": text(get(rec, columns, "TELNUM")), "payterm": whole(get(rec, columns, "PAYTRM"))}

    return pick


def _product(rec, columns, _gone):
    get, text = express_read._get, express_read._text
    code, name = _code(get(rec, columns, "STKCOD")), text(get(rec, columns, "STKDES"))
    if not code and not name:
        return None
    return {"code": code, "name": name,
            "kind": "stock" if text(get(rec, columns, "STKTYP")) == "0" else "non_stock",
            "group": _code(get(rec, columns, "STKGRP")), "unit": _code(get(rec, columns, "QUCOD")),
            "sale_unit": _code(get(rec, columns, "SQUCOD")), "sale_factor": number(get(rec, columns, "SFACTOR")),
            "purchase_unit": _code(get(rec, columns, "PQUCOD")),
            "purchase_factor": number(get(rec, columns, "PFACTOR")),
            "prices": [number(get(rec, columns, slot)) for slot in PRICE_SLOTS],
            "barcode": text(get(rec, columns, "BARCOD")), "vatcod": text(get(rec, columns, "VATCOD")),
            "negallow": text(get(rec, columns, "NEGALLOW")),
            # 全仓结存（出入库第一期，10-25）：ERP 对齐前核「STMAS 结存 = 各仓 STLOC 合计」，对不上的不抹 ERP、进差异清单。
            # 总结存是 TOTBAL（MREMBAL 是批次链，S 盘 18% 的品两者不等，11-07 改）；老表没有 TOTBAL 时按 MREMBAL
            "balance": number(get(rec, columns, "TOTBAL" if "TOTBAL" in columns else "MREMBAL"))}


def _code_table(rec, columns, _gone):
    get, text = express_read._get, express_read._text
    code = _code(get(rec, columns, "TYPCOD"))
    if not code:
        return None
    return (_code(get(rec, columns, "TABTYP")),
            {"code": code, "name": text(get(rec, columns, "TYPDES")) or text(get(rec, columns, "SHORTNAM"))})


def _stock(rec, columns, _gone):
    get, text = express_read._get, express_read._text
    qty, value = _decimal(get(rec, columns, "LOCBAL")), _decimal(get(rec, columns, "LOCVAL"))
    if number(qty) == "0" and money(value) == "0.00":
        return None
    return {"code": _code(get(rec, columns, "STKCOD")), "loccod": _code(get(rec, columns, "LOCCOD")),
            "qty": number(qty), "value": money(value)}


def _facts(folder: Path) -> dict:
    """准备写账载荷要用的账套资料（与心跳按账套上报同形状）；每块读坏了给空，不挡主档。"""
    from dataclasses import asdict

    from . import account_probe, catalog_probe

    facts = {"accounts": [], "mapping": {}, "catalog": {"isacc_candidates": []}}
    try:
        facts["accounts"] = [asdict(row) for row in account_probe.probe_chart_of_accounts(folder)]
    except Exception:
        log.info("科目表读不出（没有 GLACC？），这份不带科目表")
    try:
        facts["mapping"] = dict(account_probe.probe_default_accounts(folder) or {})
    except Exception:
        log.info("默认科目读不出，这份不带默认科目")
    try:
        # 账套开没开逐行 VAT（ISINFO.VAT_INITEM = Y）：开了的免税商品按行记，没开的 ERP 按公司问一次怎么写（实录第 10 条）
        from .account_rules import _first

        info = _first(folder, "ISINFO")
        if info is not None:
            facts["vat_initem"] = str(info.get("VAT_INITEM") or "").strip().upper()
    except Exception:
        log.info("账套设置读不出，这份不带逐行 VAT 设置")
    try:
        # 库存单（载荷 6）三个单别在 ISRUN 里的默认科目：ERP 据此判要不要先让人选一次科目（这一行没有或几行不同 → 空）
        from .dbf_stock import KINDS, book_row
        from . import dbf_transfer

        # 没有这个单别的单据簿就不报这个键（ERP 据此直接停下说明，不先让人白选一次科目；验收 10-25）
        books = {kind: book_row(folder, kind) for kind in KINDS}
        books[dbf_transfer.TRANSFER] = dbf_transfer.book_row(folder)  # 调拨 RL 没有科目（多仓闭环 P2）
        facts["stock_books"] = {kind: row.get("account", "") for kind, row in books.items() if row is not None}
        # 核过哪几个单别：ERP 只对报过的单别据「没有这个键」判没有单据簿（旧版不报 RL，ERP 照旧交给电脑核）
        facts["stock_books_checked"] = sorted(books)
    except Exception:
        log.info("单据簿读不出（没有 ISRUN？），这份不带库存单默认科目")
    try:
        ranked = catalog_probe.probe_isacc_candidates(folder)
        facts["catalog"]["isacc_candidates"] = [row for row in ranked if row["fit"]][:MAX_STOCK_GROUPS]
    except Exception:
        log.info("存货科目组读不出，这份不带存货科目组")
    return facts


def _service_vat_customers(customers: list, folder: Path) -> list:
    """每家客户带上这本账套赊销单的服务品销项税口径串 ``srv``（ARTRN.SRV_VATTYP 出现过的 1 / 2 并集，"0" = 没有历史；
    11-12 晚，同供应商的做法）；ARTRN 读不了就不带（服务端当旧版）。"""
    from . import service_vat

    known, read = service_vat.customer_report(folder)
    if not read:
        return customers
    return [{**row, "srv": known.get(row["code"]) or service_vat.NO_HISTORY} for row in customers]


def _service_vat(suppliers: list, folder: Path) -> list:
    """每家供应商带上这本账套赊购单的服务品进项税口径串 ``srv``（同心跳目录 ``catalog_probe.probe_suppliers``，10-29）；
    读账套原表（只读几栏、按行数增量，``service_vat.history``），不进副本：单据表大、录单时一直在变，复制它会让主档反复重读；
    APTRN 读不了就不带。"""
    from . import service_vat

    known, read = service_vat.supplier_report(folder)
    if not read:
        return suppliers
    return [{**row, "srv": known.get(row["code"]) or service_vat.NO_HISTORY} for row in suppliers]


def collect(folder: Path) -> tuple:
    """从复制出来的账套表读出整套主档与各仓结存 → (结果, 诊断)。读到一半坏了抛 ``express_read._Unreadable``。"""
    if not any((folder / f"{name}.DBF").is_file() for name in REQUIRED_ANY):
        raise RequestFailed("account_tables_missing")
    diag = {"rows": {}, "bad_rows": {}}
    get, text = express_read._get, express_read._text
    infos = express_read._rows(folder, "ISINFO", diag,
                               lambda r, c, _g: {**{key: _code(get(r, c, field)) for key, field in SETTINGS.items()},
                                                 "address": _address(r, c), "phone": text(get(r, c, "TELNUM"))})
    codes = express_read._rows(folder, "ISTAB", diag, _code_table)
    data = {
        "version": RESULT_VERSION,
        "settings": infos[0] if infos else {**{key: "" for key in SETTINGS}, "address": "", "phone": ""},
        "customers": express_read._rows(folder, "ARMAS", diag, _party("CUSCOD", "CUSNAM")),
        "suppliers": express_read._rows(folder, "APMAS", diag, _party("SUPCOD", "SUPNAM")),
        "products": express_read._rows(folder, "STMAS", diag, _product),
        "units": [row for kind, row in codes if kind == UNIT_TABTYP],
        "warehouses": [row for kind, row in codes if kind == WAREHOUSE_TABTYP],
        "departments": [row for kind, row in codes if kind == DEPARTMENT_TABTYP],
        "stock": express_read._rows(folder, "STLOC", diag, _stock),
        "facts": _facts(folder),
        **_receipt_masters(folder, diag, infos),
    }
    return data, diag


# 停用：状态 I（Express 主档的停用记法；NLTTEST ARMAS 4904 行 A、1309 行 I、3253 行空）或有停用日期。A / 空都是在用
INACTIVE_STATUS = "I"


def _salesman(rec, columns, _gone):
    get, text = express_read._get, express_read._text
    code = _code(get(rec, columns, "SLMCOD"))
    if not code:
        return None
    return {"code": code, "name": text(get(rec, columns, "SLMNAM")),
            "inactive": text(get(rec, columns, "STATUS")).upper() == INACTIVE_STATUS or bool(get(rec, columns, "INACTDAT"))}


def _receipt_masters(folder: Path, diag: dict, infos: list) -> dict:
    """收款单 RE 要的主档（载荷 20，评估 2026-11-11 契约「读：主档补充」）：收款类型（ISRUN ZR 逐行，科目按 SHORTNAM→BKMAS 否则
    ACCNUM01，``payment_methods``；另带进的户头 ``bank_account`` 与原生用量 ``uses``）、RE 单据簿（前缀、下一个号、默认部门）、销售员 OESLM、现金科目 ISINFO.ACCNUM00。各块读坏了给空，不挡主档。"""
    from . import payment_methods

    out = {"receipt_types": [], "receipt_run": None, "salesmen": [], "cash_account": ""}
    try:
        out["receipt_types"] = payment_methods.types(folder, payment_methods.RECEIPT_TYPES)
    except Exception:
        log.info("收款类型读不出（没有 ISRUN？），这份不带收款类型")
    try:
        get = express_read._get
        runs = express_read._rows(folder, "ISRUN", {"rows": {}, "bad_rows": {}},
                                  lambda r, c, _g: ({"prefix": _code(get(r, c, "PREFIX")), "next": _code(get(r, c, "DOCNUM")),
                                                     "department": _code(get(r, c, "DEPCOD"))}
                                                    if _code(get(r, c, "DOCTYP")) == "RE" else None))
        out["receipt_run"] = runs[0] if runs else None
    except Exception:
        log.info("收款单据簿读不出，这份不带 receipt_run")
    try:
        # 户头（11-13，按钮标签「银行简称 ·账号末 4 位」）：方式进的户头 bank_account，没有就不带；用量 uses 见 :func:`_method_uses`。
        # 旧服务端不认这两栏、照旧显示方式名
        accounts = payment_methods.bank_rows(folder)
        for row in out["receipt_types"]:
            bank = payment_methods.bank_account_of(row, accounts)
            if bank:
                row["bank_account"] = bank
    except Exception:
        log.info("收款方式的户头读不出，这份不带户头")
    out["salesmen"] = express_read._rows(folder, "OESLM", diag, _salesman)
    try:
        from .account_rules import _first

        info = _first(folder, "ISINFO") or {}
        out["cash_account"] = str(info.get("ACCNUM00") or "").strip()
    except Exception:
        log.info("现金科目读不出，这份不带现金科目")
    return out


def _method_uses(types: list, run, folder: Path) -> list:
    """每种收款方式带 ``uses``：原生收款单用过几次（同一户头被几种方式指到时，服务端只列用得多的那种）。只有出现这种情况才只读
    原账套的方式行表 ARRCPCQ 来数（主档副本不复制它，平时不多读一张表）；用不到或读不到都是 0。"""
    codes = [row["bank_account"]["code"] for row in types if row.get("bank_account")]
    counts = {}
    if len(codes) != len(set(codes)):
        from . import payment_methods

        try:
            counts = payment_methods.method_uses(folder, (run or {}).get("prefix", ""))
        except Exception:
            log.info("收款方式用量读不出，这份用量记 0")
    for row in types:
        row["uses"] = counts.get(row["prefix"], 0)
    return types


def mark(folder: Path, tables=MARK_TABLES) -> str:
    """这份账套主档的变化标记：:data:`MARK_TABLES`（``ar_mark`` 是 :data:`AR_MARK_TABLES`）几张表的大小与修改时间（只看文件属性，
    不读内容；表不在记缺）。"""
    stamps = []
    for name in tables:
        for candidate in (f"{name}.DBF", f"{name.lower()}.dbf"):
            try:
                stat = (folder / candidate).stat()
            except OSError:
                continue
            stamps.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
            break
        else:
            stamps.append(f"{name}:-")
    return hashlib.sha1("|".join(stamps).encode("utf-8")).hexdigest()[:20]


def read_marks(cfg, params: dict, **_ignored) -> Result:
    """按需请求 ``express_marks``：{account_set, account_root} → ``{"mark", "ar_mark"}``（gzip JSON）。账套判据同整套读（``resolve``）。
    ``ar_mark``：单据表 ARTRN 的变化标记（收款单 RE 的未收清发票快照，``express_ar_open``）；旧服务端不认这个键、照旧只看 ``mark``。"""
    account_set, account_root = _params(params)
    folder = express_read.resolve(cfg, account_set, account_root)
    content = gzip.compress(json.dumps({"mark": mark(folder), "ar_mark": mark(folder, AR_MARK_TABLES)}).encode("utf-8"))
    return Result(content, {"format": "json.gz"})


# ── 处理函数（request_worker.HANDLERS["express_masters"]）────────────────────


def read_masters(cfg, params: dict, *, sleep: Callable[[float], None] = time.sleep) -> Result:
    """{account_set, account_root} → 整套主档与各仓结存（gzip JSON）。"""
    started = time.monotonic()
    account_set, account_root = _params(params)
    folder = express_read.resolve(cfg, account_set, account_root)
    read, changed = None, False
    for attempt in range(express_read.READS):
        stamp = mark(folder)  # 复制前取标记：复制途中又改了，下一次看标记会不同、再读一遍，不漏
        try:
            with express_read.snapshot(folder, tables=TABLES, sleep=sleep) as (copy, moved):
                read, changed = collect(copy), moved
        except express_read._Unreadable as error:
            log.info("账套副本读不全，稍后重读: %s", error)
        else:
            if not changed:
                break
            log.info("复制账套途中有表变了，稍后重读")  # 读满次数仍在变就交最后一份（主档差一行不影响镜像，下次再读）
        if attempt < express_read.READS - 1:
            sleep(express_read.REREAD_WAIT)
    if read is None:
        raise RequestFailed("account_unreadable")
    data, diag = read
    data["suppliers"] = _service_vat(data["suppliers"], folder)
    data["customers"] = _service_vat_customers(data["customers"], folder)
    data["receipt_types"] = _method_uses(data.get("receipt_types") or [], data.get("receipt_run"), folder)
    data = {"version": data.pop("version"), "account_set": express_read._norm(folder)[:500], "mark": stamp,
            "read_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **data}
    content = gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if len(content) > MAX_BYTES["express_masters"]:
        raise RequestFailed("too_large")
    meta = {"format": "json.gz", "version": RESULT_VERSION,
            "counts": {key: len(data[key]) for key in ("customers", "suppliers", "products", "units", "warehouses",
                                                       "departments", "stock")},
            "read_seconds": round(time.monotonic() - started, 2)}
    if diag["bad_rows"]:
        meta["bad_rows"] = diag["bad_rows"]
    if changed:
        meta["snapshot_changed"] = True
    return Result(content, meta)
