"""每个「停下」的出口都要有下一步（多产品共用，纯契约：不依赖 Frappe、不读库、不做 IO；总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 第 9 节）。

用户 11-14：「要点的要做的可以引导式啊，cc 为什么拒绝，决定交给用户」。流程停下来的地方不能只回一个原因码；每个原因码在这里
归成三类之一（:data:`KINDS`）：

- :data:`ASK` 问：缺的是一个决定，定了系统就能办。写明选项从哪来（``source``）、选了以后谁执行（``by``）。
- :data:`WAIT` 等：不缺决定，等一个会自己变的状态。写明等的是什么（``until``）。
- :data:`ACT` 用户去做：系统确实替不了。写明那一句「要做哪一件事」的术语表键（``tell``）；做完由系统复核再接着办。

``live`` = 现在的代码已经这样处理；否则 ``phase`` 写排在哪一期改。这里只归类、不改行为。原因码散在各自的登记处（代录、直写回执、
ERP 停下原因、收款拒绝码、写入计划跳过原因、MR.ERP……），闸门 ``scripts/check-stop-exits.py`` 按源码把它们收齐：新加一个原因码而
这里没归类 = 红；存量还没归类的记在 ``scripts/stop-exits-baseline.json``，只许减少。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

ASK, WAIT, ACT = "ask", "wait", "act"
KINDS = (ASK, WAIT, ACT)
SERVER, DESKTOP = "server", "desktop"          # 选了以后谁执行：站点（记下 → 重判 → 接着办）/ CC 桌面版
PHASES = ("p0", "p1", "p2", "p3", "p4", "mrerp")
# 选项来源：写账前置判定的题类 / 回执带回的候选 / 屏幕上的原文 / 复核卡上就地改那一格 / 固定几项 / 本对话账套候选
SOURCE = re.compile(r"^(precheck:[a-z_]+|receipt:[a-z_]+|screen|review|fixed|ledger)$")
_KEY = re.compile(r"^stop\.act\.[a-z0-9_.]+$")
_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def ask(source: str, by: str = SERVER, phase: str = "", live: bool = False) -> Dict[str, Any]:
    return {"next": ASK, "source": source, "by": by, **({"live": True} if live else {"phase": phase})}


def wait(until: str) -> Dict[str, Any]:
    return {"next": WAIT, "until": until, "live": True}


def act(name: str, phase: str) -> Dict[str, Any]:
    return {"next": ACT, "tell": f"stop.act.{name}", "phase": phase}


EXITS: Dict[str, Dict[str, Any]] = {
    # ── 代录（express_operator.contracts.REASONS）──────────────────────────────────────────────────────────────
    "party_missing": ask("precheck:new_party", DESKTOP, "p2"),          # 换成已有的 / 新建
    "item_missing": ask("precheck:new_item", DESKTOP, "p2"),
    "tax_setting_mismatch": ask("review", SERVER, "p2"),
    "period_locked": act("period_locked", "p2"),
    "period_outside": ask("ledger", SERVER, "p2"),                      # 有覆盖这个日期的别的账套才成题，没有则是要人去开期间
    "negative_stock": ask("fixed", DESKTOP, "p2"),                      # 账本允许的解决选项（改数量 / 换仓 / 先补入库 / 改物料设置 / 先留下），用户 11-14
    "login_failed": act("desktop_login", "p2"),
    "login_missing": act("desktop_login", "p2"),
    "license": act("license", "p2"),
    "company_mismatch": act("company_link", "p2"),
    "company_pick": ask("screen", DESKTOP, "p2"),
    "account_in_use": act("account_in_use", "mrerp"),
    "account_set_unavailable": act("company_link", "mrerp"),
    "recipe_mismatch": ask("fixed", SERVER, "p2"),                      # 待补配方：这张先留下 / 这批先停（总设计 9.5）
    "unsupported": ask("fixed", SERVER, "p2"),
    "readback_mismatch": ask("fixed", DESKTOP, "p2"),                   # 列出差异：已核对没问题 / 改后再读
    "unknown_screen": ask("screen", DESKTOP, "p2"),                     # 总设计第 10 节；乙类框的做法 B 等用户定
    "menu_choice": ask("receipt:menu_options", DESKTOP, live=True),
    "crash_repeated": act("ours_retry", "p2"),
    "cc_restarted": act("ours_retry", "p2"),
    "placeholder_left": act("placeholder_left", "p2"),
    "interrupted": ask("fixed", DESKTOP, "p2"),                       # 有人在 Express 里动过：继续 / 停止（1.12.23 ⑥）
    "dialog_choice": ask("screen", DESKTOP, "p2"),                     # 中途配方外的业务选择框：框的原文与按钮带回对话，人选了 CC 去点（1.12.23 ③）
    "left_open": act("cancel_in_express", "p2"),                      # 保存前停下、放弃收不干净：Express 里这张没保存的单开着，人在那台电脑的 Express 里取消
    "partial_saved": act("partial_left", "p2"),                        # 保存途中停住、账套里留了半截单（不重录、不作废）：说清哪张缺什么，人去账本补完
    "network_down": act("computer_online", "p2"),
    "budget": act("ours_retry", "p2"),
    "duplicate_ref": wait("reported"), "voided": wait("reported"), "ai_resolved": wait("reported"),
    "express_restarted": wait("reported"),
    "screen_locked": wait("computer_free"), "network_wait": wait("network"),
    "user_active": ask("fixed", DESKTOP, "p2"),                         # 人在账本里操作打断了 CC：继续 / 停止（用户 11-14，总设计 10.6）
    "account_busy": wait("ledger_free"), "waiting_start": wait("start_time"),
    # ── 直写回执（cc_desktop.wire.ack_rules、express_rules.push.common）────────────────────────────────────────
    "UNIT_CHOICE": ask("receipt:choice", DESKTOP, "p2"),
    "COST_BASIS_CHOICE": ask("receipt:choice", DESKTOP, "p2"),
    "CASH_ACCOUNT_CHOICE": ask("receipt:choice", DESKTOP, "p2"),
    "UNIT_CODE_NOT_FOUND": ask("precheck:unit", SERVER, "p2"),
    "ITEM_GROUP_NOT_FOUND": ask("precheck:stock_group", SERVER, "p2"),
    "PARTY_TYPE_NOT_FOUND": ask("precheck:new_party", SERVER, "p2"),
    "INVALID_DUE_DATE": ask("review", SERVER, "p2"),
    "SUPPLIER_NOT_IN_APMAS": ask("precheck:new_party", DESKTOP, "p2"),
    "CUSTOMER_NOT_IN_ARMAS": ask("precheck:new_party", DESKTOP, "p2"),
    "ERR_LOCATION": ask("precheck:location", SERVER, "p2"),           # 仓码不在仓库表：换一个仓
    "ACCOUNT_CODE_IS_STOCK_ITEM": ask("precheck:account", SERVER, "p2"),  # 费用行的科目码其实是库存品：换科目或改走库存
    "STOCK_IN_FAILED": ask("review", SERVER, "p2"),                     # 采购入库失败：回复核卡改这张（覆盖表 G-B3）
    "CUSTOMER_BRANCH_AMBIGUOUS": ask("precheck:party", SERVER, "p2"),
    "SUPPLIER_BRANCH_AMBIGUOUS": ask("precheck:party", SERVER, "p2"),
    "CUSTOMER_DUP_SUSPECTED": ask("precheck:party", SERVER, "p2"),
    "SUPPLIER_DUP_SUSPECTED": ask("precheck:party", SERVER, "p2"),
    "DUPLICATE_CONTENT_DIFFERS": ask("fixed", SERVER, live=True),
    "PRIOR_DOC_STILL_IN_ERP": ask("fixed", SERVER, live=True),
    "FIELD_TOO_LONG": ask("review", SERVER, "p2"),
    "STOCK_INSUFFICIENT": ask("fixed", DESKTOP, "p2"),
    # ── ERP 单据停下的原因（pearnly_erp_portal.express.contracts）──────────────────────────────────────────────
    "choice_needed": ask("precheck:any", SERVER, live=True),
    "express.company_tax_missing": act("company_link", "p2"),
    "express.account_set_missing": act("company_link", "p2"),
    "express.stock_book_missing": act("ledger_setting", "p4"),
    "express.account_set_several": ask("ledger", SERVER, "p0"),
    "express.account_period_mismatch": ask("ledger", SERVER, "p2"),
    "express.no_writer": wait("computer_online"),
    "express.masters_reading": wait("masters_read"),
    "express.pos_refund_waiting": wait("original_written"),
    "express.cc_desktop_not_connected": act("computer_online", "p2"),
    "express.cc_desktop_offline": act("computer_online", "p2"),
    "express.warehouse_unmapped": ask("precheck:location", SERVER, live=True),
    "express.item_unmapped": ask("precheck:new_item", DESKTOP, "p4"),
    "express.printed_total_missing": ask("review", SERVER, "p2"),
    "express.printed_total_mismatch": ask("review", SERVER, "p2"),
    # ── 收款（pearnly_erp_portal.collection.contracts）：都回到复核卡上对应那一格，改了再确定 ─────────────────────
    **{f"collection.{name}": ask("review", SERVER, "p1") for name in (
        "invoices_empty", "invoices_too_many", "invoice_duplicate", "invoice_not_found", "over_collected", "amount_invalid",
        "amount_mismatch", "receipt_type_invalid", "method_duplicate", "methods_too_many", "discount_only", "voucher_missing",
        "salesman_invalid", "department_invalid", "changed")},
    "collection.cash_account_missing": act("ledger_setting", "p1"),
    "collection.busy": wait("ledger_free"),
    "already_collected": ask("review", SERVER, live=True),              # 退回草稿，可改了再放行（码照 CC 桌面版原样小写）
    "invoice_not_found": ask("review", SERVER, live=True),
    # ── 写入计划跳过的原因（assistant.write_plan）──────────────────────────────────────────────────────────────
    "moved_to_not_entered": ask("fixed", SERVER, "p2"),
    "already_entered": ask("fixed", SERVER, "p2"),                      # 再办一次 / 跳过，不预选
    "last_write_result_unknown": ask("fixed", SERVER, live=True),       # 没有入账 / 已入账
    "undecided_items": ask("precheck:any", SERVER, live=True),
    "waiting_for_computer": wait("computer_online"),
    "no_account_set": act("company_link", "p2"),
    "account_period_mismatch": ask("ledger", SERVER, "p2"),
    "ledger_needed": ask("ledger", SERVER, live=True),
    # ── S3（11-16）：原先只在基线里的 41 个码，逐个归类、基线清零（代录停下出口 H5 方案第 ② 节的五类）──────────────────
    # 要人选一项（A 类）：开始前就该问到；写到一半才回的，回到对应那一题
    "STOCK_ACC_GROUP_UNFIT": ask("precheck:stock_group", SERVER, "p2"),  # 新物料挂的存货科目组不合格（S1 改成开始前问）
    "SERVICE_VAT_BASIS_CHOICE": ask("precheck:service_vat", SERVER, "p2"),  # 赊购服务品进项税口径定不了
    # 要人改单 / 核对（B 类）：回复核卡改那一格
    "PAYLOAD_INVALID": act("ours_retry", "p2"),                          # 载荷不合格是我们的问题：我们修、再试
    "STOCK_ITEM_NOT_FOUND": ask("review", SERVER, "p4"),                 # 库存单认不出 Express 的库存品
    "receipt_type_invalid": ask("review", SERVER, "p1"),                 # 收款方式没有可用科目（CC 桌面版回执，同 collection.receipt_type_invalid）
    "collection.consent_missing": ask("review", SERVER, "p1"),           # 没有同意就放行：回复核卡再确定
    # 要人在账本里做（C 类）：一句「要在 Express 做哪一件」+ 做完再试
    "ITEM_GROUP_ACCOUNT_MISSING": act("ledger_setting", "p2"),           # 物料科目组的科目不在科目表（原生 1155）
    "PERIOD_LOCKED": act("period_locked", "p2"),                         # 写进已关账的会计期
    "JOURNAL_BOOK_NOT_FOUND": act("ledger_setting", "p3"),               # POS 日汇总：没有可用的普通日记账单据簿
    "ACCOUNT_NOT_POSTABLE": act("ledger_setting", "p3"),                 # POS 日汇总：选的收款科目不能记账
    "cash_account_missing": act("ledger_setting", "p1"),                 # 收款：账套没设现金科目（同 collection.cash_account_missing）
    "REFUND_CUSTOMER_NOT_COPIED": act("ledger_setting", "p3"),           # 跨年退货：新年度账套没有现金客户档
    "REFUND_ORIGINAL_NOT_FOUND": act("check_in_ledger", "p3"),           # 收银退货：原单找不到 / 改过 / 没有这一行 / 退得比原单多
    "REFUND_ORIGINAL_CHANGED": act("check_in_ledger", "p3"),
    "REFUND_LINE_NOT_FOUND": act("check_in_ledger", "p3"),
    "REFUND_EXCEEDS_ORIGINAL": act("check_in_ledger", "p3"),
    "express.pos_refund_cross_year": act("check_in_ledger", "p3"),
    "express.pos_refund_original_manual": act("check_in_ledger", "p3"),  # 原单是人手工处理的：退货也要手工处理
    "VOID_DOCUMENT_CHANGED": act("check_in_ledger", "p4"),               # 作废库存单：原单被改过 / 找不到
    "VOID_DOCUMENT_NOT_FOUND": act("check_in_ledger", "p4"),
    "express.voided_after_write": act("check_in_ledger", "p2"),          # ERP 已作废但 Express 里已写成：人去 Express 处理
    "collection.not_withdrawable": act("check_in_ledger", "p1"),         # 已写进 Express 的收款撤不了：在 Express 原生作废
    # 电脑 / 设置（D 类）：一句「在哪台电脑、哪里设」+ 再试
    "ACCOUNT_COMPANY_MISMATCH": act("company_link", "p2"),               # 这份账套属于另一家公司
    "ACCOUNT_DIR_NOT_ALLOWED": act("desktop_folder", "p2"),              # 这台电脑上这个文件夹不可写
    "NO_ACCOUNT_SELECTION": act("desktop_folder", "p2"),                 # 这台电脑的 CC 桌面版里没有这份账套
    "REINDEX_UNAVAILABLE": act("desktop_reinstall", "p2"),               # 正式包里的索引重建程序跑不起来：重装 CC 桌面版
    "POS_INVALID": act("desktop_update", "p3"),                          # 这台电脑的 CC 桌面版不收 POS 块：更新
    "express.disconnected": act("company_link", "p2"),                   # 断开 Express 时还没写进去：重新连接
    # 等（不用人做事）
    "express.operator_owned": wait("operator_done"),                     # 交给代录、还没录成：等代录录完，直写不接
    "collection.not_draft": wait("reported"),                            # 已放行、不是草稿了：看结果即可
    # 写入计划跳过（assistant.write_plan）
    "not_found": act("not_yours", "p2"),                                 # 不是本人能写的单 / 找不到：说清楚，不编
    # MR.ERP 用户数据错（mrerp.push.user_data.USER_DATA_ERROR_CODES）
    "ERR_NO_CLIENT": ask("precheck:party", SERVER, "mrerp"),
    "ERR_NO_CUSTOMER_MAPPING": ask("precheck:party", SERVER, "mrerp"),
    "ERR_CUSTOMER_NAME_MISMATCH": ask("precheck:party", SERVER, "mrerp"),  # 名字复核对不上：重选对方
    "ERR_PRODUCT_NAME_MISMATCH": ask("precheck:new_item", SERVER, "mrerp"),
    "ERR_CUSTOMER_CODE_TOO_LONG": ask("review", SERVER, "mrerp"),
    "ERR_NEGATIVE_AMOUNT": ask("review", SERVER, "mrerp"),
    "ERR_QTY_UNREADABLE": ask("review", SERVER, "mrerp"),          # 明细数量读不懂又没行金额：人改这一格再推（11-19）
    "ERR_UNIT_PRICE_UNREADABLE": ask("review", SERVER, "mrerp"),   # 明细单价读不懂又没行金额：同上
    "ERR_DUPLICATE_INVOICE": ask("fixed", SERVER, "mrerp"),              # 同号已在 MR.ERP：跳过 / 改号再录，不预选
    "ERR_NO_SEED_CUSTOMER": act("ledger_setting", "mrerp"),              # MR.ERP 里缺种子客户 / 物料：先在 MR.ERP 建
    "ERR_NO_SEED_PRODUCT": act("ledger_setting", "mrerp"),
    "ERR_MRERP_MASTER_UNREADABLE": wait("masters_read"),                 # 读不到 MR.ERP 主档：等读到
    # ── MR.ERP（pearnly_erp_portal.mrerp.contracts.CHOICE_CODES）──────────────────────────────────────────────
    "ERR_MRERP_MENU_CHOICE_REQUIRED": ask("precheck:any", SERVER, live=True),
    "ERR_MRERP_MENU_CHANGED": ask("precheck:any", SERVER, live=True),
    "ERR_MRERP_DEFAULT_CHOICE_REQUIRED": ask("precheck:any", SERVER, live=True),
    "ERR_MRERP_DEFAULT_CHANGED": ask("precheck:any", SERVER, live=True),
    "ERR_INVOICE_NO_TOO_LONG": ask("review", SERVER, "mrerp"),
    "ERR_BILL_NO_TOO_LONG": ask("review", SERVER, "mrerp"),
    "ERR_NO_INVOICE_NO": ask("review", SERVER, "mrerp"),
    "ERR_NO_INVOICE_DATE": ask("review", SERVER, "mrerp"),
    "ERR_NO_TOTAL_AMOUNT": ask("review", SERVER, "mrerp"),
    "ERR_AMOUNTS_NOT_CLOSED": ask("review", SERVER, "mrerp"),
    "ERR_TAX_ID_INVALID": ask("review", SERVER, "mrerp"),
    "ERR_DATE_FUTURE": ask("review", SERVER, "mrerp"),
}


def problems(exits: Mapping[str, Mapping[str, Any]] = EXITS) -> List[str]:
    """归类表自己合不合规矩（空 = 合）：三类之一；问要有选项来源与执行者；等要写等什么；用户去做要有那一句的键；
    不是现成的要写排在哪一期。"""
    out: List[str] = []
    for code, item in exits.items():
        kind = item.get("next")
        if kind not in KINDS:
            out.append(f"{code}: next {kind!r} is not one of {KINDS}")
            continue
        if kind == ASK and (not SOURCE.match(str(item.get("source") or "")) or item.get("by") not in (SERVER, DESKTOP)):
            out.append(f"{code}: an ask exit names where the options come from and who carries out the choice")
        if kind == WAIT and not _ID.match(str(item.get("until") or "")):
            out.append(f"{code}: a wait exit names what it waits for")
        if kind == ACT and not _KEY.match(str(item.get("tell") or "")):
            out.append(f"{code}: an act exit names the glossary key of the one thing the user has to do")
        if not item.get("live") and item.get("phase") not in PHASES:
            out.append(f"{code}: an exit that is not live names the phase it is planned for")
    return out


def of(code: str) -> Dict[str, Any] | None:
    found = EXITS.get(str(code or ""))
    return dict(found) if found else None


__all__ = ["ACT", "ASK", "DESKTOP", "EXITS", "KINDS", "PHASES", "SERVER", "WAIT", "of", "problems"]
