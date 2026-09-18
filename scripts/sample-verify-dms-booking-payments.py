# -*- coding: utf-8 -*-
"""LINE 付款资料解析与校验验证（纯逻辑，零 IO，不打真 DMS）。

这一层的价值全在一句话上：**宁可多问一次，也不把账号/金额猜着拆开写进 DMS。**
所以下面每一条坏输入都必须回 `None` / 报错，而不是"尽力猜一个"。

用法（容器里）：
    /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_booking_payments.py
"""


def main():
    from pearnly_dms import dms_booking_payments as bp
    from pearnly_dms import dms_text_fields as tf
    from pearnly_dms.dms_payments import MANUAL_BANK_FLAG

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def raises_code(fn):
        try:
            fn()
        except bp.PaymentValidationError as exc:
            return exc.code
        except Exception as exc:  # noqa: BLE001
            return "抛的是 %s" % type(exc).__name__
        return "没抛"

    # ══ 1. 分段：一次只用一类分隔符，段数不对就不认 ═══════════════════════
    check("竖线分段", tf.split_fields("12345 | 678", 2) == ["12345", "678"],
          tf.split_fields("12345 | 678", 2))
    check("全角竖线归一后分段", tf.split_fields("12345｜678", 2) == ["12345", "678"])
    check("中文逗号 / 顿号也行", tf.split_fields("a，b", 2) == ["a", "b"])
    check("居中点也算分隔符", tf.split_fields("KBank·VISA", 2) == ["KBank", "VISA"])
    check("点号两侧留白才认", tf.split_fields("a . b", 2) == ["a", "b"])
    check("无留白点号按段数拆（KBank.VISA）", tf.split_fields("KBank.VISA", 2) == ["KBank", "VISA"])

    # 刻意不拆的四类 —— 每一条都是"猜错就把钱写错"
    check("千位逗号不当分隔符（1,000.00 必须原样）",
          tf.split_fields("1,000.00 | KBANK", 2) == ["1,000.00", "KBANK"],
          tf.split_fields("1,000.00 | KBANK", 2))
    check("账号里的斜杠不当分隔符（123/456）", tf.split_fields("123/456|X", 2) == ["123/456", "X"],
          tf.split_fields("123/456|X", 2))
    check("日期不是三段（19/09/2569）", tf.split_fields("19/09/2569", 3) is None,
          tf.split_fields("19/09/2569", 3))
    check("时间里的冒号不是分隔符", tf.split_fields("14:36", 2) is None)
    check("点号后跟空格不拆（Mr. Somchai）", tf.split_fields("Mr. Somchai", 2) is None)
    check("小数不拆（19.09）", tf.split_fields("19.09", 2) is None)
    check("空段不算字段（A |  | C）", tf.split_fields("A |  | C", 3) is None)
    check("段数对不上就不认", tf.split_fields("12345 | 678", 3) is None)
    check("空输入 / expected<=0 → None",
          tf.split_fields("", 2) is None and tf.split_fields("x", 0) is None)

    # ══ 2. 一行资料 → 原生槽位 ═══════════════════════════════════════════
    check("支票：号 + 簿号",
          bp.parse_payment_detail("cheque", "12345 | 678")
          == {"cheque_no": "12345", "cheque_book_no": "678"},
          bp.parse_payment_detail("cheque", "12345 | 678"))
    check("支票：只有空格也能拆（号 + 簿号）",
          bp.parse_payment_detail("cheque", "12345 678")
          == {"cheque_no": "12345", "cheque_book_no": "678"})
    check("支票：拆不出来 → None（让用户重问，不猜）",
          bp.parse_payment_detail("cheque", "只有一段") is None)
    check("刷卡：整行就是卡种", bp.parse_payment_detail("card", "VISA") == {"card_type": "VISA"})
    check("其它：整行就是说明", bp.parse_payment_detail("other", "现金补差") == {"detail": "现金补差"})
    check("占位符 - 不算资料", bp.parse_payment_detail("card", "-") is None)
    check("空行 → None", bp.parse_payment_detail("cheque", "   ") is None)
    check("手工银行：支票多一段银行名（在末段）",
          bp.parse_payment_detail("cheque", "12345|678|KBank", manual_bank=True)
          == {"cheque_no": "12345", "cheque_book_no": "678", "bank_name": "KBank"},
          bp.parse_payment_detail("cheque", "12345|678|KBank", manual_bank=True))
    check("手工银行：- 段不算（不猜）",
          bp.parse_payment_detail("cheque", "12345|-|KBank", manual_bank=True) is None)
    check("手工银行：卡在首段",
          bp.parse_payment_detail("card", "KBank|VISA", manual_bank=True)
          == {"bank_name": "KBank", "card_type": "VISA"})
    check("非手工银行时不给卡套上银行名",
          bp.parse_payment_detail("card", "KBank|VISA") == {"card_type": "KBank|VISA"})

    # ══ 3. 编辑校验：报的是机器码，不是话 ═══════════════════════════════
    BANKS = [["77", "KBANK", "Kasikorn", "Silom", "123-4-56789-0"]]
    MASTERS = {"company_banks": BANKS, "cheque_banks": BANKS, "card_banks": BANKS,
               "cashier_banks": BANKS}

    got = bp.normalize_editor_payments(
        [{"channel": "transfer", "amount": "1,500.5", "extra": {"dst_id": "77"}}], MASTERS)
    check("转账：账户资料来自主档（金额规范化到两位）",
          got[0]["amount"] == "1500.50" and got[0]["extra"]["dst_account_no"] == "123-4-56789-0",
          got[0])
    check("空付款 → payment_required",
          raises_code(lambda: bp.normalize_editor_payments([], MASTERS))
          == "dms_booking.payment_required")
    check("未知渠道 → invalid_payment",
          raises_code(lambda: bp.normalize_editor_payments(
              [{"channel": "crypto", "amount": "1"}], MASTERS)) == "dms_booking.invalid_payment")
    check("同渠道重复 → duplicate_payment",
          raises_code(lambda: bp.normalize_editor_payments(
              [{"channel": "cash", "amount": "1"}, {"channel": "cash", "amount": "2"}], MASTERS))
          == "dms_booking.duplicate_payment")
    check("金额非法 → invalid_amount",
          raises_code(lambda: bp.normalize_editor_payments(
              [{"channel": "cash", "amount": "abc"}], MASTERS)) == "dms_booking.invalid_amount")
    check("金额为 0 → invalid_amount（0 元不是一笔付款）",
          raises_code(lambda: bp.normalize_editor_payments(
              [{"channel": "cash", "amount": "0"}], MASTERS)) == "dms_booking.invalid_amount")
    check("转账指向不存在的收款账户 → invalid_bank",
          raises_code(lambda: bp.normalize_editor_payments(
              [{"channel": "transfer", "amount": "100", "extra": {"dst_id": "999"}}], MASTERS))
          == "dms_booking.invalid_bank")
    check("支票银行不在实时目录 → invalid_bank",
          raises_code(lambda: bp.normalize_editor_payments(
              [{"channel": "cheque", "amount": "100",
                "extra": {"bank_id": "999", "cheque_no": "1", "cheque_book_no": "2"}}], MASTERS))
          == "dms_booking.invalid_bank")
    check("支票资料不全 → payment_detail_required",
          raises_code(lambda: bp.normalize_editor_payments(
              [{"channel": "cheque", "amount": "100", "extra": {"bank_id": "77"}}], MASTERS))
          == "dms_booking.payment_detail_required")
    good = bp.normalize_editor_payments(
        [{"channel": "cheque", "amount": "100",
          "extra": {"bank_id": "77", "cheque_no": "12345", "cheque_book_no": "678"}}], MASTERS)
    check("支票资料齐全 → 原生槽位（bank_id/bank_name 来自实时目录）",
          good[0]["extra"]["bank_id"] == "77" and good[0]["extra"]["bank_name"] == "Kasikorn"
          and good[0]["extra"]["cheque_no"] == "12345"
          and MANUAL_BANK_FLAG not in good[0]["extra"], good[0]["extra"])
    # 「目录权威为空（空列表）」与「取数失败（缺席/None）」必须分得开：
    # 前者允许手工银行名，后者只能报银行无效（拿不到名录不等于名录里没有）。
    manual = bp.normalize_editor_payments(
        [{"channel": "cheque", "amount": "100",
          "extra": {"bank_name": "手工银行", "cheque_no": "1", "cheque_book_no": "2"}}],
        {"cheque_banks": []})
    check("目录权威为空 → 手工银行留名、id 留空、挂手工标记",
          manual[0]["extra"].get("bank_id") == "" and manual[0]["extra"].get("bank_name") == "手工银行"
          and manual[0]["extra"].get(MANUAL_BANK_FLAG) == "1", manual[0]["extra"])
    check("目录**取数失败**（缺席）→ 只能报 invalid_bank，不许当空目录放行",
          raises_code(lambda: bp.normalize_editor_payments(
              [{"channel": "cheque", "amount": "100",
                "extra": {"bank_name": "手工银行", "cheque_no": "1", "cheque_book_no": "2"}}], {}))
          == "dms_booking.invalid_bank")
    check("规范化结果能直接喂给 dms_payments（边界在这层接上）",
          (lambda: (__import__("pearnly_dms.dms_payments", fromlist=["x"])
                    .payment_form_fields(tuple(good))).get("txtmoneycheque") == "100.00")())

    # ══ 4. 确认卡摘要：与 DMS 字段同源 ═══════════════════════════════════
    transfer = {"channel": "transfer", "amount": "1500.50",
                "extra": {"src_bank_name": "SCB", "src_account_no": "111",
                          "dst_bank_name": "BBL", "dst_account_no": "222"}}
    detail = bp.payment_preview_detail(transfer)
    check("转账摘要显示 来源 → 到账", "SCB" in detail and "BBL" in detail and "→" in detail, detail)
    cheque = {"channel": "cheque", "amount": "100.00",
              "extra": {"cheque_no": "12345", "cheque_book_no": "678", "bank_name": "KBANK"}}
    detail = bp.payment_preview_detail(cheque)
    check("支票摘要含号/簿号/银行",
          all(x in detail for x in ("12345", "678", "KBANK")), detail)
    detail = bp.payment_preview_detail({"channel": "other", "amount": "50.00",
                                        "extra": {"detail": "补差"}})
    check("其它渠道摘要含说明", "补差" in detail, detail)

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
