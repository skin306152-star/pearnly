# 前端页面清单（ADR-005 施工顺序第 1 步）

日期：2026-09-18。范围：**只列要迁移的三个产品 + 租户管理**。
旧系统其余部分（会计、报税、POS、AI 管家、Daily、工资、邮件接入、Excel 对账、
工单）不在本清单内 —— 见 `docs/p0/04-scope-and-drop-list.md`。

## ⚠️ 先看这条：菜单以线上为准，本地代码是旧的

本地所有 checkout（`pearnly-app`、`origin/master`、工作树 `pearnly-app-erp-root`）
**都落后于线上实际跑的版本**。三处不一致的实据：

| | 本地代码 | 线上（用户 2026-09-18 截图） |
|---|---|---|
| 侧栏有没有"入库/出库" | 本地工作树没有；`origin/master` 有 | 有 |
| 商品那组 | `商品`，只有 收发存报表 | **`商品与库存`**，含 商品数据/库存/入库/出库/收发存报表 |
| 供应商那组 | `进项管理` | **`采购系统`** |
| 主数据里 | 有"客户管理" | 有 **`公司主档`**（本地代码里搜不到这个词），客户移到了销售系统下 |

结论：**本清单的菜单结构一律以用户截图（线上）为准**；本地代码只用来查
"这一页对应哪些接口"。以后核 UI 别再看本地工作树。

## 首页 = 订阅与计费（三个产品同一个页面）

用户 2026-09-18：

> "dms 的套餐与余额，同步为 erp 和 cowork 的首页这样吧，它们都是一样的充值入口。"

所以三个产品的**首页都是同一个"订阅与计费"**：当前套餐（订阅/未订阅）、账户余额、
**充值**入口、套餐选择（Package S/M/L）、计费规则、记录明细、导出明细。

现状是**两套各写各的**（所以要合）：

| | 实现 | 规模 | 调的接口 |
|---|---|---|---|
| 产品首页 | `src/home/subscription.ts` | 303 行 | `/api/me/subscription`、`/api/subscription/{subscribe,cancel}`、`/api/credits/usage-history` |
| DMS 套餐与余额 | `static/dms/dms-billing*.js` + `dms-billing.css` | **1747 行** | `/api/me/credits`、`/api/credits/topup/*`、`/api/credits/billing-export`、`/api/me/subscription` |

合并后**只留一套**，三个产品共用；DMS 侧边栏那一项"套餐与余额"并进"首页"。

顺带说明一个历史遗留：这是功能目录里的 `home_subscription`（"首页（订阅与计费）"）。
P4.04 当时记过"它没有对应 DocType，所以无从约束" —— 现在明确了：**它是一个真实页面，
不是数据表**，所以它的门禁将来不能用 DocType 权限那套来表达。

## 通用部分（三个产品都一样）

| 位置 | 内容 |
|---|---|
| 登录 | 统一登录页（已完成，见 `docs/p4/10-login-theme.md`） |
| 侧栏顶部 | 品牌标识 + 折叠 |
| 侧栏导航 | 按产品分组的页面（下表），**只显示买到的功能** |
| 顶部右侧 | 账套切换 + 账号 |
| 侧栏底部 | 账号菜单：改密、语言切换、退出（改密见 `docs/p2/03-password-change.md`） |
| 主区 | 当前功能页 |
| 皮肤 | 色值沿用旧产品设计系统，见 [ADR-007](../adr/0007-frontend-skin.md) |

## Cowork

预设 `COWORK_PRESET` = `dashboard · cowork · master · clients · company · integrations · guide`

```text
首页（订阅与计费）       dashboard
工作协作                work        ← 已不再迁移（2026-09-21 用户决定：团队协作后期自行开发）
Pearnly Cowork ▼        (firm)
  录入工作台             dms-intake
  识别记录               history
  推送日志               push-logs
  对账中心               reconcile
  库存盘点               stocktake
主数据 ▼                (master)
  客户管理               clients
  公司资料               company
  团队管理               erp-team
  集成                   integrations
使用教程 ▼              (guide)
  Express 推送手册        guide
```

> 已按用户要求移出：`Google Sheet 留档`（`pos-sheets`）、`异常栏`（`exceptions`）。

## DMS —— 网页端大幅收窄（用户 2026-09-18 定）

> "DMS 身份证录入网页端不要了，业务几乎都在 LINE 完成，网页只负责绑定第三方 DMS
> 和其它不变，只取消网页前端的录入入口。"

