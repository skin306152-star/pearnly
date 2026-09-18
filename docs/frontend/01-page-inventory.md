# 前端页面清单（ADR-005 施工顺序第 1 步）

日期：2026-09-18。范围：**只列要迁移的三个产品 + 租户管理**。
旧系统其余部分（会计、报税、POS、AI 管家、Daily、工资、邮件接入、Excel 对账、
工单）不在本清单内 —— 见 `docs/p0/04-scope-and-drop-list.md`。

## 清单从哪来（不靠回忆、不靠看界面猜）

| 产品 | 权威来源 | 说明 |
|---|---|---|
| Cowork / ERP | `pearnly-app` **`origin/master`** 的 `src/home/nav-presets.ts` | 旧前端把侧栏菜单按业态写成"拍板清单"，注释写着"以拍板清单为唯一事实源" |
| 各页中文名与分组 | 同分支 `src/home/app-shell-sidebar-html.ts` | 侧栏标记本身 |
| DMS | `pearnly-app/static/dms/` 的 `dms.html` + `dms-i18n-{zh,th,en,ja}.js` | DMS 是独立外壳，不走 home 那套预设 |

> ⚠️ **本地那份 checkout 落后 `origin/master` 119 个提交**，差别正好落在这次要用的地方：
> 本地侧栏里**没有"入库/出库"**，`origin/master` 里才有。所以本清单一律以
> `origin/master` 为准（`git fetch` 已确认是最新）。以后核 UI 别再看本地工作树。

## 通用部分（三个产品都一样）

| 位置 | 内容 |
|---|---|
| 登录 | 统一登录页（已完成，见 `docs/p4/10-login-theme.md`） |
| 侧栏顶部 | 品牌标识 + 折叠 |
| 侧栏导航 | 按产品分组的页面（下表），**只显示买到的功能** |
| 侧栏底部 | 账号菜单：改密、语言切换、退出（改密见 `docs/p2/03-password-change.md`） |
| 主区 | 当前功能页 |
| 皮肤 | 色值沿用旧产品设计系统，见 [ADR-007](../adr/0007-frontend-skin.md) |

## Cowork

预设 `COWORK_PRESET` = `dashboard · cowork · work · master · clients · company · integrations · guide`

```text
首页                    dashboard
工作协作                work        ← WeKan 看板那层（用户 2026-09-18：算 Cowork 内部功能，留着）
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
连接第三方 DMS          dms-connect   ← 网页端的主要职责
LINE 绑定码             绑定码生成 + 显示（用户在 LINE 里把码发过来完成绑定）
推送记录                records
操作员                  roster
套餐与余额              billing
```

**移出**：`身份证录入`（`intake`）。旧网页端四步（上传证件 → 客户匹配 → 资料确认 →
推送 DMS）整条不做网页版 —— 它在 LINE 里完成。

两点说明，避免以后误会：

- **"网页生成绑定码"本来就存在**：`pearnly_dms/line_handlers.py` 的提示语就是
  "请把网页上生成的绑定码发给我"，所以绑定码页属于"网页负责绑定"这一块，保留。
- **"连接第三方 DMS"= MR.ERP DMS 连接向导**（`dms-connect.js`）：DMS 地址写死、
  只维护一组有修改权限的账号密码，凭据加密落地。这是网页端保留的核心职责。
- 功能目录里的 `dms:id_card_ocr`、`dms:customer_match` 两个功能**不因此删掉** ——
  能力还在卖，只是交付方式变成 LINE。等做 LINE 那条时再核对应关系。

## ERP（用户 2026-09-18 已拍板）

预设 `ERP_PRESET` = `dashboard · firmGoods · purchases · sales · master · clients · company · integrations · team`

```text
首页                    dashboard
商品与库存 ▼            (firm-goods)
  商品数据               sales-products   ← 依你截图加入，见下
  库存                   inventory        ← 同上
  入库                   stock-in         ← 你要的
  出库                   stock-out        ← 你要的
  收发存报表             stock-card
进项管理 ▼              (expense)
  采购 / 进项            purchase
  供应商                 purchase-suppliers
  采购设置               purchase-settings
销售系统 ▼              (sales)
  销售记录               sales-records
主数据 ▼                (master)
  客户管理               clients
  公司资料               company
  团队管理               erp-team
  集成                   integrations
```

### 按你的决定移出的

| 页面 | 路由 | 决定 |
|---|---|---|
| 发票工作台 | `sales-invoices` | **不要** |
| 账套 / 开票资料 | `sales-account` | **不要** |
| Google Sheet 留档 | `pos-sheets` | **不要** |
| 异常栏 | `exceptions` | **不要** |

移出后销售那组只剩"销售记录"——**这就是"录完采购和销售之后不要会计"的落地**。

### 商品数据 / 库存 这两条是我的推断，说明一下

代码里 `ERP_PRESET` **没有**把"商品数据""库存"放进 ERP 壳（它们在 `products` 组里，
那是 POS/商户端用的）。但你截图里"商品与库存"这组下面**有**这两条，而且做入库/出库
本来就得先有商品与库存。所以我**按你截图加入**了。不对就说一声 —— 这是整份清单里
唯一一处"代码与截图不一致、我替你做了判断"的地方。

## 第一条纵向切片（已换，原来的 DMS 身份证录入作废）

DMS 身份证录入改走 LINE 之后，它不再是网页端的样板。新的第一条切片选：

### **ERP 采购录入**（进项管理 → 采购 / 进项）

选它的理由是有实据的：

| 理由 | 证据 |
|---|---|
| **后端已经真跑通**，只差界面 | `docs/p3/02-erp-slice.md`：建单生成 `PUR-2026-00002`、含税合计 1123.5、汇总对账、**重复上传被去重拦下**、跨租户查 0 条 |
| 正好是你说的"录完采购" | 业务链是 采购 → 销售 → 库存，这是链头 |
| 界面不复杂 | 一张建单表单 + 一个列表 + 汇总 |
| 门禁能一起验 | `erp:purchase` → 角色 **ERP 采购** → `Purchase Document` / `Purchase Line` |

切片要打通的完整链路（ADR-005 要求）：

```text
发号 → 登录 → 进 ERP → 采购录入 → 建一张真实的采购单 → 列表里查到
     → 没买这个功能的租户：菜单不显示 + 直接调接口 403
```

### 这条切片用到的接口（已存在，不用新写）

| 接口 | 方法 | 用途 |
|---|---|---|
| `pearnly_erp_portal.purchase.api_create_purchase` | POST | 建单。**金额由服务端重算**，不接受前端传合计 |
| `pearnly_erp_portal.purchase.api_list_purchases` | GET | 列表，支持 供应商 / 状态 / 起止日期 / 条数 |
| `pearnly_erp_portal.purchase.api_summary` | GET | 期间汇总（总额、增值税、应付净额、未付） |

服务层实现在 `apps/pearnly_erp_portal/pearnly_erp_portal/purchase.py`（171 行）。

## 还没做

- **其余页面的接口清单**：上面只列了首条切片用到的三个。其余页面等做到那一步再列，
  免得列了不用、还对不上代码。
- **ERP 的"商品数据/库存"归属**待你确认（见上）。
