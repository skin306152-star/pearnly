# 订车地基按线上补齐：权威只读闸 / 组织归属 / 订车载荷

日期：2026-09-18。承接 [27-customer-geo-parity.md](27-customer-geo-parity.md)。
线上基准 `501589f0`（开工前已核对未变）。第 3 批的第二件，也是订车链的前置。

## 一、为什么先搬这四件

上一批查出 `client_ops` 的订车段不能单独换 —— 它要用 `booking_payload / org /
submit / readback`，而这四件**新平台一件都没有**。线上这四个文件合计约 1100 行。
所以顺序是：**先搬订车件 → 再改 `client_ops` 订车段 → 最后才动 `dms_docno`**
（`dms_docno` 现在被旧订车路径用着，直接换成线上的 autonum 状态机会把活路径打断）。

本批先把**不依赖 readback/submit** 的四件搬完。

## 二、这一批搬了什么

| 新平台 | 线上 | 干什么 |
|---|---|---|
| `dms_admin_read.py`（**新增**） | `services/erp/dms_admin_read.py` | **权威只读闸**：管理员会话只许走路径白名单，写路径与附件一律拒 |
| `dms_booking_org.py`（**新增**） | `mrerp_dms_booking_org.py` | 按**顾问 id** 解原生组织（订车/销售两侧 + 四级主管，共 24 个表单字段） |
| `dms_booking_payload.py`（**新增**） | `mrerp_dms_booking_payload.py` | 主档行 → 载荷（纯函数，零 IO）；pinned id 不许回落 |
| `dms_booking_customer.py`（返工） | `mrerp_dms_booking_customer.py` | 按客户号**直读** + 身份归一比对 |

顺带补齐两个前置（新平台缺的）：

- `dms_master_rows.py`：补 `row_by_id` / `ref_from_row`（线上就在这个模块里）；
  `dms_client_ops.py` 改为 import 它，不再自己定义一份。
- `dms_models.py`：`DMSBookingPayload` 补 `organization_fields`。

## 三、三处**不是照抄、但必须知道**的地方

### 1. 权威只读闸是"机械约束"，不是自觉

管理员凭据在 DMS 侧权限更高，**一次误写不可回滚**。所以线上把"只读"做成
`_ReadOnlyTransport`：路径白名单（`component/` 与 `/form.php`）之外一律抛
`ERR_DMS_TECHNICAL`；**带附件的 POST 无条件拒**（挂附件是写）。这条在本批的验收里
逐条钉了（`cus/new.php`、`cus/edit.php`、`drfcbc/new.php`、`drfcbc/edit.php`、附件）。

### 2. `booking_customer` 那版是更旧的实现

新平台原来是"按证号**搜索**客户 → 比客户号"，线上是"按客户号**直读** + 归一后比证号"。
差别是实的：

| | 旧实现（新平台原来） | 线上（已改成） |
|---|---|---|
| 取数 | `lookup_customer`（搜索） | `read_customer`（直读，不受"新客隐身期"影响） |
| 身份比对 | 比客户号字符串 | `normalize_thai_id` 归一后比 13 位证号 |
| 身份矛盾 | 继续往下走 | **硬冲突，就此停手**——不再去问别的账号，否则别人的客户资料会漏出来 |
| 销售看不到客户档 | 直接失败 | 只读借管理员；借不到就明确失败关闭 |

改完与线上**逐行等价**（91 行 → 91 行，只差 `DmsClientError` 这个类名约定）。

### 3. 命名约定差异（不是漂移，别再当 bug 查）

新平台统一用 `Dms*`（`DmsClientError`、`DmsClientOpsMixin`），线上是 `DMS*`。
本批的 `dms_master_rows` / `dms_models` 两个小文件反复比对后确认**只有这一处差异**，
没有行为差异，所以没动。

## 四、验证（45/45）

新增 `scripts/sample-verify-dms-booking-foundation.py`（+ `.sh`）：

```bash
scripts/sample-verify-dms-booking-foundation.sh dms   # 合计: 通过 45 / 失败 0
```

其中几条是"这条错了就是建错单"的：

| 用例 | 锁住的规则 |
|---|---|
| 只读闸拒绝 `cus/new.php` / `cus/edit.php` / `drfcbc/new.php` / `drfcbc/edit.php` | 管理员会话绝不落写 |
| 只读闸拒绝**带附件**的任何 POST | 挂附件是写 |
| 没配管理员 → `require_admin` 直接拦 | 不许静默降级到销售的不完整视图 |
| 同一个读取块只解析一次管理员登录 | 别把 DMS 单会话踢下线 |
| 只读会话**异常退出**也要换回原 transport | 别让 client 停在管理员态 |
| 坏组织（格数不足 / 非 JSON / 分店名缺 / 组名缺）被拦 | 组织不完整不成草稿 |
| 组织是按顾问 id 问的（`idusers=7`） | **分店列表首行不是这个顾问的分店** |
| 钉死的顾问 id 不在名册 → `ADVISOR_UNMATCHED` | 绝不回落首行（提成归属） |
| 第 2 页的 pinned id 也算数 | 只用第 1 页会悄悄回落 |
| 没有 pin、code 多命中 → 拦 | 不猜 |

回归：`cards 34/34`、`advisor 25/25`、`intake 24/24`、`draft 21/21`、`client 45/45`。

**没跑的**：`sample-verify-dms-booking-real.sh` 需要站点参数，且会往真 DMS **写单** ——
对外部系统的写入要用户明确授权，本批没有跑。

## 五、下一批要动的（已探明，含新发现）

| 文件 | 线上 / 新平台 | 关键缺口 |
|---|---|---|
| `dms_payments.py` | 158 / 122 | **`validate_payment_completeness` 整个没有** —— 收款不完整时线上在本地就拦（抛 `ERR_DMS_PAYMENT_INCOMPLETE`），新平台放行到 DMS |
| 同上 | | `src_time: txttimetffrom` 是**过期残留**：线上在 `08fb411c` 删掉了（"dropping optional field"，DMS 不要求转账时间） |
| `dms_company_banks.py` | 278 / 130 | 按原生支付渠道补齐银行目录及必填字段 |
| `dms_booking_readback.py` | 358 / — | 三层判据（身份 → 关键 → 展示镜像）+ 定因阶段；线上 `2026-09-12` 生产事故的直接产物 |
| `dms_booking_submit.py` | 344 / — | 只许"明确的重复号拒绝"触发重写；结果不明**绝不重发**（抛 `OUTCOME_UNKNOWN` 保草稿） |
| `dms_client_ops.py` | 470 / 505 | 订车段改用上面的四件；`txtuserstel` 缺了要抛 `ERR_DMS_ADVISOR_UNMATCHED`，新平台现在填 `"."` |
| `dms_docno.py` | 261 / 92 | 严格 autonum 状态机（协议不符就抛）+ 隐藏字段 + 未占号扫描 |

⚠️ **加 `validate_payment_completeness` 之前要先看 LINE 支付层**：新平台的
`dms_qa_util.CHANNEL_EXTRA_SHAPE` 给 cheque/cashier_cheque/card 用的是单个
`"ref"` 槽位，线上用的是 `cheque_no` / `card_type` 等原生槽位名 ——
校验一上去，用 `ref` 的路径会立刻被判"缺字段"。这两处要**同一批**对齐
（属第 5 批：订车逐问）。
