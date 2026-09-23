# ERP 采购记录实施与验收记录

> **2026-09-23 换窗口入口**：用户要求下一包做销售记录，前端交互与采购一致，仅替换销售业务字段与契约。先读本文末尾 [销售记录接续交接](#销售记录接续交接2026-09-23)，其中包含交互定稿、代码入口、8147 容器与源码挂载、即时预览命令和验证边界。当前代码定稿为 `a4b95398cc332b46f8b2595dc06da81e15be3a63`；本次只补交接文档，销售尚未施工。

## 已对齐的实施要求（本轮最新用户指示，优先于下方调查稿）

- **后续用户纠偏已实施**：提供的 Paypers 收入截图仅作桌面/手机布局参考，不授权收入、AI 提示、客户管理或其他产品能力。官方原生组件用于组合采购自己的页面，不照搬 ERPNext 采购发票表单。
- 采购首屏与已验收商品/库存共用原生 page-level List：搜索、筛选按钮、新建；导出收在菜单。删除展开的“过滤条件 / 更多筛选 / 概要”、统计区和列配置入口；后台查询/汇总与导出合同仍保留。
- **最新纠偏：连续页面，取消 Tab Break 分页。** 电脑左侧约 60% 是连续的详细信息、供应商、物料与金额字段，右侧约 40% 为附件区，最小宽度 320px；手机按附件、详细信息、物料连续排列，顶部固定导航点击只滚动定位，下划线随滚动同步。电脑不显示该导航。原生可折叠 Section + FieldGroup 保留全部业务字段；自动建物料/入库仍是新单默认行为。
- **录入方式与查看器已按最新要求调整**：新单默认选中“上传识别”，可切换“手动录入”；两张互斥选择卡片使用原生黑底白勾方框、白底、标题与短说明，只占左侧。上传识别在识别成功前隐藏业务字段，识别中使用原生冻结层并禁止提交，成功后在原页复核；手动录入立即显示字段，上传仅留作附件。票图沿用旧采购查看器的缩放、拖动、旋转、复位与多页 PDF 行为，通用按钮和全屏弹窗使用 Desk 原生组件。详见末尾最新验证。
- 此前两版（整张未整理长表单、切换页签）的浏览器通过只证明当时操作可用，**用户已分别纠偏**；本节及末尾连续页面证据描述当前实现。当前布局仍待用户验收。

- 用户进一步明确：不扩建产品没有的功能和入口；框架自带按钮也必须有真实产品功能、权限及配置承接。识别执行受真实 OCR 配置控制；未配置时仍默认选中上传识别，真正点击添加文件才原生提示尚未配置；LINE 仅在有本人可处理单据时展示；Link 禁止跳出到通用新建/高级搜索入口。
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
| P01 | 列表：日期、供应商、摘要、来源、业务类型/分类、含税金额、进项税额、草稿/已确认/已作废、未付/部分付/已付、附件数 | `purchase-list.ts`、`purchase-common.ts` | `purchasing/records.py` + 原生列表固定显示主要业务列；其余业务字段在对应区域/明细，源元数据在存储及导出保留 |
| P02 | 全部/进货/费用/未付；关键词搜索；票种、付款状态、分类多选筛选 | `purchase-list.ts`、`purchase-list-filters.ts` | 统一查询 `records.query`；列表、汇总、导出共享条件 |
| P03 | 票面日期/上传日期口径；本月、上月、本季度、自定义起止日期；按月分组、折叠、月合计/条数 | 同上 | 票面/创建日期与期间筛选留在筛选按钮内；月分组/计数/合计数据合同保留，按最新用户指示不占首屏 |
| P04 | 本月花费、进货额/笔数、费用额/笔数、可抵进项税、未付额/供应商数；未付快捷查看 | `purchase-list.ts`、`services/purchase/reports.py` | 按最新用户纠偏移除首屏概要；统一汇总仍供既有导出与后端合同使用，使用单据汇率折成本币 |
| P05 | 单行/按月多选；仅草稿可批量删除；已确认记录不得当草稿删除 | `purchase-list.ts:bulkDelete`、`purchase_routes.py` | 按最新要求始终提供原生行勾选与本页全选；仅全部选中项为草稿且有权限时提供删除；混选已过账单不显示删除操作，服务端整批拒绝 |
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
| P16 | 原图/附件上传和查看；多张/多页切换、缩略图、放大、缩放、旋转、复位；PDF/图片；票图/付款凭证/替代收据/WHT 凭证分类、受权访问 | `purchase-form-bill.ts`、`purchase_routes.py` | 原生 FileUploader / Attachment Gallery，用户指定沿用旧采购查看器交互；原生 Button / Dialog，私有 PDF 分页只读渲染，源文件不改写 |
| P17 | 列表快捷已付/未付；付款金额、方式、日期、备注、多次付款、未付余额、部分付款；方式含 cash/transfer/promptpay | `purchase-list.ts`、`purchase-modals.ts`、`PayIn` | 列表付款快捷操作、原生付款弹窗；付款流水追加冲销，重复请求不重复付款 |
| P18 | 历史草稿编辑、过账只读、作废冲销、更正关联与附件保留 | `purchase-detail.ts`、`services/purchase/correct.py`、`posting.py` | 用户有意改变：更正录完直接提交；原单冲销与替代单付款/入库原子完成，使用 corrected_from 避开框架保留字段 |
| P19 | 自动/明确入库条件、商品关联、库存单位/数量/成本、同单只入一次、作废只回冲一次、回读库存状态 | `services/purchase/posting.py`、ERP 录单链 | 复用已验收 inventory.stock / positions / warehouses；无新增入库模块、菜单或独立入库动作 |
| P20 | 生成/下载替代收据、WHT 凭证；资格条件/权限/附件归属 | `purchase_routes.py`、`services/purchase/documents.py` | 原生打印 PDF + 私有 File；WHT 凭证仅有 WHT 时显示；实际 PDF 渲染已测试 |
| P21 | Excel 导出；Drive/Sheets 按本轮指示取消 | `purchase-export.ts`、`routes/export_routes.py`、`services/export/` | 用户明确取消 Google Drive / Sheets，当前新采购后端未迁入；仅保留作用于同一查询的四表 Excel |
| P22 | 租户/公司隔离、员工取数范围、功能/岗位权限、去重、重复提交、失败回滚、私有附件 | `purchase_common.py`、`services/purchase/member_scope.py`、采购/导出路由 | 岗位、本人范围、原生 Document/File 权限；请求、单号、源文件去重；失败回滚、租户隔离与并发检查 |

## 产品入口与条件

| 操作 | 实际承接 / 条件 |
| --- | --- |
| 新建、提交 | purchase.write；原生 Page / FieldGroup / Section；entry.post 在一个事务中落采购、商品、付款与库存 |
| 搜索、筛选、导出 | purchase.read；同一 records.query；首屏为原生列表。筛选走原生 Dialog，不显示概要/列配置；Excel 实际四张表，有冻结、筛选、文本公式转义 |
| 付款、修订、取消 | 本人范围或 ERP Admin，且已过账；真实付款流水、原单冲销/新单关联、库存幂等回冲 |
| 列表选择 / 历史草稿删除 | 始终提供单行、多行、本页全选；选择不产生业务写入。删除仅全部选中项为 draft 且有写权限时出现，服务端整批锁定后校验 |
| 附件 | 原生私有上传/预览；已取消只读；跨单据/跨用户不能绑定或读取 |
| 替代收据 / WHT 凭证 | 已过账且有写权限；WHT 还须大于 0；原生 PDF、私有附件、重复生成复用 |
| 明细分类 | 原生 Link 与 ERP Purchase Category；最多两级，作为既有明细字段依赖；已移除页面“新建分类”按钮 |
| 录入方式 / OCR | 新单 purchase.write，选择上传识别或手动录入；识别必须已配置共享 OCR。未配置时原生提示，失败后才显示重试；本地只验证响应替身，未进行付费调用 |
| LINE | 只有可见的采购待处理单据才显示；确认走同一个采购事务，原生 File 权限随 Intake 范围 |

Link 不跳转到通用 DocType 新建/高级搜索/后台管理。菜单没有新增采购以外模块。原生上传器自带的上传/相机/图片裁切为真实框架能力；禁用 Google Drive、外链与公共文件切换。查看器是用户本轮明确指定沿用的旧项目业务交互；通用控件仍用原生 Button / Dialog，旧图库壳、鉴权全局变量和上传控件不迁入。

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


## 连续页面与右侧附件区（2026-09-22 最新纠偏）

- 续接提交 `7939cfce37efe08bf68220f0d394242f9da50a6f`，本轮仅修改 ERP App 的采购布局、附件预览组合和资产声明；没有新增后端、字段、导航菜单或业务功能。以上“本轮界面重排”中的 Tab Break 版本是历史证据，已由本节替代。
- `purchase-fields.js` 把三个 Tab Break 改为三个有标题的原生 Section。`purchase-layout.js` 只组织原生 FieldGroup 已生成的 section 节点，全部区域始终存在；电脑 70/30 两栏，右侧附件随页面滚动停留。上传前区域按内容高度显示，不设大面积占位。
- 手机导航沿用 Desk `form-tabs-list / nav / form-tabs / nav-link` 原生样式，使用普通按钮滚动定位，不使用 tab role、tab pane 或切换事件；滚动位置驱动 active 下划线与 aria-current。桌面隐藏导航，原生字段、焦点、按钮尺寸与色彩不变。CSS 仅管采购业务区域排列、粘性位置与票图尺寸；原生 Tab Break 会隐藏内容，不适合用户明确要求的连续页面，因此本次只组合其官方导航样式，不改框架或另造通用组件。
- 右侧票图为业务 HTML 内容，点击仍走原生 PhotoSwipe（缩放、旋转、复位）；多图缩略图由原生 Attachment Gallery 提供。PDF 保持官方附件原文件入口。附件列表仍是原生 List，全集已加载，不显示无用途的每页条数按钮，也不保留通用列表的 200px 空白最小高度。
- OCR 原有动作移到附件区，仍受真实配置能力控制。识别结果直接回填当前表单，并定位详细信息供复核；提交继续走原来的同一原子事务。未配置的本地 Site 不显示 OCR，未启用真实识别服务或外部凭据。
- 浏览器验证：桌面 1280×850 无页签导航、右侧约 30%；390×844 附件/信息/物料始终连续显示，点击滚动定位成功，手动滚动切换下划线；页头下方导航 top=49px，定位区域 top≈99px。手机 scrollWidth=390；来回定位没有改变 URL 或丢失填写值。
- 本地 OCR 回填测试：仅通过浏览器 XHR 响应替身临时提供配置能力及合成识别 JSON，未访问真实 OCR。回填单号/供应商/一条物料，识别数量 2；实际键入复核为 3 后金额从 214 更新为 321，再真实提交隔离 Site。`UI-CONTINUOUS-20260922-03` → `PUR-2026-00198` / `PE2569-000072`，数据库回读 submitted、is_paid=1、paid_amount=grand_total=321、auto_create_product=update_stock=1、有 stock_posted_at。测试拦截已清除，导航后恢复真实配置。
- 图片样本 `PUR-2026-00067` 的右侧原图与原生放大查看已验证。新样本生成的替代收据 PDF 是有效 PDF-1.4、30148 bytes、1 页且能提取文字；当前内置浏览器独立打开 PDF 也显示失败，故**不声称 PDF 浏览器显示通过**。没有另造 PDF 阅读器，页面不放空白嵌入框，继续保留原生文件入口；真实浏览器/手机 PDF 显示仍待验收。
- JS 语法、架构 0 违规、前端词条、翻译生成与 diff 检查通过；本轮不改金额/付款/库存后端，没有重复前轮 20 组全套业务检查。全仓 strict-i18n 的旧库存 workbook 问题仍见上一节，没有修改该文件或放宽规则。
- 证据及代码哈希见 [verification-continuous-layout.json](erp-purchase-records/verification-continuous-layout.json)。无生产部署、CI、付费 OCR、实际 LINE 或真机验收；用户视觉验收尚未完成。
- 交接保留：同一采购工作树/分支、6 个自有容器及 sites/db/log 卷、原样本和本轮 00198 样本供验收。解除条件仍为用户验收且按授权集成后核对占用再清理。关闭本轮 PDF 临时浏览器页、移除响应替身、恢复临时视口；没有新建候选镜像、临时文件或长期测试服务。
- 本地主线回读 `717d202af3cfcd06815e215ef088e07f4251e00e`，实际远端 main `ac33e0c7362915ae314295cb26f7748cb5589de0`，远端采购分支不存在。本轮未合并、推送或上线；保留采购工作树供继续验收。


## 录入方式与旧采购查看器（2026-09-22 最新实施）

### 当前行为与实现范围

- 本轮基线 `6c602dc18ea73ca97e3b7a9a13477697d050d16e`。以上连续页面节的 PhotoSwipe、PDF 原文件入口和手动 OCR 按钮描述是上一版证据，当前以本节为准；采购列表、连续布局、手机滚动导航和原有业务字段不变。
- 新建先选录入方式，使用原生 FieldGroup / Select（本产品现有原生 Autocomplete 适配）。未选择时业务字段隐藏、提交禁用。选择上传识别后上传整批附件，等待每个 File 成功响应再只调用一次识别；识别前/识别中不显示业务字段，原生 `frappe.dom.freeze` 显示“识别中…”，提交与方式选择禁用。识别成功才显示回填字段，原页修改后提交。失败保留附件、原生错误提示、解除加载并提供“重试”，不自动重试付费调用。
- 手动录入立即显示字段；附件上传不会调用识别。取消上传会恢复提交状态。切换方式不会自动识别或丢弃已经填写的值。历史单据、更正和 LINE Intake 保持原入口合同，不强制重新选择方式。
- 源码 `purchase-entry.js` 专门等待原生上传器的 File 响应状态，不能把字节传输结束当作上传完成；路由切换清理其轮询。修复 Page 新建路由返回 Promise 导致框架重新启用“提交”的问题。
- 用户明确要求采用旧查看器，因此只将已生产的 `src/home/purchase-form-bill.ts`、`src/home/image-viewer.ts` 的变换与交互合同适配到 ERP App 的 `purchase-viewer.js`：缩放 0.4–6 倍、拖动、滚轮、双指缩放、旋转 90°、复位、缩略图选图、PDF 翻页。全屏使用原生 Frappe Dialog；关闭返回原附件区，保持表单值。原生 Button 提供控件样式和禁用态，无新增第三方前端框架或通用控件。
- PDF 通过 `purchasing.preview.page_image` 只读渲染为 PNG，使用底座已有锁定的 PyMuPDF 1.28.2；不新增依赖、File 记录或外部服务。校验 purchase.read、原生 File 权限、采购/Intake 可读范围与文件绑定、私有文件所有权、PDF 签名、20MB 上限及页码；单页渲染长边最多 1800 像素。旧鉴权 token/全局对象不迁入，采用当前 Site 会话。
- 已有替代收据/WHT 凭证生成后直接进入同一查看器；没有新增凭证功能或业务入口。右侧上传区保持紧凑；查看器有文件后才占用预览高度。

### 入口能力核对

| 控件 | 用途 / 配置与权限 / 点击去向 / 当前证据 |
| --- | --- |
| 录入方式 | 新建且有 purchase.write；选上传识别或手动录入，原生字段显隐；从列表新建与直接地址均通过 |
| 添加文件 | 现有私有 FileUploader；手动仅附加，识别模式整批成功后识别；真实两文件上传、手动零 OCR 请求通过 |
| 重试 | 仅配置有效且本次识别失败；调用原识别 API，无隐式重试；浏览器错误响应及重试替身通过 |
| 前后页 | 仅多页 PDF；受权限保护的当前文件页，按钮在边界禁用；两页真实 PDF 通过 |
| 放大/缩小/旋转/重置 | 当前已加载票图变换；原生 Button；浏览器样式与图像显示验证通过 |
| 全屏 | 原生 Dialog 展开同一查看器，关闭原位恢复并移除空 Dialog；桌面及 390px 通过 |

### 验证证据及限制

- 开工与交付前只读回采证据分别为 [production-baseline-viewer-start.json](erp-purchase-records/production-baseline-viewer-start.json)、[production-baseline-viewer-delivery.json](erp-purchase-records/production-baseline-viewer-delivery.json)，信封校验和分别 `16638765d2aaf5c6b53c4c7c258b94119ea578e5e68d9b9c1117bd03acc5d5cb`、`292996195131aa1fe4bb1393eea54290a55024114db1e5b69565a932e7d59cb8`。collector compare 通过，生产仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` 和镜像 `178af2ed…`，没有来源漂移。
- 实际运行仍是 `colima-pearnly` / `pearnly-purchase-lab` / 8147，6 个容器使用原有共享底座 `90c1d545…` 加本任务只读 App 挂载。仅清缓存和重启自己的 web；原盘点 8136 / 8126 未动。
- `scripts/erp-purchase-viewer-verify.py` 通过：实际私有凭证 PDF 渲染、内存两页 PDF、非法页码/内容/大小拒绝、其他用户及 Guest 拒绝。无业务写入或外部调用；生成的本地合成 PDF/PNG 仅供上传验证。
- 浏览器实际上传 PDF + PNG：手动方式无 `api_recognise_purchase` 请求。识别方式用浏览器 XHR 替身确认整批上传仅一次识别；等待时字段隐藏、提交禁用且显示“识别中…”；模拟 417 错误后附件保留、无字段、重试可用；重试成功再回填。当前 Site 没有 OCR 凭据，选择上传识别的真实结果是“尚未配置文字识别”，**不能把替身通过写成真实识别通过**。
- 回填合成数量 2 后实际键入改为 3，金额自动更新 321 并真实提交本任务隔离 Site：`UI-ENTRY-20260922-04` → `PUR-2026-00202` / `PE2569-000073`。数据库回读 submitted、source=ocr、is_paid=1、paid_amount=grand_total=321、auto_create_product=update_stock=1、stock_posted_at=`2026-09-22 23:40:10.389846`；商品 PRD-00201、3 盒×100。私有 PDF/PNG 两附件保留，随后原入口生成替代收据并原位显示，当前共 3 个附件。
- PDF 两页、缩放 120%、旋转 90°、复位、拖动位移 55×42px、滚轮缩放 112%、全屏与关闭后原位恢复通过。390×844 无横向溢出，原手机连续滚动导航可用，全屏 Dialog 宽 390px。双指手势已沿用旧实现，但当前内置浏览器不支持触摸事件模拟，**真机触摸验收未做**。
- 原生控件语法、架构 0 违规、前端词条与生成翻译、diff 检查通过。全仓 strict-i18n 仍是已有库存 workbook 的 18 处问题；不改基线/忽略。未重复金额/库存全套检查，受影响只读 PDF 路径已做定向测试，真实复核提交已回读。
- 本轮没有构建发布候选、CI、真实付费 OCR、实际 LINE、生产部署或用户验收；详细文件哈希与证据见 [verification-entry-viewer.json](erp-purchase-records/verification-entry-viewer.json)。

### 收尾：交接保留

- 保留采购工作树 `/Users/skin/.codex/worktrees/erp-purchase-records/pearnly`、分支 `codex/erp-purchase-records`、6 个容器及自有 sites/db/log 卷和合成单据，用途是本次采购验收；当前尚未合并且源码仍被挂载。解除条件：用户验收、按授权集成后核验独有提交及占用再清理。
- 清除浏览器响应拦截，恢复临时视口，预览停留在干净新建页面；本轮生成的 `/tmp/erp-purchase-viewer/` PDF/PNG 夹具在主机及 bench 容器中逐项移除，Site 附件和业务样本保留。
- 本地主线 `717d202af3cfcd06815e215ef088e07f4251e00e`，实际远端 main `ac33e0c7362915ae314295cb26f7748cb5589de0`；远端采购分支不存在。本轮未合并、未推送、未上线。没有删除未集成的任务工作树/分支或共享资源，状态为交接保留。


## 术语、录入选择及列表调整（2026-09-23）

### 当前实现

- 基础为 `077ba073dcad10211d090eab207fb60c60782696`。本轮调整现有页面，不新增来源业务迁移；不改采购字段、金额、库存、付款或文件契约。
- 按用户要求，全局中文界面将“商品”统一为“物料”：Core / ERP 术语表共 41 处中文值、对应生成 CSV，以及一条仅在历史 CSV 中维护的称重字段；产品声明和说明同步术语。日语原词、内部字段/API 名、历史导入别名和已有业务数据保持原义。共享 Core 只改中文词条，不改共享组件和加载结构。
- 新建默认“上传识别”，业务字段隐藏且提交禁用；识别成功后才显示复核字段。录入方式使用原生 FieldGroup / Check，两个选项互斥、无卡片底色、原生黑底白勾；在原生 label 插槽内放标题与用户要求的简短说明，点击整张 label 即选择。上传期间两项及提交按钮禁用，切换方式本身不触发识别或丢弃输入。
- 选择卡片只占左侧。桌面左右约 60% / 40%，附件区至少 320px 并从顶部开始，左侧可用宽度较小时卡片自动纵向排列。附件外层不设置 overflow / max-height，避免原生附件类型下拉菜单被内容高度裁切；票图自身仍在查看器画布内裁切。
- 本地未配置 OCR 时，默认选项仍正确显示；真正点“添加文件”才由原生 msgprint 提示尚未配置，手动模式仍可使用原生附件上传。服务端的配置/权限校验保留。
- 采购列表始终启用同其他页面的原生行勾选与本页全选，不再以历史草稿是否存在作为显示条件。选择只更新本地选中状态；原有删除仅在有写权限且全部选中项是草稿时提供，未新增批量付款、删除已过账记录等业务入口。
- 查看器原生 Button 使用 only_input，消除内部 form-group 底部空隙；页码、缩放值与按钮垂直居中，导航/缩放/操作按组换行，数字不会单独落行。

### 验证与限制

- 架构检查 0 违规；前端词条检查 538 个键 / 27 文件通过；生成翻译一致性检查 1645 词条 / 4 App 通过；三个受影响 JS 语法和 diff 检查通过。全仓 strict-i18n 仍有既有库存 workbook 的 18 处 bare CJK，未新增或扩大忽略。
- 只读 Site 验证：物料导出模板首列为“物料名称”；物料与库存导入的新名称和历史“商品名称”别名均映射 product_name。全 App 中文 CSV / 术语表中文列无剩余“商品”界面词条，未替换客户数据或历史兼容别名。
- 桌面浏览器验证：默认上传识别已勾选、字段隐藏、提交禁用；手动模式显示字段，切回识别重新隐藏；再次点击已选项保持互斥状态。手动打开 FileUploader 时两个选项和提交均禁用；本地无 OCR 配置的上传入口弹出真实原生提示。
- 列表实际勾选第一条、第二条、本页 20 条全选与取消全选通过；勾选不跳详情，已过账选中项不出现删除动作。390×844 下复验两行勾选、选择卡片与默认识别页面，通过；这不是手机真机验收。
- 查看器实际两页 PDF 验证 1 / 2、2 / 2、边界禁用与 120% 缩放，桌面及 390px 的全屏工具栏垂直对齐。附件类型原先被裁切的“付款凭证”选项在修复后完整显示，并实际选择回读成功。
- 本轮没有业务数据写入、付费 OCR、真实 LINE、发布镜像/CI 或生产部署；用户验收待继续。只清理本任务 Site 缓存、重载自身 web，现有合成样本保留。

### 收尾：交接保留

- 实际运行沿用 `colima-pearnly` / `pearnly-purchase-lab` 六个容器 / 8147，镜像 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`，App 只读挂载回读指向本采购工作树。原盘点环境未动。
- 保留工作树 `/Users/skin/.codex/worktrees/erp-purchase-records/pearnly`、本地分支 `codex/erp-purchase-records`、该项目容器/自有卷及合成数据，归本采购任务，原因是未集成且仍用于验收/源码挂载。用户验收、按授权集成且解除占用后复核清理。
- 本地主线仍为 `717d202af3cfcd06815e215ef088e07f4251e00e`；实际远端 main 回读 `ac33e0c7362915ae314295cb26f7748cb5589de0`，远端采购分支不存在。本轮未合并、未推送。没有满足清理条件的任务分支/工作树可删除；没有新建临时夹具或进程。临时手机视口恢复，预览停在默认上传识别的新建页。

## 销售记录接续交接（2026-09-23）

### 本次目标、基线和下一步

用户本次只要求写交接，下一窗口才开发销售记录：**销售的前端交互与采购一模一样，只替换业务字段及其服务端契约**。采购已经反复纠偏定稿，不再重新设计录入方式、电脑/手机布局、文件查看器或列表。用户发来的 Paypers 图片是布局参考，不是增加其功能、字段或文案的指令。

- 采购代码定稿：`a4b95398cc332b46f8b2595dc06da81e15be3a63`；包含本机 main `717d202`、盘点 `8a67e7b` 和共享 ADR-009 `db4c7dc`。本次随后提交只保存交接文档，最终含文档的版本取 `git log -1`。
- 当前工作树 `/Users/skin/.codex/worktrees/erp-purchase-records/pearnly`，分支 `codex/erp-purchase-records`。本次开工时工作区、暂存区干净；不得在 `/Users/skin/pearnly` 的旧 main 上继续写销售而遗漏采购成果。
- 销售归 ERP App 的 `sales` 域；前端进该 App 的 `public/desk/`，数据为 `ERP Sales Document` / `ERP Sales Document Line`，菜单、功能、词条仍由 ERP 自己声明。复用现有 Core 原生外壳和列表，不为这次接续重写跨产品组件。
- 先读 [开发入口](../../CONTRIBUTING.md)、[架构](../development/architecture.md)、[共享基线对齐](2026-09-22-shared-foundation-alignment.md)，核验实际 Git 状态，再按下面的步骤创建销售工作树并接手预览。本次没有替下一窗口创建分支、修改销售代码、重建镜像或切换挂载。
- 销售字段必须对照旧项目实际生产版本，不能只照当前 `sales.js` 的少量字段做。按 [迁移基准](../development/migration-baseline.md) 在开工和交付前重新只读采集；上文采购的 `ec2e5c957e6b…` 是当时证据，不冒充销售开工时的最新线上版本。旧仓 `/Users/skin/Developer/Pearnly/pearnly-app` 只读，已有功能和字段不漏，确认过的产品边界不扩展。

### 必须沿用的采购交互

| 区域 / 状态 | 当前定稿；销售按此实现 |
| --- | --- |
| 首屏列表 | 原生 page-level List 按列显示记录；简洁搜索框、筛选按钮、新建、刷新。筛选在原生 Dialog 内；已有导出收进菜单。不要把统计概要、展开的过滤条件、列配置或官方发票整套入口放出来。销售是否提供导出等动作要有自己的实现和权限承接，不能复制一个空按钮。 |
| 选择记录 | 原生方框：单行勾选、多行勾选、本页全选/清空；点击方框不打开详情。选择只是本地状态；批量动作另按真实能力、角色和单据状态判断。采购的“全部为草稿才可删除”是采购规则，不直接套到销售。 |
| 新建 / 详情 | 列表进入独立路由的连续页面；返回列表、浏览器前后退、直接访问都能还原。新建不使用整个表单塞进大 Dialog 的方式。切页清理 Page 字段、列表、滚动监听、观察器和上传轮询，不能遗留旧按钮或让迟到响应写入新页面。 |
| 默认录入方式 | 每次进入新建页默认“上传识别”已勾选。两张互斥卡片为原生 FieldGroup / Check：左侧中间是黑底白勾方框，白底无选中底色，标题加一行短说明，无装饰 SVG；整张 label 可点。不能改回圆圈、下拉选择、默认空选或手动。 |
| 卡片尺寸与位置 | 仅占左侧表单列，不跨到附件列；当前最小高度 80px，原生列以 250px 为换行依据，左侧不足时纵排。保留足够点击面积，避免首屏大段留白。短说明仅解释“上传后识别并复核 / 手填且文件仅作为附件”，销售替换业务称谓。 |
| 电脑布局 | 左侧连续业务字段约 60%，右侧附件约 40%、至少 320px，从顶部开始并随滚动停靠。电脑没有顶部区域导航/分页 Tab。附件空态紧凑，有文件才显示预览画布；当前画布 `min(360px, 48vh)`。 |
| 手机布局 | 767px 以下单列，录入选择后按附件、详细信息、物料连续排列；固定横向导航点击滚动到区域，手动上下滑动也更新下划线。它是滚动定位栏，不能切换/隐藏其他区域或拆成分页。原生 Section 折叠不等于分页。 |
| 上传识别前 | 显示录入方式和上传区；所有业务字段先隐藏，提交不可点。当前 Site 无识别配置时仍选中上传识别，真正点添加文件才由原生提示阻止识别上传；不退回空选。 |
| 上传 / 识别中 | 上传期间禁用两种选择及提交；等所有文件的服务端成功响应后，整批仅触发一次 OCR。上传字节结束不是 File 保存成功，取消或部分失败不能识别半批文件。识别使用原生 `frappe.dom.freeze` 的“识别中…”提示；提交在所有入口均不可执行。 |
| 识别完成 / 失败 | 成功后在同页展开并回填字段，直接对照右侧票图复核、修改、提交，不跳第二个复核页面。失败保留附件，原生错误提示，解除加载但保持字段隐藏；有配置且本次失败才出现重试，不隐式重试付费调用。 |
| 手动录入 | 选择后立即显示业务字段；上传仅附加文件，绝不触发 OCR。切换方式本身不触发识别、不丢已有输入；识别完成标记按当前实现保留。上传器取消后应恢复可操作状态。 |
| 字段与提交 | 原生 FieldGroup / 可折叠 Section / 数据形态对应的官方组件；字段完整，使用官方规范术语。新采购是一次提交即过账，默认已付款、自动建未匹配物料、自动入库且不暴露三个额外开关。销售保持一次录入、复核、确认的交互，付款/收款与出入库方向必须接销售自己的契约，不能调用采购事务或机械复制入库默认值。 |
| 文件查看器 | 沿用旧项目已适配的图片/PDF 缩放、拖动、旋转、复位、缩略图、多页翻页、滚轮/双指缩放。原生 Button + 全屏 Dialog，关闭回到原附件位置并保留表单；`1 / 2`、`100%` 与按钮垂直居中，按钮组整体换行。不要另换成只能打开新标签的查看器。 |
| 附件下拉菜单 | 外层附件列不能用 `overflow:hidden/auto` 或过小的 `max-height` 截断菜单。最后一张用户截图的问题已修复：只裁切票图画布，附件类型下拉完整显示且已实际选择“付款凭证”。 |
| 产品范围与文案 | 中文界面已全局统一“物料”；不改用户业务数据或旧导入兼容别名。官方组件只提供控件，不授权官方所有业务功能。无用开关、帮助长文、概要、审批、Google Drive / Sheets 同步均不追加；旧截图中的 AI 标记、分支管理等不照搬。 |

现有采购只读详情、付款、更正、作废、凭证按钮均依照自己的权限与状态。销售应核对自身已有能力再映射，不通过改一个 API 前缀把采购付款、凭证或库存逻辑套过去。

### 代码阅读顺序

以下前端路径均相对 `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/`：

| 文件 | 可直接参考的实现 |
| --- | --- |
| `purchase.js` | `p.routed_page` 路由、列表列定义、搜索/筛选、原生选择、动作恢复、销毁和返回 |
| `purchase-entry.js` | 默认 OCR、原生 Check 卡片互斥、字段显隐、`can_submit`、上传成功状态、整批一次识别、清理 |
| `purchase-layout.js` | 连续 DOM、电脑左右布局、手机滚动定位与下划线、标题高度变化、ResizeObserver/滚动监听清理 |
| `purchase-fields.js` | 原生 Section / FieldGroup、逐项明细字段、金额区及附件区；字段名和采购规则不能原封不动复制 |
| `purchase-form.js` | 同页回填、金额更新、原生冻结层、提交状态、已存单与更正单生命周期 |
| `purchase-viewer.js` | 原生 Button 的 `only_input`、工具栏分组、页码/缩放居中、图片变换与全屏还原 |
| `purchase.css` | 60/40 与 320px、卡片只占左侧、767px 手机断点、紧凑预览和附件菜单防裁切 |
| `purchase-intake.js` | 已有 LINE 待处理能力的条件展示；只是参考，不为销售凭空增加 LINE 入口 |
| `sales.js` | 当前销售入口；仍是列表进入大弹窗、先保存草稿再开单，尚未适配采购交互 |

其他落点：

- ERP `hooks.py` 登记脚本依赖顺序与 CSS；资源通过 `versioned_asset` 带版本参数。新拆脚本必须登记，不能只写文件但不加载。
- Core `public/desk/list.js`、`public/desk/product.js` 提供原生列表/选择/路由/布局；沿用其接口。列表 formatter 返回转义文字，需要标色用 `cell_class`，不要返回自制 HTML。
- 采购 PDF 后端 `purchasing/preview.py` 是只读、鉴权后分页渲染，包含采购/Intake 绑定校验。销售复用查看器时需接自己的文件归属权限，不能绕过鉴权或直接把销售文件送入采购归属校验。
- 销售已有 `sales/api.py`、`service.py`、`contracts.py`、`ocr.py`、`render.py`；DocType 在 ERP 模块的 `doctype/erp_sales_document` 与 `doctype/erp_sales_document_line`。原有创建/更新、开单、作废、OCR、打印等接口已存在；接口存在不代表应全部展示按钮。
- 当前销售编辑字段包括 `buyer_name`、`doc_date`、`doc_kind`、`party_type`、`tax_id`、`address`、`branch_type/no`、`has_vat`、`vat_rate`、`payment_status/method/date`、`note`、`lines`；仅为查代码入口，不是完整迁移字段清单。现有 `draft/pending_approval/issued/void` 与分步开单要在服务端协调为目标交互，不再给用户增加不需要的草稿/审批步骤。
- 词条写 ERP `i18n/messages.tsv` 后生成 CSV；保持现有中/泰/英/日翻译入口。采购卡片的两条说明是用户明确要求的例外，不因此给全部字段新增 description。

### 当前演示环境：原样保留，可继续在右侧看

本次交接已只读回查镜像、Compose 标签、挂载和容器状态；六个容器仍运行，db healthy：

| 项目 | 实际值 |
| --- | --- |
| Docker context / Compose project | `colima-pearnly` / `pearnly-purchase-lab` |
| Compose 文件 | `infrastructure/compose/erp-pos-lab.yml` + `infrastructure/compose/erp-purchase-preview.yml`，来自当前采购工作树 |
| App 底座镜像 | 四个 App 服务均为 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`；源码挂载预览，不代表代码已烘焙进候选镜像 |
| `pearnly-purchase-lab-bench-1` | 常驻 bench / Python 检查入口 |
| `pearnly-purchase-lab-web-1` | Gunicorn，实际响应 Desk/API；日常代码修改后重启这个 |
| `pearnly-purchase-lab-socketio-1` | Socket.IO |
| `pearnly-purchase-lab-frontend-1` | nginx；`127.0.0.1:8147 → 8080` |
| `pearnly-purchase-lab-db-1` / `redis-1` | MariaDB / Redis；继续沿用，不重建数据或清卷 |
| Site / 数据 | `erp-pos-a.localhost` 演示；`erp-pos-b.localhost` 隔离检查。均为有 lab 标记的合成数据；没有外部 OCR 凭据与调度器 |
| 持久卷 | `pearnly-purchase-lab_sites`、`pearnly-purchase-lab_db` 及容器日志卷，均保留 |
| 源码挂载 | 当前采购树下 Core / ERP / Control App 包只读；Cowork / DMS 只挂 features.json；scripts → `/workspace/scripts`，tests → `/workspace/tests`，均只读 |
| 登录 | 本地隔离测试账号 `purchase-preview@example.test`，现有浏览器会话可继续用；如过期，从 `scripts/erp-purchase-preview-init.py` 读取本地夹具登录信息，不重跑初始化重置设置。本环境不是 Keycloak SSO 验收 |

预览入口：

- [采购列表](http://erp-pos-a.localhost:8147/desk/pearnly-purchase) / [默认上传识别新建页](http://erp-pos-a.localhost:8147/desk/pearnly-purchase/new)。
- [PUR-2026-00202 查看器样本](http://erp-pos-a.localhost:8147/desk/pearnly-purchase/PUR-2026-00202)：既有合成单 `PE2569-000073`，此前回读 3 盒 × 100、含税 321、已付及入库，PDF/PNG/替代收据共三个附件。识别回填当时用响应替身，实际提交和附件为本地真实操作；不要当作真实 OCR 成功记录。
- [销售现有入口](http://erp-pos-a.localhost:8147/desk/pearnly-sales)。这是现状页，未完成目标改造；`/new` 等销售子路由要施工后再验，不冒称已经可用。

原盘点的 `colima-pearnly-stocktake` / `pearnly-stocktake-acceptance` / 8136、8126 属于已交接环境。本销售包不要调用 `stocktake-preview.sh` 或接管它们。

### 修改后立即看到：日常最短命令

当前容器还挂采购树时，在该树改代码。交接销售树并切换挂载后，在销售树改代码。路径必须与实际挂载一致；仅换 shell 当前目录不会让容器换源码。

```bash
cd /Users/skin/.codex/worktrees/erp-purchase-records/pearnly
docker --context colima-pearnly exec pearnly-purchase-lab-bench-1 bench --site erp-pos-a.localhost clear-cache
docker --context colima-pearnly restart pearnly-purchase-lab-web-1
```

随后刷新右侧内置浏览器自己的预览标签即可。**这是源码挂载 + 手动重载，不是 HMR 自动刷新。** JS、CSS、Python、hooks 都走这条短路；CSS 也要重启 web，因为 `?v=` 资源版本在进程中缓存。无需为了 Desk 原始 JS/CSS 每次构建 Vue 或 Docker 镜像。翻译修改先在宿主工作树执行 `python3 scripts/i18n-build.py`，再做上述重载。只有 DocType / patch 等结构变更才按影响做原生 migrate，不能每次 CSS 调整都迁移或重新灌数据。

右侧用同一 8147 地址。下一窗口重新获取可用浏览器和标签，使用自己的预览标签；旧 browser/tab ID 不作为固定配置，不改用户正在编辑的页面。桌面验证后如设置 390×844 临时视口，离开前恢复。

### 下一窗口创建销售工作树并沿用这套容器

以下是**接手后的操作步骤，本次文档交接没有执行**。先确认采购树干净、没有另一个窗口继续改同一预览；这是同一演示环境的交接，不是两个窗口同时使用不同源码。项目名仍沿用 `pearnly-purchase-lab`，保留 URL、镜像和 Site 数据。

1. 读取采购分支最新 tip（应包含代码定稿和本次交接），核验主线/共享基线。销售路径与分支若已存在，先查归属，不覆盖。尚不存在时执行：

```bash
git -C /Users/skin/.codex/worktrees/erp-purchase-records/pearnly status --short --branch
git -C /Users/skin/.codex/worktrees/erp-purchase-records/pearnly log -1 --format='%H %s'
git -C /Users/skin/.codex/worktrees/erp-purchase-records/pearnly worktree add \
  -b codex/erp-sales-records /Users/skin/.codex/worktrees/erp-sales-records/pearnly \
  codex/erp-purchase-records
```

2. 从销售树解析相同 Compose，再只重建四个 App 服务以切换只读源码挂载；不运行 down，不删卷，不重新初始化 Site：

```bash
cd /Users/skin/.codex/worktrees/erp-sales-records/pearnly
docker --context colima-pearnly compose -p pearnly-purchase-lab \
  -f infrastructure/compose/erp-pos-lab.yml -f infrastructure/compose/erp-purchase-preview.yml config --quiet
docker --context colima-pearnly compose -p pearnly-purchase-lab \
  -f infrastructure/compose/erp-pos-lab.yml -f infrastructure/compose/erp-purchase-preview.yml \
  up -d --no-deps --force-recreate bench web socketio frontend
```

这里的 `-p pearnly-purchase-lab` 不能省略；基础 Compose 的默认项目名不同。四个服务需一起切，避免 web、bench 和 nginx 各读不同工作树；db/redis 保持原样。若配置失败先解决原因，不能继续运行下一条。回退挂载时从保留的采购树执行同一组命令，不能靠删 Site 恢复。

3. 回读四个 App 容器的镜像与挂载：App / scripts / tests 应全部指向销售树，sites 卷仍为原卷。然后清缓存、重启 web、刷新右侧页面。

```bash
docker --context colima-pearnly inspect \
  pearnly-purchase-lab-bench-1 pearnly-purchase-lab-web-1 \
  pearnly-purchase-lab-socketio-1 pearnly-purchase-lab-frontend-1 \
  --format '{{.Name}} {{.Image}}{{range .Mounts}}{{println}}{{.Source}} -> {{.Destination}} RW={{.RW}}{{end}}'
docker --context colima-pearnly exec pearnly-purchase-lab-bench-1 bench --site erp-pos-a.localhost clear-cache
docker --context colima-pearnly restart pearnly-purchase-lab-web-1
docker --context colima-pearnly compose -p pearnly-purchase-lab \
  -f infrastructure/compose/erp-pos-lab.yml -f infrastructure/compose/erp-purchase-preview.yml ps
```

### 销售接续验证重点与已知限制

- 本次只是文档交接：检查文档链接/路径、命令语法、Compose 解析、Git 差异及运行信息；不重跑业务探针或为文档重载 web。此前采购结果绑定上面的代码及各批证据，不自动算销售通过。
- 销售修改后按影响执行架构、JS 语法、前端词条/翻译一致性及定向业务检查。仅改 Desk JS/CSS 不默认构建与该页无关的 Vue。全仓 strict-i18n 已有库存 workbook 的 18 处 bare CJK，历史总输出另含内部日志诊断；不扩大忽略或把已知失败写成全绿。
- 浏览器至少覆盖：列展示/搜索/筛选；单行、两行、本页全选与清空；方框不跳详情；默认 OCR 隐藏字段/禁提交；手动附件零识别；上传取消/部分失败；识别等待/失败/成功原位复核；重复提交、回退、直接路由；390px 连续滚动与下划线；附件菜单不裁切；多页 PDF 页边界、缩放居中、全屏返回；提交后的服务端字段、金额、库存/收款状态回读。
- **不要直接在保留的演示 Site 运行 `scripts/erp-pos-lab-sales-verify.py`。** 本次只读检查发现其第 71–72 行会遍历删除 Site 内全部 `ERP Sales Document`，还会重建夹具用户、追加 100 单位库存。下一窗口先改为限定本次样本/非破坏性验证，或另用全新隔离测试环境；不能为测试通过清掉用户演示记录。采购探针也不是 CSS 修改的默认刷新命令。
- 当前 8147 没有 OCR 凭据。可以核验配置拒绝和使用明确标记的响应替身验证交互；真实付费识别、真实 LINE、生产环境、手机触摸/真机及用户最终验收都不能据此写“已通过”。销售真实文件识别需要后续真实配置与对应授权，不为演示偷偷连生产。
- 用户已确认物料/库存六页，采购尚无最终验收声明。本窗口不代签采购验收；下个窗口保留其可访问样本，销售开发不得破坏采购交互。

### 本次收尾：交接保留

本次实际远端回读 main 为 `ac33e0c7362915ae314295cb26f7748cb5589de0`，远端 `codex/erp-purchase-records` 不存在；本地主线仍为 `717d202af3cfcd06815e215ef088e07f4251e00e`，没有包含采购新增提交。本次仅在采购任务分支提交两份交接文档，未合并、未推送、未部署。

| 保留项 | 归属、用途和解除条件 |
| --- | --- |
| 采购工作树及本地分支 | 本采购任务成果和销售基线；未合入主线且当前仍被源码挂载。下一窗口接手时复核；按授权集成、无独有未保存成果且解除挂载后才清理 |
| 六个 `pearnly-purchase-lab` 容器、db/sites/日志卷 | 本采购演示环境，交接给销售继续演示；本次原样运行。下一窗口完成挂载切换后在销售任务记录登记接手；待演示不再需要且数据已获妥善保留后再清理 |
| 合成采购单和私有附件 | 给用户与下一窗口对照交互/查看器，包含 PUR-2026-00202；保留，不作为临时文件删除；后续明确清理样本时按编号处理 |
| 原 stocktake / ERP-pos / Cowork 工作树和环境 | 归各自任务，本次只读查看状态，没有修改或获得清理授权 |

本次没有创建临时夹具、额外服务或销售工作树，没有可删除的本任务新增临时资源；运行环境不关停。交接完成后下一步是销售来源/字段核对、创建销售工作树、接手 8147 挂载，再按上述定稿施工。
