# 任务管理复测遗留四处：Cowork 同源两处 + 共用层两处（2026-10-24）

## 目标与范围

ERP 任务管理（分支 `pearnly/cool-bouman-26647e`，照 Cowork 复制，未合入 main）复测时报出的 4 条，ERP 那边已修或不在那里改，
见那条分支 `docs/work/2026-10-23-erp-tasks.md`「第 1 轮复测」表 a–d。用户 10-24 早上定「要处理」。

| 条 | 在哪里改 | 完成条件 |
| --- | --- | --- |
| a（中） | Cowork 任务卡片弹窗 `pearnly_cowork/public/desk/work.js`（照 ERP 提交 `013e9c72`） | 「取消关注 → 改成员和标签 → 保存」能存 |
| c（低） | Cowork 默认看板 `pearnly_cowork/work/boards.py`（照 `013e9c72`） | 泰文站点新建默认看板，三列是泰文 |
| b（低） | 共用页面注册 `pearnly_core/public/desk/product.js` 的 `p.register` | 没权限时打开页面，看到原生无权限提示 |
| d（低） | 共用下载 `pearnly_core/exports` + `after_request` 钩子 | 中 / 泰文文件名下载后不乱码，三个产品都生效 |

不做：其他行为不变；ERP 分支不动；不合入 main、不部署（由用户定随哪一批上线）；测试机被「ERP 下一批」验收占用，不碰。

- 仓库 / 分支：`pearnly/work-followups-d3d5bf`（桌面 App 为本任务建的工作树 `.claude/worktrees/work-followups-d3d5bf`）
- 基线：`origin/main` = `cecc475e`（开工时核对，本地与远端一致）
- 状态：本次范围完成，待用户决定上线批次（交接保留，见文末）

## 改法

**a 卡片弹窗接过新版本号**（`work.js`）：服务端「关注 / 取消关注」和「删掉作为封面的附件」都会把卡片存一次、版本号变了，
弹窗仍拿着打开时的版本号，之后再点「保存」被当作别人改过（报「已被其他用户修改」，什么都没存）。改为：关注后用 `watch` 回的
`modified`；删附件后取一次 `card`，删的是封面就用它的 `modified`（不是封面时服务端没存卡片，不接，免得盖掉真正的别人修改）。
与 ERP `013e9c72` 逐行同一改法。

**c 默认三列按站点语言取名**（`boards.py`）：`bench migrate` 时 `frappe.local.lang` 是 en，`ensure_on_migrate` 建默认三列前
临时换成 System Settings 的语言，建完换回。与 ERP `013e9c72` 同一改法。已建成英文的现有租户不在本次改（它们的列名用户可以自己改；
需要一次性改名另说）。

**b 原生无权限页**（`product.js`）：`p.register` 发现当前用户没有这个页面时，不再先建页面壳、只在标题旁写红字「Not Permitted」
（页面一片空白），改为直接调框架自带的 `frappe.show_not_permitted(name)`：Frappe Desk 的标准无权限页（官方图、官方文案
「Sorry! You are not permitted to view this page.」及其各语言翻译、「首页」按钮）。没有新文案，三个产品所有经 `p.register` 的页面同时生效。

**d 下载文件名 RFC 5987**（`pearnly_core/exports/disposition.py` + `service.encode_filename` + `hooks.after_request`）：
Frappe 16.33 的原生下载（`type` = download / binary / pdf）把文件名按 latin-1 原样写进 `Content-Disposition`，浏览器读成乱码
（实验室读回：`จัดการงาน-2026-10-24.xlsx` 原样写出 → 浏览器按 latin-1 读成 `à¸à¸±à¸...`）。
共用层加一个 `after_request` 钩子：响应是原生下载、文件名含非 ASCII（或引号、反斜杠、控制字符）时，改写成
`attachment; filename="<ASCII 兜底>"; filename*=UTF-8''<百分号编码>`，保留 attachment / inline；纯 ASCII 文件名不动。
因为挂在共用钩子上，三个产品所有走框架原生下载的入口都生效：共用导出（当场给的和排后台做完下载的）、Cowork 原件下载、
CC 对话附件、ERP 模板等。页面里 `p.export_file` 早就会解 `filename*`，不用改前端。兜底名：非 ASCII 的一段换成 `_`、去掉开头分隔符，
名字部分空了用 `download`（例：`任务管理-2026-10-24.xlsx` → `2026-10-24.xlsx`，`盘点.xlsx` → `download.xlsx`）。

## 验证

