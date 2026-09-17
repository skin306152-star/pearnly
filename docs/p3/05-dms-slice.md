# P3.05 DMS 纵向切片：身份证录入

日期：2026-09-17。站点：`dms-a.localhost`。

## 切片范围

对应旧系统界面上的四步：**上传证件 → 客户匹配 → 资料确认 → 推送 DMS**。

| 产出 | 说明 |
|---|---|
| DocType `DMS ID Card Intake` | 证件录入：状态、匹配结果、端点与 DMS 引用、证件资料、识别留证、推送引用 |
| DocType `DMS Customer Link` | **证件号 → DMS 客户引用**的本地对照，用于"证件号相同即同一客户" |
| 服务层 `pearnly_dms/id_card.py` | 建单、客户匹配、确认、推送、列表、汇总；六个接口 |

### 两条从旧系统继承的规则

1. **证件号相同默认视为同一客户**，避免重复建档（旧系统界面上明写的设计原则）。
2. **客户主数据在外部 MR.ERP DMS**，本地只存对照与引用；推送状态的真值仍在
   `erp_push_logs`（`trigger='id_card'`），本表只存引用位。

### 证件号是个人敏感信息

列表接口**不返回**证件号（实测返回里没有 `id_number` 字段）；代码里另有
`mask_id_number()` 供需要展示末四位的场景使用。

## 实测结果（九步全过）

脚本 `scripts/sample-dms-slice.sh`：

| 步骤 | 结果 |
|---|---|
| OIDC 登录 | 身份 `tenant-admin@sample.local` |
| 上传一张证件（识别完成、未匹配） | 生成 `IDC-2026-00001` |
| **未匹配就想确认** | **被拒**：`必须先完成客户匹配才能确认` |
| 客户匹配（给出 DMS 引用） | `match_result: new`，`external_ref: DMS-CUST-9001` |
| 资料确认 | `status: confirmed` |
| 推送 DMS | `status: pushed`，`trigger: id_card` |
| 列表 | **不含证件号字段** |
| 同一证件号再录一条并匹配 | `match_result: existing`，复用 `DMS-CUST-9001` |
| 汇总 | `new_customers: 1`、`reused_customers: 1` |

最后两行是这条切片的重点：**同一个证件号第二次录入不会重复建档**，而是匹配到
已有的 DMS 客户引用。

## 本轮踩到的坑

1. **zsh 下 `set -- $kv` 不分词**。配置脚本里用它对键值对做拆分，结果键和值被拼成
   一个字符串写进站点配置，站点随后报 417。已改为**显式一次一个键值**，并新增
   `scripts/sample-configure-site.sh` 统一处理，脚本里带一道配置校验（键值必须分成两列）。
2. **`bench console` 吞多行脚本**。带 `try/except` 的多行片段在 REPL 里不执行也不报错，
   表现为"什么都没发生"。改用单行表达式调用。
3. 每个部署各有一套 Keycloak 客户端与站点配置，`dms-a` 一开始没配，登录链路直接断。
   这是设计要的，不是缺陷；补配置后正常。

## 未做

1. **证件 OCR**：`id_card_image` 与 `recognition_raw` 字段已建，真正的图片识别
   （Google Document AI / 自建模型）未接入，属 P6。
2. **主数据缓存**：旧系统的 `dms_masters_cache`（按端点缓存 DMS 主数据）还没迁。
3. **操作员花名册**：DMS 的 `roster`（仅责任人可管）未做。
4. **真实 DMS 推送**：现在只到"标记为待推送"，对外部 MR.ERP 的实际写入未接。
5. 权限仍是"登录即可"。
