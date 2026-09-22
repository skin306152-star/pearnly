# ERP 采购记录实施与验收记录

## 已对齐的实施要求（本轮最新用户指示，优先于下方调查稿）

- **后续用户纠偏已实施**：提供的 Paypers 收入截图仅作桌面/手机布局参考，不授权收入、AI 提示、客户管理或其他产品能力。官方原生组件用于组合采购自己的页面，不照搬 ERPNext 采购发票表单。
- 采购首屏与已验收商品/库存共用原生 page-level List：搜索、筛选按钮、新建；导出收在菜单。删除展开的“过滤条件 / 更多筛选 / 概要”、统计区和列配置入口；后台查询/汇总与导出合同仍保留。
- 录入和详情改为原生 Tab Break：附件、详细信息、物料。明细用原生可折叠 Section + FieldGroup，桌面两列、手机纵向填写，不暴露通用 Grid 的设置/导入等动作。内部字段不展示；自动建商品/入库作为新单默认行为，历史值仍沿用原记录。
- 此前整张长表单的浏览器通过只证明当时操作可用，**该布局已被用户否定**；本节及本轮重排证据替代旧布局结论。当前重排仍待用户验收。

- 用户进一步明确：不扩建产品没有的功能和入口；框架自带按钮也必须有真实产品功能、权限及配置承接。OCR 仅在已配置时展示；LINE 仅在有本人可处理单据时展示；Link 禁止跳出到通用新建/高级搜索入口。
- 其余范围已确认，开始实施。字段名称采用 Frappe/ERPNext 官方术语；不照搬旧文案，不增加无业务用途的说明。
- 新采购录入不要求保存草稿；填写后直接提交并过账。默认已付款、自动创建未匹配商品、自动入库；在一个事务内完成，失败不留下半成品。
- 原有草稿/历史单据保留兼容，不删除业务数据；“无草稿步骤”约束新录入流程。
- 取消 Google Drive 归档与 Google Sheets 同步，采购仅保留 Excel 导出。已全仓检索当前新平台：采购对应接口、队列与连接实现尚未迁入，不能虚称删除了不存在的后端；从迁移范围删除且不再引入。来源旧生产仓仍只读，本次不部署或修改旧生产。
- 单据字段、付款、更正、附件/凭证、商品和库存联动等其余已对齐范围继续完成。


## 当前状态与归属

- 采购列表、录入、详情、付款、更正、作废、附件、凭证、导出与旧字段合同已实现；本地 Site 和浏览器验证见下方。采购尚未经用户验收，不能代签。
- 商品/库存六页的用户验收来自本次用户确认；没有重做其页面，也没有修改原 8136 / 8126 的容器、挂载或数据。
- 工作树 `/Users/skin/.codex/worktrees/erp-purchase-records/pearnly`，分支 `codex/erp-purchase-records`；基线 `8a67e7be65a43cffd7cc6d122d568ba64b99b369`。共享 ADR-009 提交 `db4c7dc` 已进入基线。
- 业务归 `pearnly_erp_portal/purchasing`，入口 `public/desk/purchase*.js`。菜单/权限目录/词条归 ERP App；没有另建通用组件或第二套前端壳。
- 本地主线核验值 `717d202af3cfcd06815e215ef088e07f4251e00e`；远端 main `ac33e0c7362915ae314295cb26f7748cb5589de0`。本任务尚未合并、推送或部署。
- 前序成果/交接仍见 [ERP UI 交接](2026-09-22-erp-ui-handover.md)、[盘点工作台](2026-09-22-stocktake-workbench.md)。本文件是本采购任务唯一工作记录。

## 固定生产来源

