# 交接：ERP 前端（商品 / 库存 / 采购 / 盘点 / 差异看板）

## ⚠️ 最新交接（2026-09-22 收工，stocktake-workbench 批）—— 换窗口先读这一节

- 工作树 `/Users/skin/.codex/worktrees/stocktake-workbench/pearnly`，分支 `codex/stocktake-workbench`，
  **代码提交止于 `db2d6f9`**（8126 的验收镜像就是这个状态），其后是本轮交接的文档提交；
  取当前 tip 用 `git log --oneline -1`，工作区**干净**。
  已包含本机 `main` 的 `717d202`（`git rev-list --count HEAD..main` = 0，合 main 可快进）。
  **未推送、未上线**：`origin/main` 仍停在 `ac33e0c`。
- 逐批来龙去脉、每个决定与验证证据都在 [盘点工作台记录](2026-09-22-stocktake-workbench.md)
  （本轮全部批次都在那一个文件里，续写它，别另开新的）。
- **用户要求"原地交接、不关停任何东西"**：两条 lane 都还在跑 ——
  预览 `http://stocktake.localhost:8136`（挂当前源码、秒级生效）、
  验收 `http://stocktake.localhost:8126`（按 HEAD 构建的正式 amd64 镜像，当前 `sha256:051c4752…`）。
  站点 `stocktake.localhost` 两条 lane 共用，数据/样式一致。
- 本节以下的所有内容是**更早窗口的快照**（那时候跑的是 `qerp.localhost:8102` 那套 stack 与
  `codex/erp-pos-migration` 分支）——只当背景读；跑命令、看端口一律以本节为准。

## 本轮一句话现状

盘点（工作台 / 记录 / 导出 / 照片）、出入库、收发存、商品与库存列表按本轮口径做完，
并在本机两条 lane 逐格验过；**用户还没点验收**；有**两件事等他一句话**（见文末）。

## 本轮的 6 个提交（都在 `db2d6f9` 这条链上）

| 提交 | 做了什么 |
| --- | --- |
| `7584464` | 表头灰条不再超出列宽。真因：`frappe-datatable` 的 fluid 布局给每个可伸缩列减 2px，而表头行被拉满容器宽度 → 多出来的灰被容器圆角切掉，看着像"缺口"。**上一版"改成直角"被用户否掉，已撤销**；也没保留手改列宽那段 JS |
| `f965b8f` | 有差异的地方标红：工作台表、记录弹窗、任务列表的"有差异"格、导出两张表；红取差异看板同一个 `--red-600`（`#cc2929`） |
| `950c85f` | 列表那格红改成列上的 `cell_class` —— 共享列表的 formatter **只出转义文本**（框架拿 `$()` 量宽），从 `format` 返回 HTML 会被显示成标记（用户报障原话："这里显示乱码"） |
| `78b0ce9` | 盘点时可拍照/传照片：原生 `frappe.ui.FileUploader`（多选 + 拍照 + 私有文件），单条最多 5 张；导出补齐**冻结首行 / 自动筛选 / 边框 / 照片页**（一张图一行 + "返回汇总 / 返回记录"跳转） |
| `caeab90` | 时间全站统一到分钟（根因见下）+ `api_stock_count_item_photos` + 记录弹窗表格下面能看该商品的全部照片 |
| `db2d6f9` | 照片说明行去掉时间，只留 `仓库 / 仓位 · 数量`（+ 作废标记） |

## 本轮定下的口径（别推翻）

- **时间**：产品里所有时间走 `frappe.pearnly.datetime_text`（core `desk/product.js`），
  只做"去 `T`、去微秒尾巴、秒收到分钟"，**不做时区换算**。为什么：业务时间在库里就是**站点时区的裸时间**
  （`counted_at` / `sold_at` 都是站点 `now()` 写进去的），`frappe.datetime.str_to_user` 会按"UTC→观看者"
  再换算一次，本机实测整体偏 1.5 小时（账号与 `DefaultValue` 还留在 Frappe 装的 `Asia/Kolkata`）。
  本机验收站的用户与默认时区已对齐 `Asia/Bangkok`，`frappe.boot.time_zone` 现在 system=user。
- **照片**：单条 ≤5 张；桌面端走原生上传器（`allow_take_photo`）、私有文件；已有照片只读预览，
  本次新加的可先"移除"；5 张满了就不再给"添加照片"（服务端 `PHOTO_LIMIT=5` 仍是权威）。
  说明行只写"仓库 / 仓位 · 数量（作废标记）"，**不放时间**（弹窗上面的记录表里已有）。
