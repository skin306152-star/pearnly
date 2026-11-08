# CC 设置「位置」选年度文件夹被吞（11-08）

分支 `pearnly/cc-express-places-nested`（基线 `ff7db152b`），随 CC 1.12.19（版本号由主会话发版时定）。

## 现象

生产 KORN（erpcowork，Cowork，CC 1.12.x）：设置 → 连接里「โฟลเดอร์ข้อมูล Express」写「P:\69EXP รวม 17 ปีบัญชี」，下面的位置清单只有「P:\」。
用户：「设置里添加了 69 多账套，为什么下面文件夹还是 P」。

## 根因

- 上面那行是全部成员授权的年度文件夹（数据根）的并集：「主根 + 共 N 个年度」，不是用户选的。
- 位置 P:\ 是 1.12.0 升级时自动换算出来的（`ProfileStore._settle_places`：`place_of(主根 P:\69EXP)` = 上一级 P:\）。
- 用户「เพิ่มโฟลเดอร์」选 P:\69EXP：`add_place` 原样加进清单，随后 `merge`（一个位置在另一个下面的只留外面那个）把它丢掉，接口照样回成功。
  `ProfileStore.set_places` 也过同一个 `merge`。
- 只剩一个位置时不能删 → 怎么点都改不成只读这一年。
- 共用电脑隔离（`place_channels`）按位置登记，嵌套位置被吞时只能把整个 P:\ 勾给同台电脑上客户的 ERP。

## 改法（主会话 11-08 审定）

| 项 | 改动 |
|---|---|
| 位置清单按选的原样存 | `express_places.add`：同一个文件夹的不同写法才算重复，嵌套照样单列；`add_place`、`set_places` 用它。`merge` 只留给找年度 / 自动换算内部去重 |
| 外层可删 | 不吞嵌套之后自然成立（仍至少留一个位置）；删 P:\ 剩 P:\69EXP 就只读这一年 |
| 选年度文件夹只读这一年 | 原本如此；要覆盖以后新年度就选上层文件夹 / 盘。自动默认与 1.12.0 升级换算仍取上一级（不是人选的） |
| 每个位置下面有哪些年度 | 桥多给 `place_years`（`[{path, years}]`，新的在前）；设置页位置下面一行小字列出相对名字（P:\ 下的 69EXP · 68EXP …），位置本身就是年度文件夹时不写；旧版桥没有这一项，设置页不列 |
| 主根 | `apply_roots`：登记所选账套的年度不止一个（复制出来的年度文件夹，登记表写绝对路径）时留原来的主根，不再按新找到的顺序换 |
| 老配置 | 不迁移、不改：已存的位置照旧（KORN 仍是 P:\，不突然多扫或少扫）；主会话定不提醒岳父删 P:\ |
| 上面那行文案 | **不改**（用户 11-08 经主会话回「不改」）：「Express 数据文件夹」那行四语保持「{path} รวม {n} ปีบัญชี」原样；只靠位置下面那行年度名把范围写清楚，不加新词 |

## 验证

| 层 | 结果 |
|---|---|
| 会红的测试（改前失败、改后通过） | `test_a_year_folder_added_inside_a_place_is_kept_as_picked_and_the_outer_place_can_go`（KORN 场景：加 P:\69EXP 两个都在、扫描一个不少、`place_years`、删 P:\ 后只读 69EXP、最后一个不能删）；`test_places_keep_what_was_picked_nested_or_not_and_only_drop_the_same_folder_twice`；`test_adding_a_copied_year_folder_keeps_the_main_root` |
| 守护（改前已过） | `test_nested_places_given_to_different_members_scan_only_their_own`：P:\ 给 Cowork、P:\69EXP 给 ERP → ERP 只扫 69EXP |
| CC 全部测试（本机 venv，Mac） | 1586 过、42 跳过（Windows 专属） |
| 设置页（本机构建产物 + cc-visual 假服务端 + Playwright 假桥） | 泰文：P:\ 下面一行列出 69EXP…53EXP 共 17 个、P:\69EXP 单列、两个都有 ×；中文旧版桥（无 `place_years`）：和改前一样 |
| `check-impact` | 命中 cc-assistant-settings / cc-desktop-bridge / cc-places-heartbeat；入口逐项见下 |

### 影响清单入口

| 入口 | 结果 |
|---|---|
| cc.settings.connection（auto，流程 cc.settings） | 没测到：本机已有三套实验栈在跑（上限三套），未起第四套，待主会话安排；该流程走浏览器、不经桌面桥，本改动只在桥给 `place_years` 时出现 |
| cc.desktop.bridge（manual） | 部分：假桥设置页截图核过位置行；Windows 测试机 CC 实机没测（要先问主会话） |
| cc.desktop.places（manual） | 部分：离线单测覆盖添加 / 移除 / 嵌套 / 按成员筛；测试机实机没测 |
| cc.mrerp-login、cc.operator.page、cc.desktop.operator、cc.desktop.requests、erp.cc.connect | 没测到：本改动不碰这些路径（只改位置清单存法、主根取法、设置页位置行） |