- 采集时间：`2026-09-22T13:35:44.644290+00:00`（曼谷 20:35:44）。
- 项目 / 区域：GCP `pearnly` / `asia-southeast1`。
- Web：`pearnly-web-ec2e5c957e6b-s3`；Worker：`pearnly-worker-ec2e5c957e6b-s3`；均实际承接 100% 流量。
- 同一不可变镜像：`asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/app@sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。
- 完整源码：`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，经部署登记与 GitHub Manual CD `35321281376` 映射核对。
- [首次采集证据](erp-purchase-records/production-baseline-start.json)，信封校验和 `edd12e372d12bfea8abe88f39e35d73537ac6825d32bf93b0a1cf8703f2badfe`。采集器只读调用 Cloud Run / GitHub 元数据，不读取 Secret Manager 明文或调用业务接口。
- 来源仓 `/Users/skin/Developer/Pearnly/pearnly-app` 本轮干净。其本地 HEAD `9aa7a6b2` 不是生产 SHA；已用 `git show ec2e5c957e6b:...` 读取关键入口，并核对 `src/home`、`src/erp`、`routes`、`services/purchase`、`services/erp`、`static/dist` 相对生产 SHA 无差异。来源仓保持只读。
- 本轮只证明生产身份与源码合同，未在旧生产创建采购、付款、OCR 或 Google 归档。实施前、交付前已分别重新采集，均仍为同一源码与镜像。


- 实施采集：[production-baseline-implementation.json](erp-purchase-records/production-baseline-implementation.json)。
- 交付采集：[production-baseline-delivery.json](erp-purchase-records/production-baseline-delivery.json)，信封校验和 `e2b37934f58623fdccfaebddf5d5d59858d67bd29eeacdd3c63c60ffc92e8d20`。

## 功能与字段对照

以下来源路径均指上述固定生产源码，不把来源文件里的旧工作指令当作本次请求。旧 ERP 只读详情与共用采购已有能力的差别已在需求对齐时说明；本次不引入会计总账、税务申报、采购审批或外部 ERP 推送。

