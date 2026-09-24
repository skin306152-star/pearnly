# ERP 单框录入、菜单权限与公司快捷切换

状态：本地实现和定向验证完成，保留独立分支及预览，等待用户验收。用户最初要求只读定位，随后明确授权独立分支实施并在本地预览；最新要求是恢复之前左上角的公司快捷子菜单，以及团队权限每行两组的层级排版。未部署生产，未合并或推送。

## 已实现与落点

- **公司切换**：参考旧提交 `7f7a33d` 恢复“左上角 → 公司 › → 公司列表”，当前公司打勾，选中后更新当前用户公司并刷新页面。原 ERP `SidebarHeader` 子类被后加载的共用外壳类覆盖，导致原有菜单消失；改为 core 提供菜单扩展回调，ERP 自己声明公司内容，继续使用 Frappe 原生嵌套菜单。额外修正原生 hover 打开后第一次点击又关闭子菜单的问题。实现：`public/desk/company-menu.js`、core `public/desk/product.js`。
- **采购/销售单框录入**：供应商、客户、物料各一个原生 Autocomplete 可见输入，点击查询候选，同一入口扫码；显示名称和内部主键分开保存。OCR 名称立即可见，清空、更名及重新识别清除旧关联，提交前等待选择同步。候选按已授权业务上下文查询，物料支持编码/条码/二维码，往来单位支持名称/税号；查选不开放对应管理模块。实现：`masters/selection.py`、`public/desk/entry-choice.js` 及采购/销售表单。
- **采购/销售服务端**：税号和分支优先匹配，名称仅在唯一匹配时采用，同名歧义要求选择；不把自由文本当内部主键。OCR 仅接受票面字段白名单，禁止注入供应商/客户/商品主键、公司、仓库和记账开关。开单采用当前公司，避免旧原生默认值串入已切换公司的单据；原生及业务接口核对公司范围。
- **盘点**：新模板枚举启用商品并合并库存，尚未入库的商品账面数为 0，实盘数留空；上传先解析再创建任务，空表不会留下空盘点单。已创建任务继续使用自己的快照。
- **菜单权限**：18 个叶子菜单映射到原生 Role/Custom DocPerm；父组只负责全选/取消，部分子项选中显示半选。产品 hooks 声明分组、菜单和术语，服务端能力和前端入口遵循同一目录。旧五类员工角色原生幂等 patch 转换为明确叶子角色，并保留原来的首页/个人信息能力；不覆盖管理员后续勾选。
- **团队排版**：原生 Section Break/Column Break/Check/MultiCheck，每行两组，子项单列缩进在父项下；公司范围另设一组。角色与公司范围在写入前一起校验，同一次本地事务保存，不向成员授予 ERP Admin 或 System Manager。
- **隔离补齐**：侧栏、左下角用户区、首页面包屑与初始落地路由按授权显示；专用 RPC 和原生文档接口检查角色、公司及入/出库方向。只有盘点权限不能直接调用任意库存调整；收银记录、交班记录、POS 分离，保留各自所需筛选查询。首页、个人信息、团队管理独立授权。Frappe 自动生成的 User 自共享可能绕过个人信息撤权，新增通用 hook 撤掉不允许个人信息的用户自共享；未声明该权限的其他产品保持原行为。
- **共用菜单过滤修复**：使用 `frappe.get_attr`，保留合法空结果，过滤异常时关闭入口，避免公司 POS 菜单过滤失效。只在本任务分支修改共用 core，DMS 分支和运行环境未改。

## 菜单与实际入口

业务菜单还受购买功能约束；POS 相关入口还受公司 POS 开关约束。角色名均为 `ERP Menu <key>`，管理员保留原有管理能力，个人信息仍限本人。

| 组 | 独立子菜单 / key | 点击去向与用途 |
| --- | --- | --- |
| 常规 | 首页 / home | `pearnly-home`，现有首页 |
| 物料与库存 | 物料 / products；库存 / inventory；入库 / stock-in；出库 / stock-out；收发存报表 / stock-card；盘点 / stock-count | 对应 `pearnly-<key>` 原生业务页 |
| 采购系统 | 采购记录 / purchase；供应商 / suppliers | 采购列表/录入及供应商主档 |
| 销售系统 | 销售记录 / sales；收银 POS / cashier；收银记录 / pos-receipts；交班记录 / pos-shifts；客户 / clients | 销售列表/录入、POS 及历史记录、客户主档 |
| 用户设置 | 个人信息 / profile；团队管理 / team；公司 / company；仓库 / warehouses | 本人 `User` 表单，其余为对应 `pearnly-<key>` 原生管理页 |

