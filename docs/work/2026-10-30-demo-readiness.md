# 演示准备：ERP（AI 记账）主流程（11-04 Wz shop、Tucl 集团新公司；11-05 vision tech）

主会话 10-30 交办：岳父约了三家公司演示，演示前保证主流程稳定。按岳父面对新客户最可能演示的顺序列步骤，每一步对到流程用例
（`tests/flows/registry.toml`），没有的补上，两套数据（新开户 / 老数据）× 电脑 / 手机宽度都跑；生产部署后在 testerp 上只读冒烟一遍页面。
分支 `pearnly/demo-readiness`。

## 演示步骤与流程用例

| # | 演示步骤 | 流程用例 | 状态（10-30） |
|---|---|---|---|
| 1 | 登录 | 无（实验室没有身份服务） | 部署后在 testerp 只读冒烟登录页与登录后首页 |
| 1a | fatcc.com 用 Chrome 保存密码能带出用户名 | 无 | 结构核对通过（见下）；保存提示只在真浏览器里有，要人点一次 |
| 2 | 开公司 | `erp.company.create`（新补）、`erp.company.settings` | 新建公司 4/4 通过 |
| 3 | 建商品：Excel 导入 | `erp.products.import`（新补）、`erp.products.create` | 4/4 通过 |
| 4 | 采购单 | `erp.purchase.create` | 已在全套里 |
| 5 | 销售单 | `erp.sales.scan`（work-followups，下一轮合入） | 待合入 |
| 6 | 上传发票识别（AI 记账） | 无 | 待主会话定：实验室模型是假服务，真实识别要在 testerp 用测试额度传几张 |
| 7 | 盘点：扫码 | `erp.stocktake.scan`、`erp.line.stocktake.phone` | 已在全套里 |
| 8 | 盘点：扫到没有的码当场新建 | `erp.stocktake.new-product`、`erp.line.stocktake` | 已在全套里 |
| 9 | 盘点：报表（差异报表、打印、导出） | `erp.stocktake.report`（新补） | 打印点数表 / 结果表、导出通过；**库存岗打不开差异报表**（缺陷 D1） |
| 10 | 收银整班 | `erp.cashier.first-sale`；`erp.pos.shift`（work-followups，下一轮合入） | 首单已在全套里 |
| 11 | 员工：加用户 | `erp.team.add-member`（work-followups，下一轮合入） | 待合入 |
| 12 | 员工：改密码 | 无（改密码在身份服务） | 部署后在 testerp 用测试账号实测 |
| 13 | 写 Express（CC 桌面版） | 无（要 Windows 上的 CC 桌面版和 Express） | 待主会话定：测试机实测，动测试机前先问 |
| 14 | F13：CC 页面刚打开就发的第一条消息不丢（10-15 夜间批遗留） | `cc.chat` 单列断言「F13：页面刚加载就发的第一条不丢」 | 第一步就是输入框一出现就发，刷新后两轮都在；两套数据 × 两种宽度都过：**F13 不复现** |

## 发现

- **D1 库存岗打不开盘点差异报表**（已报主会话，转开发窗口）：员工打开提示「您没有权限访问报表：盘点差异看板」。报表
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
