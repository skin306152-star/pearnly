# ERP 单框录入、菜单权限与公司快捷切换

状态：最新第五轮已按用户要求修正团队长弹窗、恢复统一候选样式，回到快速预览 `http://erp-pos-a.localhost:8157/desk/pearnly-team`。8167 的真实验收容器和网络已删除，其专属虚拟机已停止，不再继续该环境测试。独立分支保留，未合并、未推送、未部署。第四轮应用 `afca8c8b450a2ec4df304e5b45fe96111dda4867` / 镜像 `sha256:1ff37316b1bb78700f99fceae29c7fbe0060ebb29639f48509feefc6d4eaa586` 及其验收结果为历史证据，不代表现在运行的环境；最新 UI 修改见第五轮。

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

## 第二轮：扫码避让与原地维护主档（2026-09-24）

用户进一步要求：所有扫码/箭头重叠处统一避让；供应商、客户、物料、仓库的新增和箭头点击留在当前页面内打开已有弹窗，保存后刷新主档候选，保留已录单据。

### 实现

- 共用 core `desk/link.js` 登记产品提供的弹窗 opener，ERP 四个主档页复用各自原有编辑器，只打开 Dialog，不构建目标页面、不切路由、不关闭发起弹窗。原生 Link 与采购/销售 Autocomplete 都接入；没有登记弹窗的其他产品入口保持原路由。
- 原生选择动作在清空字段前被拦截，箭头阻止冒泡到 Frappe 全局路由。取消和保存都保留原表单实例；新增不自动替换已有选择。商品原地新增不提供跳转到批量录入页的“全屏编辑”。
- 保存后清除同一主档类型的候选缓存，下次展开直接查询新主档；不触发原业务字段 onchange，不覆盖已录名称、税号、数量、单价或备注。主档编辑器及服务端原权限保持不变；缺少 create 权限不显示新增项。
- 共用样式覆盖普通 Link、原生 Grid 单元格及采购/销售单框。平时扫码按钮宽 24px、右距 4px，箭头隐藏；悬停时扫码宽 20px 并按原生箭头实际宽度左移。移开后归位；键盘聚焦箭头也会保留操作空间，系统减少动态效果设置得到尊重。Grid 的按钮改挂在同时包含输入与箭头的原生 `link-field` 中。
- 新增候选名称使用产品术语“供应商 / 客户 / 物料 / 仓库”，不显示 ERP DocType 英文名。

### 本轮验证

同一专用环境、Site A 合成资料、内置浏览器，未调用生产或付费 OCR。

- 入库单保留“测试咖啡豆”、数量 7、成本 60、备注，箭头原地开物料详情，取消后内容保持；原地新增物料和仓库成功，单据仍在且所有原输入保持。
- 采购页保留供应商、税号、发票号 `LOCAL-INLINE-UNSAVED`、数量 9、单价 57；原地新增供应商后无需刷新页面即可在候选列表中看到它。
- 销售页保留测试客户、地址、数量 4、单价 75、备注；原地新增客户后候选出现，箭头原地编辑现有客户并保存后，单据仍保持。临时修改的演示客户主档备注已还原为空。
- 四个主档菜单逐页打开：均显示自己的列表和新增按钮，能看到新增测试记录，没有错误地自动弹出新增窗。
- 原生 Table/Grid 验证：悬停时扫码与箭头间距 3px，移开后箭头 opacity 为 0，扫码宽度/右距恢复 24px/4px；详情关闭后 Grid 数量仍为 7。临时网格验证界面已通过页面刷新清除。
- 仅采购成员：四类主档 can_create 均 false，供应商候选没有新增项，直接新增供应商 RPC 返回 403。随后恢复管理员预览会话。
- 变更 JS 语法、`git diff --check`、架构检查均通过；前端 i18n 675 键 / 35 文件无问题；原生 Desk JS 4 组通过；`python3 tests/test_native_desk_contract.py` 22 项通过；DMS 离线 331 项通过。没有后端规则或迁移改动，未重复跑上一轮 13 项业务全套。
- 合成新增资料：`原地新增验证物料0924`、`原地新增验证供应商0924`、`原地新增验证客户0924`、`原地新增验证仓库0924`。保留在本任务 Site A 供预览；本轮入库/采购/销售草稿没有提交成业务单据。仍未进行真机扫码验收。

### 保存与保留

继续使用 `codex/erp-purchase-menu-diagnosis`，本轮基线 `fd7d1bc489f11cc33045af8c02ed7daca954cce1`，保存为后续本地提交。预览入口为 `http://erp-pos-a.localhost:8157/desk/pearnly-purchase/new`，管理员已登录。沿用前述工作树、6 个容器和专属数据卷，没有新增容器/卷/后台进程；四条新演示主档随预览保留，用户结束预览并完成代码保存安排后复核清理。主线、远端、DMS 工作树与环境均未修改；未合并、推送或部署。

## 第三轮：手机附件拍照预览溢出

基线 `748f2e9a44d277b8ff6d98d4bb0cf623dacdaa13`；所属 App 为 `pearnly_core`，落点为原生产品外壳 `public/desk/product.css`。本轮只有显示样式变更，没有更换上传控件、改变原图或上传/OCR/权限契约。