公司快捷切换仅列当前用户可访问的启用公司，不需要公司管理菜单权限；只有一家时不显示切换子菜单。普通成员仅授权采购时，仍能查选采购必需的物料/供应商，不能进入这些主档的管理接口。

## 验证结果与限制

验证环境为专属 Docker Compose `pearnly-erp-entry-lab`，Frappe 16.33、无 ERPNext，合成 Site 和合成资料。结果针对本分支源码，不代表 testerp 已更新。

| 检查 | 结果 |
| --- | --- |
| `scripts/erp-entry-lab-verify.py` | 13 个真实 Site 定向测试通过：采购落账与库存、销售落账、查询与管理权限拆分、独立菜单、个人信息撤权及共享、POS 历史筛选、原生出入库隔离、零库存模板和盘点过账、团队原子校验与公司切换、OCR 禁止注入主键、同名歧义、旧角色幂等迁移、访客/错误上下文拒绝 |
| `tests/test_erp_portal_access.py` | 102 项：96 通过，6 项既有环境依赖跳过 |
| `tests/test_native_desk_contract.py` | 22 通过；包含 DMS/Cowork 既有菜单契约及过滤空结果/异常 |
| `node --test tests/test-native-desk.cjs` | 4 组通过 |
| `scripts/check-architecture.py` | 0 违规 |
| `scripts/check-dms-offline.py` | 331 项离线断言通过，无外部业务调用 |
| `scripts/i18n-build.py --check`、前端 i18n 检查 | 通过；前端 675 个键 / 35 个文件，0 问题 |
| 全库 `check-i18n --strict` | 498 项既有问题，输出与基线一致；没有扩大忽略或删除断言 |
| `check-desk-ui --strict` | 1 项既有问题：company.js:286 的 Table 位于 Column Break 后，main 同样存在 |
| 新 Site 安装、已有 Site 升级 | 临时新 Site 原生安装 core + ERP 成功，18 角色及依赖授权正确；预览 Site 原生 migrate 连续两次成功 |

浏览器在本机内置浏览器完成：

- 左上角“公司”原生子菜单打开，切换一店/二店，标题和当前公司同步。
- 添加用户显示两列分组和完整中文菜单；勾选“物料与库存”后取消“盘点”，父组半选且其余五项保留。
- 单框采购/销售查选，注入固定 OCR 返回值验证实际表单回填；扫码入口注入解码值后走真实选择接口，供应商/客户及物料能命中且 OCR 单价保持。临时浏览器桩已恢复并刷新清除。
- 本机合成采购单 `PUR-2026-00012`（票号 `LOCAL-ENTRY-001`）实际提交，采购、付款及库存回读正确，保留为预览数据。
- 仅“采购记录”成员登录 `/desk` 落地采购页，只见采购入口；供应商管理、首页和本人 User 资源接口均返回 403；直接进入团队页没有团队数据。随后恢复预览管理员会话。

**证据边界**：真实相机/扫码设备、真实付费 OCR 供应商、testerp 的原发票重新采购和用户验收尚未执行。菜单相关自动测试与定向浏览器验证不等于所有 18 项页面逐一真机验收。未发送外部消息、未修改正式租户资料、未执行生产部署。

## 本地预览与复验

