# CC 工具编码保真（11-15，挡上线缺陷，分支 `pearnly/cc-code-fidelity`，基线 `origin/pearnly/integration-1115` dc86d2d26）

## 现象（真机窗口 11-15）
ERP /assistant（账套 69EXP\TEST）对话录收款「客户 นาย ทดสอบ …」→ 查客户、发票、存草稿、写入计划都对 → 点「写入」后 CC 桌面版拒：
`CUSTOMER_NOT_IN_ARMAS · customer 'นาย ทดสอบ' is not in Express`。草稿 COL261115-78F7（source=cc）存的是普通空格，Express 里是
`นาย\xa0ทดสอบ`；同一客户 LINE / 网页那两笔原样带 \xa0（RE0000021 / RE0000022）。CC 随后叫用户「先在 Express 里建立这个客户」。

## 根因（两处叠加，都已实测）
1. **模型复述编码时把不换行空格换成普通空格**：真模型最小实验（opus-5-5，工具结果给 `นาย\xa0ทดสอบ`，让它接着调查发票工具），3/3 回普通空格（≈0.02 美元）。
   我们的代码路径没有空白归一化（工具只 strip 首尾，`toolrun` 原样 JSON 存取）。LINE / 网页对，是因为编码来自按钮 / 下拉，不经模型复述。
2. **服务端照单全收**：库是 `utf8mb4_unicode_ci`，实验栈真 MariaDB 实测 `code = 'นาย ทดสอบ'` 同时命中 `…C2A0…`（不换行空格）与 `…E38080…`（全角空格）两行。
   `_customer_name` / `snapshot.rows` 按等号「查得到」，`create_draft` 却存了模型的串；CC 桌面版写 Express 按原样认编码，就拒。
   同一原因下，只差空白的两位客户还会互相串发票（快照按客户取行）。
3. 「去 Express 建客户」：放行 / 状态结果只带原始原因串（`[CUSTOMER_NOT_IN_ARMAS] … not in Express`），没有给模型的定式说明，模型自由发挥。

## 修法（一处共用、各工具接入）
- 纯契约 `pearnly_core/express_rules/contracts/codes.py`：`key`（NFKC、去不可见字符、各种空白并一、去首尾，不折大小写）、`like_pattern`（各段间放 `%` 取候选）、
  `resolve`（先逐字；不中按 `key` 等价，唯一一个回主档原样；几个回 `several`、没有回 `none`）、`choose`（候选在手时的简写，认不出回原串，调用方照旧拒）。
- 框架侧 `pearnly_core/assistant/codes.py`：`canonical(doctype, field, given, filters)`（`like` 取候选、Python 逐字判，不信库的比较）、`unmatched`（给模型的「认不出」结果：
  原因码 `code_not_matched`、像的候选、说明「不说不存在、不叫人去 Express 建、用 ask_user 让人从候选里选或停止」）。
- 不透明键（工具结果给键、后续只收键）没采用：要改所有工具出入参与提示，改动大；回查在服务端一处判，覆盖同样全。

## 覆盖表（工具 × 参数 × 修前 → 修后）
普查范围：ERP 与 Cowork 全部 CC 工具（`assistant/tools.py`、`express_operator/tools.py`、ERP `collection` / `express` / `inventory` / `masters` 的 `cc_tools.py`、Cowork `assistant/tools.py` / `company_tools.py`）。

| 工具 × 参数 | 修前 | 修后 |
|---|---|---|
| erp_collection_draft `customer`（新建） | 库等号查得到、**存模型原串**进草稿与 RE 载荷 | `service.customer_code` 换回账本客户原样；认不出 / 几个只差空白 → `code_not_matched` + 候选 |
| erp_collection_invoices `customer` | 原串发给电脑、回给模型 | 同上；结果里 `customer` 是原样编码 |
| 快照按客户取发票（`snapshot.rows`） | 库等号，只差空白的两位客户串发票 | 取回后按原样逐字过滤 |
| erp_collection_draft `invoices[].docnum` | 快照键逐字比，空白变了报「找不到发票」 | 先 `codes.choose` 换回快照原样（只给单号按总额分配那条路同样） |
| erp_collection_draft `methods[].prefix`、erp_collection_voucher `prefix` | 逐字比 | 先换回目录 / 草稿里的原样 |
| erp_collection_draft `salesman` / `department` | 逐字比集合 | 先换回主档原样 |
| pearnly_precheck_answer `answers[].value`（共用层 `write_precheck.contracts.expand`） | 有选项的逐字比 | 先换回选项原样（ERP、Cowork 同一处） |
| ERP 预选卡保存 `express.precheck.save` / `save_mrerp` | 逐字比 | 先换回选项原样 |
| Cowork 预选卡保存 `push.application.service`（对方、物料、科目、部门、仓…） | 逐字比 | 先换回主档原样 |
| cowork_company_choice_save `value` | 主档编码先 strip 再比（首尾不换行空格的编码会被改） | 主档编码保原样，`company_choices.canonical` 换回原样再核再存 |
| cowork_save_folder_task(s) `defaults.account` / `department` | 逐字比 | `folder_intake.rules._faithful` 先换回科目表 / 部门原样 |
| cowork_mrerp_setup `defaults{}`（`mrerp.masters.choose`） | 逐字比 | 先换回读到的原样 |
| erp_stocktake_count `location`（`inventory.positions.check`） | 逐字比库位清单 | 先换回清单原样 |
| erp_collection_push / status / card：写前被拒退回草稿、写失败 | 只给原始原因串，模型自由发挥成「去 Express 建客户」 | 带定式说明 `REFUSED_NOTE`：一句话说清 Express 没收、用 ask_user 给「重新选客户 / 发票改草稿」或「停止」，不叫人去 Express 改或建、不猜原因 |

