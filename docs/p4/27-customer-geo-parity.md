# 客户地址/称谓：不许猜、不许兜底（对着线上补齐）

日期：2026-09-18。承接 [26-dms-advisor-parity.md](26-dms-advisor-parity.md)。
线上基准 `501589f0`。第 3 批（客户写入链）的第一件。

## 一、找到的问题：新建客户会带上"没人选过的地址"

新平台的 `_guard_required_selects` 还是**旧实现**：

```python
if not data.get("selprefix"):
    opts = self._select_options(form_html, "selprefix")
    if opts:
        data["selprefix"] = opts[0][0]      # ← 空就拿第一项补上
```

两个真害处：

1. **新建客户继承表单默认地址。** `cus/form.php` 的新建表单自带一组预选的省/县/区/邮编。
   过去建客户时没人清它 → 客户**带着一个没人选过的地址**被存进 DMS。事后看不出来，
   对账/寄件才发现。
2. **称谓被凭空捏造。** 空值/主档里已删的值会拿"第一项"顶上 —— 客户档里就有了一个
   没人确认过的称谓。

这条正是线上 `b78697f3`（2026-09-12）那次修复的题目：**禁止旧值及首项兜底**。
新平台那个"空的必填 select 会触发误导性报错，所以补成有效值"的注释，是这次修复
**之前**的理由，代码注释跟着一起过期了。

## 二、改了什么

| 文件 | 改动 |
|---|---|
| `dms_customer_geo.py`（**新增**） | 搬线上 `services/erp/mrerp_dms_customer_geo.py`，逻辑一字未改 |
| `dms_client_intake.py` | create 前 `clear_customer_select_defaults(data)`；`_guard_required_selects` 改为委托 `validate_customer_selects`；模块开头第 2 条说明改对 |

新规矩（两条一起才放行）：

```
显式给值 + 与实时父级链核对通过
```

细节照线上：三套地址（`""` / `_ct` / `_sd`）分别校验；表单里压根没有那个 select 的
可选地址块 → 跳过；整套地址都空 → 跳过（没用上的就该留空）；父级链一级一级对，
对不上抛 `ERR_DMS_MASTER_UNMATCHED`，名册取不到抛 `ERR_DMS_MASTER_UNAVAILABLE`。
称谓先在表单里找，找不到再走双来源 `list_prefixes()`（有些合法称谓只在订车快速建客
表单里），**但绝不凭空造一个不存在的**。

## 三、连带修掉两条**过期断言**

收紧之后，两个验收脚本红了 —— 查下来**都是断言还停在旧行为**，不是新代码错：

| 脚本 | 旧断言 | 为什么错 |
|---|---|---|
| `sample-verify-dms-draft.py` | "称谓认不出 → 回退主档首项" | 线上 `_prefix_id` **认不出就返回空**，留给用户确认；新平台 `_prefix_id` 与线上逐字一致，是这条断言停留在更早的行为上（旧实现会把 นาย 写成 นางสาว） |
| `sample-verify-dms-client.py` | 调用 `save_customer` 只给证号+姓名 | 现在会被正确拦下（缺显式称谓/地址）。已按新契约补齐，并**新增拦截用例** |

顺带把 `docs/p4/18-dms-business-client-ported.md` 里"补成有效值……没顺手改好"那段的
结论更正了（当时说对了现象、说错了收尾）。

## 四、验证

```bash
scripts/sample-verify-dms-client.sh dms    # 45/45
scripts/sample-verify-dms-draft.sh  dms    # 21/21
scripts/sample-verify-dms-intake.sh dms    # 24/24
scripts/sample-verify-dms-cards.sh  dms    # 34/34
scripts/sample-verify-dms-advisor.sh dms   # 25/25
```

新增的关键用例（不是走过场）：

| 用例 | 锁住的规则 |
|---|---|
| 存下去的是面板选的地址（P9/D9/S9/Z9，不是表单自带的 P1/D1/S1/Z1） | 面板值优先 |
| 新建前清掉表单自带的地址默认值（含 `_ct`/`_sd`） | **清默认值**这条规则本身 |
| 清默认值时不动客户姓名/证号 | 只清下拉，别误伤 |
| 称谓不在名册里 → 拦 | 不猜 |
| 省不在实时名册里 → 拦 | 不猜 |
| 县不属于所选省 → 拦 | 父级链对不上要拦 |

## 五、这批**没做**的（第 3 批还没完）

客户写入链剩下的文件仍待逐个对线上（规模：线上约 2370 行 vs 新平台约 1450 行）：

| 文件 | 线上 | 新平台 | 差异 |
|---|---|---|---|
| `dms_docno.py` | 261 | 92 | 大：线上是严格 autonum 状态机（协议不符就抛）+ 隐藏字段 + 未占号扫描 |
| `dms_payments.py` | 158 | 122 | 中 |
| `dms_company_banks.py` | 278 | 130 | 大（按原生支付渠道补齐银行目录） |
| `dms_customer_geo.py` | 63 | — | ✅ 本批补上 |
| `dms_client_forms.py` | 171 | 179 | 小（无外部调用方，叶子） |
| `dms_client_intake.py` | 368 | 392 | 中 |
| `dms_intake.py` | 354 | 334 | 中 |
| `dms_client_ops.py` | 470 | 505 | 大，且**订车段依赖尚未搬的** `booking_payload/org/submit/readback` |

已核对为**等价、未动**的三个小文件：`dms_master_rows.py`、`dms_client_base.py`、
`dms_models.py` —— 差异只有命名约定（新平台统一 `Dms*`，线上 `DMS*`）和订车结构
（`organization_fields` 属第 4 批）。

## 六、一个必须记住的顺序

`dms_client_ops.py` 的订车段**不能单独换**：它要用 `dms_booking_payload` /
`dms_booking_org` / `dms_booking_submit` / `dms_booking_readback`，而这四个**新平台还
没有**。线上这四个文件合计约 1100 行（还有 `booking_customer` 110 行）。

**所以顺序是：先搬订车四件 → 再改 `client_ops` 的订车段 → 最后才动 `dms_docno`。**
`dms_docno` 现在被 `client_ops` 的旧订车路径用着（`next_unoccupied_docno`），
直接换成线上的 `DMSBookingAutonumState` 会把这条活路径打断。
