# 按产品各自维护：菜单、功能目录、术语表（2026-09-21）

## 目标与完成条件

用户要求："除了 OCR、登录页、订阅、UI，其它各产品维护自己的源码。" 落到本仓就是：
把**产品内容**从共用文件里搬回各产品自己的目录，共用层与控制面只做汇总。完成条件是
三处共享点各自有明确正本、拆表前后行为逐字段不变、并由自动检查挡住以后往回堆。

**本次明确不做**：不改业务行为、不搬共用层里"三产品共用"的模块（OCR 引擎、浏览器层、
外部连接层）、不合并主线、不推送、不部署。

## 位置

- 仓库 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支
  `codex/cowork-backend-migration`，开工 HEAD `6dbf8a4`（第三十六批，银行单/GL 第一层）。
- 这是**共用层与三个产品**的一次结构整理，从 Cowork 工作树做；另一窗口（ERP）已按用户
  要求停手，所以不会互相踩。任务远端分支仍不存在。

## 改了什么

| 共享点 | 原来 | 现在 |
| --- | --- | --- |
| 可售卖功能目录 | 控制面一张 352 行总表：三个产品的功能、岗位、授权表、只读表、图标、模块全在一起 | 各产品 `features.json`（数据）自己声明；`pearnly_control/features.py` 按 bench 路径读文件汇总，缺文件**报错**；首页功能、基础角色、产品顺序这类跨产品策略留在控制面 |
| 侧边栏菜单 | `pearnly_core/desk/navigation.py` 里三个产品的 `PAGES` + 共用 `AVAILABLE_PAGES` + 抽屉式准入判据 | 各产品 `hooks.py` 的 `pearnly_navigation`（页面、已做出来的页面、每页准入判据）；共用层用 `frappe.get_hooks` 取**已装产品**的声明 |
| 术语表 | `scripts/i18n/messages.tsv` 一张 1568 行总表，用 `app` 列区分归属 | `apps/<app>/<app>/i18n/messages.tsv` 各一份（`app` 由位置决定）；`scripts/i18n_glossary.py` 共用读写，`i18n-build.py`/`i18n-apply.py` 只汇总；旧总表**删除**，生成脚本发现它还在会直接报错 |

顺带：`Pearnly Feature` 的字段说明从"与 product_manifest.py 的功能键一致"改成"与产品
自己的 features.json 一致"（旧 msgid 的 4 份翻译行一并清掉，不留悬空译文）。

## 为什么这样分而不是别的

- **控制面不能 import 产品**：控制面站点只装 `frappe + pearnly_core + pearnly_control`，
  所以功能目录只能是"产品放纯数据文件、控制面读文件"。用 `sys.path` 之类的办法假装能
  import 会把结构问题藏起来（架构文件第 5 条本来就禁止）。
- **菜单用钩子而不是文件**：菜单要按"这个站点装了什么产品"生效，`frappe.get_hooks` 天然
  只返回已装 App 的声明 —— 顺带得到一条更强的保证：**未装产品的页面在任何站点都出不来**。
- **术语表跟着产品走**：译文落在哪个 App 仍由"谁渲染"决定（租户站点不装控制面，所以控制面
  文案仍要落 `pearnly_core`）。拆开之后"放哪个 App"就是"把那一行写进谁的术语表"。

## 验证

