# 演示准备：ERP（AI 记账）主流程（11-04 Wz shop、Tucl 集团新公司；11-05 vision tech）

主会话 10-30 交办：岳父约了三家公司演示，演示前保证主流程稳定。按岳父面对新客户最可能演示的顺序列步骤，每一步对到流程用例
（`tests/flows/registry.toml`），没有的补上，两套数据（新开户 / 老数据）× 电脑 / 手机宽度都跑；生产部署后在 testerp 上只读冒烟一遍页面。
分支 `pearnly/demo-readiness`。

## 演示步骤与流程用例

| # | 演示步骤 | 流程用例 | 状态（10-30） |
|---|---|---|---|
| 1 | 登录 | 无（实验室没有身份服务） | 部署后在 testerp 只读冒烟登录页与登录后首页 |
| 1a | fatcc.com 用 Chrome 保存密码能带出用户名 | 无 | 结构符合，真机待用户顺手验（主会话 10-30 定，不再占时间） |
| 2 | 开公司 | `erp.company.create`（新补）、`erp.company.settings` | 新建公司 4/4 通过 |
| 3 | 建商品：Excel 导入 | `erp.products.import`（新补）、`erp.products.create` | 4/4 通过 |
| 4 | 采购单 | `erp.purchase.create` | 已在全套里 |
| 5 | 销售单 | `erp.sales.scan`（work-followups，下一轮合入） | 待合入 |
| 6 | 上传发票识别（AI 记账） | 无 | 主会话定：在 testerp 用测试识别额度（50 美元那份）传 3–5 张不同类型的真实样本，核对结果，费用记进本记录（新窗口做） |
| 7 | 盘点：扫码 | `erp.stocktake.scan`、`erp.line.stocktake.phone` | 已在全套里 |
| 8 | 盘点：扫到没有的码当场新建 | `erp.stocktake.new-product`、`erp.line.stocktake` | 已在全套里 |
| 9 | 盘点：报表（差异报表、打印、导出） | `erp.stocktake.report`（新补） | D1 已在 b79ee82d 修复，库存岗身份 4/4 通过 |
| 10 | 收银整班 | `erp.cashier.first-sale`；`erp.pos.shift`（work-followups，下一轮合入） | 首单已在全套里 |
| 11 | 员工：加用户 | `erp.team.add-member`（work-followups，下一轮合入） | 待合入 |
| 12 | 员工：改密码 | 无（改密码在身份服务） | 部署后在 testerp 用测试账号实测 |
| 13 | 写 Express（CC 桌面版） | 无（要 Windows 上的 CC 桌面版和 Express） | 主会话同意在测试机实测：先登记占用；代录的实机已挪到录制机，测试机应空着，起栈前看一眼（新窗口做） |
| 14 | F13：CC 页面刚打开就发的第一条消息不丢（10-15 夜间批遗留） | `cc.chat` 单列断言「F13：页面刚加载就发的第一条不丢」 | 第一步就是输入框一出现就发，刷新后两轮都在；两套数据 × 两种宽度都过：**F13 不复现** |

## 发现

- **D1 库存岗打不开盘点差异报表**（已修，b79ee82d：报表角色加 `ERP Menu stock-count`；`erp.stocktake.report` 库存岗身份 4/4 通过）：员工打开提示「您没有权限访问报表：盘点差异看板」。报表
  `erp_stock_count_variance.json` 的角色只有 `ERP Stock`、`System Manager`（09-22 定）；员工岗位后来改成按菜单发角色
  （`ERP Menu stock-count` 等），库存岗员工没有 `ERP Stock`。老板账号正常。`erp.stocktake.report` 以库存岗身份跑，修好之前这条不过；
  临时换管理员身份一格全过，断言本身无误。这条流程和修复一起合。
- 商品岗（员工）导入 Excel 时成本列不入库：成本只有老板与库存岗能设（字段权限），口径同「员工看不到进货价」，不是缺陷；演示用老板账号，成本照表入库。

## fatcc.com 保存密码（结构核对，只读取回首页，没有填任何东西）

- 登录表单 `<form id="entry">`：用户名框 `name=login`、`autocomplete="username"`；密码框 `type=password`、`autocomplete="current-password"`；
  提交按钮 `type="submit"`。
- 提交走表单的 `submit` 事件（脚本拦下后 POST `assistant_entry.api.start`，成功后整页跳转）。
- Chrome 的密码管理器靠表单提交事件 + 随后的跳转判断登录成功、靠 `autocomplete="username"` 认用户名：按结构，保存时会带出用户名。
  保存提示只有真浏览器里有，无头浏览器测不到；在正式站点上输入密码登录不在验收窗口能做的范围内，要人用测试账号点一次确认。

## 暂停点与交接（10-30，验收窗口归档前）

- 分支 `pearnly/demo-readiness`（已变基到冻结点 b79ee82d，已推送，**未合入 main**）：新补 `erp.products.import`、`erp.company.create`、
  `erp.stocktake.report` 三条流程与 cc.chat 的 F13 断言，都在 b79ee82d 的产品代码上两套数据 × 两种宽度通过。合入照规矩：变基没冲突就不重跑，
  分支 CI 绿后快进；它只改测试工具与文档，不影响已部署的 b79ee82d。
- 演示清单剩下的（新窗口按顺序做）：
  1. 生产部署 b79ee82d 后，在 testerp 只读冒烟登录页、登录后首页（第 1 步），用测试账号实测改密码（第 12 步）。
  2. 发票识别（第 6 步）：testerp 上用测试识别额度传 3–5 张不同类型的真实样本，核对识别结果，费用记进本记录。
  3. 写 Express（第 13 步）：测试机实测，先登记占用、起栈前看一眼测试机是否空着。
  4. 销售单、收银整班、员工加用户（第 5 / 10 / 11 步）：在 work-followups 的分支 `pearnly/fcb65cca-entry-audit`（ed818ea8，已删旧框架、
     `erp.sales.scan` 已修）里，下一轮合入：变基到最新 main，按 `coverage.py` 点名从存量清单删条目，只跑这 8 条受影响的流程，CI 绿后合入。
- 实验室：回归实验栈（`pearnly-regression-lab`，含 `agents` 假智能体服务、后台 worker）由验收窗口起，归档时停掉、数据卷保留；新窗口用时
  `PEARNLY_REGRESSION_SRC=<工作树> docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml up -d --wait`。