本机一次性实验室 `pearnly-followups-lab`（`~/pearnly-devtools/followups-lab/`：自带 MariaDB / Redis、不挂共享卷、只开
`127.0.0.1:18741`），底座是线上同一镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:29404c6a…a184a`（Frappe 16.33.0），
产品代码只读挂本工作树。三个站点 `cowork-fu` / `erp-fu` / `dms-fu.localhost`，各装 `pearnly_core` + 本产品。浏览器是本机无头 Chrome。

| 条 | 场景 | 结果 |
| --- | --- | --- |
| a | 页面实测（泰文用户）：打开一张自己关注着的卡 → 点「เลิกติดตาม（取消关注）」→ 勾两个成员 + 一个标签 → 保存 | 修后：提示「บันทึกแล้ว（已保存）」，读回成员 2 人、标签 1 个、未关注。**旧代码对照**：同一步骤报「มีผู้ใช้อื่นแก้ไขการ์ดนี้แล้ว（已被其他用户修改）」、什么都没存。截图 [修前](2026-10-24-tasks-followups/a-before-conflict.png) / [修后](2026-10-24-tasks-followups/a-after-saved.png) |
| a | 页面实测：卡片封面是一张图 → 删掉这张图 → 勾一个标签 → 保存 | 已保存，封面清空、附件 0、标签在 |
| a | `scripts/test-cowork-work-native.py`（真实站点，整段回滚）新增：关注后 `watch` 回新版本号、用旧版本号保存被拒；取消关注后用新版本号一次改成员 + 标签能存；删封面后旧版本号被拒、`card().modified` 能存 | `RESULT: PASS` |
| c | 真实迁移命令：`cowork-fu` 站点语言设 th，删掉默认看板三列（无卡片），跑 `bench --site cowork-fu.localhost migrate` | 修前英文 `To do / In progress / Done` → 修后 `รอดำเนินการ / กำลังทำ / เสร็จแล้ว` |
| c | `scripts/test-cowork-work-native.py` 新增同一场景（迁移语言 en、站点语言 th） | 修后 PASS；临时退回旧 `boards.py` 跑：`FAIL ['To do', 'In progress', 'Done']` |
| b | 页面实测：Cowork 只有录入权限的员工开 `/desk/pearnly-work`、ERP 只有首页的员工开 `/desk/pearnly-sales`、DMS 非管理员开 `/desk/pearnly-roster` | 三处都是框架原生无权限页：官方图、`ขออภัย! คุณไม่ได้รับอนุญาตให้ดูหน้านี้`（泰文）/ `抱歉，你不允许查看此页面。`（中文）、「首页」按钮指向 `/desk`；没有空白页面壳。截图 [Cowork](2026-10-24-tasks-followups/b-cowork-not-permitted.png) / [DMS](2026-10-24-tasks-followups/b-dms-not-permitted.png) |
| b | `tests/test-native-desk.cjs` 新增：不在 `pearnly_pages` 的页面调 `frappe.show_not_permitted`、不建页面壳；有权限的照常渲染 | PASS |
| d | 页面实测 Cowork：任务管理表格视图点「ส่งออก Excel」（当场下载，泰文界面） | 浏览器存为 `จัดการงาน-2026-10-24.xlsx`；响应头 `attachment; filename="2026-10-24.xlsx"; filename*=UTF-8''%E0%B8%88…` |
| d | 页面实测三个产品的共用后台导出下载（`pearnly_core.exports.api.download`，大导出做完后点的那个链接）：Cowork `การกระทบยอดธนาคาร-2026-10-24.xlsx`、ERP `销售记录-2026-10-24.xlsx`、DMS `ทะเบียนลูกค้า-客户名册.xlsx` | 三个都按原名存下，不乱码 |
| d | 页面实测 ERP 商品「下载导入模板」（ASCII `products-import-template.xlsx`） | 原名存下；响应头与改前一致 `attachment; filename=products-import-template.xlsx`（钩子不动 ASCII 名） |
| d | `tests/test_download_filenames.py`（离线，进 Development checks）：中 / 泰文编码与兜底、inline 保留、ASCII 不变、引号换行不能破坏响应头、钩子已登记 | 6 项 OK |
| d | `tests/test_exports.py` 新增（生产镜像里跑，候选离线检查车道）：用 Frappe 原生 `build_response` 生成下载响应再过钩子 | 见下方候选检查 |

说明：ERP、DMS 在 main 上的导出文件名目前都是 ASCII（ERP 的中文名导出是任务管理那条分支上的「导出 Excel」），所以这两个产品用共用后台
下载路径放了中 / 泰文文件名来点；钩子不分产品，ERP 任务管理合入后它的导出同样经过这里。ERP 实验站点没装 ERPNext，销售列表本身打不开，
ASCII 对照改用商品导入模板。

其余检查：`scripts/test-cowork-work-boards-native.py`（看板权限矩阵）在实验室 `RESULT: PASS`；本地全闸门与生产镜像候选离线检查见「闸门」。

### 闸门

- 本地全闸门（`~/pearnly-devtools/gate.sh`，按 `development.yml` 全部步骤，含新加的 `test_download_filenames.py`）：115 步全部 ok、0 失败
- 生产镜像完整候选离线检查（`cand-local.sh offline`，镜像 `29404c6a…`，只读挂本工作树）：72 步，`LANE EXIT 0`；
  `tests/test_exports.py` 17 项 OK（含新加的「中 / 泰文文件名经原生 `build_response` + 钩子」一项）
- 共享提交钩子：架构检查 0 违规；文案闸门新增 0 · 改动 0
- 分支 CI（Development checks）：推送后回读，结果见下方「收工」

## 界面文案

没有新增或改动的文案。b 用的是框架自带无权限页的官方文案；去掉的是原来标题旁那句 `__("Not Permitted")` 的用法（框架词条，不在本仓术语表）。

## 未覆盖

- 测试机未验证（被「ERP 下一批」验收占用）；真机未验证。
- 已经建成英文三列的现有 Cowork 租户不自动改名。
- 部署：未做；随哪一批上线由用户定。合入前 ERP 任务管理分支若先合，两边 `work.js` / `boards.py` 是各自产品的文件，不冲突。

## 收工

见交付时更新。
