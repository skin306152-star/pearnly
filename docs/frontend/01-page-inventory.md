# 前端页面清单（ADR-005 施工顺序第 1 步）

日期：2026-09-18。范围：**只列要迁移的三个产品 + 租户管理**。
旧系统其余部分（会计、报税、POS、AI 管家、Daily、工资、邮件接入、Excel 对账、
工单）不在本清单内 —— 见 `docs/p0/04-scope-and-drop-list.md`。

## 清单从哪来（不靠回忆、不靠看界面猜）

| 产品 | 权威来源 | 说明 |
|---|---|---|
| Cowork / ERP | `pearnly-app` **`origin/master`** 的 `src/home/nav-presets.ts` | 旧前端把侧栏菜单按业态写成"拍板清单"，注释写着"以拍板清单为唯一事实源" |
| 各页中文名与分组 | 同分支 `src/home/app-shell-sidebar-html.ts` | 侧栏标记本身 |
| DMS | `pearnly-app/static/dms/dms.html` + `dms-i18n-{zh,th,en,ja}.js` | DMS 是独立外壳，不走 home 的那套预设 |

> ⚠️ **本地那份 checkout 落后 `origin/master` 119 个提交**，而且差别正好落在这次要用的
> 地方：本地侧栏里**没有"入库/出库"**，`origin/master` 里才有。所以本清单一律以
> `origin/master` 为准（`git fetch` 已确认是最新）。以后核 UI 别再看本地工作树。

## 通用部分（三个产品都一样）

按用户 2026-09-18 确认的形态：**左侧一列导航，里面就是这家租户买到的功能；
顶上或底部放账号菜单与语言切换。**

| 位置 | 内容 |
|---|---|
| 登录 | 统一登录页（已完成，见 `docs/p4/10-login-theme.md`） |
| 侧栏顶部 | 品牌标识 + 折叠 |
| 侧栏导航 | 按产品分组的页面（下表），**只显示买到的功能** |
| 侧栏底部 | 账号菜单：改密、语言切换、退出（改密已在 P2 做过，见 `docs/p2/03-password-change.md`） |
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
  Google Sheet 留档      pos-sheets   ← POS 遗留，见文末待确认
  异常栏                 exceptions   ← 见文末待确认
  集成                   integrations
使用教程 ▼              (guide)
  Express 推送手册        guide
```

## DMS（独立外壳，4 个视图）

```text
身份证录入   intake
推送记录     records
操作员       roster
套餐与余额   billing
```

四语文案全部现成（`dms-i18n-*.js`），例如 `身份证录入 / บันทึกจากบัตร / ID Card Intake / 身分証登録`。

## ERP（用户 2026-09-18 已拍板）

预设 `ERP_PRESET` = `dashboard · firmGoods · purchases · sales · master · clients · company · integrations · team`

```text
首页                    dashboard
商品与库存 ▼            (firm-goods)
  商品数据               sales-products   ← 依你截图加入，见文末说明
  库存                   inventory        ← 同上
  入库                   stock-in         ← 你要的
  出库                   stock-out        ← 你要的
  收发存报表             stock-card
进项管理 ▼              (expense)
  采购 / 进项            purchase
  供应商                 purchase-suppliers
  采购设置               purchase-settings
销售系统 ▼              (sales)          ← 只保留销售记录，见下
  销售记录               sales-records
主数据 ▼                (master)
  客户管理               clients
  公司资料               company
  团队管理               erp-team
  集成                   integrations
  Google Sheet 留档      pos-sheets       ← 见文末待确认
  异常栏                 exceptions       ← 见文末待确认
```

### 按你的决定移出的两页

| 页面 | 路由 | 决定 |
|---|---|---|
| 发票工作台 | `sales-invoices` | **不要**（2026-09-18 你确认） |
| 账套 / 开票资料 | `sales-account` | **不要**（同上） |

移出后，销售那组只剩"销售记录"，**这本身就是"录完采购和销售之后不要会计"的落地**。

### 商品数据 / 库存 这两条是我的推断，说明一下

代码里当前版本 `ERP_PRESET` **没有**把"商品数据""库存"放进 ERP 壳（它们在另一个
`products` 组里，那是 POS/商户端用的）。但你发来的截图里，"商品与库存"这组下面
**有**商品数据与库存，而且做入库/出库本来就得先有商品与库存。

所以我**按你截图加入**了这两条。如果不对，说一声就撤 —— 这是本清单里唯一一处
"代码与截图不一致、我替你做了判断"的地方，其余都直取代码。

## 文末待确认（两条，都不阻塞开工）

| 条目 | 情况 |
|---|---|
| `Google Sheet 留档`（`pos-sheets`） | 路由名带 `pos`，属 POS 遗留；但它在 ERP 的"主数据"组里。要还是不要？ |
| `异常栏`（`exceptions`） | 旧代码注释写着"2026-07-26 下线，由侧栏标记恒隐"，但节点还在预设里。核实是不是真的已下线 |

## 第一条纵向切片

清单列完后先做**一条完整样板**（ADR-005）。建议 DMS 身份证录入：

```text
上传证件 → 客户匹配 → 资料确认 → 推送 DMS
```

| 为什么选它 | |
|---|---|
| 后端已经通 | `pearnly_dms` 的切片九步全过（`docs/p3/05-dms-slice.md`） |
| 界面就四步 | 一屏能装下，适合当样板 |
| 旧界面成熟 | 直接照做，不用重新设计交互 |
| 能顺带验门禁 | **买到的能进、没买的 403** —— 这是第一次能在界面上看出来 |

## 还没做

- **接口清单**：每个页面对应哪些接口还没逐页列（ADR-005 第 1 步要求"页面与对应接口"）。
  下一步做，先做首条切片用到的那几个（`pearnly_dms/id_card.py` 的六个接口已经现成）。