| 项目 | 命令 / 场景 | 结果 |
| --- | --- | --- |
| 控制面目录等价 | 用桩 frappe 导出行前的 `features.py`，与运行中的新目录各输出同一份 JSON（产品、标签、图标、模块、功能码、岗位、授权表、只读表、admin 角色、工作区标签、三份 `roles_payload`） | **逐字段相同**（唯一差别：`admin_extra_doctypes` 现在三个产品都列出，取值不变） |
| 菜单契约 | `python3.14 -m unittest tests.test_native_desk_contract`（16 个，含项数 4/11/14、功能归属、只列已做出来的页面、DMS 管理员判据、个人排序、保存校验） | PASS |
| 未装产品不出现 | 同上新增用例：只装 DMS 时 `products()` 只有 dms、`available_pages('cowork')` 为空 | PASS |
| 功能目录契约 | `python3.14 -m unittest tests.test_product_feature_catalog`（13 个：功能码唯一、无角色者不假装有授权、只读表必须在同一岗位的授权表内、公司资料归管理员、身份证录入只读、工作区标签指向已授权表、管理员角色互不相同） | PASS |
| ERP 角色一致性 | `python3.14 -m unittest tests.test_erp_portal_access`（96 个）：该用例原本从控制面文件里抠字面量，改成读 ERP 自己的 `features.json`，所以"两边改名就对不上"这条保护仍在 | PASS |
| 术语表等价 | 与行前总表按 `app/msgid/en/zh/th/ja` 逐行比对 | 1561 条去重文案**完全相同**，仅 1 条有意改文案 |
| 翻译包不变 | 生成前后对 20 个 `translations/*.csv` 取 sha256 | **逐字节相同** |
| 术语表自检 | `python3 scripts/i18n-build.py`（汇总 5 个 App）、`python3 scripts/i18n-apply.py --check`（1441 条中文→英文，待改 0 处） | PASS / 0 处待改 |
| 真实 Site | 两个隔离 Site `bench --site ... migrate`；`boot_session` 与侧边栏回读（`app=pearnly_cowork`，2 项） | 通过，侧边栏按声明重建 |
| 未回归 | `scripts/cowork-income-recon-lab-verify.py`（19 用例/Site，匿名 403 全齐） | PASS |
| 静态检查 | `python3 scripts/check-architecture.py`、`python3 scripts/i18n-build.py --check`、`python3 scripts/check-i18n.py`（REPORT-ONLY）、`git diff --check` | PASS / PASS / REPORT-ONLY / 干净 |
| 全量离线 | `PYTHONPATH=apps/* python3.14 -m unittest discover -s tests` | 1011 个用例运行，976 通过 / 2 跳过 / 35 个模块级导入错误（全部是缺 pydantic、openpyxl、PIL、frappe、docx 这类本机没有的依赖，与本次改动无关） |

## 未做 / 未覆盖

- 未在**控制面站点**跑 `sync_features()`（控制面站点不在本任务可用的隔离环境里）；等价性
  用"行前 vs 行后同一份 JSON"证明，`sync_features` 本身没改。
- 未跑 DMS / ERP 站点的界面：DMS 声明的三项（connect/roster/records）与 ERP 的一项
  （purchase）与原 `PAGES` 逐字相同，由契约测试断言；DMS 站点的菜单重建属于该产品窗口的验证。
- 未合并主线、未推送、未部署；另一窗口回来后需要知道**改到哪里加东西**（见 ADR-009）。

## 给另一个窗口

- 加功能：写 ERP 自己的 `features.json`；控制面不用改。
- 加/改菜单：写 ERP 自己的 `hooks.py` 里的 `pearnly_navigation`；共用层不用改。
- 加文案：写 `apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv`（旧
  `scripts/i18n/messages.tsv` 已删除，往那儿加不会生效，生成脚本会报错提醒）。
- 合并时注意：本批同时删了旧的菜单表与旧的总表，冲突按"保留本仓的按产品声明"解决。

### 合并时**必须照做**的一步（ERP 菜单）

本分支没合 main（用户要求各做各的、最后再合），而 main 上 ERP 的菜单已经比本分支新。
所以合并时**不能**直接取任一侧的 `navigation.py`，要按下面的对应关系把 main 那一版
ERP 菜单落到它自己的声明里：

| main 的 `PAGES['erp']` | 合并后应落在 |
| --- | --- |
| 15 项（含 `nav.companyMaster → pearnly-company-master`，图标为 `house`/`package`/`warehouse`/`download`/`upload`/`list-checks`/`shopping-cart`/`store`/`sun`/`receipt-text`/`user`/`users`/`contact`/`users-round`/`plug`） | `apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py` 的 `pearnly_navigation[0]["pages"]`（本分支现在是 14 项、旧图标：**以 main 为准**） |
| `AVAILABLE_PAGES` 里的 `pearnly-products` | 同处 `available`（本分支没有这个页面实现，所以没列；合过来之后才能列） |
| `_can_open_page` 里的页面→DocType 映射 | 同处 `permissions`（`{"pearnly-products": {"doctype": "..."}}` 这种形状） |

漏了这一步的后果：ERP 的商品页会从侧栏消失（`available` 里没有它），而这是那一边
刚做出来的第一页。`tests/test_native_desk_contract.py` 的项数与"可用页面集合"断言就是
用来挡这个的 —— 合并后它会直接失败，照着改到通过即可。

## 追加：推送台账搬给 DMS（2026-09-21，用户确认）

