# -*- coding: utf-8 -*-
"""DMS 顾问归属匹配验证（纯逻辑，零 IO，不打真 DMS）。

**为什么这个必须验**：订车单上那一栏顾问 = 月底提成算给谁。它是**我们 POST 上去**的
（真机取证"填谁存谁"，三键留空整单被拒）。老实现恒填顾问主档第一行 → 全公司的单
记到同一个人头上。所以这里每条都在钉一件事：**认不准就拦下来，绝不猜**。

用例按老产品 `tests/unit/test_dms_advisor_match.py` 迁移（线上基准 `501589f0`），
其中三组是线上 `b78697f3` 那次修复的契约：

· 无传入快照时**一律现抓**（`force_refresh=True`），不认缓存；
· 钉死的 id 必须在**当前**名册里，名字取**实时**值；
· **读取失败不得用旧配置放行**。
"""


def main():
    from pearnly_dms.masters import advisor as dms_advisor
    from pearnly_dms.masters import masters_cache as dms_masters_cache

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    # DMS 顾问下拉行形：[id, code, name, tel]（tel 是员工私人号码，从不下发）
    ADVISORS = [
        ["297", "SALE01", "สมชาย", "0812345678"],
        ["335", "sale02", "sale02", "0899999999"],
    ]
    EMPLOYEES = [{"id": "297", "code": "SALE01", "login": "sale01", "name": "สมชาย"}]

    # ── 1. 启发层：登录名 → 顾问行（认不准弃权）────────────────────────
    check("命中 code 列", (dms_advisor.match_advisor(ADVISORS, "sale02") or {}).get("id") == "335")
    check("code 列不中时命中 name 列",
          (dms_advisor.match_advisor([["9", "S99", "sale02"]], "sale02") or {}).get("id") == "9")
    check("大小写与空格不影响",
          (dms_advisor.match_advisor(ADVISORS, "  SALE02 ") or {}).get("id") == "335")
    check("code 层优先于 name 层",
          (dms_advisor.match_advisor(
              [["1", "sale02", "别的"], ["2", "zzz", "sale02"]], "sale02") or {}).get("id") == "1")
    check("同层多命中 → 弃权（重名重号宁可当场拦）",
          dms_advisor.match_advisor([["1", "dup", "a"], ["2", "dup", "b"]], "dup") is None)
    check("完全不中 → None", dms_advisor.match_advisor(ADVISORS, "查无此人") is None)
    check("空登录名 → None", dms_advisor.match_advisor(ADVISORS, "  ") is None)
    check("空名册 → None", dms_advisor.match_advisor([], "sale02") is None)

    # ── 2. 精确层优先：员工表认得人就地定论 ──────────────────────────
    m = dms_advisor.match_in_masters({"advisors": ADVISORS, "employees": EMPLOYEES}, "sale01")
    check("精确层命中员工表 → 归属他（不是猜 code 列）", (m or {}).get("id") == "297", m)
    check("名字取自顾问下拉行（不是员工表别名）", (m or {}).get("name") == "สมชาย", m)

    # 员工在员工表、但不在顾问下拉 = 没资格当顾问，是合法状态 → 拦，不许回落启发层
    trap = {"advisors": [["A1", "sale01", "别的顾问"]],
            "employees": [{"id": "Z9", "code": "Z9", "login": "sale01", "name": "没资格"}]}
    check("员工没顾问资格 → 拦截，不猜给谁",
          dms_advisor.match_in_masters(trap, "sale01") is None)
    check("员工表为空 → 不猜测编号或姓名",
          dms_advisor.match_in_masters({"advisors": ADVISORS, "employees": []}, "sale02") is None)
    check("登录名不在员工表 → 不分配给编号恰巧相同的人",
          dms_advisor.match_in_masters({"advisors": ADVISORS, "employees": EMPLOYEES}, "sale02") is None)
    check("员工表无法读取 → 不猜测归属",
          dms_advisor.match_in_masters({"advisors": ADVISORS, "employees": None}, "sale02") is None)
    duplicates = EMPLOYEES + [{**EMPLOYEES[0], 'id': '335'}]
    check("重复登录名 → 不猜测归属",
          dms_advisor.match_in_masters({"advisors": ADVISORS, "employees": duplicates}, "sale01") is None)

    # ── 3. 端点解析：现抓、钉死校验、失败不放行 ───────────────────────
    calls = []

    def fake_masters(endpoint, *, force_refresh=False, require_complete=False):
        calls.append((force_refresh, require_complete))
        return {"advisors": ADVISORS, "employees": EMPLOYEES}

    import pearnly_dms.masters.masters_cache as cache_mod

    real_get = cache_mod.get_masters
    real_defaults = dms_advisor.booking_defaults
    real_username = dms_advisor.dms_username
    try:
        cache_mod.get_masters = fake_masters
        dms_advisor.booking_defaults = lambda ep: {}

        dms_advisor.dms_username = lambda ep: "sale01"
        calls.clear()
        advisor, username = dms_advisor.resolve_operator_advisor("EP-1")
        check("匹配到顾问", (advisor or {}).get("id") == "297", advisor)
        check("同时回出是哪个账号（拦截话术要报账号）", username == "sale01", username)
        check("无快照时一律现抓 + 要完整主档",
              calls == [(True, True)], calls)

        dms_advisor.dms_username = lambda ep: ""
        check("解不出登录名 → (None, ''), 不猜账号",
              dms_advisor.resolve_operator_advisor("EP-1") == (None, ""))

        # 钉死归属
        dms_advisor.booking_defaults = lambda ep: {"advisor_id": "335", "advisor_name": "旧名字"}
        calls.clear()
        advisor, username = dms_advisor.resolve_operator_advisor("EP-1")
        check("钉死的 id 在名册里 → 放行", (advisor or {}).get("id") == "335", advisor)
        check("钉死时名字取实时值，不信存下来的旧名字",
              (advisor or {}).get("name") == "sale02", advisor)
        check("钉死时不解析登录名", username == "", username)
        check("钉死也现抓（不认缓存）", calls == [(True, True)], calls)

        dms_advisor.booking_defaults = lambda ep: {"advisor_id": "335", "advisor_name": "旧名字"}
        cache_mod.get_masters = lambda ep, **kw: {"advisors": []}
        check("钉死的 id 已从名册删除 → 拦截",
              dms_advisor.resolve_operator_advisor("EP-1") == (None, ""))
        cache_mod.get_masters = lambda ep, **kw: {}
        check("名册取数失败 → 拦截（不得用旧配置放行）",
              dms_advisor.resolve_operator_advisor("EP-1") == (None, ""))

        cache_mod.get_masters = fake_masters
        dms_advisor.booking_defaults = lambda ep: {"advisor_id": "335", "advisor_name": "旧名字"}
        advisor, _ = dms_advisor.resolve_operator_advisor(
            "EP-1", masters={"advisors": [["335", "sale02", "现在的名字"]]}
        )
        check("外部快照里名字是实时值 → 覆盖存下来的旧名字",
              advisor == {"id": "335", "name": "现在的名字"}, advisor)
        advisor, _ = dms_advisor.resolve_operator_advisor("EP-1", masters={"advisors": []})
        check("外部快照里没有这个 id → 拦截", advisor is None)
    finally:
        cache_mod.get_masters = real_get
        dms_advisor.booking_defaults = real_defaults
        dms_advisor.dms_username = real_username

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
