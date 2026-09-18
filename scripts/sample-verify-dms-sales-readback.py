# -*- coding: utf-8 -*-
"""销售数据读取层验证：解析 DMS 原生网页 + 登录读取的协议形状。

这一层是"查资料"的底座，**错法都是静默的**：列错位 → 客户名跑到车型栏；
分页算错 → 用户以为"就这么多"；`err::` 被当数据 → 回半份假结果。所以用**真实形状的
HTML 夹具**把字段映射、分页、失败关闭都钉住（不连 DMS、不需要授权）。

用法: scripts/sample-verify-dms-sales-readback.sh <组> <站点>
"""


def main():
    from datetime import date

    from pearnly_dms import dms_sales_readback as rb
    from pearnly_dms.dms_client_base import DmsClientError

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    # ── 1. 常量：值与 DMS 表头顺序一一对应，不能随手改 ────────────────
    ck("搜索列 11 个且顺序键名齐全",
       list(rb.SEARCH_COLUMNS) == ["booking_no", "booking_date", "delivery_date", "advisor",
                                   "customer", "vehicle", "color", "finance_status",
                                   "allocation_status", "sales_doc_no", "engine_no"],
       list(rb.SEARCH_COLUMNS))
    ck("状态筛选 15 个且 active 是 3（与 DMS 的 ftd 对齐）",
       len(rb.STATUS_FILTERS) == 15 and rb.STATUS_FILTERS["active"] == "3", len(rb.STATUS_FILTERS))
    ck("看板分组/指标编码照旧",
       rb.TOP_GROUPS == {"model": "1", "type": "2", "subtype": "3"}
       and rb.TOP_METRICS == {"quantity": "1", "amount": "2"}, rb.TOP_GROUPS)
    ck("两个 DMS 原生路径照旧",
       rb.LIST_PATH == "drfcbc/component/showdata.php"
       and rb.TOP_SALES_PATH == "home/component/carmaxsellchart.php")

    # ── 2. 单元格归一 ────────────────────────────────────────────────
    ck("去标签 + 解实体 + 压空白",
       rb._text("<p>  ก&amp;ข   ค </p>") == "ก&ข ค", rb._text("<p>  ก&amp;ข   ค </p>"))
    ck("DMS 的空值占位 - 归一成空串", rb._text("-") == "" and rb._text("<p>-</p>") == "")

    # ── 3. 列表解析：字段映射不能错位 ────────────────────────────────
    def row_markup(rid, cells):
        body = "".join("<p>%s</p>" % c for c in cells)
        return '<tr data-val="%s">%s</tr>' % (rid, body)

    good = ["", "ปกติ", "BK0001", "01/09/2569", "20/09/2569", "dmstest", "คุณสมชาย",
            "DMAX", "ขาว", "อนุมัติ", "จัดสรรแล้ว", "SO-1", "-"]
    short = ["", "ปกติ", "BK0002"]
    no_no = ["", "ปกติ", "", "01/09/2569", "20/09/2569", "dmstest", "คุณสมหญิง",
             "DMAX", "ดำ", "รอ", "-", "SO-2", "EN-2"]
    body = row_markup("101", good) + row_markup("102", short) + row_markup("103", no_no)
    rows = rb.parse_sales_rows(body)
    ck("只留完整行（列数不足 / 单号为空的行跳过）",
       len(rows) == 1 and rows[0]["id"] == "101", [r["id"] for r in rows])
    r = rows[0]
    ck("字段逐列对位（错一列就会把客户名显示成车型）",
       (r["booking_no"], r["booking_date"], r["delivery_date"], r["advisor"], r["customer"],
        r["vehicle"], r["color"], r["finance_status"], r["allocation_status"],
        r["sales_doc_no"], r["engine_no"])
       == ("BK0001", "01/09/2569", "20/09/2569", "dmstest", "คุณสมชาย", "DMAX", "ขาว",
           "อนุมัติ", "จัดสรรแล้ว", "SO-1", ""),
       {k: r[k] for k in ("booking_no", "customer", "vehicle", "engine_no")})
    ck("空列表也能解析（不炸）", rb.parse_sales_rows("") == [])

    # ── 4. 一页列表的读取：载荷、分页、失败关闭 ──────────────────────
    calls = []

    class FakeAdapter:
        base_url = "https://dms.example/dms/"

        def transport(self):
            raise AssertionError("读列表不该碰首页")

    class FakeClient:
        def __init__(self, body="", table=None):
            self.body = body
            self.table = table or {}
            self.posted = []

        def _post_text(self, path, data):
            self.posted.append((path, dict(data)))
            return self.table.get(data.get("status"), self.body)

    def fake_login(client, adapter=None):
        """替掉 `run_logged_in`：跑真回调，异常按线上口径收敛成 err dict。"""
        adapter = adapter if adapter is not None else FakeAdapter()

        def _run(endpoint, fn):
            try:
                return fn(client, adapter)
            except DmsClientError as exc:
                return {"ok": False, "error_code": exc.error_code}
        return _run

    real_login = rb.run_logged_in
    real_endpoint = rb._enabled_endpoint

    def use_endpoint(value):
        rb._enabled_endpoint = (lambda user_id: value) if not callable(value) else value

    try:
        use_endpoint({"id": "EP1", "name": "EP1"})
        three = "".join(row_markup(str(100 + i), good) for i in range(3))
        client = FakeClient(three)
        rb.run_logged_in = fake_login(client)
        got = rb.fetch_sales_records("u", field="customer", query="สมชาย", status="ready",
                                     limit=99, page=0)
        path, data = client.posted[-1]
        ck("取列表打的是原生 showdata.php", path == rb.LIST_PATH, path)
        ck("列/状态/关键词都翻成 DMS 的编码",
           data["selcolsort"] == "5" and data["ftd"] == "10" and data["sd"] == "สมชาย"
           and data["selcolsorttype"] == "2", data)
        ck("页大小夹在 1~30、页码至少 1", data["sdtamt"] == "30" and data["sdtpage"] == "1", data)
        ck("返回带上查询口径（界面据此回显）",
           got["ok"] and got["field"] == "customer" and got["status"] == "ready"
           and got["query"] == "สมชาย",
           {k: got.get(k) for k in ("field", "status", "query")})

        use_endpoint({"id": "EP1", "name": "EP1"})
        rb.run_logged_in = fake_login(FakeClient(three))
        got = rb.fetch_sales_records("u", limit=3)
        ck("拿满一页 → 认为还有下一页",
           got["has_more"] is True and len(got["rows"]) == 3, (len(got["rows"]), got["has_more"]))

        use_endpoint({"id": "EP1", "name": "EP1"})
        rb.run_logged_in = fake_login(FakeClient(three))
        got = rb.fetch_sales_records("u", limit=10)
        ck("没拿满 → 没有下一页", got["has_more"] is False, (len(got["rows"]), got["has_more"]))

        use_endpoint({"id": "EP1", "name": "EP1"})
        rb.run_logged_in = fake_login(FakeClient('err::"ไม่มีสิทธิ์"'))
        got = rb.fetch_sales_records("u")
        ck("DMS 回 err:: → 明确失败，**不当成数据**", got.get("ok") is False, got)

        use_endpoint(None)
        rb.run_logged_in = fake_login(FakeClient(three))
        ck("没有可用端点 → ERR_NO_CREDS（让上层说'没有可用账号'）",
           rb.fetch_sales_records("u").get("error_code") == "ERR_NO_CREDS")

        def boom(user_id):
            raise RuntimeError("端点数不出来")

        use_endpoint(boom)
        ck("端点解算自己炸了 → ERR_UNEXPECTED（不把异常抛给用户）",
           rb.fetch_sales_records("u").get("error_code") == "ERR_UNEXPECTED")

        # ── 5. 看板：首页取筛选条件 + 两次 POST ──────────────────────
        HOME = (
            '<input type="hidden" id="idusers" value="999">'
            '<select id="carmaxsellbranch">'
            '<option value="0" selected>ทั้งหมด</option>'
            '<option value="7">สาขา A</option><option value="7">สาขา A</option>'
            '<option value="8">สาขา B</option></select>'
            '<select id="carmaxsellteam">'
            '<option value="0">ทั้งหมด</option><option value="3">ทีม 1</option></select>'
        )

        class TopAdapter(FakeAdapter):
            def transport(self):
                class _T:
                    @staticmethod
                    def get(url, **kwargs):
                        return type("R", (), {"text": HOME})()
                return _T()

        # DMS 本页响应是**三层外层数组**：`[labels, ids, values]`（线上就是按三个变量解的）
        top = FakeClient(table={"all": "25", "page": '[["DMAX","HILUX"],["1","2"],[10,5]]'})
        use_endpoint({"id": "EP1", "name": "EP1"})
        rb.run_logged_in = fake_login(top, TopAdapter())
        got = rb.fetch_top_sales("u", group="type", metric="amount", limit=10, page=2)
        statuses = [d.get("status") for _, d in top.posted]
        ck("看板打两次（一次总数、一次本页）", statuses == ["all", "page"], statuses)
        p = top.posted[0][1]
        ck("用户 id / 分店 / team 从首页读回，占位 0 去掉、重复项合并",
           p["idusers"] == "999" and p["carmaxsellbranch"] == "7,8"
           and p["carmaxsellteam"] == "3", p)
        ck("分组/指标/页大小/页码都翻成编码",
           p["carmaxselltypedata"] == "2" and p["carmaxsellqnt"] == "2"
           and p["carmaxsellshow"] == "10" and p["carmaxsellpage"] == "2", p)
        ck("返回带回总数与是否有下一页（第 2 页 × 10 < 25）",
           got["ok"] and got["total"] == 25 and got["has_more"] is True, got.get("total"))
        ck("行是 label/value 对，编号串去重、名字列表照 DMS 原样（不含'全部'）",
           got["rows"] == [{"label": "DMAX", "value": 10}, {"label": "HILUX", "value": 5}]
           # ⚠️ 名字**不去重**（线上同款）：让 DMS 的 <option> 长什么样、回显就什么样，
           # 去重是 `_concrete_ids` 对**编号串**做的事（同一个 id 贴两次会推两次筛选）。
           and got["branches"] == ["สาขา A", "สาขา A", "สาขา B"]
           and got["teams"] == ["ทีม 1"],
           (got["rows"], got["branches"], got["teams"]))

        today = date.today()
        ck("没给期间 → 默认本月 1 号到今天，而且用**佛历**",
           p["txtcarmaxselldate_bg"] == "01/%02d/%d" % (today.month, today.year + 543)
           and p["txtcarmaxselldate_to"]
           == "%02d/%02d/%d" % (today.day, today.month, today.year + 543),
           (p["txtcarmaxselldate_bg"], p["txtcarmaxselldate_to"]))

        bad = FakeClient(table={"all": "25", "page": "不是 JSON"})
        use_endpoint({"id": "EP1", "name": "EP1"})
        rb.run_logged_in = fake_login(bad, TopAdapter())
        ck("本页不是合法 JSON → 明确失败（不拼半份表）",
           rb.fetch_top_sales("u").get("error_code") == "ERR_DMS_TECHNICAL")

        class NoFilterClient(FakeClient):
            pass

        class NoFilterAdapter(FakeAdapter):
            def transport(self):
                class _T:
                    @staticmethod
                    def get(url, **kwargs):
                        return type("R", (), {"text": ""})()
                return _T()

        use_endpoint({"id": "EP1", "name": "EP1"})
        rb.run_logged_in = fake_login(NoFilterClient(), NoFilterAdapter())
        ck("首页读不到筛选条件 → 明确失败（不拿空筛选去查）",
           rb.fetch_top_sales("u").get("ok") is False)
    finally:
        rb.run_logged_in = real_login
        rb._enabled_endpoint = real_endpoint

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