- 锁定的 Frappe 16.33 `ui/capture.js::render_preview` 把原图放进原生缩略图网格，但图片没有最大宽度。390×740 浏览器中，1440×2560 合成照片仍显示为 1440×2560，Camera modal 的 scrollWidth 为 1460；与用户截图中的横向撑开相同。
- 在 `.pearnly-product-desk .frappe-capture .fc-preview-container img` 上设置最大宽度 100%、自动高度、最大高度 60dvh（60vh 回退）及块级居中。限定相机缩略图，不改变单据查看器的缩放或扫码视频。
- 共用覆盖：采购/销售附件、盘点实盘照片、物料编辑和批量物料 Grid 的 Attach Image、首页凭证及框架原生附件入口。Excel 导入仍禁用拍照。LINE 独立盘点用系统 file input，不创建这个相机弹窗。

### 本轮验证与限制

- 同一专属 Compose/Site A 和源码挂载，清缓存后仅重启本任务 web；未重启 DMS 或其他 ERP 预览。
- 采购手动模式，使用浏览器生成的竖图 1440×2560、横图 2560×1440、长图 200×8000，通过一次性的 Capture.show 测试替身进入真实 render_preview；真实上传器回调、删除和返回动作保持原样，没有访问相机硬件或上传服务器。
- 390×740、740×390、1280×800 下，Camera modal 的 scrollWidth 均等于 clientWidth，删除控件和底部按钮在屏幕内；删除长图后提交回到上传列表，两个文件预览仍保留 1440×2560 / 2560×1440 原始像素。取消上传后手动模式和提交按钮恢复可用。
- 物料编辑 → 添加文件 → 摄像头三层原生弹窗在 390×740 下均为 clientWidth=scrollWidth=390；1600×2400 图片显示为 112×168，提交回上传列表成功。测试未保存物料、没有新增附件记录。
- 架构检查 0 违规；原生 Desk Python 合同 22 项、原生 Desk JS 4 组通过；diff 检查通过。纯 CSS 修改不重复后端业务全套或付费 OCR。
- 测试替身随页面刷新清除，浏览器恢复默认尺寸。桌面 Chromium 的窄屏验证不等于 iPhone 真机验收；实际系统拍照/相册选图、正式上传与生产 testerp 尚未验收或部署。

### 保存与保留

本轮保存为独立分支上的后续本地提交；继续保留工作树 `/Users/skin/.codex/worktrees/erp-purchase-menu-diagnosis/pearnly`、分支 `codex/erp-purchase-menu-diagnosis` 及 `pearnly-erp-entry-lab` 的六个容器和专属卷，用于用户本地预览。没有新增后台进程、卷或持久化测试文件。解除保留条件为用户结束预览并明确合并/保存安排，届时再次核验后清理。当前主线和远端 main 仍为 `57039ca15595641932f053fd34c49d4e45dba964`，未合并、推送或部署；远端任务分支不存在，交接保留。

## 第四轮：完整性复查与生产结构本地验收（已完成本地验证）

用户要求“修复完整，并搭建真实线上环境容器在本地做最终验收”。本轮开始于 `7b2d40e47a3b4c21defe986c170187d10b5cfb26`，以下缺口由新复现确认，不能沿用前几轮通过结论把它们标为完成。

- 异步扫码旧响应可覆盖后来的手选、输入或 OCR 回填；无主档绑定的 OCR 名称纠错会清空税号/地址/分支。公共单框组件已修，52 项行为回归通过；旧实现同测 32 项失败。
- 单据公司、行仓及库存落账使用了不同的当前公司默认值；已贯通采购/销售、旧草稿、出入库草稿和历史冲销，新增真实 Site 正反向回归通过。
- 业务叶子的原生依赖过宽，采购用户可以直接读跨公司库存余额和成本；盘点/POS 的部分按号入口及过滤参数同样缺公司约束。原生列表、文档、导出、专业服务及历史授权撤回已修复并通过真实权限测试。
- 团队委派管理没有限制目标公司的范围，清空公司等于全公司；停订功能成员可能从团队列表消失。服务端目标/公司/角色校验及成员可见性已修复并通过回归。
- 叶子菜单矩阵从手写 13 项改为产品声明中的全部 18 项。覆盖率按实际场景及测量范围报告，不把测试数量换算成代码覆盖率。

### 当前生产基线与本地环境

