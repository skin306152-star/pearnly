# 权威只读入口 + 五类银行目录并进完整度判据

日期：2026-09-18。承接 [31-docno-and-client-ops-parity.md](31-docno-and-client-ops-parity.md)。
线上基准 `501589f0`。这是 [29](29-payments-company-banks-parity.md) 第四节写下
"卡住、不能只并判据"的那一件，现在把两处一次并进去。

## 一、先加开关：`run_logged_in(..., authoritative_read=True)`

线上 `_run_logged_in` 有这个开关：**整块 DMS 读取**借管理员会话（配了独立管理员凭据组时），
销售的挑选/匹配因此看到的是**权威映射**，而不是自己的不完整视图。整块只解析一次管理员
transport；未配管理员照样走销售；配了但管理员登录失败 → `ERR_DMS_ADMIN_AUTH`
**明确失败关闭**，不静默降级。写操作（上传/提交）不走这个入口。

顺带对齐线上那两处"提交类失败不能被误报成掉线"的判据：

```python
# 提交失败带着"已经发出去了"的证据时，不要报"被别人顶下线" —— 单可能已经建了
if adapter.concurrent_login_detected and not getattr(exc, "response_body", {}).get("submitted"):
    return err("ERR_DMS_CONCURRENT_LOGIN", ...)
# 收尾复查同理：已经拿到单号的结果算数
if adapter.concurrent_login_detected and not (isinstance(out, dict) and out.get("booking_id")):
    return err("ERR_DMS_CONCURRENT_LOGIN", ...)
```

## 二、再把三处取数改成权威读

| 取数 | 为什么必须权威读 |
|---|---|
| `_fetch_masters_via_login` | 走 `fetch_payment_bank_masters(adapter, client=client)` 取**五类**银行目录（原来只取 `company_banks`） |
| `_fetch_paints_via_login` | 颜色主档在销售账号下可能被裁成空表 → 会被误判成"这车没颜色" |
| `_fetch_paints_batch_via_login` | 同上（批量预热版） |

## 三、然后才敢并进完整度判据

```python
_COMPLETE_KEYS = (..., "company_banks", "source_banks", "cheque_banks",
                  "cashier_banks", "card_banks")
```

只并判据不权威读 = 销售账号看不到这些目录 → 空表被当成"没抓到" → 完整度永不满足 →
每次读主档都重登 DMS → **又变成互踢**。这正是当初延后的原因，现在两处一起改才对。

顺带两处一致性：

- `cache_usable` 改成要求**全部** `_COMPLETE_KEYS` 在位（与线上一致）。缺任何一类就当
  缓存不可用，否则"残缺主档"会被当成"已经取全"，跟完整度判据来回打架。
- `refresh_from_client`（订车成功后就地刷新）原来只保留 `company_banks`，会把另外四类
  银行键**从缓存里抹掉** → 判据立刻判不可用 → 下次又重登。改成保留 `_BANK_KEYS` 全部。

## 四、验证（33/33，新增 4 条）

```bash
scripts/sample-verify-dms-intake.sh dms    # 合计: 通过 33 / 失败 0
```

| 用例 | 锁住的规则 |
|---|---|
| `run_logged_in` 有 `authoritative_read` 开关 | 别把这个入口删掉/改名 |
| 五类银行目录都在完整度判据里 | 少一类就报出来 |
| 缓存齐全且在有效期内 → **不重登** DMS | 别为了"保险"每次都重登（会互踢） |
| 缓存缺一类银行目录 → 判不可用并重取 | 不把残缺当齐全 |

（只读会话"异常退出也要换回原 transport""同一个读取块只解析一次管理员登录"两条，
已在 `sample-verify-dms-booking-foundation.sh` 里钉过。）

全套 **277** 项：cards 34、advisor 25、intake 33、draft 21、client 77、
booking-foundation 45、booking-readback 42。

## 五、下一批

1. `booking_attempt`（线上：原子登记提交意图 + 草稿归属核验，防并发重复）——
   `create_booking_via_form` 的 `on_attempt` 现在还没有人传。
2. 订车逐问（线上 9 个文件约 2800 行）：`booking_qa*` / `qa_cards` /
   `qa_payment_cards` / `booking_payments`（LINE 文本 → 原生槽位的那一层）/ `text_fields`。
3. 查询流、菜单层 + Rich Menu + 登录票据、字段修正（浏览器编辑页）、改写审批。