不用改的：只收不透明键的（`document` / `plan` / `collection` + `version` / 各种记录 id / 公司 / 连接 / 账套键）；本来就换成主档值的（盘点 `code`、`warehouse`，
对方单号 `cowork_company_choice_save document`）；科目前缀正则不放行空白（`cowork_express_readback accounts`）；`stock_groups.acccod`（数字组码）；
`*_resolve_push_result docnum`（用户报的票面号，不是主档编码）。主档没读回（列表为 None）时照旧不核、存原样——没有候选可换，属既有口径，不在本次。

## 验证
- 单元：`tests/test_express_rules_codes.py`（13 条：不换行 / 首尾 / 连续 / 全角空格 / 全角字母、逐字优先、只差空白的几件交人选、不折大小写）；
  `test_erp_collection_cc.CodeFidelity`（5 条：五种写法查发票与建草稿都落成原样，放行后写入载荷 `customer` 是 `นาย\xa0ทดสอบ`、单号多空白换回；两位只差空白
  的客户回 `code_not_matched` + 两个候选；逐字有的那位不列另一位的发票；不认识的不说「不存在」；写前被拒 `CUSTOMER_NOT_IN_ARMAS` 带定式说明）；
  `test_write_precheck`（预选卡答案换回原样）、`test_cowork_mrerp_masters`（MR.ERP 默认值换回原样）。替身库的等号 / like 按 `unicode_ci` 近似（不换行空格 = 空格、不分大小写）。
- 实验栈真 MariaDB：排序规则与 `like` 回查模式（见根因 2）。
- 没测到：真 CC 桌面版写 Express（实验室没有）；真模型整条对话（只做了复述实验）。真机复验请真机窗口按原句再走一遍（草稿 78F7 留作样本未动）。

## 追加（主会话 11-15）：刚开的发票被说成「已收清」
- 现象：发票 IV691115-003 刚开、还没进未收发票快照，CC 说「已经收清……请在 Express 里核对」；用户说「刚开的，请重新读一次」才读到未收 572.45、正常写入。LINE 同场景回「正在读取 Express」+ 刷新。
- 根因：`erp_collection_invoices` 回的是上次快照 + 一个 ``refresh: reading``，附注只说「过一会再查」，模型照列表下结论；`erp_collection_draft` 的发票不在快照时直接报
  `INVOICE_NOT_FOUND`，附「This invoice is no longer open」，没有先读回。CC 桌面版读回只报未收清的发票，所以就算读到了也分不出「收清了」与「账套里没有这张」。
- 修法（`collection/cc_tools.py`）：
  - 查发票先按客户即时读一次（已有 `express_ar_open` 按需请求），能等的地方短等 8 秒（每秒看一次，照 LINE：读不到就如实说还在读）；结果带 `read` 四选一 + 给模型的定式说明：
    `ready`（刚读回，列表就是此刻 Express 里的未收发票；不在列表的只能说「此刻不是这位客户的未收发票」，不猜是收清还是没有、不叫人去 Express 核对）、
    `reading`（还在读，列表是上次的，不许说收清 / 没有 / 不是未收）、`offline`（电脑未连接，可重试）、`outdated`（电脑要更新）；说不清的一律按 `reading`。
  - 建草稿时发票不在快照：先同样读一次；读到了就照原样再建一次（刚开的发票直接成），仍没有则回 `not_saved` + `INVOICE_NOT_FOUND` + 同一个 `read` 状态与说明，不带「no longer open」那句。
  - 快照按客户对齐（`snapshot.replace`）也改按原样比客户，免得删掉只差空白的另一位客户的发票行。
- 测试 `test_erp_collection_cc.ReadBack`：快照为空（新发票读到后建成）、快照过期（已收清的读回后说「此刻不是未收」）、读回超时（只说还在读，之后再读到照常建）、电脑未连接。
- 「三种状态」里「已读回、确实收清」与「已读回、账套里没有这张」现在分不开：要分得让 CC 桌面版多报「按单号查到、余额 0」的行，属 CC 桌面版改动，记下一车。

### 同类「没读到就当没有」按类过了一遍（本车只修收款发票，其余记下一车）
| 工具 | 依赖什么 | 风险 | 处理 |
|---|---|---|---|
| erp_collection_customers | ERP 客户主档（从 Express 主档同步） | Express 里刚建的客户同步前搜不到，模型可能说「没有这位客户」 | 下一车：搜不到时请电脑重读主档（`express.masters.ensure` / 已有的 master_refresh）并回读取状态 |
| erp_collection_methods | 账套资料 `facts`（收款类型） | 没读过时已抛「正在读取」 | 已正确，不改 |
| erp_collection_status / card / list、erp_list_documents / erp_document_detail | ERP 自己的记录 | 不依赖读回 | 不属此类 |
| erp_stocktake_* / erp_stock_card_export | ERP 自己的库存 | 不依赖读回 | 不属此类 |
| Cowork 预选卡与公司选择（`reported_*` 主档） | 账套主档读回 | 主档为 None 时「不核、照存」，不会说成「没有」；为空列表时会拒 | 下一车核：空列表与「没读过」是否混了 |
| cowork_express_readback / _status | 显式读回任务 | 有自己的状态 | 不改 |
| pearnly_tax_id_lookup | 税务局查询 | 查询失败与「查无此号」是否分开未核 | 下一车核 |