- **导出**：汇总 + 明细 + 照片页三张表，统一 `freeze_panes=A2`、`auto_filter`、`A6ACB3` 细边框；
  时间写 `yyyy-mm-dd HH:mm`（与界面同形）；差异格标红；照片页把图缩到 200×140 嵌入，坏图跳过。
- **列表里的颜色**只能走列上的 `cell_class`（core `desk/list.js`），列定义不再从 `format` 返回 HTML；
  工作台/弹窗是真 DataTable，那里可以返回 HTML（差异标红就是这么做的）。

## 怎么跑（本轮一直在用的）

```bash
cd /Users/skin/.codex/worktrees/stocktake-workbench/pearnly
./scripts/stocktake-preview.sh status     # 两条 lane 的端口 / 镜像 digest
./scripts/stocktake-preview.sh reload     # 改 Python/hooks/CSS：清缓存 + 重启**预览** web
./scripts/stocktake-preview.sh assets     # 只清站内资源缓存（约 2 秒）
./scripts/stocktake-preview.sh faithful   # 慢路：按当前 HEAD 重建正式镜像并升级 **验收** 8126（约 1.5 分钟）
```

- 容器上下文固定 `docker --context colima-pearnly-stocktake`，容器名 `pearnly-stocktake-acceptance-*`
  （预览 `…-web-preview-1`、验收 `…-web-1`）。
- **改完 CSS 也要 `reload`**：资源版本号 `?v=` 是进程启动时算的，只 `assets` 会拿着旧 URL 看旧内容。
- 验收用本机原生 Chrome（用户就是在这里点验收的）；`mcp__cua_repl` 绑 `browser id 1`，
  **只在自己的新标签里验**（用户自己的标签别动）；临时视口用
  `cdp.send("Emulation.setDeviceMetricsOverride", …)`，用完必须 `clearDeviceMetricsOverride`。
- 导出这类二进制不方便在浏览器里存盘时，可以在页面里 `fetch` 接口拿 base64，
  写到本地 `/tmp/*.xlsx`，再 `docker cp` 进容器用 `env/bin/python` + openpyxl 回读（本轮就是这么验的）。
- 浏览器原生"选择文件"对话框在这个插件里点不动（`locator.setFiles` 与 CDP `DOM.setFileInputFiles`
  都被拒），要喂图就构造 `File` + `DataTransfer`，往原生上传器的拖放区（`.file-uploader`）丢，
  再点弹窗里的"上传"。

## 验证到哪一层

- **实现**：已完成；8136（挂源码）与 8126（正式镜像）都逐格量过（宽度/颜色/时间/照片数/导出回读）。
- **检查**：`python3 scripts/check-architecture.py` → 0 违规；`i18n-build.py --check` → PASS（ERP 369 条）；
  `check-frontend-i18n.py --strict` → 0 问题；`check-i18n.py --strict` → 与主目录一致的 113 条基线；
  `node tests/*.cjs`（10 个）全绿；`python3 tests/test_erp_portal_access.py` 112 通过、
  `test_native_desk_contract.py` 17 通过。
- **部署**：**没做**（没合 main、没推送、没上线）。
- **用户验收**：**没做** —— 这一轮全部是我在本机验的，用户还没点过。

## 还欠用户一句话的两件事

1. **照片要不要落盘前压缩**（对齐旧项目：≤1600px、JPEG q85），顺带把桌面端单张上限也收到 1MB？
   现状：桌面端走 Frappe 默认上限 **10MB/张**（站点没设 `max_file_size`），一条记录最坏 50MB，不压缩会吃盘；
   手机端（base64）那一档本来就是 1MB/张。导出不受影响（嵌进 xlsx 前缩到 200×140）。
2. **租户开通里要不要把 Frappe 装出来的 `Asia/Kolkata` 默认时区一并设成 `Asia/Bangkok`**？
   现在只在本机验收站对齐了（`User` 与 `DefaultValue`），仓库的 `provisioning.py` 只设了
   `System Settings.time_zone`，生产新站点还会带着旧默认出生 → 原生 Frappe 界面会偏 1.5 小时。

## 保留项登记（用户要求"原地交接、不关停"，这些都别动）