- URL：`http://erp-pos-a.localhost:8157/desk/pearnly-team`。
- 管理预览账号：`purchase-preview@example.test`；仅采购成员：`menu-preview@example.test`；两者本地合成密码均为 `erp-pos-lab-only`。
- 两家公司：ERP 预览一店、ERP 预览二店；物料：测试咖啡豆、测试茶叶、尚未入库商品；均为本任务造的演示资料。
- Docker context：`colima-pearnly`。Compose 基础文件 `infrastructure/compose/erp-pos-lab.yml`，本任务覆盖文件 `infrastructure/compose/erp-entry-preview.yml`；固定镜像 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`。
- backend 使用内部网络，前端仅绑定 `127.0.0.1:8157`；本任务源码只读挂载，数据库、Redis 和 Site 卷独立，无外部凭据，调度/邮件关闭。
- 在本工作树复验：`docker exec pearnly-erp-entry-lab-bench-1 env/bin/python /workspace/scripts/erp-entry-lab-verify.py`。测试只允许带实验标记的 Site B，新增测试业务资料，不清空用户数据；账号幂等复用，遵守框架限流。
- 仅在需要重新建演示资料时运行 `scripts/erp-entry-preview-init.py`；它会重设预览账号密码并使旧会话失效，不作为每次重载动作。

## 交接保留与清理

- 基线、本地目标 `main` 及远端 `refs/heads/main` 核对为 `57039ca15595641932f053fd34c49d4e45dba964`；任务远端分支不存在。
- 本地分支 `codex/erp-purchase-menu-diagnosis`，工作树 `/Users/skin/.codex/worktrees/erp-purchase-menu-diagnosis/pearnly`。本次成果保存为本地提交；按用户要求保留独立分支供预览，尚未合入 main、未推送。
- 保留本任务 6 个容器（bench/web/frontend/socketio/db/redis）及 named volumes `pearnly-erp-entry-lab_db`、`pearnly-erp-entry-lab_sites`；另有运行中容器专属的 4 个日志匿名卷和 1 个 Redis 匿名卷。均属于本任务预览，挂载此工作树；删除工作树会破坏预览，不能为了清理提前删除。
- 预览 Site A 与回归 Site B 的合成账号、业务资料继续保留，服务于用户验收和后续问题复现。解除条件：用户结束预览并完成代码保存/合并安排；届时先重新核对容器挂载、分支独有提交和资料归属，再逐项清理本任务资源。
- 临时新安装验证 Site `erp-entry-install.localhost` 已通过原生 drop-site 删除数据库/用户，其任务专属归档目录一并移除；不触及 A/B 数据。
- 本次不删除工作树、本地任务分支或运行预览资源；无任务远端分支可删。DMS 及其他 ERP 工作树/容器全部保留原状。这是交接保留，不是已合并发布或全部清理完成。

## 初始只读诊断记录

以下为用户首次要求“先定位”时取得的历史证据；涉及线上镜像、数据数量和原实现的描述均指该诊断时点，不能据此断言生产已修复。后续实现和本地验证以本文前半部分为准。

## 基线与核验范围

- 日期：2026-09-24，Asia/Bangkok。
- 本地 main、origin/main 缓存及远端 `refs/heads/main` 回读均为 `57039ca15595641932f053fd34c49d4e45dba964`。
- testerp 当前 ERP web 镜像：`sha256:a11eb9e36bae8ca16e3dc37cbda45915fd1ccf18d4b28714c21d235fbff4934f`；OCI 源码标签 `dd81a3f009e255c2facbb8da818cd92a98face30`。
- 生产 Site 为 `testerp.pearnly.com`，容器为 `pearnly-production-erp-web-1`，业务卷 `pearnly-production_sites-erp`。App 代码来自镜像，没有工作树源码挂载。
- 当前 main 相对镜像源码，ERP App 与 core/desk 无差异；九个相关文件的生产 SHA256 与本地逐项一致：purchase-fields.js、purchase-form.js、purchasing/entry.py、purchasing/recognition.py、access/rules.py、team.js、hooks.py、inventory/api.py、inventory/count.py。
- 数据查询显式使用 `START TRANSACTION READ ONLY`，结束 rollback/destroy。未重放 OCR、采购提交或盘点导入，未调用权限修改接口。

## 已定位的问题

### 1. OCR 供应商名称与可见输入框断开

证据入口：

- [purchasing/recognition.py](../../apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/recognition.py)：OCR 合同返回 `supplier_name`、税号、分支和地址，不负责产生数据库供应商主键。
- [purchase-form.js](../../apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-form.js)：识别结果按字段名直接回填，没有供应商匹配步骤。
- [purchase-fields.js](../../apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-fields.js)：可见字段是 `supplier`，类型 Link/ERP Supplier；真正收到识别名称的 `supplier_name` 是 hidden。

因此，即使识别结果有供应商名称，也不能出现在可见的供应商框中。只设置主档 title_field 不会把票面的自由名称转换成一条已有主档。该原因可由当前生产源码证明，但本轮没有取得当次 OCR 的原始响应，不能断言模型当时实际返回的名称字符串。

识别服务目前只验证顶层字典及 lines，未做完整字段白名单。修复时还要阻止识别结果直接提供 `supplier` 等数据库引用，供应商主键必须来自服务端匹配。

### 2. `ERP Supplier 1234 未找到` 是供应商引用失败

[purchasing/entry.py::_supplier](../../apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/entry.py) 优先把非空 `supplier` 当作 ERP Supplier 主键读取；只有它为空时才按 `supplier_name` 匹配或在正式提交内新建。

- 当前 Site 只有一条供应商记录，主键 `SUP-00008`，不存在主键 `1234`。
- 在只读事务中调用 `_supplier({"supplier": "1234"})`，得到 `DoesNotExistError: ERP Supplier 1234 not found`，与图二相符，执行在读主档处中止，没有数据写入。
- 仅凭截图与现存记录，无法判断 `1234` 来自输入、旧页面残值还是异常响应；本轮不把来源猜测写成事实。

实际业务状态：商品 4 条；采购单、库存流水、库存余额、出入库单、盘点任务均为 0。可以确认采购尚未落账，不能据此宣称供应商修好后整笔采购一定通过。商品匹配、单位换算、公司仓库、附件及库存入账仍需隔离环境完整验证。

### 3. 盘点上传的是空模板，生成逻辑遗漏“已建商品但未入库”场景

- Site 原生 File `3843e33535`，文件名 `stock-count-template (1).xlsx`，创建时间 `2026-09-24 12:29:11.216369`。
- 只读解析：唯一工作表 `Stock count`，1 行、8 列；第 1 行是编码、物料名称、条码/二维码、单位、账面数量、实盘数量、仓库、仓位。非空数据行 0。
- 使用当前纯解析器 `inventory.workbook.parse_workbook` 读该原文件，得到 `ValueError("empty")`。没有重新上传或导入。
- [inventory/count.py::template_rows](../../apps/pearnly_erp_portal/pearnly_erp_portal/inventory/count.py) 从 ERP Stock Balance 枚举商品，随后关联商品主档。当前余额为 0 条，因此会生成只有表头的模板；录商品主档本身不生成库存。

报错与文件内容一致。需要改善的是模板覆盖和下载前反馈，不能让空表被当作成功导入，更不能把未填写的实盘数量自动当成 0。

### 4. 团队授权粒度和菜单判据都不足

- [access/rules.py](../../apps/pearnly_erp_portal/pearnly_erp_portal/access/rules.py) 实際只有五种可分配业务角色：ERP Products、ERP Stock、ERP Purchase、ERP Sales、ERP Cashier。
- `warehouses/stockcard/stocktake` 共用 ERP Stock，库存、入库、出库、收发存报表和盘点无法单独授权；采购记录和供应商共用 ERP Purchase；销售记录和客户共用 ERP Sales；POS、收银记录、交班记录共用 ERP Cashier。
- [team/service.py](../../apps/pearnly_erp_portal/pearnly_erp_portal/team/service.py) 输出平面角色目录；[team.js](../../apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/team.js) 只渲染一份平面 MultiCheck。
- [hooks.py](../../apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py) 的多数页面权限按 DocType read 判断。[core/desk/navigation.py](../../apps/pearnly_core/pearnly_core/desk/navigation.py) 将该判据同时用于侧栏和左下角用户菜单。
- 生产 Custom DocPerm 回读：采购、销售、收银都有 ERP Product 和 ERP Company 的 read；收银还有 ERP Customer 的 read；库存角色还有 ERP Warehouse 的 read/write/create。它们部分是开单查询依赖，却会同时满足对应管理页面的菜单准入。应拆分“业务选择时能查询”和“有权进入这个模块”。

这不是已证明的跨租户数据泄漏；本轮确认的是当前模型无法满足用户要求的子菜单授权与入口隔离。后续必须同时核对专用 RPC、原生资源 API、列表/导出/文件权限，不能只把菜单藏起来。

### 5. 公司 POS 菜单过滤器在当前生产中调用失败

[core/desk/navigation.py::_product_page_filter](../../apps/pearnly_core/pearnly_core/desk/navigation.py) 使用 `from frappe.utils import get_attr`。当前运行框架在此报 ImportError；应使用实际存在的框架入口 `frappe.get_attr`。

- 今天 Error Log 中 `erp page_filter failed` 共 61 条；抽查 12:34 和 12:36 两条均为上述 ImportError。
- 异常分支返回原 offered，所以公司级 POS 过滤没有实际生效。服务端 `cashier.*` 的公司开关检查仍存在，不能把菜单显示故障等同为业务调用已绕过。
- `get_attr(spec)(offered) or offered` 还会把合法的空过滤结果替换成原菜单；应区分空字典、无声明和失败，不以原菜单回退掩盖过滤错误。

另有两条 11:32/11:33 的启动日志为“账号没有可用公司”。当前已有一间启用公司及其仓库、收银台，一间停用公司及其原仓库/收银台。该历史启动异常不能直接当作图二采购失败的原因。

### 6. 关联影响

- [sales-fields.js](../../apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/sales-fields.js) 同样用可见 customer Link 加隐藏 buyer_name，销售识别合同返回 buyer_name；存在同类回填断点，本轮只做静态确认，未执行真实销售识别。
- 当前团队新增/修改角色与公司范围是两个独立请求，前一步会提交；第二步失败时可能留下公司范围未落下的账号，而当前空公司权限代表不限制。菜单权限改造应把本 Site 的角色与公司范围校验、保存合并成一次事务；外部身份创建仍按现有归属和幂等协议处理，不假装能跨系统数据库回滚。

