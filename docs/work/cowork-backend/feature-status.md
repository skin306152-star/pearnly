# Cowork 后端迁移：功能清单与剩余量（2026-09-22，第五十一批更新）

回答"还有多少没迁"。**按功能码给结论、给落点、给剩余项**；行数是**规模参考**，不是进度百分比——旧 monolith 里同一个模块常被多个产品线共用，行数不能直接换算成"完成度"。

口径：本表只覆盖 Cowork 后端（`product_manifest.py` 的 8 个功能码 + 已按用户要求移除的 `boards`）。DMS、ERP 是另外的 App/工作窗口；旧 monolith 里的 POS、工资、工单、管家、销售、库存、费用、会计等**不属于新产品范围**，见文末。

文件/行数沿用早期规模快照，不当作第五十一批实时统计；功能状态以本次交接为准。
实现与验证均为本地后端层，不代表已合并、部署、接前端或业务验收。

## 一、按功能码

| 功能码 | 旧侧规模（参考） | 新侧落点（文件·行） | 状态 | 剩余项（有记录可查） |
| --- | --- | --- | --- | --- |
| `intake` 录入工作台 | `services/ocr` 98·16,929；`importer` 9·1,451；`summary_import` 8·987；`excel` 8·1,494；`fileconv` 17·2,364；`intake_bridge` 8·1,493 | `ocr/` 52·5,163（引擎/提供方/校验/表引擎/表输入/执行/回读）；`roundtrip/` 13·1,433；`files/` 5·797；`intake.py` | **主体已迁** | 真实模型/网络调用经 `pearnly_core` 提供方；按批记录仍未迁的个别引擎档与运行编排见 `ocr-pipeline.md`、`table-*.md`、`roundtrip-*.md` |
| `ocr_history` 识别记录 | `services/ocr_history` 10·1,619 | `history/`、`contracts/duplicates.py`、`contracts/classification.py`；`ocr/execution/`（批次·工作·计费） | **后端已迁** | 第五十批重复提醒与个人开关；第五十一批分类规则、公司科目树与隔离的复核记忆已补齐，见 [分类业务链](invoice-classification.md)。未接前端；归属改挂/解绑仍为跨域待决策 |
| `push_log` 推送日志 | `services/erp/express_push` 29·5,683；`services/erp/push_*.py`、`shared_express_*`；`routes/erp_push_log_routes.py` 350 | `push/` 39·4,936（`native/` 账本、`express/` 25 个载荷与映射、`application/` API）；`contracts/push.py`；`history/push_state.py` | **主体已迁** | MR.ERP 网络适配器本体与真实推送不迁（`mrerp_http/`）；重试/死信运行侧见 `push-lifecycle.md`、`express-payload.md` |
| `recon` 对账中心 | `services/recon` 56·13,956；`recon_jobs` 6·1,551；`services/vat` 21·4,696；`routes/recon_*` 9·约 2,547 + `bank_recon_routes.py` 337 | `reconciliation/` 34·6,556 | **银行与收入两族闭合** | 银行 run、读取器、文件入口与文档档位已交付；剩余可选 OCR 流程件见总交接 P3，族边界见 `reconciliation-family-audit.md` |
| `stocktake` 库存盘点 | `services/stocktake` 11·1,223；`routes/stocktake_routes.py` 176 | `stocktake/` 9·951 | **已迁** | 记录明确不迁的项（照片、LINE、库存记账）见 `stocktake-native.md`、`stocktake-export.md` |
| `master_data` 主数据 | `services/clients` 3·938；`services/products` 3·279；客户、商品、分类、税务画像、过账、导入与设置路由 | `masters/`、`access/` 与原生客户/商品/供应商过账 DocType | **主体已迁，账套导入有缺口** | 七块历史记录见 [买家归属](masters-buyer-resolve.md)、[客户规则](masters-client-records.md)、[客户名册](masters-client-roster.md)、[商品规则](masters-product-rules.md)、[商品存储](masters-product-store.md)、[导入归一](masters-client-import.md)、[过账档案与导入端点](masters-supplier-posting.md)。第五十一批确认：已有导入落到买方，不能算账套主体导入完成；现有 RPC 不静默改义。纯展示分类与供应商列表名称不扩建，M1 闸属于未迁工单分拣。业务分类树/记忆已归 history 交付；改挂/解绑仍待决定。客户以 ERP 为准，商品/供应商/科目各产品维护，见 [最新归属核对](invoice-classification.md) |
| `companion` 小助手 | `routes/companion_installer_routes.py` 30；旧 `shared_express_*` 的机器侧 | `companion/` 16·1,664（凭据·心跳·目录·领取·回执·HTTP 鉴权·投递） | **服务端已迁** | Windows 程序本体不在本仓；客户端真机验收未做 |
| `express_guide` Express 指引 | 同上（目标/载荷/生命周期） | `targets/` 9·1,207；`connections/` 13·1,693；`push/express/` 25 | **后端已迁** | 指引文案属前端；`connections-native.md` 记录未迁的 MRERP 网络采集器 |
| `boards` 协作看板 | WeKan 集成 | — | **不迁**（用户指示） | 已迁部分在第二十二批删除，见 `collaboration-removal.md` |