| 保留项 | 位置 / 值 | 为什么留 | 解除条件 |
| --- | --- | --- | --- |
| 工作树 + 分支 | `/Users/skin/.codex/worktrees/stocktake-workbench/pearnly` / `codex/stocktake-workbench` @ `db2d6f9`（干净） | 成果未合未推 | 合并并推送后按本仓收尾流程删除 |
| 预览 lane | 容器 `pearnly-stocktake-acceptance-erp-web-preview-1`，`http://stocktake.localhost:8136` | 下个窗口改完要立刻看 | 任务收尾时再 `down` |
| 验收 lane | 容器 `pearnly-stocktake-acceptance-erp-web-1` 等，`http://stocktake.localhost:8126`，镜像 `sha256:051c4752…`（= 代码提交 `db2d6f9`） | 用户验收环境（= 上线形状） | 同上 |
| 站点数据 | `stocktake.localhost`：`STC-00017`（进行中，1 条记录 7 个、5 张测试图）、`STC-00005`（已过账）等 | 业务数据，也是验收样本 | 用户自己决定要不要删 |
| 别的窗口 | `codex/cowork-backend-migration` 等分支与工作树 | 不属本任务 | 不动 |

## 别做

- 别合 main、别推送、别上线（用户没授权；AGENTS 也要求分开报告实现/测试/部署/验收）。
- 别关停 lane 或容器、别清卷、别碰别的工作树。
- 别在 Desk JS 里写 `<input|select|textarea|table` 字面量，也别用 `alert/confirm/prompt`
  （架构检查会拦）；改用原生 FieldGroup / Dialog / DataTable / `cell_class`。
- 提交身份用命令行给：`git -c user.name="zihaopi" -c user.email="skin@zihaopideMacBook-Pro.local" commit`，
  不加联合署名，不用 `--no-verify`。

> 接续优先读 [2026-09-22 共享底座对齐](2026-09-22-shared-foundation-alignment.md)。本文下方是早期 UI 交接快照；词库/导航路径及后续仓位进展以新记录和当前源码为准。盘点工作台及容器调整按用户要求暂停。

给换窗口继续用。**逐批的来龙去脉在 [ERP/POS 迁移记录](2026-09-21-erp-pos-migration.md)**
（收尾批 F–K），这份只写"现在在哪、怎么跑、什么不能动、还欠什么"。

> 2026-09-22 后续修复：盘点任务和记录已有独立地址，空记录可逐级返回；差异看板也已接入
> “首页 / 盘点 / 任务 / 看板”。窄屏用原生面包屑菜单返回，已验证 390px 浏览器视口，未做真机验收。
> 实现、验证与本地收尾见 [盘点面包屑修复](2026-09-22-erp-stock-count-breadcrumbs.md)。下方版本号是本交接首次编写时的快照。

> 同日下载/扫码核查：商品、库存、盘点的五处下载按钮曾因调用不存在的前端方法而全部失败，已修复并验证附件响应/文件内容；列表重复模板按钮已移除。**扫码并非只欠真机验收**：桌面无扫码入口，手机 GET 参数丢失，保存后未自动续扫，旧解码兼容层未迁。详见 [下载修复与扫码核查](2026-09-22-erp-template-scan-audit.md)，后续施工以该记录纠正下方旧快照。

## 现状

- 本机 `main` = `bf08244`（本会话 40 个提交，全部**没推送**）；`origin/main` 停在 `ac33e0c`。
  **没有部署**，线上还是旧的。
- 分支 `codex/erp-pos-migration` 的工作树 `/Users/skin/.codex/worktrees/erp-pos-migration/pearnly`
  已 fast-forward 到 `bf08244`，干净、跟 main 同点。
- 另一个窗口的 `codex/cowork-backend-migration`
  （`/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`）**不要碰**，他还在收。
- 站点上有演示数据：商品 2 个、一份进行中的盘点「演示盘点」（2 行，其中 1 行数了 6，账面 3）。

## 本机怎么跑

- 租户站点 `http://qerp.localhost:8102`（账号 `owner@qerp.local`，密码登录被关，走身份服务）。
  控制面 `http://control.localhost:8104`（`earn` / `earn`）。
- 容器一律走 `scripts/stack.sh`：`bash scripts/stack.sh exec -T erp-bench bench --site qerp.localhost <cmd>`、
  `restart erp-web`、`clear-cache`。
- **改完 Python 必须 `restart erp-web`**（模块在进程里缓存着，`clear-cache` 不重载它 —— 踩过）；
  改 JS/CSS 也要 restart：hooks 里的内容 hash 是进程启动时算的，不重启浏览器拿的是旧文件。
- 站点结构改了要 `migrate`：`bash scripts/stack.sh exec -T erp-bench bench --site qerp.localhost migrate`。

## 验证怎么跑

