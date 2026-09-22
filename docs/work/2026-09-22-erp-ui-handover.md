# 交接：ERP 前端（商品 / 库存 / 采购 / 盘点 / 差异看板）

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
