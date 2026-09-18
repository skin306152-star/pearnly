# 前端页面清单（ADR-005 施工顺序第 1 步）

日期：2026-09-18。范围：**只列要迁移的三个产品 + 租户管理**。
旧系统其余部分（会计、报税、POS、AI 管家、Daily、工资、邮件接入、Excel 对账、
工单）不在本清单内 —— 见 `docs/p0/04-scope-and-drop-list.md`。

## 清单从哪来（不靠回忆）

| 产品 | 权威来源 | 说明 |
|---|---|---|
| Cowork / ERP | `pearnly-app/src/home/nav-presets.ts` 的 `COWORK_PRESET` / `ERP_PRESET` | 旧前端把侧栏菜单按业态写成"拍板清单"，注释写着"以拍板清单为唯一事实源" |
| 各页中文名 | `pearnly-app/src/home/app-shell-sidebar-html.ts` | 侧栏标记的 `data-i18n` 与文案都在这里 |
| DMS | `pearnly-app/static/dms/dms.html` + `dms-i18n-{zh,th,en,ja}.js` | DMS 是独立外壳，不走 home 的那套预设 |

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

## Cowork（预设 `COWORK_PRESET`）

```text
首页
Pearnly Cowork ▼
  录入工作台   dms-intake
  识别记录     history
  推送日志     push-logs
  对账中心     reconcile
  库存盘点     stocktake
主数据 ▼
  客户管理     clients
  公司资料     company
  团队管理     erp-team
  集成         integrations
使用教程 ▼
  Express 推送手册  guide
```

## DMS（独立外壳，4 个视图）

```text
身份证录入   intake
推送记录     records
操作员       roster
套餐与余额   billing
```

四语文案全部现成（`dms-i18n-*.js`），例如 `身份证录入 / บันทึกจากบัตร /
ID Card Intake / 身分証登録`。

## ERP —— ⚠️ 这里有两套不一样的菜单，需要确认

本地代码里的 `ERP_PRESET` 是：

```text
首页
商品（事务所端） ▼   收发存报表   stock-card
采购系统 ▼          采购 / 进项   purchase
                    供应商       purchase-suppliers
                    采购设置     purchase-settings
销售系统 ▼          销售记录     sales-records
                    发票工作台   sales-invoices
                    账套 / 开票资料 sales-account
主数据 ▼            客户管理 / 公司资料 / 集成
团队管理             erp-team
```

但**用户在 2026-09-18 发来的截图**（线上 `pearnly.com/erp#/dms-intake`）里是另一套：

```text
商品与库存 ▼   商品数据 / 库存 / 入库 / 出库 / 收发存报表
采购系统 ▼     采购记录 / 供应商 / 采购设置
销售系统 ▼
主数据 ▼
```

两者的差别不是大小事：**多了"入库""出库"，少了"发票工作台""账套/开票资料"**，
而且"商品数据/库存"从另一组并进了"商品与库存"。

核实到的情况：

- 本地侧栏标记里**搜不到"入库""出库"**（`rg '入库|出库' app-shell-sidebar-html.ts` 无结果）；
- 线上 `/erp` 现在**只是个登录门**（标题 `ERP · Pearnly`，账号密码登录，见
  `static/erp/erp.html` / `routes/pages_routes.py:215`），进去之后才进产品壳；
- 所以**线上跑的那份前端比本地 check-out 新**，截图反映的是较新的形态。

**这条要用户拍板**（我倾向以截图那套为准，因为那是用户现在实际在用的界面，
而且"入库/出库"是真实业务动作；但发票工作台/账套开票资料要不要，得用户说）：

1. ERP 导航以**截图那套**为准，还是以**本地代码那套**为准？
2. "发票工作台""账套 / 开票资料"要不要？（用户说过"录完采购和销售之后不要会计"，
   这两页算不算会计，需要明确）

## 第一条纵向切片

按 ADR-005 的施工顺序，清单列完之后先做**一条完整样板**。建议 DMS 身份证录入：

```text
上传证件 → 客户匹配 → 资料确认 → 推送 DMS
```

| 为什么选它 | |
|---|---|
| 后端已经通 | `pearnly_core`/`pearnly_dms` 的切片九步全过（`docs/p3/05-dms-slice.md`） |
| 界面就四步 | 一屏能装下，适合当样板 |
| 旧界面成熟 | 直接照做，不用重新设计交互 |
| 功能门禁能一起验 | **买到的能进、没买的 403**，这是第一次能在界面上看出来 |

## 还没做的

- **接口清单**：每个页面对应哪些接口还没逐页列（ADR-005 第 1 步要求"页面与对应接口"）。
  等 ERP 那套菜单确认了再一起列，免得列两遍。
- **ERP 的 IA 冲突**未定（见上）。
