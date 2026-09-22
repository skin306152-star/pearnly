# ERP 采购记录：接续与需求对齐

## 当前请求与状态

用户 2026-09-22 要求：接续 ERP 前端；图二的商品、库存、入库、出库、收发存报表、盘点已经做完且验收无问题；下一项是采购系统的采购记录，旧项目功能和字段不许减少或遗漏，允许新增，前端全部使用官方原生组件，先对齐需求再开工。

- **本轮为需求对齐与独立工作区准备，未修改业务代码。** 下方实现方案是待对齐稿，不能写成用户已逐项批准或采购已实现。
- 商品与库存六个入口的用户验收，以本次用户消息为依据。旧交接中的“用户未验收”被本次确认更新；不由此推断已部署或补做了设备测试。
- 原照片压缩、默认时区等历史待办不自动加入采购任务。
- 本任务正本为本文。此前盘点成果、环境和历史证据仍见 [ERP UI 交接](2026-09-22-erp-ui-handover.md) 与 [盘点工作台](2026-09-22-stocktake-workbench.md)。

## 实际基线与环境

| 项目 | 本轮只读核验结果 |
| --- | --- |
| 新工作树 / 分支 | `/Users/skin/.codex/worktrees/erp-purchase-records/pearnly` / `codex/erp-purchase-records` |
| 起点 | `8a67e7be65a43cffd7cc6d122d568ba64b99b369`（盘点交接 tip；代码止于 `db2d6f9`） |
| 本地主线 / 集成目标 | `main` = `717d202af3cfcd06815e215ef088e07f4251e00e`；是本任务基线祖先 |
| 共享 ADR-009 基线 | `db4c7dc` 是基线祖先，产品声明与词库拆分已进入本任务，不从旧布局续写 |
| 远端主线 | 实际 `git ls-remote` 回读 `refs/heads/main=ac33e0c7362915ae314295cb26f7748cb5589de0` |
| 原预览 | `stocktake.localhost:8136`；`colima-pearnly-stocktake` / `pearnly-stocktake-acceptance`；Web 挂载 **stocktake-workbench** 的五个 App |
| 原验收 | `stocktake.localhost:8126`；正式镜像 `sha256:051c475267e25a99e7f61ce64664182b8775c42a62ec35f3e80e65a5a8614645` |
| 数据 | 两个原环境共用 Site/数据库；本轮不切挂载、不重载、不改数据、不关停 |

采购新工作树尚未接入运行环境。后续启动前核对 Compose project、Site、DB/Site/Redis 卷和源代码挂载，不能直接在新工作树运行旧 `stocktake-preview.sh up/faithful` 接管原环境。

## 旧线上来源：本轮重新核验

- 采集时间：`2026-09-22T13:35:44.644290+00:00`（曼谷 20:35:44）。
- 项目 / 区域：GCP `pearnly` / `asia-southeast1`。
- Web：`pearnly-web-ec2e5c957e6b-s3`；Worker：`pearnly-worker-ec2e5c957e6b-s3`；均实际承接 100% 流量。
- 同一不可变镜像：`asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/app@sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。
- 完整源码：`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，经部署登记与 GitHub Manual CD `35321281376` 映射核对。
- [首次采集证据](erp-purchase-records/production-baseline-start.json)，信封校验和 `edd12e372d12bfea8abe88f39e35d73537ac6825d32bf93b0a1cf8703f2badfe`。采集器只读调用 Cloud Run / GitHub 元数据，不读取 Secret Manager 明文或调用业务接口。
- 来源仓 `/Users/skin/Developer/Pearnly/pearnly-app` 本轮干净。其本地 HEAD `9aa7a6b2` 不是生产 SHA；已用 `git show ec2e5c957e6b:...` 读取关键入口，并核对 `src/home`、`src/erp`、`routes`、`services/purchase`、`services/erp`、`static/dist` 相对生产 SHA 无差异。来源仓保持只读。
- 本轮只证明生产身份与源码合同，未在旧生产创建采购、付款、OCR 或 Google 归档。后续开始业务移植与交付前均按迁移规则复核来源。

## 范围与原生界面方案（对齐稿）

旧 ERP 与共用采购模块使用部分不同入口：

1. ERP 的采购列表复用 `src/home/purchase-list.ts`；“记一笔采购”转 `erp-intake`。手工录入复用 `src/erp/manual-document.ts` → 原采购表单，保存适配在 `pos-form-adapter.ts`。
2. 已确认单据进入 `showErpOrderDetail`，显示原表单只读态；它不会继续执行共用详情页的付款、更正等按钮渲染。
3. 共用采购模块和真实接口还具备付款、更正、凭证、附件与导出等能力。因此不能只截 ERP 已确认详情的一张图，就把其余采购能力当成不存在。

