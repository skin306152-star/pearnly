# P3.02 ERP 纵向切片：采购单

日期：2026-09-17。站点：`erp-a.localhost`（对照 `erp-b.localhost`）。

## 切片范围

从旧系统 `purchase_docs` / `purchase_lines` 出发，映射成两个 DocType：

| DocType | 说明 |
|---|---|
| `Purchase Document` | 单据头：单据类型、供应商与税号、外部单据号、日期与到期日、币种与汇率、金额分列、审批与付款状态、外部引用与去重键 |
| `Purchase Line` | 明细：类型、品名、商品外部标识、数量与单位、单价、行折扣、税率、行金额 |

服务层 `pearnly_erp_portal/purchase.py`：建单、列单、期间汇总，以及三个给前端用的
接口（`api_create_purchase` / `api_list_purchases` / `api_summary`）。

### 保留的业务含义

币种、汇率、含税标志、**税额分列**（增值税 / 预扣税 / 整单折扣 / 尾差）、
审批状态与付款状态分开表达、**外部单据号原样保留**、去重键与影像指纹。

### 刻意没有搬过来的

- `ocr_raw`（OCR 原始报文）——流转中间态，不是业务字段。
- `requester_user_id`——内部外键，用链接表达更清楚。
- `amount_override`——阈值开关，等有真实需求再补。

计划要求"不机械把每张表照搬成用户可访问 DocType"，这就是那个判断的落点。

## 两条硬规则落在代码里

1. **金额一律服务端重算**。`PurchaseDocument._recompute()` 从小发明细算小计、
   按行税率算增值税、再算价税合计与应付净额；接口**不接受**前端传来的合计。
   金额运算用 `Decimal`，四舍五入到分。
2. **付款状态跟着实付金额走**，不允许出现"已付金额为 0 但状态是已付"这种矛盾组合。

## 实测结果（全过）

脚本 `scripts/sample-erp-slice.sh`，可重复执行：

| 步骤 | 结果 |
|---|---|
| OIDC 登录 `erp-a` | 成功，身份 `tenant-admin@sample.local` |
| 建单（两行：100×2.5 与 1×800，7% 税，整单折扣 50，预扣税 30） | 生成 `PUR-2026-00002` |
| 查回 | 含税合计 `1123.5` |
| 汇总 | `grand_total 2247.00`、`vat_amount 147.00`、`net_payable 2087.00`、`outstanding 2087.00` |
| 同一张票再传一次 | **被去重拦下**：`这张单已经录过：PUR-2026-00002` |
| 另一租户站点 `erp-b` 查同一接口 | **0 条** |

金额核对：小计 1050.00 + 增值税 73.50 = 1123.50；减整单折扣 50 与预扣税 30 →
应付净额 1043.50。与接口返回一致。

## 跨部署隔离的实测（比接口层更硬）

第一次在 `erp-b` 查 `tabPurchase Document` 时，返回的是
**表不存在**（`ERROR 1146`）——因为 `erp-b` 当时还没跑迁移。给 `erp-b` 迁移之后，
同代码同结构，`erp-a` 有 1 条、`erp-b` 有 0 条。

也就是说隔离不是靠 SQL 条件过滤实现的，而是**两个站点本来就落在两个不同的数据库**。

## 过程中踩到的两个坑

1. **`**kwargs` 会把框架的 `cmd` 参数一起吃掉**，接口报
   `create_purchase() got an unexpected keyword argument 'cmd'`。三个接口都要先
   `kwargs.pop("cmd", None)`。
2. `erp-b` 做身份绑定时报"站点配置缺少 pearnly_oidc_issuer"——因为它**没有自己的**
   Keycloak 客户端与站点配置。每个部署各有一套，这正是设计要的；补上 `erp-b`
   客户端与配置后正常。

## 未做

- Cowork 与 DMS 的纵向切片还没做（下一个）。
- 权限仍按"登录 + 角色"收口，正式权限码（如 `purchase.doc.view/create`）等角色模型落地后接入。
- 供应商还是文本字段，`Supplier` 主数据未建。
- 附件、审批流、ERP 推送都还没接进这条切片。