| 编号 | 必须保留的能力/字段 | 旧源依据 | 本次落点 / 明确边界 |
| --- | --- | --- | --- |
| P01 | 列表：日期、供应商、摘要、来源、业务类型/分类、含税金额、进项税额、草稿/已确认/已作废、未付/部分付/已付、附件数 | `purchase-list.ts`、`purchase-common.ts` | `purchasing/records.py` + 原生列表固定显示主要业务列；其余业务字段在对应页签/明细，源元数据在存储及导出保留 |
| P02 | 全部/进货/费用/未付；关键词搜索；票种、付款状态、分类多选筛选 | `purchase-list.ts`、`purchase-list-filters.ts` | 统一查询 `records.query`；列表、汇总、导出共享条件 |
| P03 | 票面日期/上传日期口径；本月、上月、本季度、自定义起止日期；按月分组、折叠、月合计/条数 | 同上 | 票面/创建日期与期间筛选留在筛选按钮内；月分组/计数/合计数据合同保留，按最新用户指示不占首屏 |
| P04 | 本月花费、进货额/笔数、费用额/笔数、可抵进项税、未付额/供应商数；未付快捷查看 | `purchase-list.ts`、`services/purchase/reports.py` | 按最新用户纠偏移除首屏概要；统一汇总仍供既有导出与后端合同使用，使用单据汇率折成本币 |
| P05 | 单行/按月多选；仅草稿可批量删除；已确认记录不得当草稿删除 | `purchase-list.ts:bulkDelete`、`purchase_routes.py` | 查询有历史草稿时才提供选择；混选已过账单不显示删除操作；服务端整批拒绝 |
| P06 | 新建录入、上传识别、历史草稿继续编辑、提交、失败保留输入、来源追溯 | `erp-intake.ts`、`src/erp/record-form.ts`、`purchase-form.ts`、录入路由 | 用户有意改变：新建仅提交即过账，无草稿保存；原生上传/相机、已配置时 OCR、现有 LINE 待处理单。失败保留输入 |
| P07 | 我方自动编号 PE 与供应商单号/税票号分开；日期、账期/到期日 | `pos-form-adapter.ts`、`purchase-form-info.ts`、`DocIn` | 已过账标题显示 PE 编号；对方单号、记账日期、未付时到期日为原生字段；事务内取号、回滚不占号 |
| P08 | 单据业务类型：`purchase_invoice` / `purchase_order` / `expense` | `purchase-common.ts`、`purchase-form-info.ts` | 三个旧业务类型齐全；兼容原型的 goods/service 值；不新增订单履约模块 |
| P09 | 供应商选择/切换/新建；名称、税号、总公司/分公司/无分支、分支编号、分支名称、地址 | `purchase-form-info.ts`、`purchase-modals.ts`、`DocIn.supplier` | 原生 Link 选择与票面快照；无通用新建跳转，手填供应商随采购事务建档 |
| P10 | 有无税票、单号必填联动、非本币必须填写有效汇率 | `validateInfo`、`purchase-form-state.ts` | 前端必填联动 + entry 服务端权威校验 |
| P11 | THB/USD/EUR/CNY/JPY/SGD、记账汇率、单据付款状态；已有 requester/requester_user_id/approval_status 等数据合同 | `purchase-form-info.ts`、`DocIn` | 币种、外币时汇率为原生字段；申请人、来源、既有审批状态等数据合同保留但不作为录入字段，不新增审批流程/按钮 |
| P12 | 明细：商品/服务类型、商品关联/匹配状态、商品编码、条码、品名、数量、单位、单价、行折扣、行金额、VAT 税率、逐行 WHT 税率 | `purchase-line-types.ts`、`purchase-form-lines.ts`、`manual-document.ts` | 原生逐项 FieldGroup / Section，附加明细折叠；保留全部业务字段；持久化 Purchase Line |
| P13 | 商品搜索带回名称/条码/单位/默认成本/税率；匹配已有商品或新建；逐项录入/合并一条/切回恢复；增删行 | 同上、`purchase-modals.ts` | products.purchase 事务内匹配/自动建商品；合并/恢复行与原生字段按钮增删明细；有两条有效明细才显示合并，合并后可分拆恢复 |
| P14 | 行大类/子类与级联；停用分类仍保留历史选中值；历史 batch_no/expiry_date 等接口/存储字段 | 共用 `purchase-form-lines.ts`、`services/purchase/docs.py`、`correct.py` | 原生分类 DocType / Link；子类级联，历史停用值保留；批次/到期元数据保留 |
| P15 | 价内/价外；小计、折扣、VAT、WHT、凑整、含税合计、净应付；手动按票面改小计/折扣/VAT/合计及一致性校验 | `purchase-form-lines.ts`、`purchase-form-totals.ts`、`DocIn.amount_override` | purchasing.amounts 的 Decimal HALF_EVEN；价内/外、逐行 VAT/WHT、折扣/凑整/票面覆盖 |
| P16 | 原图/附件上传和查看；多张/多页切换、缩略图、放大、缩放、旋转、复位；PDF/图片；票图/付款凭证/替代收据/WHT 凭证分类、受权访问 | `purchase-form-bill.ts`、`purchase_routes.py` | 原生 FileUploader / Attachment Gallery / PhotoSwipe；私有文件范围随单据；原生预览工具栏扩展旋转/复位，源文件不改写 |
| P17 | 列表快捷已付/未付；付款金额、方式、日期、备注、多次付款、未付余额、部分付款；方式含 cash/transfer/promptpay | `purchase-list.ts`、`purchase-modals.ts`、`PayIn` | 列表付款快捷操作、原生付款弹窗；付款流水追加冲销，重复请求不重复付款 |
| P18 | 历史草稿编辑、过账只读、作废冲销、更正关联与附件保留 | `purchase-detail.ts`、`services/purchase/correct.py`、`posting.py` | 用户有意改变：更正录完直接提交；原单冲销与替代单付款/入库原子完成，使用 corrected_from 避开框架保留字段 |
| P19 | 自动/明确入库条件、商品关联、库存单位/数量/成本、同单只入一次、作废只回冲一次、回读库存状态 | `services/purchase/posting.py`、ERP 录单链 | 复用已验收 inventory.stock / positions / warehouses；无新增入库模块、菜单或独立入库动作 |
| P20 | 生成/下载替代收据、WHT 凭证；资格条件/权限/附件归属 | `purchase_routes.py`、`services/purchase/documents.py` | 原生打印 PDF + 私有 File；WHT 凭证仅有 WHT 时显示；实际 PDF 渲染已测试 |
| P21 | Excel 导出；Drive/Sheets 按本轮指示取消 | `purchase-export.ts`、`routes/export_routes.py`、`services/export/` | 用户明确取消 Google Drive / Sheets，当前新采购后端未迁入；仅保留作用于同一查询的四表 Excel |
| P22 | 租户/公司隔离、员工取数范围、功能/岗位权限、去重、重复提交、失败回滚、私有附件 | `purchase_common.py`、`services/purchase/member_scope.py`、采购/导出路由 | 岗位、本人范围、原生 Document/File 权限；请求、单号、源文件去重；失败回滚、租户隔离与并发检查 |

