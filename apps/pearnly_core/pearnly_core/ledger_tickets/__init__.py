"""录过的票（多产品共用）：我们写过的单此刻在账本里还在不在（录过的票先核账本，用户 11-10「万一 exp 那边删了呢」）。

入口 :func:`check_present`（接口定稿见共享笔记「A线-核账本接口定稿-给D线-11-10」，签名改动先通知 D 线）。共用层只分派、补齐：
怎么去核由产品在自己的 ``hooks.py`` 声明（ADR-009），共用层不认识产品的通道与 MR.ERP 的读法：

    pearnly_ledger_check = {
        "express": "<点路径>",  # Cowork / ERP 各自经自己的 CC 桌面版请求通道（express_doc_check，CC 1.12.20 起）
        "mrerp": "<点路径>",    # D 线：MR.ERP 读单（查到 present、查不到也回 unknown，gone 等探路证实后再放开）
    }

函数签名同 :func:`check_present`，回 ``{doc_id: {"state", "docnum"?}}``（可以缺项）。共用层补齐：每张都有一项、``state`` 只认
present / gone / unknown（别的都算 unknown）、``docnum`` 缺了用调用方给的、``checked_at`` 是这次核的 UTC 时刻；产品的函数抛错整批
unknown（记 Error Log，不报给用户）。只读：不写账、不改推送记录，结果不存（下一次再核）。
"""
from pearnly_core.ledger_tickets.check import (EXPRESS, GONE, ITEMS_MAX, LEDGERS, MRERP, PRESENT, STATES, UNKNOWN,  # noqa: F401
                                               WAIT_MAX, check_present, parse_result, request_params)