## 二、不属于 Cowork（不迁，不是欠账）

旧 monolith 同一仓里还有这些产品线，各有独立 `services/*` 与 `routes/*`，不在新产品范围：

`pos*`（POS 与餐饮）、`payroll`、`workorder`、`steward`、`sales*`/`sales_agg`、`inventory`、`stockcard`、`expense`、`accounting*`、`ledger`、`tax`、`daily`、`report`/`reports`、`firm`、`front_desk`、`etax`、`rd`、`archive`、`automation`、`email_ingest`、`membership`、`team`、`users`/`auth*`/`admin_*`、`dms_*`/`line_dms*`（DMS 线）、`line_erp`/`erp_*`（ERP 线）。

两条**共享树**不能按行数切分，也不能算 Cowork 的欠账：

- `services/erp/`（200 文件·42,239 行）同时服务 ERP 产品与 Cowork 推送/连接/目标，其中 Cowork 部分已落到 `push/`、`connections/`、`targets/`、`masters/push_inputs.py`；其余属 ERP 侧（另一个窗口）。
- `services/billing`、`credits`、`usage`、`tenant`、`workspace` + `routes/billing_*`、`tenant_routes.py` 等是**新产品的控制面**，已由 `pearnly_control`（`billing*.py`、`provisioning.py`、`workspace` 等）与 `pearnly_core` 承担，不从旧侧搬。

用户第五十批明确“只用于展示的就不要了”：不迁 `category_tag` 展示标签、分类补全列表、
供应商映射数量及分类分布统计。这不包含会影响 Express 入账科目的 `fields.category` 和科目映射。
第五十一批已承接有业务依赖的自动分类树与供应商分类记忆，不新增上述展示能力。
供应商主档名称富化不迁；M1 泰文名闸只在账套主体/工单分拣，不对买方补闸。

## 三、粗结论

- 识别记录、盘点、小助手服务端和 Express 指引后端已迁；银行与收入对账两族已闭合。
  录入、推送与主数据主体已迁，但仍有上表和总交接中明确列出的剩余范围。`boards` 按指示不迁。
- 主数据七块已有交付记录，但第五十一批核出导入主体错位：现有买方适配不等于旧账套导入。
  重复提醒与分类业务链已补齐；下一块是账套主体导入，跨域改挂仍待决定，不为纯展示扩建。
- 不提供全仓完成百分比：旧 monolith 的大头属于不在范围的产品线，共享树也无法干净切分，给一个总百分比只会误导。

## 依据

本文的历史规模来自固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`
与当时工作树的文件/行统计；本次没有重新统计。功能状态取自各批记录与总交接文档。
本文是范围记录，修改本表不代表对应功能已部署或验收。