## 产品入口与条件

| 操作 | 实际承接 / 条件 |
| --- | --- |
| 新建、提交 | purchase.write；原生 Page / Tab Break / FieldGroup / Section；entry.post 在一个事务中落采购、商品、付款与库存 |
| 搜索、筛选、导出 | purchase.read；同一 records.query；首屏为原生列表。筛选走原生 Dialog，不显示概要/列配置；Excel 实际四张表，有冻结、筛选、文本公式转义 |
| 付款、修订、取消 | 本人范围或 ERP Admin，且已过账；真实付款流水、原单冲销/新单关联、库存幂等回冲 |
| 历史草稿删除 | 仅 draft；整批锁定后校验；查询中无草稿时不提供选择删除入口 |
| 附件 | 原生私有上传/预览；已取消只读；跨单据/跨用户不能绑定或读取 |
| 替代收据 / WHT 凭证 | 已过账且有写权限；WHT 还须大于 0；原生 PDF、私有附件、重复生成复用 |
| 明细分类 | 原生 Link 与 ERP Purchase Category；最多两级，作为既有明细字段依赖；已移除页面“新建分类”按钮 |
| OCR | 只有已配置共享 OCR 且有采购写权限才显示；本地只验证 provider 边界替身，未进行付费调用 |
| LINE | 只有可见的采购待处理单据才显示；确认走同一个采购事务，原生 File 权限随 Intake 范围 |

Link 不跳转到通用 DocType 新建/高级搜索/后台管理。菜单没有新增采购以外模块。原生上传器自带的上传/相机/图片裁切为真实框架能力；禁用 Google Drive、外链与公共文件切换。PhotoSwipe 仅用官方 ui.registerElement 扩展原有票图旋转/复位，无自制图库。

## 文案与字段

以锁定的 Frappe 16.33.0 / ERPNext 16.34.1 官方语言目录核对；ERPNext 源码锚 `0b50853985312bc64977f9324c55b5d8c1ab2e59`。上游有对应条目的中/泰文使用上游；采购特有字段与上游缺少的日语条目在 ERP 术语表补齐，不能虚称每个词都是上游已有翻译。

采购新字段不增加 description / 帮助段落。没有虚构“ERPNext 采购发票模块已安装”：真实 Site 仅装 Frappe + Core + ERP，自有 Purchase Document 承接产品业务。

## 本任务运行环境

