# -*- coding: utf-8 -*-
"""订车逐问的**步进**验证（付款/转账/翻页/主档快照；不需要 DMS 联网）。

这一层是老产品的"注入式步进"：状态怎么改靠 `qa` 字典，发消息靠调用方注入的回调，
可以直接喂假回调、把每一步断言死 —— 比对着真 DMS 跑便宜得多，也更准。

钉住四件事：
1. **非法输入不推进状态**（未知渠道、重复渠道、金额解析失败、越界页号）——静默接受
   是这类状态机最难查的 bug。
2. **收尾前的两件一致性**：有转账没凭证 → 先要凭证（并记住拿完去哪）；有凭证没转账 →
   出冲突卡（补转账 / 删凭证），不直接放行到预览。
3. **银行目录两副面孔**：有行 → `pay_bank` 选；**权威为空** → `pay_ref` 手工填，
   并挂 `MANUAL_BANK_FLAG`（结算时据实写 DMS）。
4. **翻页只翻看**：页面令牌与 DMS 的 id 分得开；车型搜索的页数要夹住。

用法: scripts/sample-verify-dms-booking-qa-steps.sh <组> <站点>
"""


def main():
    from pearnly_dms.booking.conversation import booking_qa_pages as pages
    from pearnly_dms.booking.conversation import booking_qa_payment as pay
    from pearnly_dms.booking.conversation import booking_qa_sync as sync
    from pearnly_dms.booking.conversation import booking_qa_transfer as transfer
    from pearnly_dms.booking.conversation import qa_cards as qa_cards
    from pearnly_dms.masters.master_contract import MasterSyncError
    from pearnly_dms.contracts.payments import MANUAL_BANK_FLAG

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    class Rec:
        """假回调：把"存了什么、发了什么、重问了几次"记下来。"""

        def __init__(self):
            self.saved = 0
            self.sent = []
            self.reasked = 0
            self.previews = 0

        def persist(self, tenant_id, line_user_id, qa):
            self.saved += 1

        def send_step(self, tenant_id, line_user_id, qa, step, reply_token):
            self.sent.append(step)

        def reask(self, tenant_id, line_user_id, qa, text, reply_token):
            self.reasked += 1

        def to_preview(self, tenant_id, line_user_id, qa, reply_token):
            self.previews += 1

    def hooks(rec):
        return dict(persist=rec.persist, send_step=rec.send_step, reask=rec.reask,
                    to_preview=rec.to_preview)

    rec_out = []

    def fake_send(line_user_id, msg, reply_token=""):
        """卡片出口换成记录器 —— 不然带 reply_token 时会真的去调 LINE。"""
        rec_out.append(msg)
        return True

    transfer._send = fake_send
    pages._send = fake_send

    # ── 1. 模块形状 ────────────────────────────────────────────────────
    ck("三个步进模块 import 不成环（互相引用只在函数体内）", True)
    ck("文字步进就是这三步",
       transfer.TEXT_STEPS == frozenset({"pay_amount", "pay_bank", "pay_ref"}),
       sorted(transfer.TEXT_STEPS))
    ck("逐页主档表把五类主档都列了",
       pages.PAGED_MASTER == {"place": "place_books", "term": "term_sales",
                              "regis": "regis_behalfs", "bank": "company_banks",
                              "paint": "paints"}, pages.PAGED_MASTER)

    # ── 2. 选渠道：非法/重复都不推进 ───────────────────────────────────
    rec, qa = Rec(), {"payments": []}
    pay.pick_channel("t", "u", qa, "bitcoin", "tok",
                     persist=rec.persist, send_step=rec.send_step, reask=rec.reask)
    ck("未知渠道 → 原地重问，不写状态", rec.reasked == 1 and "step" not in qa, qa)

    rec, qa = Rec(), {"payments": [{"channel": "cash"}]}
    pay.pick_channel("t", "u", qa, "cash", "tok",
                     persist=rec.persist, send_step=rec.send_step, reask=rec.reask)
    ck("重复渠道 → 原地重问（同一笔不收两次现金）", rec.reasked == 1 and "step" not in qa, qa)

    rec, qa = Rec(), {"payments": []}
    pay.pick_channel("t", "u", qa, "transfer", "tok",
                     persist=rec.persist, send_step=rec.send_step, reask=rec.reask)
    ck("合法渠道 → 记住待填渠道 + 进金额步",
       qa["pending_channel"] == {"channel": "transfer"} and qa["step"] == "pay_amount"
       and rec.sent == ["pay_amount"] and rec.saved == 1, qa)

    # ── 3. 收尾的一致性（付款这一段的核心） ─────────────────────────────
    rec, qa = Rec(), {"payments": [{"channel": "transfer"}], "files": {}}
    pay.pick_more("t", "u", qa, "done", "tok", **hooks(rec))
    ck("有转账没凭证 → 先去要凭证，且记住拿完去预览",
       qa.get("step") == "slip_after" and qa.get("after_slip") == "preview"
       and rec.previews == 0 and rec.sent == ["slip_after"], qa)

    rec, qa = Rec(), {"payments": [{"channel": "cash"}], "files": {"slip_mid": "m1"}}
    pay.pick_more("t", "u", qa, "done", "tok", **hooks(rec))
    ck("有凭证没转账 → 出冲突卡（不直接放行）",
       qa.get("step") == "slip_conflict" and rec.previews == 0
       and rec.sent == ["slip_conflict"], qa)

    rec, qa = Rec(), {"payments": [{"channel": "cash"}], "files": {}}
    pay.pick_more("t", "u", qa, "done", "tok", **hooks(rec))
    ck("干净收尾 → 进预览", rec.previews == 1 and rec.sent == [], rec.sent)

    rec, qa = Rec(), {"payments": [{"channel": c} for c in qa_cards.PAY_LABELS], "files": {}}
    pay.pick_more("t", "u", qa, "add", "tok", **hooks(rec))
    ck("渠道已满还要加 → 原地重问（不空转）", rec.reasked == 1 and rec.sent == [], rec.sent)

    rec, qa = Rec(), {"payments": [{"channel": "cash"}], "files": {"slip_mid": "m1"}}
    pay.pick_slip_conflict("t", "u", qa, "remove", "tok", **hooks(rec))
    ck("冲突选删凭证 → 清掉凭证并直接回预览",
       qa["files"]["slip_mid"] is None and qa.get("step") == "pay_more"
       and rec.previews == 1, qa)

    rec, qa = Rec(), {"payments": [{"channel": "cash"}], "files": {"slip_mid": "m1"}}
    pay.pick_slip_conflict("t", "u", qa, "add", "tok", **hooks(rec))
    ck("冲突选补转账 → 走转账金额步",
       qa["pending_channel"] == {"channel": "transfer"} and qa.get("step") == "pay_amount", qa)

    # ── 4. 转账金额 → 下一步按渠道形态分 ───────────────────────────────
    def masters_reader(rows_by_key):
        def read(line_user_id, qa, key, persist=None):
            return rows_by_key.get(key, [])
        return read

    def amount_case(channel, text, rows_by_key=None):
        rec = Rec()
        qa = {"pending_channel": {"channel": channel}}
        transfer._on_amount("t", "u", qa, text, "tok",
                            masters=masters_reader(rows_by_key or {}),
                            persist=rec.persist, send_step=rec.send_step)
        return rec, qa

    rec, qa = amount_case("cash", "5000")
    ck("现金：金额即完结 → 回'还要加渠道吗'",
       qa.get("step") == "pay_more"
       and qa.get("payments") == [{"channel": "cash", "amount": "5000.00"}], qa.get("payments"))

    rec, qa = amount_case("transfer", "1,234.50")
    ck("转账：金额之后问收款银行",
       qa.get("step") == "pay_dst" and qa["pending_channel"]["amount"] == "1234.50", qa)

    rec, qa = amount_case("other", "300")
    ck("其它渠道：金额后要一段说明", qa.get("step") == "pay_ref", qa)

    rec_out.clear()
    rec, qa = amount_case("cash", "五千")
    ck("金额解析失败 → 出'金额不对'卡且**不推进**",
       qa.get("step") is None and qa["pending_channel"].get("amount") is None
       and len(rec_out) == 1 and rec.sent == [], (qa, rec.sent))

    rec, qa = amount_case("cheque", "500", {"cheque_banks": [["1", "KBANK", "KBank"]]})
    ck("支票：目录有行 → 去选银行", qa.get("step") == "pay_bank", qa)
    ck("目录有行时不标手工银行", not (qa.get("manual_banks") or []), qa.get("manual_banks"))

    rec, qa = amount_case("cheque", "500", {})
    ck("支票：目录权威为空 → 改问手工银行资料", qa.get("step") == "pay_ref", qa)
    ck("权威为空记进 manual_banks（结算时据此挂手工标记）",
       qa.get("manual_banks") == ["cheque_banks"], qa.get("manual_banks"))

    # ── 5. 补充资料 → 原生槽位 ─────────────────────────────────────────
    # `manual_banks` 是**上一步**（金额步）在目录权威为空时打的标记，这里照真实会话带上。
    rec_out.clear()
    rec = Rec()
    qa = {"pending_channel": {"channel": "cheque", "amount": "500.00"},
          "manual_banks": ["cheque_banks"]}
    transfer._on_reference_text("t", "u", qa, "123456 | 01 | KBank", "tok",
                                persist=rec.persist, send_step=rec.send_step)
    extra = (qa.get("payments") or [{}])[0].get("extra") or {}
    ck("支票资料拆进原生槽位并完结这一笔",
       qa.get("step") == "pay_more" and qa["payments"][0]["channel"] == "cheque"
       and qa["payments"][0]["amount"] == "500.00" and extra.get("cheque_no") == "123456"
       and extra.get("cheque_book_no") == "01", qa.get("payments"))
    ck("空目录那笔多收一段手工银行名，并挂手工标记（结算时据实写 DMS）",
       extra.get("bank_name") == "KBank" and extra.get(MANUAL_BANK_FLAG) == "1", extra)

    rec = Rec()
    qa = {"pending_channel": {"channel": "cheque", "amount": "500.00"}}
    transfer._on_reference_text("t", "u", qa, "123456 | 01", "tok",
                                persist=rec.persist, send_step=rec.send_step)
    extra = (qa.get("payments") or [{}])[0].get("extra") or {}
    ck("目录有行那笔**不收**手工银行名，也不挂手工标记",
       extra.get("bank_name") is None and MANUAL_BANK_FLAG not in extra, extra)

    rec_out.clear()
    rec = Rec()
    qa = {"pending_channel": {"channel": "cheque"}}
    transfer._on_reference_text("t", "u", qa, "只有一段", "tok",
                                persist=rec.persist, send_step=rec.send_step)
    ck("资料拆不出来 → 出'格式不对'卡且不推进",
       not qa.get("payments") and len(rec_out) == 1 and rec.sent == [], (qa, rec.sent))

    # ── 6. 选银行 ──────────────────────────────────────────────────────
    # ⚠️ `pick_bank` 的 `masters` 是**状态机直接传下来的**（形状同
    # `dms_booking_qa_sync.masters`：`(tenant_id, line_user_id, qa, key)`），
    # 与 `handle_text` 那条路的 `masters_reader` 形状不同 —— 别"统一"掉。
    def machine_masters(rows_by_key):
        def read(tenant_id, line_user_id, qa, key, persist=None):
            return rows_by_key.get(key, [])
        return read

    rec = Rec()
    qa = {"step": "pay_bank", "pending_channel": {"channel": "cheque", "amount": "500.00"},
          "pages": {"cheque_banks": 2}}
    transfer.pick_bank("t", "u", qa, "1", "tok",
                       masters=machine_masters({"cheque_banks": [["1", "KBANK", "KBank"]]}),
                       persist=rec.persist, send_step=rec.send_step)
    ck("选了渠道银行 → 挂 bank_id/bank_name 并进资料步",
       qa["pending_channel"]["extra"]["bank_id"] == "1"
       and qa["pending_channel"]["extra"]["bank_name"] == "KBank"
       and qa.get("step") == "pay_ref", qa)
    ck("选完把该目录的页码清掉（下次从头看）", "cheque_banks" not in qa["pages"], qa["pages"])

    rec = Rec()
    qa = {"step": "pay_dst",
          "pending_channel": {"channel": "transfer", "amount": "100.00",
                              "extra": {"dst_id": "9", "dst_account_no": "111",
                                        "dst_branch_name": "B"}},
          "files": {"slip_mid": "m1"}}
    transfer.pick_bank("t", "u", qa, "1", "tok",
                       masters=machine_masters({"company_banks": [["1", "SCB", "SCB", "BR1", "123-4"]]}),
                       persist=rec.persist, send_step=rec.send_step)
    # 选完就完结这一笔 → 待填渠道搬进 payments（`complete_channel`），从这里读回去
    extra = (qa.get("payments") or [{}])[0].get("extra") or {}
    ck("换了一个收款账户 → 旧账号/旧分行不残留（不然会推错账户）",
       str(extra.get("dst_id")) == "1" and extra.get("dst_branch_name") == "BR1"
       and extra.get("dst_account_no") == "123-4", extra)
    ck("收款账户选完这一笔就完结（待填渠道清空）",
       qa.get("step") == "pay_more" and not qa.get("pending_channel"), qa.get("step"))

    rec = Rec()
    qa = {"step": "pay_dst",
          "pending_channel": {"channel": "transfer", "amount": "100.00", "extra": {}},
          "files": {}}
    transfer.pick_bank("t", "u", qa, "1", "tok",
                       masters=machine_masters({"company_banks": [["1", "SCB", "SCB", "123-4"]]}),
                       persist=rec.persist, send_step=rec.send_step)
    ck("转账选完账户但还没凭证 → 要凭证（拿完回'还要加渠道吗'）",
       qa.get("step") == "slip_after" and qa.get("after_slip") == "pay_more", qa)

    # ── 7. 翻页 ────────────────────────────────────────────────────────
    ck("页面令牌才当页号", pages.page_number(qa_cards.PAGE_TOKEN + "3") == 3)
    ck("DMS 的 id 不当页号（id 是选中那一行的事）", pages.page_number("C001") is None)
    ck("页号不是数字 → 不认（不炸）", pages.page_number(qa_cards.PAGE_TOKEN + "x") is None)

    rec, qa = Rec(), {"car_search": {"hits": [["%d" % i] for i in range(30)], "page": 0},
                      "step": "car_search"}
    pages.flip_page("t", "u", qa, "car", 99, "tok",
                    persist=rec.persist, send_step=rec.send_step, reask=rec.reask)
    ck("车型页号越界夹到最后一页",
       qa["car_search"]["page"] == (30 - 1) // qa_cards.QR_PAGE_SIZE, qa["car_search"]["page"])

    rec, qa = Rec(), {"step": "place", "pages": {}}
    pages.flip_page("t", "u", qa, "place", 2, "tok",
                    persist=rec.persist, send_step=rec.send_step, reask=rec.reask)
    ck("主档翻页：记页号 + 用当前步重发",
       qa["pages"]["place_books"] == 2 and rec.sent == ["place"], (qa["pages"], rec.sent))

    rec, qa = Rec(), {"step": "place", "pages": {}}
    pages.flip_page("t", "u", qa, "nonsense", 1, "tok",
                    persist=rec.persist, send_step=rec.send_step, reask=rec.reask)
    ck("不认识的翻页动作 → 原地重问", rec.reasked == 1 and rec.sent == [], rec.sent)

    # ── 8. 静态问法 vs 读主档的问法 ────────────────────────────────────
    seen = []

    def reader_of(rows_by_key):
        def read(line_user_id, qa, key, persist=None):
            seen.append(key)
            return rows_by_key.get(key, [])
        return read

    qa = {"answers": {"car": {"id": "C1", "label": "DMAX 2.5"}}, "pages": {}}
    got = pages.question("u", qa, "place", reader_of({"place_books": [["1", "A", "B"]]}),
                         lambda *a, **k: [])
    ck("取车点这一步读的是 place_books", seen == ["place_books"] and bool(got), seen)

    seen.clear()
    qa = {"pending_channel": {"channel": "cheque"}, "pages": {}}
    pages.question("u", qa, "pay_bank", reader_of({"cheque_banks": [["1", "KBANK", "KBank"]]}),
                   lambda *a, **k: [])
    ck("支票的银行步读该渠道自己的目录（不是公司收款账户）", seen == ["cheque_banks"], seen)

    ck("不需要主档的问法是查表（不发 DMS 读）",
       pages.static_question("pay_more", {}) is not None
       and pages.static_question("nonsense", {}) is None)

    # ── 9. 会话快照：没有端点就说清楚，不炸 ─────────────────────────────
    try:
        sync.masters("t", "u", {}, "place_books", persist=lambda *a: None)
        ck("没端点 → 明确抛主档不可用（发问层据此出卡）", False, "没抛")
    except MasterSyncError as exc:
        ck("没端点 → 明确抛主档不可用（发问层据此出卡）",
           exc.code == "ERR_DMS_MASTER_UNAVAILABLE", exc.code)

    qa = {"endpoint_id": "EP1",
          "master_snapshot": {"rows": {"place_books": [["1", "A", "B"]]}}}
    ck("会话里已有快照 → 直接复用（不再读 DMS）",
       sync.masters("t", "u", qa, "place_books", persist=lambda *a: None) == [["1", "A", "B"]])

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