```bash
# 十一个实验室探针（名字：permission team products purchasing inventory cashier
#   masters integrations intake sales stock-count）
docker --context colima-pearnly exec -e PEARNLY_ERP_POS_LAB=1 -w /home/frappe/frappe-bench \
  pearnly-erp-pos-lab-bench-1 ./env/bin/python /workspace/scripts/erp-pos-lab-stock-count-verify.py
# 期望 problems 为空
python3 scripts/check-architecture.py            # 0 违规
python3 scripts/i18n-build.py --check            # PASS
python3 scripts/check-i18n.py                    # 真实发现 0
python3 tests/test_erp_portal_access.py          # 106 通过 / 6 跳过
```
探针跑的是实验室站点 `erp-pos-a/b.localhost`，不碰租户站点。

## 规矩（这几条踩过）

1. **文案唯一来源** `scripts/i18n/messages.tsv` → `python3 scripts/i18n-build.py`
   （生成的 `apps/*/*/translations/*.csv` 一起提交）。不要手工合生成的 CSV。
2. **翻译包按命名空间落到不同 App**：`purchase.*` → **pearnly_core**，
   `stock.*` / `product.*` → pearnly_erp_portal（见 `pearnly_core/browser_text.py` 的
   `APP_BY_PREFIX`）。放错 App 页面就直接显示键名（踩过）。
3. Desk 里 `__()` 认的是**语义键**（术语表里带 `key` 那一列），不是英文原句 ——
   传英文会被框架自带译文抢走（"Code"→代码、"Location"→地点）。
4. **列表不要放进对话框**：框架写了 `.modal .hidden-xs { display: none !important }`，
   列表塞进去只剩第一列（实测列宽 0）。记录类清单用页面 + 面包屑（第三级）。
5. 页面换态要 `page.clear_fields()`，否则上一态的筛选字段跟过来。
6. 数量与金额**按实际值显示**：前端 `frappe.pearnly.number` / `.money`（core `desk/product.js`），
   后端导出 `_num`。
7. 提交身份走命令行：`git -c user.name="zihaopi" -c user.email="skin@zihaopideMacBook-Pro.local" commit`；
   不推送、不部署、不碰共享钩子、不用 `--no-verify`。
8. 旧项目 `/Users/skin/Developer/Pearnly/pearnly-app` 只读参考；口径以它为基准。

## 已经做完的（本会话）

| 批 | 提交 | 做了什么 |
| --- | --- | --- |
| F | `1a9b644`…`a77371c` | 商品页字段对齐旧项目 + Excel 导入 / 模板下载 / 图片上传 / 扫码反馈；商品改名；库存一览页（库存单位口径） |
| G | `52d89b5` `b735352` | 盘点：桌面页 + LINE 手机页 + 差异过账（三张表、一次性票据、幂等、拍照） |
| H | `8fb199a` | 盘点改版：**上传导入**（模板带实盘列、重复上传覆盖）、差异看板、面包屑/去返回/去库存行内盘点/列表多选 |
| I | `d57b527` | 数量金额按实际值显示（全站） |
| J | `53a725f` | **行粒度回到旧项目**（商品 × 仓库 × 库位）；差异看板换成**原生报表** |
| K | `655a12f` | 采购记录改原生列表；记录改第三级列表页；报表菜单收窄；筛选字段清理 |

## 还欠的（按重要性）

1. **真机小屏没量过**：内置浏览器只有约 1270px 一种宽度，Frappe 的移动断点
   （`is_mobile()` <992px、`.hidden-xs` <768px）在这台机器上触发不了。改法是
   "全用原生组件 + 不把列表放进对话框"，但 390px 的真机验收欠一次。
2. **iPhone 上 LINE 内置浏览器没有 `BarcodeDetector`** → 手机盘点只能手工输码；
   旧项目当年随包塞了一个 WASM 解码器（`static/vendor/barcode-detector/zxing_reader.wasm`），
   要不要为 iPhone 把这份下载加回来是**产品决定**。
3. 「盘点在库」模式**没有仓位**（ERP 结存只有仓库一层）；要按货架盘就用「上传导入」那份表。
4. 采购记录页**没有搜索/筛选**（旧页也没有），按供应商/单号找证只能翻页。
5. 差异看板的**打印/PDF/导出**被收掉了（框架的导出要 `export` 权限，租户角色没有）；
   要的话得单独给权限或另做导出。
6. 手机端"一个商品多行时挑仓位"只跑过接口层，真 LINE 里没点过。
7. Cowork 录入页仍是"一列单据用表格显示"，属另一个窗口的范围，我没动结构。
8. ERP 侧栏还**没放**入库 / 出库 / 收发存 / 销售记录四个页面（`pearnly_core/desk/navigation.py`
   的 `AVAILABLE_PAGES` 里没有），页面本体也只有前端架子 —— 要做得先对齐旧口径。