- `colima-pearnly` / Compose `pearnly-purchase-lab`；独占 db / sites 卷和 Redis。`erp-pos-a.localhost`、`erp-pos-b.localhost` 仅合成数据，带 `.erp-pos-lab` 与 Site 标记，没有外部凭据或调度器。
- 预览镜像与原 8136 **相同**：`sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`。区别是新采购容器/数据库与本任务代码挂载；不是另换底座。
- `erp-purchase-preview.yml` 与旧预览同样使用 Gunicorn + nginx + Socket.IO；地址 `http://erp-pos-a.localhost:8147/desk/pearnly-purchase`。仅 nginx 发布本机回环端口。
- 新建之初曾临时使用 bench serve，已改为上述运行方式。隔离 lab 的 native 测试账号仅供本地验收；此环境未接 Keycloak，不能代替旧环境的 SSO 验收。
- `erp-purchase-acceptance.yml` 用本仓 canonical Dockerfile 的 linux/amd64 候选镜像覆盖四个 App 容器并移除 App 源码挂载；保留同一套合成 Site。重排前候选的构建、迁移和后端验证已执行；本轮纯 UI 重排使用同底座源码预览验证，没有把旧候选写成新布局验收镜像。无当前发布候选。
- 原 8136 / 8126 均在 `colima-pearnly-stocktake` / `pearnly-stocktake-acceptance`，原验收镜像 `sha256:051c475267e25a99e7f61ce64664182b8775c42a62ec35f3e80e65a5a8614645`；本任务从未切换其代码或停止它们。

操作本任务必须同时指定 project 与文件，不能在本树执行旧 stocktake-preview.sh 接管已验收环境：

```bash
docker --context colima-pearnly compose -p pearnly-purchase-lab -f infrastructure/compose/erp-pos-lab.yml -f infrastructure/compose/erp-purchase-preview.yml up -d web socketio frontend
docker --context colima-pearnly exec pearnly-purchase-lab-bench-1 ./env/bin/python /workspace/scripts/erp-purchase-lab-verify.py
```

## 当前验证证据

- 原生 migrate：A、B 均通过；新 DocType 与字段从源码迁入，不手改表。
- `tests/test_purchase_amounts.py` 5 个通过；`tests/test_document_numbering.py` 10 个通过。
- `scripts/erp-purchase-lab-verify.py`：实际 Site 事务、商品/库存余额、重复提交、失败回滚/取号回滚、重复单号、付款重试/部分/超额/冲销、更正/取消、原生权限、私有 File、LINE 确认、分类月份/汇总、四表 Excel、原生 PDF、完整混合外币字段回读、文件内容去重、旧版本金额及租户隔离通过。最新源码预览上 20 组检查通过（含双进程并发、严格 OCR mock 签名、v1 历史付款/库存状态）；见本轮机器证据。
- 重排前浏览器操作证据（布局已被否定）：原生表单默认三个勾选，手工录入 UI-20260922-01 → PUR-2026-00067 / PE2569-000021，商品 2 盒 × THB 100，VAT 14、付款 214、自动入库；附件真实上传和回读、图像旋转/复位/缩放；390×844 详情及付款冲销通过。属于浏览器窄屏，不是真机验收。
- 明细填入的第一次自动化失败是浏览器工具 fill 未触发原生 Grid change；改用实际键入验证成功。另外已修复预览金额与含空行 Grid 的索引对应，避免覆盖错误行。
- `check-architecture.py` 0 违规；前端词条检查、生成翻译检查、JS 语法与 diff 检查通过。本轮纯 Desk 脚本重排运行语法、架构和翻译检查；未构建新的发布候选。
- `check-i18n.py --strict` 仍报既有 `inventory/workbook.py` 18 处 bare CJK；该文件与基线相同，无本次新增问题。不改忽略规则或借采购重做已验收盘点导出。
- 原 `erp-pos-lab-purchasing-verify.py` 的业务断言通过，但总结果为失败：它要求另一 Site 所有采购/供应商总数为 0，当前 A 已有本任务样本，不满足该旧探针的空库前提。本任务探针改用独有单号/供应商检查隔离，并独立核验 v1 合同，不删除数据换取绿灯。
- Google 外部写入、真实付费 OCR、实际 LINE 发送、真机与用户验收未执行；没有上线。

## 收尾：交接保留

当前任务尚未合入主线，保留独立工作树、分支、合成样本及 `pearnly-purchase-lab` 容器/卷用于继续检查和用户验收；所属即本采购任务。解除条件是用户完成采购验收、成果按授权集成后，核验无挂载/独有提交再清理。