- 只读回读生产 ERP image=`sha256:a11eb9e36bae8ca16e3dc37cbda45915fd1ccf18d4b28714c21d235fbff4934f`，源码标签 `dd81a3f009e255c2facbb8da818cd92a98face30`。Frappe 16.33.0，ERP Site 仅安装 frappe、pearnly_core、pearnly_erp_portal。没有复制生产数据或凭据。
- MariaDB 11.8 digest=`79d59758afc91b89b120b0a8904d637f5a3b3e1c4900f29b740d6d46c72fef68`，Redis 6.2 digest=`ad151300731763a711bf517916519e5cc8f035c95139aa1750836048390439d0`；生产 Redis queue DB=2、developer_mode=0。Keycloak 26.7.4 digest=`82a77884f3af238beab1e7afd63b5f530e1b5c0590bd7aa60b40a40463e29b2c`。线上已启用 OIDC 和 native account，本地已接入真实 Keycloak 与私有身份 broker 验证同一账号链。
- 新建专属 Colima profile/context `pearnly-erp-acceptance` / `colima-pearnly-erp-acceptance`（4 CPU、8 GiB、50 GiB），不修改当前默认 context，不接管 DMS/原 ERP 预览。宿主 ARM，通过 QEMU 执行 linux/amd64 候选镜像；硬件性能不代表线上。
- `infrastructure/compose/erp-acceptance.yml` 直接继承生产服务命令及固定基础镜像；独立 DB/Site/queue 卷、分离 Redis、web/worker/OCR worker/dispatch/scheduler/socketio/nginx。业务容器无源码挂载，出网隔离；预览仅127.0.0.1:8167。
- 首次 MariaDB 初始化因 amd64 转译时8TiB虚拟预留触发内存上限，2GiB与4GiB均失败。固定镜像 `mariadbd --verbose --help` 回读最大预留=8796093022208。仅本地增加 `innodb_buffer_pool_size_max=1G` 后成功，实际缓冲池仍1GiB、容器内存上限恢复生产2GiB。两次初始化失败的专属空卷经归属核验后重建，尚无Site/业务数据。参考 [MariaDB变量说明](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-system-variables#innodb_buffer_pool_size_max)。该本机转译适配不代表线上配置已修改。
- 先从固定生产源码归档构建 baseline，再用候选代码构建不可变镜像，验证现有 Site 升级及新安装，不以 bind mount 开发环境替代。
- 临时文件仅 `/tmp/pearnly-erp-acceptance-*`，baseline 归档 `/tmp/pearnly-erp-acceptance-baseline-dd81a3f`。新环境/旧8157预览/未合并工作树本轮暂保留，待用户完成本地验收且确定保存安排后复核清理。尚未推送、合并或部署生产。

### 最终候选与新增复查结果

- 应用最终候选源码 `afca8c8b450a2ec4df304e5b45fe96111dda4867`，不可变 amd64 镜像 `sha256:1ff37316b1bb78700f99fceae29c7fbe0060ebb29639f48509feefc6d4eaa586`（tag `pearnly/erp-acceptance:afca8c8`），来自与生产相同的 Dockerfile。ERP/Billing 的 10 个服务/bench 均回读为同一 image ID；App 源码未挂入容器，Keycloak 主题单独只读挂载。
- 在 `cfd0118` 候选实际 `frappe.client.save` 中复现：A 公司员工将 B 公司仓库/收银台的公司字段改为 A 后可保存；两笔复现均回滚。最终候选同时检查保存前归属、禁止历史主档换公司，将库存单写入统一收口到带对象能力的业务服务。新增原生修改、子行修改、删除、忽略验证、跳过校验重命名的拒绝回归及正常业务正向对照。
- 第一轮真实 Site 40 项：36 通过、2 错误、2 失败。销售旧草稿错误是测试的 owner 在原生 insert 时被 Administrator 覆盖，修正 fixture 后继续验证原公司授权和撤权；成本两项是框架对隐藏数字返回 0.0，改为严格比较数据库真实成本 17 和受限返回 0.0。收银列表是实际代码读取 `current_shift` 结构错误，修为 `active["shift"]["name"]`，补无班/当班/关班检查。没有删除权限断言或跳过失败。
- HTTP 首轮通过真实 nginx/Gunicorn 路径：私有 PNG 上传与权限、供应商/商品/客户/公司/仓库/附件关联、采购入库、销售出库、重复请求、原仓冲销、跨公司原生文档/列表/导出、六类跨租户资源拒绝。销售客户用真实已建主档按名称匹配，遵守既有“不自动建客户”的业务契约。
- 宿主离线 ERP 113 项中107通过、6项因宿主无 openpyxl 跳过；同6项随后在固定候选镜像内真实 openpyxl 运行全部通过。JS 单框52项、原生 Desk Python22项、JS4组通过；DMS 离线331项通过，未运行外部业务。
- OCR worker 初始化缺少自定义队列声明已按生产 site-step 补上，两个 worker 实际监听 OCR 队列，容量 Redis使用DB6。调度器在本机QEMU下192MiB触发OOM，仅验收覆盖层提高到384MiB，生产定义保持192MiB。此差异只解决本机转译开销，不作为生产容量结论。

### 最终修复与通过证据

更深的业务验证继续发现并修复两个真实问题：销售旧草稿更新会把未传字段覆盖为空，现只更新明确传入字段，并保留既有来源和备注；收银备用金未同步现金子行，交班汇总少计备用金，现同步新班次并兼容历史子行错误值。混合现金/卡/转账、找零、当前销售列表和关班对账均有回归。

| 验证层 | 最终证据 |
| --- | --- |
| 真实 Frappe Site | 最终镜像运行 46/46，通过且无跳过：菜单与录入 13、公司关联 10、完整业务流 9、原生权限边界 14；耗时 222.874 秒 |
| 菜单覆盖 | 从 ERP 产品目录枚举全部 18/18 叶子；独立授权、依赖查询、页面与服务端拒绝分别检查，包含首页/个人信息/团队/公司/仓库 |
| 真实 HTTP | 最终镜像经 nginx/Gunicorn 的 8 组场景全部通过；采购 `PUR-2026-00404`、销售 `SD-00406` 保留于 Site B |
| 关联链 | 私有 PNG 上传 → File 权限 → 供应商/客户/商品 → 公司/行仓 → 单据 → 库存流水与余额 → 原仓冲销；重复提交/冲销不会重复落账 |
| 原生越权负例 | 跨公司 document/list/export、跨租户六类精确资源 URL、仓库/收银台/库存单换公司、已提交库存单改状态/子行数量、跳过校验的原生 rename 均拒绝；回读原记录和库存保持原值，合法业务服务可继续保存/过账/冲销 |
| 真实身份 | 团队建成员 → 私有 broker → Keycloak subject 绑定 → 用户名/邮箱登录 → 重设合成成员密码 → 旧密码拒绝通过；ERP 没有本地密码和身份管理员凭据。浏览器授权码+PKCE登录也通过 |
| 升级与安装 | 固定生产源码 `dd81a3f` 建 baseline 并播入旧草稿/主档/限公司员工；cfd/805 候选升级 A/B，通过保留 IDs、数量、公司、授权检查；最终 afca 的 A 再 migrate 和保留检查通过。独立空 Site 在 cfd 完成全新安装，805 再 migrate 通过；afca 两处业务改动不改 schema，未把前一版全新安装写成 afca 从零安装 |
| 离线与架构 | 单框 JS 52、原生 Desk Python 22、Desk JS 4 组通过；ERP 离线 107+容器补跑 openpyxl 6 项通过；DMS 离线 331 通过；架构 0 违规，diff/辅助脚本语法/Compose 配置检查通过 |

真实 Site 的重复 fixture 批次触及 Frappe 每小时 60 用户创建限制，因此只在验证脚本进程将 `frappe.conf.throttle_user_limit` 设为 1000；没有写 Site 配置、没有改变 web 的生产限流，没有跳过业务授权或文档校验。HTTP 回归默认使用保留原生合成账户的 Site B，Site A 使用真实 OIDC。

最终执行日志：`/tmp/pearnly-erp-acceptance-afca-site-tests.log`、`/tmp/pearnly-erp-acceptance-afca-http.log`。日志是本机临时证据，结果及版本已在本记录保存。未测量全仓语句/分支覆盖率，不报告“100% 代码覆盖率”；此表陈述的是受影响链的场景覆盖。

### 最终浏览器验证

- 使用 8167 的真实 OIDC 管理员会话；左上角公司子菜单从 A 切到 B，仍停留原页面，当前公司回读为 B。
- 添加用户显示 5 组/18 叶子；桌面两组一行，子项相对父项缩进；全选物料与库存后单独取消库存，父项变半选、其他五项保留。没有通过 GUI 创建无关用户。
- 采购页保留票号 `UI-验收保留-20260924`、数量 3，原地新增供应商 `SUP-00038`、物料 `PRD-00039`、仓库 `WH-00040`，原路由和已录字段保持，候选无需刷新页面就出现。
- 销售页保留数量 4、备注“客户新增前保留备注0924”，原地新增客户 `CUS-00041`，即时可选；手动选中后回填公司类型和地址；箭头再次打开该客户详情仍停在销售页，关闭后数量和备注保持。
- 保存主档只刷新候选，不自动替换单据当前字段；选择新增候选后才应用主档值，避免覆盖 OCR 和人工输入。
- 扫码按钮在悬停时由 24px 收为 20px，左移让出箭头位置；箭头和扫码框无交叠。共用原生 Link/Grid/单框行为由此前浏览器及当前 52 项组件回归共同覆盖。
- 物料原地新增 → 原生图片附件 → Camera 三层弹窗，使用临时 Capture.show 输入替身提供 1440×2560、2560×1440、200×8000 三张合成图，其余原生预览/删除/提交/上传链不替换。390×740、740×390、1280×800 下各层 scrollWidth=clientWidth，操作按钮可见；删除一张后回到上传列表成功。
- 实际上传私有原图 `/private/files/capture_2026_09_24_17_19_57.png` 并保存物料成功；关闭图片优化后 naturalWidth/naturalHeight 仍为 1440×2560。一次上传恰逢本任务候选容器重建返回 502，运行稳定后同入口重试成功，并未丢失外层表单；无后续服务重启。
- 页面刷新已清除临时 Capture 输入替身，浏览器尺寸恢复默认，团队管理页保留给用户。浏览器采购/销售验收草稿未提交；合成主档和图片保留。

### 当前入口、复验与保留

- 当前入口：`http://erp-pos-a.localhost:8167/desk/pearnly-team`，浏览器管理员已登录。合成管理员 `AcceptanceOwner` / `acceptance-owner@example.test`，合成采购员工 `AcceptanceBuyer` / `acceptance-buyer@example.test`；本地合成密码均为 `erp-acceptance-only`。全局系统时区/语言/日期仍与生产一致（Asia/Bangkok、th、yyyy-mm-dd），仅这两个预览用户为中文。
- Compose 使用 `infrastructure/compose/erp-acceptance.yml` + `erp-acceptance-identity.yml`；环境文件 `/tmp/pearnly-erp-acceptance.env` 权限 0600。只能使用 context `colima-pearnly-erp-acceptance`，不要套用默认 context 或共享样板。
- 复验可将当前辅助脚本复制到专用 bench，再运行。以下操作只向合成 Site B 新增唯一前缀测试资料，不会清库，但需要允许本地测试写入：

```sh
docker --context colima-pearnly-erp-acceptance cp scripts/erp-acceptance-verify.py pearnly-erp-acceptance-bench-1:/tmp/erp-acceptance-verify.py
docker --context colima-pearnly-erp-acceptance exec pearnly-erp-acceptance-bench-1 env/bin/python /tmp/erp-acceptance-verify.py
docker --context colima-pearnly-erp-acceptance cp scripts/erp-acceptance-http.py pearnly-erp-acceptance-bench-1:/tmp/erp-acceptance-http.py
docker --context colima-pearnly-erp-acceptance exec pearnly-erp-acceptance-bench-1 env/bin/python /tmp/erp-acceptance-http.py erp-pos-b.localhost
```

- 账号脚本 `erp-acceptance-identity.py --verify` 会重设合成采购员工密码并终止该员工旧会话；不会重设管理员密码或修改其当前公司。仅需查看时不重跑。初始化/升级/HTTP/身份脚本均有验收标记和 Site 白名单保护，不可用于生产。
- 本轮已删除闲置 `pearnly-erp-acceptance-fresh-init` 容器及其专属匿名日志卷 `bdab72ef1b6b0defdccd8d4eaca47e7042ebc22fc921dcfab197632ad481a929`。独立新安装 Site 的 named 卷 `pearnly-erp-acceptance_sites-fresh` 仍保存数据库清理凭据，数据库位于专属验收 database 卷；保留至用户结束验收后原生 drop-site，不提前丢弃配置。
- 保留验收 Compose 的 14 个容器、专属 Colima profile、5 个 named 卷及运行服务的 11 个匿名卷。保留候选/基线镜像和 `/tmp/pearnly-erp-acceptance-*` 运行配置、构建归档、日志，供当前用户验收、升级复现和问题追踪；解除条件为用户结束本地验收、完成保存/合并安排，届时按资源引用逐项复核清理。
- 旧 8157 预览仍保留作为前轮对照，DMS 和其他 ERP 环境未动。工作树 `/Users/skin/.codex/worktrees/erp-purchase-menu-diagnosis/pearnly` 与本地分支 `codex/erp-purchase-menu-diagnosis` 未合并、未推送；工作树仍被旧预览源码及新验收 Keycloak 主题引用，不能删除。目标本地/远端 main 交付前回读仍为 `57039ca15595641932f053fd34c49d4e45dba964`，任务远端分支不存在。这是交接保留。
- 交付前只读回读生产仍运行 `a11eb9e…` / `dd81a3f…`，没有部署。本地使用 localhost HTTP、QEMU 和已记录的两项内存适配，不验收生产 TLS/吞吐；没有生产数据、外部付费 OCR 凭据或第三方消息发送。真实 iPhone 系统相机/相册、硬件扫码、付费 OCR 质量/速度及 testerp 原发票重走仍未执行；用户业务验收不由 Agent 代签。

## 第五轮：UI 变更核对、原生全屏编辑与切回快速预览

用户要求说明未经指定的文案和组件变化、解决团队弹窗过长，并删除真实演示容器。基线 `1a42414e76dceb3eeb218c7d010520c815c991a5`；继续使用原任务分支和工作树。本轮仅修改 ERP 前端四个文件和本记录，不改后端，不再构建不可变镜像或执行真实验收容器。

### 此前到底改了什么

核对范围为 `main 57039ca → 1a42414`，共 16 个前端文件和 5 个词条文件。不能把这些变化概括成“只改了后端”。后端权限/关联修复不要求改变无关界面；单框、分组、原地弹窗等用户明确要求确实需要前端联动，但具体组件选择和候选视觉是实现决定。

| 位置 | 实际变化与原因 | 本轮处理 |
| --- | --- | --- |
| `purchase-fields.js`、`sales-fields.js`、`entry-choice.js` | 供应商/客户/物料由 Link 加自由名称改成一个原生 Autocomplete，可容纳已有主档、扫码及未绑定主档的 OCR 文本。自定候选行曾把名称/编码/内部 ID 拼在一行，新增项少了原有加号；物料必填标记遗漏 | 保留单框与数据契约；候选恢复原生 Link 的名称/次行说明和“＋新建”呈现，不再重复展示内部 ID；恢复物料 `reqd` 与星号 |
| `purchase-form.js`、`sales-form.js` | 同步选择/扫码/OCR 名称和隐藏主键，防止旧关联残留 | 保留修复；没有改其他业务字段布局 |
| core `link.js`，ERP `clients.js`、`suppliers.js`、`products.js`、`warehouses.js` | 复用已有 Dialog 原地新增/编辑、刷新主档候选，保存不清掉当前单据 | 保留；物料从别处原地新增时不显示会导航离开单据的旧“全屏编辑”，物料菜单里的该按钮一直保留。这项按钮可见性变化需明确说明 |
| core `product.css` | 箭头悬停与扫码按钮避让、手机 Capture 图片显示尺寸约束 | 均对应用户明确要求；未替换相机或 FileUploader |
| `company-menu.js`、core `product.js` | 恢复左上角原生公司子菜单；首页/个人信息/落地页按菜单授权显示 | 保留；不是新增弹窗切换公司 |
| `team.js` | 五个粗权限改为全部18个叶子，Check/MultiCheck父子勾选、两组一排、子项缩进；默认Dialog的尺寸和留白适配不足 | 改大原生 Dialog、账号信息分两列、减少本表单父子间距；新增原生 Desk Page 全屏编辑，不重画通用组件 |
| `cashier.js`、`pos/pearnly-pos-adapter.js` | POS订单查询改为当前班次接口，以分离收银操作与历史收银记录权限 | 保留权限边界；这是列表范围变化，虽然没有换组件或文案，也必须说明 |
| `i18n/messages.tsv` 和四语言 CSV | 新增“常用功能”“用户菜单”两处分组文案，以及“多个匹配结果”“供应商不存在请重选”“客户不存在请重选”三条错误提示；CSV另补出已有“切换公司”翻译，当前无调用 | 未改写既有词条、未批量重命名菜单；两处分组名称属于实现时自选，并非用户指定文字。本轮不额外改其他文案 |

### 当前修正与验证

- 团队快捷弹窗使用原生 `modal-lg`；用户名/邮箱与密码分列；父项/子项只用原生间距工具类调整。保留两组一横排和子项缩进，未减少任何菜单。
- 左下角“全屏编辑”使用既有 `p.edit_all`（Frappe QuickEntry 的 custom-action 插槽），进入同一团队模块的原生 Page/FieldGroup：`pearnly-team/new` 或 `pearnly-team/edit/<email>`。沿用原保存接口、校验和权限；完整字段保留，字段值仅在内存交接，不放 URL。
- 8157 浏览器实测：输入合成用户名/邮箱、勾物料与库存后取消盘点、选择一家公司，转全屏后五个叶子仍选中、父组半选、盘点未选、公司和文字不变。未输入或创建新密码，没有新建用户。
- 已有合成成员 `menu-preview@example.test` 转全屏后用户名/邮箱仍只读；原采购记录角色和一店公司保持，原样保存成功并回到两人名单。没有扩大权限。
- 390×740 全屏页 `documentElement.scrollWidth=390`，取消/保存按钮都在屏内；随后恢复默认浏览器尺寸。全屏新建取消后返回名单正常，未完成的新表单不残留到下一次添加。
- 采购候选真实浏览器显示原生名称/次行编码、“＋新建物料”，无重复内部编号；选“测试咖啡豆”回填单位“件”及成本60。物料必填星号恢复；未提交采购单。
- 52项单框回归、22项原生Desk Python、4组原生Desk JS通过；修改JS语法、架构、diff检查通过，独立静态复查未发现新的路由/状态交接问题。本轮没有运行完整真实业务容器测试，也不把第四轮镜像结果套到新UI上。

### 容器删除与当前保留

- 执行只针对 context `colima-pearnly-erp-acceptance` / project `pearnly-erp-acceptance` 的 Compose down。14个真实演示容器、`runtime`/`preview` 两个网络已删除；回读该context容器清单为空、项目网络为空。再停止 `pearnly-erp-acceptance` Colima profile，回读为 Stopped，8167/8168不再服务。
- 按本轮“删除容器”的范围，没有清空数据库卷。该已停止profile的5个named卷、11个匿名卷、4版镜像和运行凭据/日志暂作为离线验收记录保留，不消耗运行内存；下一次用户要求清空验收数据或恢复时复核处理，不再自动重建环境。未删除共享卷、未全局prune。
- 快速预览仍为 context `colima-pearnly` / project `pearnly-erp-entry-lab` 的6个容器，端口8157，只读挂载本任务源码和独立Site/DB/Redis卷。只迁移/清缓存 Site A 并重启该项目web；没有重设预览账号密码。其原生本地账号通过既有登录API恢复浏览器会话，未改身份保护设置；该预览不接真实OIDC。未触及DMS、stocktake和其他ERP环境。
- 当前浏览器已打开8157团队管理，沿用 `purchase-preview@example.test` / `erp-pos-lab-only` 合成账号。工作树和独立分支仍被快速预览使用、成果未合入main，必须保留。目标本地/远端main仍为 `57039ca15595641932f053fd34c49d4e45dba964`；任务远端分支不存在，未推送、未上线。用户结束快速预览并确定合并/保存安排后再复核本任务工作树和资源。

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

## 第六轮：老店迁入的期初库存（2026-09-24，本地验收）

用户确认“做，前端用原生的”。本轮基线 `1715ea3baeaac2ab873356a1ff92a050844a9902`，继续本任务独立分支/工作树与8157快速预览；不重建已删除的真实验收环境。

- 归属：ERP App 的 inventory 域。前端 `stock-opening.js` 组合原生 Page/FieldGroup/Section Break/FileUploader；按用户后续要求移除明细表格，改为逐条折叠表单。入口在既有库存页工具栏，子路由 `/desk/pearnly-inventory/opening`，不新增侧栏模块、不修改采购销售既有控件。
- 入口声明：用途为已有商品导入启用时的数量和成本；购买库存功能、具备库存读取和入库权限、当前公司有可用仓库才允许操作。点击进入完整原生表单，填写/上传→预览→确认入账。原生入库能力与库存只读权限保持分离。
- 数据：继续使用 `ERP Stock Opening`、不可变 `ERP Stock Movement` 与由流水推出的 `ERP Stock Balance`；不另存报表基数。服务端负责商品匹配、成本/数量/日期校验、当前公司仓库范围和整批事务。
- 初始化合同：只允许尚无库存流水的位置；完全相同的期初重放跳过，已入账的日期/数量/成本不再通过期初改写，后续差异走入库/出库或盘点。空成本不是零成本；金额最多2位、数量最多6位。原生Data用于明细数字输入以保留空与0的区别，服务端做Decimal校验，预览使用原生数字/金额显示。
- 文件：原生私有上传；xlsx模板带启用商品和库存单位，数量/成本留空；空白未填写的模板行不入账，显式0保留。导入不静默跳过未知商品和重复位置，整批核对通过后才写入。
- 验证范围：定向离线、现有专属Site B写入测试、8157浏览器手填/模板/导入/重放/手机布局，逐层记录。无外部业务/付费OCR/生产发布。

### 本轮业务口径与修正

- 用户进一步要求期初不要混入收发存来源，并询问正常ERP逻辑。采用“期初+本期入-本期出=结存”：初始化归到报表已有的期初栏，不出现在本期明细或入库量；底层不可变流水仍保留初始化审计，库存余额与成本只从同一份流水产生。
- `report.card` 与 `report.summary` 共用按仓位投影，未来初始化不提前计入；旧版期初修订仍按原顺序计算业务成本，再重述期初和显示余额，不篡改成交成本。指定仓位保留大小写和重音身份，避免数据库默认排序规则混算。
- 去掉用户未要求的常驻说明句；保留必要字段标签和操作失败反馈，不重写其它页面文案。
- 原生异步字段初始化曾清除预览：改为只响应真正的值变化、忽略旧分页回调；金额分区通过原生`refresh_sections()`更新，避免有值但被空分区隐藏。
- 用户追问官方分页后，移除自己拼接的“上一个/页面/下一个”字段，直接加载当前安装Frappe的`/assets/frappe/js/frappe/form/grid_pagination.js`。只适配数据和折叠表单渲染，原生分页负责首末页、左右箭头、输入页码及边界；每页20条、单页隐藏，未创建Grid表格或复制上游组件源码。产品词条仅把分页处的`Last`纠正为“末页”，不修改共享框架翻译。
- MariaDB快照隔离下两笔并发初始化曾报1020。专用RPC仅在没有调用方待提交写入时最多重试三次；域服务不擅自提交或回滚外层事务。真实并发验收只建立一笔库存。

### 验证与运行归属

- 离线：18项期初服务行为、8项XLSX、12项报表投影通过；既有收发存前端9组断言通过（仅补齐fixture中Frappe原生`locals`缓存）。架构、前端词条和diff检查通过。
- 前端35项状态/分页检查通过，包括原生异步初始化、5000条全量数据与每页20条控件、上传替换/取消/失效响应、空值/零值和错误定位；同35项又以当前锁定版本的真实官方GridPagination源码验证通过（临时读取，不提交上游副本）。
- 现有专属Site B：17项真实数据库检查通过，覆盖空值/零值、单位/金额精度、整批失败回滚、公司与菜单权限、私有文件、预览后主档变化、原生CRUD绕过、并发和重复提交、旧API及库存/报表关联。
- 最后追加旧历史修订舍入边界后，报表12项离线通过；Site重跑前16项通过，第17项旧验收类的账号批量建样触发Frappe原生每小时60个用户限流，业务用例未再次执行。该旧API用例在本轮前一轮17项验收已通过；未关闭限流或删断言。继续复测时先复用既有样本或等自然窗口恢复，避免反复建账号。
- 8157浏览器：手填10件×25入账，库存10/250，收发存期初10、期末10、无入库明细；下载模板事件成功；22条文件导入两页，保留零数量/零成本，整批入账后新增7件/87.50与21个零库存初始化位置。全部为本地合成验证记录。
- 原生分页浏览器验收：末页跳到第21条；输入页码1回到首20条；跨页只读核对仍包含全22条，总金额87.50可见。相同文件再次提交显示“0行新增、22行已存在未重复入账”，库存数量与金额保持不变。
- 390×740浏览器模拟：页面宽390，无横向溢出，无Table/Grid，物料、数量、成本纵向排列。尚未做手机真机验收。
- 最终官方分页在390宽度下为329像素宽，原生首末页/箭头/页码完整可见；无效页码回到当前页，新增后删除空明细保留原22条。已恢复浏览器默认尺寸并保留快速预览页。
- 本轮只重载8157快速预览的ERP DocType说明、缓存和web进程。仍为`colima-pearnly` / `pearnly-erp-entry-lab`的6个容器，源挂载本任务工作树；未重建真实验收环境，也未接管DMS/Cowork资源。
- 交接保留：分支`codex/erp-purchase-menu-diagnosis`与工作树`/Users/skin/.codex/worktrees/erp-purchase-menu-diagnosis/pearnly`供用户快速预览，尚未合并/推送/部署；用户完成本地验收并决定集成后复核清理。核读本地main与远端main均为`57039ca15595641932f053fd34c49d4e45dba964`，远端本任务分支不存在。
- 本轮清理范围：仅删除本轮生成的临时XLSX、官方组件临时只读副本、临时检查日志与`opening_workbook.cpython-312.pyc`；对应工作树/本地分支因未合并且预览仍挂载而保留，无远端任务分支可删。合成Site A/B数据与专属sites/db卷保留用于用户验收，不按缓存清理。

## 第七轮：POS 正式入口与仅 ERP 发布（2026-09-24）

用户明确授权：本轮 ERP 全部改动优先上线，同时解决 POS 官方组件未接正式入口；与 DMS 窗口协调，未验证的 DMS 改动不随本轮发布。范围包含本分支 `fd7d1bc` 至 `e15f053` 的采购/销售录入、主档原地维护、手机附件、菜单权限、公司隔离、期初库存与后续 POS 修复。

### 固定边界与协调

- 开工主线/远端均为 `57039ca15595641932f053fd34c49d4e45dba964`，本任务 HEAD 为 `e15f05341bd6d44ed0a59b31e1ba306e529ed502`，两树干净。主线比生产 `dd81a3f` 仅多两条 DMS 验证记录，没有 App 代码增量。
- 线上实际 ERP 和 DMS 运行镜像均为 `sha256:a11eb9e36bae8ca16e3dc37cbda45915fd1ccf18d4b28714c21d235fbff4934f`，标签源码 `dd81a3f009e255c2facbb8da818cd92a98face30`；私有 billing 仍为 `d60900539548…`。本轮只激活 ERP 七个服务，不重启或迁移 DMS/billing/Keycloak。
- DMS 负责人任务 `01a0c542-49d5-7602-85f7-eb0f695002f2` 已确认本轮由本任务独占 main 集成与共享文件发布；其未验证成果 `codex/dms-five-minute-snapshots@3f5306fa764f76ff9ed10827198012c6ae0b883a` 整条分支不纳入。该分支的 DMS 编辑器构建产物也不复制，正式候选从固定提交独立构建。
- DMS 的生产同步队列、容量、compose/provision/site-step、CI 等未验证改动不是 ERP 前置项。其独立实验容器、卷、私有目录和旧树未提交验证记录原样保留。发布完成向负责人回传 main、镜像及仅 ERP 激活证据，再释放协调边界。

### 已确认根因与修复边界

- 原生 POS 组件及客户状态修复已进入生产；源码/生产容器/公开静态资源哈希对应一致。`cashier.js` 仍仅在 `?pearnly_pos_next` 参数存在时进入官方组件，正常侧栏打开旧手绘代码。不是丢提交或缓存猜测，而是正式入口切换未完成。
- POS 归属 ERP cashier 前端；正常菜单改为官方 ERPNext 16.34.1 组件加现有业务适配，移除旧手绘页面及重复样式。保留扫码、客户、公司/班次、订单、收付款及库存业务契约；参数旧链接仍可进入同一页面。生命周期/异步回调按真实路由验证，不能仅换验收网址。
- 现有生产激活脚本写全局 `active-image.json`，开户维护也读取该文件。独立 ERP 激活后后续 DMS 开户可能误用 ERP 候选；本轮加最小按产品镜像记录和兼容读取，保留全局旧版本作为未迁移产品的回退，不摘入 DMS 分支部署设计。验证严格 digest、产品隔离、失败保持及原备份/迁移保护。
- 发布前备份、原生迁移和恢复材料按已有生产脚本执行；线上只读回读版本、资源、入口和业务关系，不用新增真实销售单冒充验收。具体验证、CI 和发布结果在下节追加，当前不预记通过。

### 发布前基础验证

- 用户追加要求：停止全套前端验收，基础检查通过后尽快部署。后续不扩展浏览器回归；保留既有镜像构建与生产发布门禁。
- 本轮 POS 生命周期 9/9、原生 Desk 契约 22/22、产品激活 10/10、开户镜像选择 15/15 通过；架构、JS 语法、diff 检查通过。POS 小型回归接入开发检查和生产镜像构建，直接覆盖无参数正式入口。
- 8157 本地普通菜单进入官方 POS，客户回填正确，跨页后购物车保留；合成结账生成 `SI2569-000001`，1 件 × 30，现金 30，退款原生弹窗可打开。摘要邮箱、页面标题和成交后现金回读问题已修复并有定向测试；最后三项未继续做全套浏览器复测。手机真机与线上真实收付款仍不代签。
- 既有后端验收类本轮重跑在建用户阶段遇到 Frappe 限流，0 项业务用例执行，未计作通过，也未关闭限流；使用此前对应版本的已记录后端证据及本轮基础检查。
