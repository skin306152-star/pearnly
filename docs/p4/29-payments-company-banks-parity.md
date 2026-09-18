# 收款资料与五类银行目录按线上补齐

日期：2026-09-18。承接 [28-booking-foundation-parity.md](28-booking-foundation-parity.md)。
线上基准 `501589f0`。第 3 批的第三件。

## 一、`dms_payments`：缺了一整道"本地先拦"

新平台**没有** `validate_payment_completeness` —— 收款资料不完整时，线上在本地就拦
（抛 `ERR_DMS_PAYMENT_INCOMPLETE`），新平台直接放行到 DMS，等对方报错才知道。
而那个错误码的**五种语言**早就躺在语言包里（前几批入的包）—— 说明这一环当时搬漏了。

顺带清掉两处错位的东西：

| 东西 | 为什么要删 |
|---|---|
| `_LEGACY_EXTRA`（把 `extra["ref"]` 当票据号读） | 把 LINE 的自由文本拆成 DMS 原生槽位，是**上一层**（`booking_payments`）的职责。`dms_payments` 只认原生槽位名，两处各说各话迟早不一致 |
| `extra["src"]` 的拆分行文字启发式 | 同上。线上用例直接把它钉成**必须被拒**：`{"src": "ธนาคาร 123456789"}` → `ERR_DMS_PAYMENT_INCOMPLETE` |
| `src_time: txttimetffrom` | **过期残留**：线上在 `08fb411c` 删掉了（"accept legacy time replies while **dropping optional field**"，DMS 不要求转账时间） |

补齐后的规矩（与线上逐行等价，114→114 行）：

```
转账：必须给出**到账方**银行身份（dst_bank_name + dst_bank_id 必填）；
      来源银行与到账账户/分行/公司名"有就映射"，不额外追问、也不卡住。
支票 / 现金支票 / 刷卡：原生票据字段 + bank_id 必填（目录权威为空时可手工，见下）。
其它：detail 必填。
```

## 二、`dms_company_banks`：从"只管转账目的地"补到**五类目录**

原来是 91 行、只管 `company_banks`（转账到账方）。线上是 198 行，管五类
（各自独立的原生 typeahead，**ID 与范围都可能不同，不能互相顶替**）：

| 目录 | 用在哪 |
|---|---|
| `company_banks` | 转账到账方 —— **永远**按实时目录 id 校验，永不手工 |
| `source_banks` | 转账来源银行 |
| `cheque_banks` / `cashier_banks` / `card_banks` | 支票 / 现金支票 / 刷卡 |

补进来的关键规则：

- **目录权威为空**（拿到 `[]`，不是取数失败）→ 后四类允许这一笔用手工填的银行名称，
  挂 `MANUAL_BANK_FLAG`；它**只豁免 id 槽位**，名称与其它原生字段照旧必填。
  目录非空则必须命中 —— **已删除的旧选项必被 `ERR_DMS_MASTER_UNMATCHED` 拦下**。
- `_bank_detail`：DMS 用 **`00` 当空账号/空分行的占位**，绝不把它当真实数据
  展示或提交。
- `company_bank_payment_extra(row, existing)`：目录缺账号/分行时**保留用户已填的**，
  不要抹成空。
- `sort_bank_rows`：数字码按数值升序（与原生界面一致），**助记码保持 DMS 原序**
  （DMS 没给它们定义序，不自作主张）。

原来那个"没有转账就早退"的捷径也删了：支票/现金支票/刷卡同样要按各自的实时目录核对
身份，最后统一过完整性校验。**不额外取数**这个好处仍在（循环里只对出现过的渠道取数）。

## 三、验证

```bash
scripts/sample-verify-dms-client.sh  dms   # 54/54（新增 9 条收款断言）
scripts/sample-verify-dms-intake.sh  dms   # 26/26
scripts/sample-verify-dms-cards.sh   dms   # 34/34
scripts/sample-verify-dms-advisor.sh dms   # 25/25
scripts/sample-verify-dms-draft.sh   dms   # 21/21
scripts/sample-verify-dms-booking-foundation.sh dms  # 45/45
```

新增的关键用例：

| 用例 | 锁住的规则 |
|---|---|
| 转账来源/到账**逐槽位**映射 | 原生槽位一一对上 |
| 转账**不写** `txttimetffrom` | 线上已删的字段不许复活 |
| `{"src": …}` / 只给到账账号 / 只给来源银行名 → `ERR_DMS_PAYMENT_INCOMPLETE` | 旧形态必须被拒 |
| 支票/现金支票/刷卡缺 `bank_id`、其它缺 `detail` → `INCOMPLETE` | 不只管转账 |
| 手工填来源银行名时 bank id 可省（不伪造目录 id） | 权威为空时的唯一出路 |
| 支票没说清是哪家银行 → `UNMATCHED` | 认不出就不猜 |
| 没有银行渠道时一次取数都不发生 | 不为了"顺便校验"多打扰 DMS |

改了 2 条**过期断言**（都是断言停在旧行为）：`sample-verify-dms-intake.py` 的
"没有转账就不去多打接口"（返回值现在一律带 `extra`，且该层不再早退）。

## 四、一个**没做、但已写清原因**的事

五类银行目录还**不算进主档完整度判据**（`dms_masters_cache._PENDING_COMPLETE_KEYS`）。

上次延后的理由是"还抓不到"——**取数本批已经搬完了**（`fetch_payment_bank_masters`），
但真正卡住的是**权威只读**：这四类目录在销售账号下常被裁成空表，空表会被完整度判据
当成"没抓到" → 完整度永远不满足 → 每次读主档都重新登录 DMS → **又变成互踢**。
线上靠 `_run_logged_in(..., authoritative_read=True)` 借管理员会话读，才敢并进去。

新平台的 `dms_intake.run_logged_in` **还没有** `authoritative_read` 这个开关。
所以下一步是：**先加 `authoritative_read`，再把"抓取 + 判据"两处一次并进去** ——
只并判据就会踩上面那个失败模式。原因已写在代码注释里，不会静默。

## 五、下一批

`dms_booking_readback.py`（358 行，三层判据 + 定因阶段）→
`dms_booking_submit.py`（344 行）→ 改 `dms_client_ops` 订车段 →
`dms_docno`（261 行）→ 加 `authoritative_read` 并收尾银行目录判据。
