# DMS 白天同步认错客户 + 读档账号看不全客户（2026-10-06）

用户：「修吧，修好直接部署，然后实测……这次两个问题再次一起验证」「都修吧，读档账号我去换成 dmstest003」。
分支 `pearnly/dms-live-sync-customer-code`，基线 `origin/main` a3306049。

## 现象

testdms 开着白天每分钟同步（读档账号 dmstest）。17:58 在测试 DMS 新建客户 134（代码 9112），18:58 用户 LINE 实测时
缓存里还没有它 →先出「新客户」卡，建档前实时查重才发现已存在 → 差异卡。

## 证据

- DMS 使用记录（客户模块）：`982 | dmstest | 17:58:51 | เพิ่ม | 9112`、`983 | dmstest001 | 19:02:21 | แก้ไข | 9112`、
  `981 | แก้ไข | 111`。**被改记录标识是客户代码**，不是身份证号；2026-09-24 协议记录里"末列＝身份证号"是因为那位客户的代码就是证号。
- 同步状态：`ลูกค้า` 模块 `dirty_since` 有值 —— 点名的 9112 被当证号反查，必然失败，整组标成待夜里重读。
- **DMS 按账号权限过滤客户列表**，同一时刻：dmstest001/002（改写）44 位，dmstest（读档 + 连接管理员）39 位，sale02 16 位。
  客户 134、115 最后由 dmstest001 保存后，dmstest 的列表里就没有它们（按身份证号、按代码都搜不到；直读表单可以）。
  建档前查重走改写账号，所以 18:58 那次能查到。

## 改动

| 行为 | 入口 |
| --- | --- |
| 同步按**客户代码**认记录：缓存 `customer_codes`（代码 → 客户号，唯一才算）找不到就 `search_customer_by_code` 在原生列表里按代码精确筛、读表单核对代码；删除同样按代码 | `adapter/change_sync._apply_customers`、`snapshots/store.customer_codes`、`client/snapshot_reader.search_customer_by_code`（替换按证号反查） |
| 读档账号搜不到点名的客户 → 照旧整组重读，并留日志写明代码和"读档账号可能看不全" | 同上 |
| 登记读档账号时对照客户总数：读档账号比一个改写账号（没有就连接管理员）少 → 拒绝登记，提示两边各几位（新文案 `dms.connect.read.scope`，四语）；对照数不出来不拦 | `connections/api._read_scope_gap`、`adapter/account_probe.customer_count` |

删除：`store.customer_index`、`search_customer_by_identity`（只给同步用，按证号的前提不成立）。

## 验证

- `tests/test_dms_change_sync.py` 改为按客户代码点名（22 条，含新增：缓存里没有的新客户按代码补进；读档账号看不到 → 整组重读 + 日志）。
- 新增 `tests/test_dms_read_scope.py`（4 条：少看到 → 拒绝并给两边数字；看全 → 通过；无对照账号、数不出来 → 不拦），已登记进 `check-dms-offline.py`。
