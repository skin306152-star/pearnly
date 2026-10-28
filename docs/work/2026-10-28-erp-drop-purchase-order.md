# 删掉 ERP 采购单的「采购订单」类型（10-28）

> 分支 `pearnly/erp-drop-purchase-order`，基线 main `aa845e52`。主会话转用户原话：「采购订单删掉吧」。
> 用户的采购单设计就是「已付款 + 货已到」的进货单，没有先下单流程。

## 影响评估（按 `templates/impact-assessment.md`，只列有内容的项）

**0. 理解**：采购单「单据类型」去掉 `purchase_order`；已有的采购订单单改成进货单；打印第二批的采购订单格式与入口、相关词条一并删；
识别提示词不改，读出采购订单时在落地处映射。

**生产现状（10-28 只读，SSH 一次，用 erp-web 容器里的数据库客户端，没进 mariadb 容器）**：mrerp、testerp、polymer、gize 四个
ERP 站点 `doc_kind = purchase_order` 都是 0 张（gize / mrerp / polymer 采购单总数 0；testerp 10 张：采购发票 9、费用 1）。

**4. 上下游**（全仓查过 `purchase_order` 的调用方）：

| 地方 | 处理 |
| --- | --- |
| `purchasing/entry.py` `KINDS`、`_post` | 去掉 `purchase_order`；传进来的先过 `contracts.landing_kind`（旧接口、LINE / CC 桌面版不报错，落成进货单） |
| `purchasing/service.py` 建草稿 / 改单 | 同上过 `landing_kind` |
| `purchasing/recognition.py` `clean_result`（所有识别引擎的结果都经这里） | `doc_kind` 过 `landing_kind`；**提示词不改**（识别改动要过金标闸门） |
| `recognition/claude.py` 第 58 行、`purchasing/recognition.py` 提示词、core `claude_ocr/spec.py`、Cowork 识别合同 | 不动（识别侧；ERP 落地处已映射，Cowork 不涉及） |
| `express/contracts.PURCHASE_KINDS` | 本来就不含采购订单（采购订单原来不推 Express）；只改注释。映射后的单按进货单正常推 |
| DocType `Purchase Document.doc_kind` 选项 | 去掉 `purchase_order`，`modified` 前推 |
| 网页 `purchase-fields.js` 下拉与标签、`purchase-form.js` 打印入口 | 删 |
| `purchasing/output.py`（导出标签）、`integrations/line_cards.py`（LINE 卡片标签） | 删 |
| 打印：`Pearnly Purchase Order` 格式目录、`printing/context.py` 的 `ORDER` / `FORMAT_KINDS` / `_order` 与只为它存在的 `kind` 参数、`trade.html` 的供应商侧、`_labels.html` 只给它用的标题与栏位（需要日期、参考号、申请人、预扣税、应付净额、制单、供应商确认）、`printing/contracts.TITLE_KEYS` | 删；「付款条件」「审批」报价单还在用，留 |
| 第一批收货单模板里的「ใบสั่งซื้อ (PO No.)」一栏 | 留：那是岳父样板 1:1 的栏位（留空线手写） |
| 字段 `requester` / `requester_user` | 留：采购单通用字段（识别会读、单上可存），只是采购订单打印不再用 |
| 词条「Purchase Order」（ERP 术语表 + 四种语言翻译包） | 删（再无调用方） |
| 测试 / 实验脚本 | 打印两个测试去掉采购订单用例；`scripts/erp-purchase-lab-verify.py` 改用采购发票；新 `tests/test_erp_purchase_order_retired.py` 进 CI |

**9. 删除与迁移**：ERP 补丁 `v1_16_retire_purchase_order`：已有采购订单单改成 `RETIRED_KINDS` 给的类型（别的数据不动），
站点上的「Pearnly Purchase Order」打印格式记录删掉。重跑无副作用。

**12. 口径（主会话 10-28 定）**：映射成 `purchase_invoice`（采购发票）——与下拉、识别的默认值一致，打开单子显示「采购发票」；
补丁与识别落地都按这个（`purchasing/contracts.RETIRED_KINDS` 一处）。

**行为变化**：原来的采购订单**不推 Express**（`express/contracts.PURCHASE_KINDS` 不含它）；改成采购发票后会进 Express 推送
（新提交的单照常排队；补丁只改类型、不补排队，已提交的旧单不会被自动补推）。生产四个 ERP 站点采购订单 0 张，只影响测试机与以后的单。

## 验证（本机一次性实验栈 `pearnly-drop-lab`，线上同版镜像 fb1037ad）

先用 main `aa845e52` 的源码建 ERP 站点，经正式接口建一张已提交、一张草稿采购订单（打印格式记录在），再挂本分支迁移：

| 场景 | 结果 |
| --- | --- |
| 补丁 | 执行 `v1_16`；两张采购订单都成了 `purchase_invoice`（状态不变），打印格式记录删掉 |
| DocType 选项 | `purchase_invoice / expense / goods / service` |
| 旧接口 `entry.post` 传 `purchase_order` | 落成 `purchase_invoice`，不报错 |
| 建草稿 `service.create_purchase(doc_kind="purchase_order")` | 落成 `purchase_invoice` |
| 识别结果 `clean_result({"doc_kind": "purchase_order"})` | `purchase_invoice` |