上面那条"共用层里还住着产品内容"的发现，用户拍板搬。做完的是同一批：**表 + 控制器 +
服务层一起进 DMS**，共用层改为发钩子。

| | 搬之前 | 搬之后 |
| --- | --- | --- |
| 表与控制器 | `pearnly_core/.../doctype/pearnly_push_log/`（模块 `Pearnly Core`） | `pearnly_dms/.../doctype/pearnly_push_log/`（模块 `Pearnly DMS`） |
| 服务层 | `pearnly_core/push_log.py`（根模块，登记在架构基线里） | `pearnly_dms/records/push_log.py`（records 域） |
| "本机镜像"那一步 | 共用层的 `line._mirror` 直接 `from pearnly_core import push_log` 写台账 | 共用层按 `pearnly_line_mirror` 钩子派发；DMS 在 `hooks.py` 注册 `pearnly_dms.records.push_log.line_mirror` |
| 调用方 | `tests/check_dms_records_runtime.py`、`scripts/sample-verify-foundation.py` 从共用层导入 | 改从 `pearnly_dms.records` 导入 |
| 架构基线 | `pearnly_core` 根模块含 `push_log.py` | 基线里删掉这一条（表与服务的判据是"谁在用"，不是"谁先写的"） |

表名与字段一字未改（`tabPearnly Push Log` 不变），所以**既有数据不搬迁**；RPC 路径
`pearnly_core.push_log.api_list/api_status` 变成 `pearnly_dms.records.push_log.*`（仓库内
没有任何调用方用旧路径，已核对；记录页走的是 DMS 自己的 `query.records.list_records`）。

### 验证

| 项目 | 场景 | 结果 |
| --- | --- | --- |
| 数据存活 | 隔离 DMS 站点：**迁移前**用旧代码写一条台账 → 应用本批改动 → `migrate` → 回读 | 那条原样在（`PL-2026-00001`，正文一致），表主人的模块变成 `Pearnly DMS` |
| 共用层不再引用产品表 | 同一站点开"本地镜像"，调 `pearnly_core.line._mirror` | 返回"已投递"且**通过钩子**写出 `PL-2026-00002`（`trigger=line_mirror`，业务键正确） |
| 没装 DMS 的站点 | Cowork 站点开同样的开关调 `_mirror` | 返回"已投递"、不报错、不记账（没有钩子处理器）；`migrate` 会把这个 DocType 的登记从站点移除 |
| DMS 记录页读法 | 以 DMS 管理员身份调 `pearnly_dms.query.records.list_records` | 读到经新服务层写入的那条；匿名调用 `PermissionError` |
| DMS 地基自检 | `scripts/sample-verify-foundation.py`（连接 + 台账租约/状态/重试） | **17/17 PASS** |
| 静态检查 | `check-architecture.py`、`check-dms-offline.py`（42+43 通过）、`i18n-build --check`、`git diff --check` | 全部通过 |
| 全量离线 | `python3.14 -m unittest discover -s tests` | 1011 用例、976 通过 / 2 跳过 / 35 个缺依赖的导入错误（与本次改动无关） |

### 顺手修的一处（与本批无关，但会一直红）

`scripts/sample-verify-foundation.py` 里"测试连接必须诚实"那条检查写死了中文子串
（`"没有真的连" in note`）。服务端的这些文案早先已改成英文 msgid（`b51643b`），所以在
英文站点上它从那时起就一直 FAIL。改成比较**同一个 msgid 在本站点语言下的译文**，与语言
无关。改完 17/17。

### 有意差异与遗留

- **镜像在没装 DMS 的站点上不再记账**：台账归 DMS 之后这是必然（那张表在那些站点上本来
  也不该由共用层写）。发送结果仍是"已投递"，本机验收流程不受影响。
- **孤表**：没装 DMS 的站点上，`tabPearnly Push Log` 的空表会留下（Frappe 从不自动删表）。
  内容是本机镜像留下的测试痕迹，没有代码再读它。删表属于数据删除，**不在本次授权范围**，
  保留并登记。
- 台账的字段标签与那句镜像说明的译文仍留在共用层的词库里（DMS 站点也装共用层，所以照常
  显示）；按"其它的就这样"不动。
- lab 配方新增一个只装 DMS 的站点（`cowork-backend-ledger.localhost`），用于上面这套
  迁移验证；Cowork 站点不变。