```text
首页（订阅与计费）       ← 原"套餐与余额"并过来，与 ERP/Cowork 同一个页面
连接第三方 DMS          dms-connect   ← 网页端的主要职责
LINE 绑定码             生成码 + 显示（用户在 LINE 里把码发过来完成绑定）
推送记录                records
操作员                  roster
```

**移出**：`身份证录入`（`intake`）。旧网页端四步（上传证件 → 客户匹配 → 资料确认 →
推送 DMS）整条不做网页版 —— 它在 LINE 里完成。

两点说明，避免以后误会：

- **"网页生成绑定码"本来就存在**：`pearnly_dms/line_handlers.py` 的提示语就是
  "请把网页上生成的绑定码发给我"，属于"网页负责绑定"这一块，保留。
- **"连接第三方 DMS"= MR.ERP DMS 连接向导**（`dms-connect.js`）：地址写死、
  只维护一组有修改权限的账号密码，凭据加密落地。
- 功能目录里的 `dms:id_card_ocr`、`dms:customer_match` **不因此删掉** ——
  能力还在卖，只是交付方式变成 LINE。

## ERP（菜单以用户截图为准）

```text
首页（订阅与计费）      dashboard        ← 截图里 首页 高亮、页面就是"订阅与计费"
商品与库存 ▼
  商品数据             sales-products
  库存                 inventory
  入库                 stock-in
  出库                 stock-out
  收发存报表            stock-card
采购系统 ▼
  采购记录             purchase
  供应商               purchase-suppliers
  采购设置             purchase-settings
销售系统 ▼
  销售记录             sales-records
  客户                 buyer-clients
主数据 ▼
  公司主档             ← 本地代码里没有这个词，按截图记录
  公司资料             company
  团队管理             erp-team
  集成                 integrations
```

### 按你的决定移出的

| 页面 | 决定 |
|---|---|
| 发票工作台（`sales-invoices`） | **不要** |
| 账套 / 开票资料（`sales-account`） | **不要** |
| Google Sheet 留档（`pos-sheets`） | **不要** |
| 异常栏（`exceptions`） | **不要** |

移出后销售那组只剩"销售记录"和"客户" —— 这就是"录完采购和销售之后不要会计"的落地。

> "商品数据""库存"这两条：当前本地代码的 ERP 菜单里没有（它们在 POS 那组），但
> 你截图里有，而且做入库/出库本来就得先有它们，所以按截图保留。

## 第一条纵向切片：ERP 采购录入

DMS 身份证录入改走 LINE 之后，它不再是网页端的样板。新的第一条切片：

### **采购录入**（采购系统 → 采购记录）

| 理由 | 证据 |
|---|---|
| **后端已经真跑通**，只差界面 | `docs/p3/02-erp-slice.md`：建单生成 `PUR-2026-00002`、含税合计 1123.5、汇总对账、**重复上传被去重拦下**、跨租户查 0 条 |
| 正好是"录完采购" | 业务链 采购 → 销售 → 库存 的链头 |
| 界面不复杂 | 建单表单 + 列表 + 汇总 |
| 门禁能一起验 | `erp:purchase` → 角色 **ERP 采购** → `Purchase Document` / `Purchase Line` |

要打通的完整链路：

```text
发号 → 登录 → 进 ERP → 采购录入 → 建一张真实采购单 → 列表里查到
     → 没买这个功能的租户：菜单不显示 + 直接调接口 403
```

### 这条切片用到的接口（已存在，不用新写）

| 接口 | 方法 | 用途 |
|---|---|---|
| `pearnly_erp_portal.purchase.api_create_purchase` | POST | 建单。**金额由服务端重算**，不接受前端传合计 |
| `pearnly_erp_portal.purchase.api_list_purchases` | GET | 列表，支持 供应商 / 状态 / 起止日期 / 条数 |
| `pearnly_erp_portal.purchase.api_summary` | GET | 期间汇总（总额、增值税、应付净额、未付） |

实现在 `apps/pearnly_erp_portal/pearnly_erp_portal/purchase.py`（171 行）。

## 还没做

- **其余页面的接口清单**：上面只列了首条切片那三个。其余页面等做到再列，
  免得列了不用、还对不上代码。
- **"公司主档"是什么页**：本地代码里没有这个词，只有截图。等做到主数据那组时确认。
- **首页（订阅与计费）合并后的接口**：两套现有接口不一样（见上表），合并时以哪套为准
  要单独定，属于那次改造的范围。