**建议按用户“不许少和漏、允许新增”的要求，将采购记录完整能力作为交付范围：旧 ERP 可见字段/功能全部保留，共用采购已有的相关能力逐项纳入；其 ERP 新入口标为增强。** 会计总账、税务申报、外部 ERP 推送不因读取到旧共用模块而自动成为本批产品范围。供应商选择/新建、分类和采购设置的必要依赖随本批接通；供应商、采购设置独立页面不自动整页重做。

- 页面结构：`采购记录列表 → 新建/草稿编辑/单据详情`，采用原生 Page、面包屑、列表与表单，记录集合不塞进 Dialog。
- 条件与表头：原生 FieldGroup / Link / Select / Date / Currency / Check 等字段。
- 单据明细：原生 Grid；只读明细、汇总按数据形状使用原生表格/报表。
- 付款、确认、作废等短操作：原生 Dialog、confirm、msgprint、show_alert。
- 附件：原生 FileUploader 和私有 File；预览优先复用框架能力。缩放/旋转等旧能力需要核验锁定 Frappe 的组件是否覆盖，不能靠删功能满足“原生”。若框架缺少能力，先记录具体差距，不自行重做通用组件。
- 保留既有品牌强调色与原生白底/深色文字；桌面与 390px 都列为验收场景。新增字段不得挤掉旧字段。

## 逐项功能与字段合同

下表为施工与验收清单，**不是已完成清单**。字段包含 UI、接口与持久化合同；“存在于接口/历史数据”不代表必须全部摊在列表上，但保存、重开、导出不能丢失。

