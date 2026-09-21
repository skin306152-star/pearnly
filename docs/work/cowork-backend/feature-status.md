# Cowork 后端迁移：功能清单与剩余量（2026-09-21 快照）

回答"还有多少没迁"。**按功能码给结论、给落点、给剩余项**；行数是**规模参考**，不是进度百分比——旧 monolith 里同一个模块常被多个产品线共用，行数不能直接换算成"完成度"。

口径：本表只覆盖 Cowork 后端（`product_manifest.py` 的 8 个功能码 + 已按用户要求移除的 `boards`）。DMS、ERP 是另外的 App/工作窗口；旧 monolith 里的 POS、工资、工单、管家、销售、库存、费用、会计等**不属于新产品范围**，见文末。

## 一、按功能码

| 功能码 | 旧侧规模（参考） | 新侧落点（文件·行） | 状态 | 剩余项（有记录可查） |
| --- | --- | --- | --- | --- |
| `intake` 录入工作台 | `services/ocr` 98·16,929；`importer` 9·1,451；`summary_import` 8·987；`excel` 8·1,494；`fileconv` 17·2,364；`intake_bridge` 8·1,493 | `ocr/` 52·5,163（引擎/提供方/校验/表引擎/表输入/执行/回读）；`roundtrip/` 13·1,433；`files/` 5·797；`intake.py` | **主体已迁** | 真实模型/网络调用经 `pearnly_core` 提供方；按批记录仍未迁的个别引擎档与运行编排见 `ocr-pipeline.md`、`table-*.md`、`roundtrip-*.md` |
| `ocr_history` 识别记录 | `services/ocr_history` 10·1,619 | `history/` 5·424；`ocr/execution/`（批次·工作·计费） | **已迁** | 无已知缺口（记录见 `invoice-execution.md`、`invoice-feedback.md`） |
| `push_log` 推送日志 | `services/erp/express_push` 29·5,683；`services/erp/push_*.py`、`shared_express_*`；`routes/erp_push_log_routes.py` 350 | `push/` 39·4,936（`native/` 账本、`express/` 25 个载荷与映射、`application/` API）；`contracts/push.py`；`history/push_state.py` | **主体已迁** | MR.ERP 网络适配器本体与真实推送不迁（`mrerp_http/`）；重试/死信运行侧见 `push-lifecycle.md`、`express-payload.md` |
| `recon` 对账中心 | `services/recon` 56·13,956；`recon_jobs` 6·1,551；`services/vat` 21·4,696；`routes/recon_*` 9·约 2,547 + `bank_recon_routes.py` 337 | `reconciliation/` 34·6,556 | **银行收尾中；收入完成** | 银行：run 的业务警告与锚点快照/覆盖缺存储（见 `reconciliation-family-audit.md`）；M2 发票存档族与 V1 通用对账未定；异步任务/轮询判为不需要原生副本 |
| `stocktake` 库存盘点 | `services/stocktake` 11·1,223；`routes/stocktake_routes.py` 176 | `stocktake/` 9·951 | **已迁** | 记录明确不迁的项（照片、LINE、库存记账）见 `stocktake-native.md`、`stocktake-export.md` |
| `master_data` 主数据 | `services/clients` 3·938；`services/products` 3·279；`routes/clients_routes.py` 306、`products_routes.py` 422、`categories_routes.py` 35、`tax_profile_routes.py` 411、`supplier_posting_routes.py` 98、`client_import_routes.py` 129、`settings_routes.py` 132 | `masters/` 3·214；`access/` 2·154 | **进行中（方向已定）** | 方向（用户定）：**客户以 ERP 为准；商品/供应商/科目各产品自己维护**。已交付两块：买家→客户的匹配与归属决策（`masters/buyer_resolve.py`，第四十三批，见 [masters-buyer-resolve](masters-buyer-resolve.md)）与客户档案的字段规则（`masters/client_records.py`，第四十四批，见 [masters-client-records](masters-client-records.md)）。第三块已交付：客户名册的存储与 RPC（`masters/clients.py` + `Cowork Client` 字段补全 + 新 `Cowork Buyer Memory` + 五个 RPC），第四十五批，见 [masters-client-roster](masters-client-roster.md)。仍缺：客户导入归一、商品与单位、供应商与科目/税率档、设置与分类；另有一处**跨域待决策**：识别记录归属的改挂/解绑要不要在 history 复核契约开口（现在删带票客户是拒绝的）|
| `companion` 小助手 | `routes/companion_installer_routes.py` 30；旧 `shared_express_*` 的机器侧 | `companion/` 16·1,664（凭据·心跳·目录·领取·回执·HTTP 鉴权·投递） | **服务端已迁** | Windows 程序本体不在本仓；客户端真机验收未做 |
| `express_guide` Express 指引 | 同上（目标/载荷/生命周期） | `targets/` 9·1,207；`connections/` 13·1,693；`push/express/` 25 | **后端已迁** | 指引文案属前端；`connections-native.md` 记录未迁的 MRERP 网络采集器 |
| `boards` 协作看板 | WeKan 集成 | — | **不迁**（用户指示） | 已迁部分在第二十二批删除，见 `collaboration-removal.md` |

## 二、不属于 Cowork（不迁，不是欠账）

旧 monolith 同一仓里还有这些产品线，各有独立 `services/*` 与 `routes/*`，不在新产品范围：

`pos*`（POS 与餐饮）、`payroll`、`workorder`、`steward`、`sales*`/`sales_agg`、`inventory`、`stockcard`、`expense`、`accounting*`、`ledger`、`tax`、`daily`、`report`/`reports`、`firm`、`front_desk`、`etax`、`rd`、`archive`、`automation`、`email_ingest`、`membership`、`team`、`users`/`auth*`/`admin_*`、`dms_*`/`line_dms*`（DMS 线）、`line_erp`/`erp_*`（ERP 线）。

两条**共享树**不能按行数切分，也不能算 Cowork 的欠账：

- `services/erp/`（200 文件·42,239 行）同时服务 ERP 产品与 Cowork 推送/连接/目标，其中 Cowork 部分已落到 `push/`、`connections/`、`targets/`、`masters/push_inputs.py`；其余属 ERP 侧（另一个窗口）。
- `services/billing`、`credits`、`usage`、`tenant`、`workspace` + `routes/billing_*`、`tenant_routes.py` 等是**新产品的控制面**，已由 `pearnly_control`（`billing*.py`、`provisioning.py`、`workspace` 等）与 `pearnly_core` 承担，不从旧侧搬。

## 三、粗结论

- 要迁的 8 个功能码里：**4 个已迁完**（`ocr_history`、`stocktake`、`companion`、`express_guide` 的后端）、**3 个主体已迁且有明确收尾项**（`intake`、`push_log`、`recon`）、**1 个明显未完成**（`master_data`）。`boards` 按指示不迁。
- 银行对账已收尾（银行 run 记录、两个读取器族、文件入口与 OCR 文档档位均已交付）；剩余量集中在**主数据**（方向已定，第一块已交付，见上表）。
- 不提供全仓完成百分比：旧 monolith 的大头属于不在范围的产品线，共享树也无法干净切分，给一个总百分比只会误导。

## 依据

本文的行数由固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b` 与本工作树现算（`find … | wc -l`）；功能状态取自 `docs/work/cowork-backend/*.md` 的各批记录与 `docs/work/2026-09-20-cowork-backend-migration.md`。本文只读，不改代码、不写库。
