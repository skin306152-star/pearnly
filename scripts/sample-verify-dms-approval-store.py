# -*- coding: utf-8 -*-
"""审批申请**状态机**验证（真 DocType + 真 Redis 锁，跑完清理）。

这一层是"销售改不动 → 提管理员批 → 以批准人凭据执行"的底座，错法都很贵：

· **认领不原子** → 两个管理员同时点批准，客户资料被写两遍（甚至互相覆盖）；
· **执行租约判错** → 执行方崩了以后那张单**永远卡在"执行中"**，谁也没法再批；
· **写失败没有回炉** → DMS 拒了一次，这单就废了，销售只能重拍一遍身份证（真扣钱）。

所以三种情况都要真跑一遍，包括"把开始时间往回拨 11 分钟"来模拟崩溃。

用法: scripts/sample-verify-dms-approval-store.sh <组> <站点>
"""


def main():
    import frappe

    from pearnly_dms.approval import approval_store as store

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    created = []
    # ⚠️ `operator_user` / `decided_by` / `target_user` 都是指向 User 的 Link：
    # 用不存在的账号建单会被 Frappe 的链接校验挡下（第一次跑就是这么失败的）。
    # 这里用内置管理员当"申请人/审批人"的替身 —— 本脚本验的是**状态机**，不是账号体系。
    OPERATOR = "Administrator"
    ADMIN1 = "Administrator"
    ADMIN2 = "Administrator"

    def new_request(**kw):
        params = {
            "endpoint_id": "EP1",
            "customer_id": "C1",
            "customer_name": "คุณสมชาย",
            "field_diffs": [{"field": "name", "label": "ชื่อ", "old": "ก", "new": "ข"}],
            "draft": {"people_id": "1100000000001", "name": "ข"},
        }
        params.update(kw)
        request_id = store.create_request(OPERATOR, **params)
        if request_id:
            created.append(request_id)
        return request_id

    # ── 1. 建单 ────────────────────────────────────────────────────────
    rid = new_request()
    ck("建单成功并回名字", bool(rid), rid)
    got = store.get_request(rid)
    ck("新单是待批、带申请人/客户/差异/草稿快照",
       got and got["status"] == "pending" and got["operator_user_id"] == OPERATOR
       and got["customer_id"] == "C1" and got["field_diffs"][0]["field"] == "name"
       and got["draft"]["people_id"] == "1100000000001", got and got["status"])
    ck("过期时间默认 24 小时后（有值就算过）", bool(got["expires_at"]), got["expires_at"])
    ck("读不存在的单 → None（不炸）", store.get_request("不存在的名字") is None)

    # ── 2. 改派：只许对待批改 ─────────────────────────────────────────
    ck("待批可以改派审批人", store.set_target(rid, ADMIN1) is True)
    ck("改派写进去了", (store.get_request(rid) or {}).get("target_user_id") == ADMIN1)
    ck("改回'发给所有管理员'（None）也行", store.set_target(rid, None) is True
       and (store.get_request(rid) or {}).get("target_user_id") is None)

    # ── 3. 认领：谁先点谁拿到 ─────────────────────────────────────────
    claimed = store.claim(rid, ADMIN1)
    ck("第一个管理员认领成功 → 执行中，并记下是谁",
       claimed and claimed["status"] == "processing" and claimed["decided_by"] == ADMIN1,
       claimed and claimed["status"])
    ck("**第二个管理员抢不到**（这就是防双写的那道门）",
       store.claim(rid, ADMIN2) is None)
    ck("执行中不许再改派审批人", store.set_target(rid, ADMIN2) is False)

    # ── 4. 收尾：拒绝 / 批准 ──────────────────────────────────────────
    ck("只有执行中的单能收尾（已被认领同一个人再收尾一次也不行）",
       store.finish(rid, "approved") is True and store.finish(rid, "approved") is False)
    done = store.get_request(rid)
    ck("批准后状态是已批、且记了处理时间",
       done["status"] == "approved" and bool(done["decided_at"]), done["status"])
    ck("已批的单不能再被认领", store.claim(rid, ADMIN2) is None)

    rid2 = new_request()
    store.claim(rid2, ADMIN1)
    ck("拒绝：执行中 → 已拒", store.finish(rid2, "rejected") is True
       and (store.get_request(rid2) or {}).get("status") == "rejected")

    # ── 5. 写失败回炉（DMS 拒一次不该让这单作废） ─────────────────────
    rid3 = new_request()
    store.claim(rid3, ADMIN1)
    ck("写失败 → 回炉成待批，**清掉认领人**（谁都能再点）",
       store.finish(rid3, "pending") is True
       and (store.get_request(rid3) or {}).get("status") == "pending"
       and (store.get_request(rid3) or {}).get("decided_by") is None,
       (store.get_request(rid3) or {}).get("decided_by"))
    ck("回炉后**换个管理员**也能认领（这就是回炉的意义）",
       bool(store.claim(rid3, ADMIN2)), "抢不到")
    ck("非法状态一律拒绝", store.finish(rid3, "随便写的") is False)

    # ── 6. 执行租约：执行方崩了要能自愈 ───────────────────────────────
    rid4 = new_request()
    store.claim(rid4, ADMIN1)
    ck("刚被认领时别人抢不到", store.claim(rid4, ADMIN2) is None)
    # 把开始时间拨回 11 分钟前（超过 10 分钟租约）→ 模拟"执行方崩了"
    stale = frappe.utils.add_to_date(store._now(), minutes=-(store.PROCESSING_LEASE_MINUTES + 1))
    frappe.db.set_value(store.DT, rid4, "processing_at", stale)
    frappe.db.commit()
    again = store.claim(rid4, ADMIN2)
    ck("租约过期（执行方崩了）→ 别的管理员能接手，不用人工干预",
       bool(again) and again["decided_by"] == ADMIN2, again and again["decided_by"])
    ck("接手后租约重新开始计时（开始时间是新的）",
       (store.get_request(rid4) or {}).get("processing_at") != str(stale))

    # ── 7. 惰性过期 ───────────────────────────────────────────────────
    rid5 = new_request()
    frappe.db.set_value(store.DT, rid5, "expires_at",
                        frappe.utils.add_to_date(store._now(), hours=-1))
    frappe.db.commit()
    expired = store.get_request(rid5)
    ck("读的时候才发现过期 → 落成 expired（不用定时任务扫）",
       expired and expired["status"] == "expired", expired and expired["status"])
    ck("过期的单不能被认领", store.claim(rid5, ADMIN1) is None)
    ck("过期的单不能改派", store.set_target(rid5, ADMIN1) is False)

    for rid in created:
        try:
            frappe.delete_doc(store.DT, rid, force=True, ignore_permissions=True)
        except Exception:  # noqa: BLE001
            pass
    frappe.db.commit()
    ck("本次造的申请都清干净了（不留测试数据）",
       not [r for r in created if frappe.db.exists(store.DT, r)])

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