| 编号 | 必须保留的能力/字段 | 旧源依据 | 新平台当前差距 / 落点 |
| --- | --- | --- | --- |
| P01 | 列表：日期、供应商、摘要、来源、业务类型/分类、含税金额、进项税额、草稿/已确认/已作废、未付/部分付/已付、附件数 | `purchase-list.ts`、`purchase-common.ts` | 当前仅供应商/对方单号/日期/单据状态/合计/净应付，需补齐投影 |
| P02 | 全部/进货/费用/未付；关键词搜索；票种、付款状态、分类多选筛选 | `purchase-list.ts`、`purchase-list-filters.ts` | 当前页面未接；关键词至少保留供应商与摘要，拟增加我方/对方单号 |
| P03 | 票面日期/上传日期口径；本月、上月、本季度、自定义起止日期；按月分组、折叠、月合计/条数 | 同上 | 当前无；过滤、分页、统计、导出必须统一取数范围，避免只过滤当前页 |
| P04 | 本月花费、进货额/笔数、费用额/笔数、可抵进项税、未付额/供应商数；未付快捷查看 | `purchase-list.ts`、`services/purchase/reports.py` | 现有汇总仅总额/净应付/未付，需恢复旧维度 |
| P05 | 单行/按月多选；仅草稿可批量删除；已确认记录不得当草稿删除 | `purchase-list.ts:bulkDelete`、`purchase_routes.py` | 当前是批量作废已提交，不能当旧批量删除已实现 |
| P06 | 新建手工录入、拍照/上传文件识别、草稿保存/继续编辑、确认、重试保留输入；来源追溯 | `erp-intake.ts`、`src/erp/record-form.ts`、`purchase-form.ts`、录入路由 | 现有采购页仅简表单；接既有录单域，相关缺口登记且补齐，不只放入口 |
| P07 | 我方自动编号 PE 与供应商单号/税票号分开；日期、账期/到期日 | `pos-form-adapter.ts`、`purchase-form-info.ts`、`DocIn` | 现有 `system_no` 已有，UI 未显示；`due_date` 已有但未提供 |
| P08 | 单据业务类型：`purchase_invoice` / `purchase_order` / `expense` | `purchase-common.ts`、`purchase-form-info.ts` | 新 `doc_kind=goods/service/expense` 与旧不等价；需兼容已有单据与调用方，不直接替换枚举丢历史 |
| P09 | 供应商选择/切换/新建；名称、税号、总公司/分公司/无分支、分支编号、分支名称、地址 | `purchase-form-info.ts`、`purchase-modals.ts`、`DocIn.supplier` | 现有采购页仅名字；供应商表缺 `none`，文档需保留票面快照；分支名旧为表单/适配信息，需保真回读 |
| P10 | 有无税票、单号必填联动、非本币必须填写有效汇率 | `validateInfo`、`purchase-form-state.ts` | 有 VAT 勾选但未完整联动；业务权威校验需在服务端 |
| P11 | THB/USD/EUR/CNY/JPY/SGD、记账汇率、单据付款状态；已有 requester/requester_user_id/approval_status 等数据合同 | `purchase-form-info.ts`、`DocIn` | 文档已有 currency/fx_rate/approval_status，UI 未接；申请人并非旧列表列，不凭旧设计图另造审批流 |
| P12 | 明细：商品/服务类型、商品关联/匹配状态、商品编码、条码、品名、数量、单位、单价、行折扣、行金额、VAT 税率、逐行 WHT 税率 | `purchase-line-types.ts`、`purchase-form-lines.ts`、`manual-document.ts` | `Purchase Line` 缺逐行 WHT；条码等投影与联动未齐。不能把表头类型代替行类型 |
| P13 | 商品搜索带回名称/条码/单位/默认成本/税率；匹配已有商品或新建；逐项录入/合并一条/切回恢复；增删行 | 同上、`purchase-modals.ts` | 保留已迁商品/单位体系；未配商品的入库应明确处理 |
| P14 | 行大类/子类与级联；停用分类仍保留历史选中值；历史 batch_no/expiry_date 等接口/存储字段 | 共用 `purchase-form-lines.ts`、`services/purchase/docs.py`、`correct.py` | 旧 ERP 方向隐藏分类输入，共用表单可见；作为补齐项登记；现有行无这些数据字段 |
| P15 | 价内/价外；小计、折扣、VAT、WHT、凑整、含税合计、净应付；手动按票面改小计/折扣/VAT/合计及一致性校验 | `purchase-form-lines.ts`、`purchase-form-totals.ts`、`DocIn.amount_override` | 当前页面无价内外/票面调整；金额字段虽部分存在，不能算端到端完成 |
| P16 | 原图/附件上传和查看；多张/多页切换、缩略图、放大、缩放、旋转、复位；PDF/图片；票图/付款凭证/替代收据/WHT 凭证分类、受权访问 | `purchase-form-bill.ts`、`purchase_routes.py` | 当前采购 UI/域未迁完整附件；原生上传与私有读取必须验证，不能用盘点照片规则替代采购规则 |
| P17 | 列表快捷已付/未付；付款金额、方式、日期、备注、多次付款、未付余额、部分付款；方式含 cash/transfer/promptpay | `purchase-list.ts`、`purchase-modals.ts`、`PayIn` | API 有但采购页面无；付款子表 enum 是 cash/bank/cheque/other，需兼容旧方式且不丢现有方式 |
| P18 | 草稿编辑；已确认只读；作废及已入库回冲；更正时作废原单并复制新草稿、保留附件与关联 | `purchase-detail.ts`、`services/purchase/correct.py`、`posting.py` | 旧 ERP 已确认详情只读；共用更正/付款入口作为新增 ERP 能力。当前无更正；当前禁止已付款单直接作废，需对齐实际完整冲回语义，不能直接移除保护 |
| P19 | 自动/明确入库条件、商品关联、库存单位/数量/成本、同单只入一次、作废只回冲一次、回读库存状态 | `services/purchase/posting.py`、ERP 录单链 | 复用现有 `purchasing.service._post_stock` → `inventory.stock`；保留本仓已验收仓库/库位能力 |
| P20 | 生成/下载替代收据、WHT 凭证；资格条件/权限/附件归属 | `purchase_routes.py`、`services/purchase/documents.py` | 当前未迁；表单里有按钮不等于旧 ERP 每个只读态可执行，需按真实前置条件落地 |
| P21 | Excel 导出、Google Drive 归档、Google Sheets 同步；日期范围、四语列头、未连接引导、任务进度/失败/结果链接 | `purchase-export.ts`、`routes/export_routes.py`、`services/export/` | 三者均有旧真实接口。新平台不是只加导出按钮；Google 连接与队列是依赖项，未接通不能标完整迁移 |
| P22 | 租户/公司隔离、员工取数范围、功能/岗位权限、去重、重复提交、失败回滚、私有附件 | `purchase_common.py`、`services/purchase/member_scope.py`、采购/导出路由 | 复用本仓 access.guard 与原生权限；列表/详情/导出/附件/写操作逐个核对 |

其中 P08、P12、P15、P17、P18 是数据/行为合同差异，必须连同必要后端、DocType 原生迁移与调用方一起处理，不能只修 JS。

## 已确认的旧交接误差

