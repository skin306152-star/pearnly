# MR.ERP 页面夹具（离线测试用）

2026-09-28 由「DMS产品视频脚本」窗口从 MR.ERP 测试公司（TEST2019）抓取，用于
`tests/test_cowork_mrerp_menus.py`：三个导入页（菜单下拉与 `data-chklac`）、已关联菜单 68 的列表页 /
回读结果（有一行、没有数据）、未关联菜单 67 的列表页（「กรุณากำหนดการเชื่อมโยง "สมุดรายวัน"」）与在它下面查到的无前缀旧单。

入库前已处理：删去抓取时注入的模糊样式；内部用户号改成 1、登录名改成 demo01 / demo02、公司编号清零、
真实供应商名换成「บริษัท ผู้ขาย ตัวอย่าง จำกัด」。不含会话 cookie、密码或令牌。列表页是浏览器渲染后的 HTML
（含前端追加的行），不是服务器原样。

`raw-0928.json`：同日由该窗口在用户重新登录的 test01 会话里只读抓取的**服务器原样响应**（三个导入页、68 / 67 / 118 / 125 / 126
列表页与几次 showdata 查询；只有 GET 与列表查询 POST，没有上传或导入）。同样打码：列表行对方名称换成占位名、URA 与页脚登录名、
内部用户号、公司编号。

`golden-rules.json`：MR.ERP 规则下沉共用层（2026-11-02，评估 §13 第 5 步）**搬移前**的代码在生产镜像里生成的黄金样本
（`python tests/test_mrerp_rules_golden.py --write`），给 `tests/test_mrerp_rules_golden.py` 比对：导入表的成员级与整份字节摘要、
推送全程发出的请求与回执。输入全是合成值（假 MR.ERP 站点、合成税号与公司名），不含任何真实数据。

`operator-draft-golden.json`：MR.ERP 代录要填的值（`pearnly_core.mrerp.push.operator_draft.block`，ADR-026 第 3 步）的黄金样本，
四种单（赊购货品、赊购费用、赊销价内、现销散客带折扣）各一份：固定输入（票面与 `ready_rows` 之后的 mappings）与固定输出的 `mrerp` 块，
由 `python3 tests/test_mrerp_operator_draft.py --write` 生成，`tests/test_mrerp_operator_draft.py` 逐字符比对（含键的先后）。CC 第 2 步
（`mrerp_operator/draft.py`）那一侧的契约测试读同一份样本的 `block`。输入全是合成值，不含任何真实数据或密码。

`export_customers.xlsx` / `export_suppliers.xlsx` / `export_products.xlsx` / `report_stock_by_store.xlsx`：MR.ERP 自带导出页
（`exparmas` / `expapmas` / `expstkmas` 的 `component/expdata.php`）与「สินค้าคงเหลือ:แยกคลัง」报表 241 的原样输出，
2026-11-14 从我们专属的测试账套 test01 · TEST2019 只读取得（全是测试单留下的测试档，不含客户数据），给
`tests/test_mrerp_master_export.py` 核对整读主档的解析。