本任务未创建远端分支。原 stocktake 工作树/容器属前序已验收任务，本轮仍在运行，保留原因与解除条件见其原记录。生产源码仓只读；未进行跨任务清理、合并、推送或部署。本轮资源回读见下；不以代码实现代替用户验收。

## 本轮界面重排验证与保留（2026-09-22）

- 代码基础 `9f6cf3a08b807a568955b7599078b0c8c770dc78` 加本轮三个采购 Desk 脚本与 ERP 词条变更；实际文件哈希及测试结果见 [verification-layout.json](erp-purchase-records/verification-layout.json)。无共享组件、产品导航、后端业务或已验收六页修改。
- 官方原生项：Page 列表/工具栏、Filter Dialog、Tab Break、Section、FieldGroup、FileUploader、Attachment Gallery、PhotoSwipe。每项明细的嵌套容器只组合框架 `px-0 / px-sm-3 / mt-0 / pt-0` 间距工具类，避免手机叠加三层边距；没有自制输入框、按钮、页签、表格或 CSS 组件。
- 默认桌面 1272×776：主要八列可见；按供应商单号搜索只返回命中的测试单；筛选弹窗本月自动得到 2026-09-01 至 2026-09-30，应用后记录一致。手机 390×844：首屏显示单号、明细、金额；表单三页签和逐项纵向字段无水平溢出。临时视口检查后恢复默认。
- 浏览器新样本：`UI-MOBILE-20260922-02` → `PUR-2026-00194` / `PE2569-000071`。新增后删除空明细仍保留第一行；数量 0 被服务端拒绝，输入保留；改为 2 后提交。数据库回读 `submitted`、`is_paid=1`、付款 214、总额 214、`auto_create_product=1`、`update_stock=1`、有 stock_posted_at；商品 `PRD-00193`、2 盒×100，税 14。
- 未提交的两条临时明细 10 + 15 合并为 25，再分拆恢复两条；VAT 1.75、合计 26.75。无效/无明细时不显示合并入口。已过账详情只显示付款状态，不显示不可编辑的付款开关或供应商内部主键。
- 私有附件样本 `PUR-2026-00067` 在手机附件页签可见；此轮未重复上传或付费识别。Socket.IO 浏览器回读 connected=true。真实手机设备、真实外部 OCR/LINE、生产部署及用户验收仍未执行。
- 最终只读来源回读 [production-baseline-layout.json](erp-purchase-records/production-baseline-layout.json)，信封校验和 `c2a5c9acb8bfb9c30be2b5451a5a78f14657cba8516c3f015db95266cafa1f9d`；旧生产仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，无需补来源增量。
- 保留：本任务工作树/分支、本任务 6 个运行容器、自有 sites/db 卷及各容器日志卷、合成验收样本。用途是继续用户验收；解除条件为布局和业务验收完成、成果按授权集成后，核对独有提交和源码挂载再清理。
- 清理：无容器引用后删除重排前专属候选镜像 `28f13cf`、`a42a547`、`final`；先前 `cae4c15` 对应镜像已不存在。沿用的共享预览镜像 90c1d545… 保留，未清理任何共享依赖、业务数据、卷或旧库存环境。
- 本地主线仍 `717d202af3cfcd06815e215ef088e07f4251e00e`，实际 origin/main 仍 `ac33e0c7362915ae314295cb26f7748cb5589de0`；远端采购任务分支不存在。本轮不合并、不推送、不上线；状态为**交接保留，待用户验收**。

- 重排实现提交 `fa31b11d19e70b271dbb05fe70e586ad2ef61b2e`，提交钩子架构检查通过，提交后工作区干净。结构化验证证据入库后，逐文件核对名称/大小并删除本任务 `/tmp/erp-purchase-*` 中 22 个中间日志、生成脚本、官方词条缓存与本地图像夹具；Site 私有附件和业务记录保留。