- 旧交接说“采购记录没有搜索/筛选（旧页也没有）”。固定生产源码明确含关键词、日期、文档类型、付款状态、分类与日期口径切换；该句不能作为减项依据。
- 旧新平台 `goods/service/expense` 不是旧单据分类；goods/service 是行级商品/服务类型。
- 旧文档的“附件和凭证留采购设置后续”不能覆盖本次“采购记录功能字段不缺漏”的要求。它们仍是本任务依赖/待办，不能算已完成。
- 共用采购详情、ERP 只读详情、接口实际能力三者要分开登记，不把共用详情按钮误称旧 ERP 已可见，也不因 ERP 没展示便删去数据合同。

## 实现归属与推进顺序

- 所属 App：`pearnly_erp_portal`；业务域 `purchasing/`。记录入口 `pearnly-purchase`，持久化 `Purchase Document`、`Purchase Line`、付款、供应商及必要新增 DocType。
- 产品声明/词条只维护 ERP 的 `hooks.py`、`features.json`、`i18n/messages.tsv`。根 `purchase.py` 继续作为薄兼容入口；共用层不保存第二份采购业务。
- 依赖方向：采购入口 → 采购业务与纯金额合同 → 商品/供应商/库存公开入口；录单域调用采购建单，附件/导出遵守记录权限。共享 Google/OCR/组件若需修改，先核对 Cowork 窗口归属。
- 建议实施顺序：① 数据合同/旧值兼容/权限；② 原生列表、筛选与统计；③ 完整表单/详情/附件；④ 付款、更正、库存联动；⑤ 导出与必要录单/Google 接口；⑥ 逐项验收与差异收口。顺序不是删减范围，前几步通过不代表采购整体完成。
- 允许新增的建议：我方/对方单号搜索、准确分页、可直达详情地址、明确入库/付款状态、失败保留输入。普通实现选择自行决定，不为填 UI 扩建无关模块。

## 验收约定

每项保留“旧固定源码 → 新字段/入口 → 场景 → 实际结果”的证据。至少覆盖：

1. 所有旧字段：输入 → 保存 → 重开 → 详情 → 导出，值不丢；已有数据升级不被默认值覆盖。
2. 采购票/采购单/费用、商品/服务混合行、币种汇率、含税/未税、行折扣/WHT、票面覆盖与校验失败。
3. 手工/上传/录单进入同一记录事实源；重复文件/重复单号/重复确认不重复建单或入库。
4. 全部筛选组合、票面/上传日期、跨页选择、汇总与导出范围一致；空、加载、失败、无权限状态。
5. 草稿删改、提交只读、多次/部分/全额付款、超额拒绝、更正、作废、已付款/已入库单据完整处理。
6. 商品/库存/收发存相关回归；不把已验收模块无关页面重新设计一遍。
7. 附件越权拒绝、原图/PDF 回读、导出文件实际打开检查；不能只看 HTTP 200。
8. 架构检查、翻译生成与前端词条检查、受影响 Python/Node/Site 测试、桌面/390px 浏览器；原生正式镜像验收与真机/外部业务验收分层记录。

真实付费 OCR、发 LINE、Google 写入等外部结果不冒充本地验证。先完成可本地验证的实现与具体样例，再按用户已给授权安排必要外部操作。

## 本轮验证与收尾

| 层级 | 当前结果 |
| --- | --- |
| 需求调查 | 已核对交接、固定生产源码、真实接口与新平台字段差异；范围对齐稿完成 |
| 业务实现 | 未开始，不计为采购完工 |
| 本地检查 | 本轮只做文档、证据 JSON 与 Git 状态检查；不重跑旧业务全套 |
| 用户验收 | 商品与库存六项为本次用户确认；采购尚未验收 |
| 合并 / 推送 / 部署 | 未执行；本任务继续保留在独立分支 |
| 外部业务 | 未创建订单、付款、OCR、LINE 或 Google 文件 |

收尾状态：**交接保留 / 等待需求对齐后实施**。

- 新工作树/分支归本 ERP 采购任务，用于保存本对齐稿、固定来源证据和后续实现；下一轮开工时复核基线与旧线上身份，任务完成且成果按授权合入主线后清理。
- 原 `stocktake-workbench` 工作树/分支与两条 lane 仍归已验收 ERP 前端接续任务，当前有运行挂载和未合入成果，保持原样。采购环境隔离安排确定后再核查原资源用途，不自动删除样本数据。
- 未创建本任务容器、卷、业务样本或长期进程；远端任务分支未创建。临时采集文件移入上述证据路径并校验后删除原临时副本。
- 当前主线与远端 SHA 见“实际基线与环境”，不称全仓已统一。
