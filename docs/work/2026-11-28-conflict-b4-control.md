# ④ B8 控制面 LINE 解绑 / 改绑删了别的租户刚挂上的专用身份（11-28）

分支 pearnly/conflict-b4-control（起点 51686e58b），工作树 `.claude/worktrees/b4-control`。只做 ④ 清单 B8：
`apps/pearnly_control/pearnly_control/account/member_service.py` 的 `unlink_line`、`link_line`、`_person_lock`、`_release`，入口
`member_api.execute`。与 ④ 主记录 `2026-11-28-conflict-silent-loss.md` 分开写，避免两个分支改同一份文件。

## 现象

同一个 LINE 可以绑在几个租户的账号上，身份服务里每个池只有一个「LINE 专用身份」，最后一个站点解绑时才删。
`unlink_line` 先普通读本站关联（这一笔事务的读视图在这里或更早就建了）→ 删 → 逐个拿 `GET_LOCK` → `_release` 用
`frappe.db.exists`（普通读）判「池里还有没有站点绑着这个 LINE」。另一个租户在这边拿到命名锁之前刚 `link_line` 同一个 LINE
并提交：这边的读视图看不到那一行，判成「没人绑了」，把对方刚挂上的专用身份删掉，对方 LINE 登录失效（关了快照隔离，不报 1020）。
`link_line` 改绑到别的 LINE 时释放旧 LINE 走同一个 `_release`，同样会删；它的旧绑定 `previous` 也是拿锁之前普通读。

## 先核实的几点

- **`GET_LOCK` 提交不放**：MariaDB 命名锁是会话级的，提交 / 回滚都不放，只有 `RELEASE_LOCK` / `RELEASE_ALL_LOCKS` / 会话结束才放。
  原文档串「连接结束或提交后释放」写错了，已改。账号入口 `member_api.execute` 成功路径「提交 → `_release_locks`」，
  `MemberError` / 其它异常（含提交本身抛错）都落到「回滚 → `_release_locks`」，回滚抛错也接住后照样放；只有非 `Exception`
  （进程被杀之类）走不到，那时连接断开、锁随会话放。管理通道（`billing_control` 的换池、注销）不调 `release_all_locks`，靠请求结束关连接放（没在 Frappe 源码上核实请求结束必关连接，本机没有 Frappe 源码）。
- **锁放在提交之后**：所以等这把锁的另一路拿到锁时，这边的库内变化已经提交；它只要用加锁读就看得到。普通读看不到——这就是本条的根因。
- **清单的「锁 → 库内删 → 提交 → 外部摘 / 删」不采用**：核了站点对账。`line_login.sync_user` 失败时 `login_link` 留 pending /
  原值，每小时 `reconcile` 按 `_needs_sync` 重发 `unlink-line`（`member_client._post` 当场也会对 `unavailable` 再发一次）。
  重发时控制面按**关联行**决定删哪个 LINE 的专用身份。若先提交删掉关联行、外部删身份再失败，重发时行已经没了，
  只会再摘一次租户账号上的旧关联，专用身份永远留着（留着的身份站点不认人，不是越权，但对不上账、也清不掉）。
  改绑同理：行已改成新 LINE，重发时 `previous` 等于新 LINE，旧 LINE 的身份不再释放。
  所以保留本文件 `purge_members` 已有的口径：**库内变化与外部动作同一笔，外部动作幂等、在提交之前做；失败整笔回滚，关联行还在，重发照行补做**。
  入口没有 `conflicts.retried` 之类的进程内整笔重来，外部动作不会因 1213 被重放；站点重发是新请求、新事务、重新判。
  外部动作幂等已核：`release_line_person` 找不到身份算完成、删 404 算完成；`unlink_line` 404 算完成；`ensure_line_person` 已有就沿用、撞名按归属证明认领。

## 改法

1. `_release` 的判断换成 `_still_bound`：`select name … where realm / alias / line_user_id … limit 1 for update`（加锁读，读最新已提交）。
   调用方都已拿着这个 LINE 的命名锁，所有会给这个 LINE 插关联行的路（只有 `link_line`）也拿同一把锁到提交之后，所以这里读到的就是定论。
   `move_members`、`purge_members` 也走 `_release`，一并变成加锁读。
2. 命名锁先于行锁、按固定顺序拿齐（新增 `_person_locks`）：
   - `unlink_line`：普通读本站关联只用来知道锁哪几个 LINE → 拿齐命名锁 → `select … for update` 加锁重读本站关联为准（多出来的补拿）→ 库内删 → 身份服务摘租户账号旧关联、逐个 `_release` → 入口提交 → 放锁。
   - `link_line`：普通读旧绑定只当提示 → 新 LINE 和旧 LINE 的命名锁一起按顺序拿 → `get_value(..., for_update=True)` 读旧绑定为准（清单 ③）→ 其余照旧；改绑时释放旧 LINE 前的 `_person_lock` 保留（与提示一致时可重入，不一致时补拿）。
   - 不这样排的后果：A 租户改绑离开 L（锁着自己那行、等 L 的命名锁）× C 租户解绑 L（拿着 L 的命名锁、加锁读扫到 A 那行要等）——数据库看不出这种互等，只能等到 20 秒命名锁超时，A 回「请稍后重试」。
3. `Pearnly LINE Login Link.line_user_id` 加索引（DocType JSON `search_index`，改 `modified`，原生迁移建）。原来只有 `realm` 上有索引（两个池两个值），`for update` 会按池锁住几乎整张表的行和间隙，任何两个租户的 LINE 绑定都要互等；加索引后只锁同一个 LINE 的行。
4. 文档串据实改：`_person_lock`（会话级、提交不放、谁来放）、`_release`（为什么外部删在提交之前）、`unlink_line`（顺序与重发）。

## 基线与检查

- `scripts/unlocked-overwrite-baseline.tsv` 删 `member_service.py link_line … 判断` 一行。`check-unlocked-overwrite.py --extended`：新增 0（14 处已标注；三类 287 处，基线 271、误报 16）。
- `check-architecture.py` PASS（0）；`check-unbounded-queries.py` 通过；`check-copy.py`：新增 / 改动 / 删除文案 0、拦截 0。
- 没改共用件代码、没有新调用方，`shared-parts.toml` 不用登记。

## 离线单测（生产镜像一次性容器，`--network none`，只挂本树 pearnly_core / pearnly_control）

`pearnly_control.account.test_member_service`：48 条全过（原 44 条 + 新增 4 条）。替身 `TestLineLinks` 加了读视图（另一连接刚提交的行普通读看不到、加锁读看得到，提交 / 回滚后看得到）、提交 / 回滚、调用顺序记录；`FakeDb` 加 `sql`（命名锁与两句加锁读）。

| 新增单测 | 修前（51686e58b 的 member_service） | 修后 |
|---|---|---|
| `test_unlink_keeps_the_person_another_tenant_just_bound`：拿命名锁那一刻另一个租户刚绑上同一个 LINE 并提交 → 解绑 | 红：删了专用身份 | 绿：不删，对方关联在 |
| `test_rebind_keeps_the_old_person_another_tenant_just_bound`：同上，本租户改绑到另一个 LINE；旧绑定在拿锁之后加锁读 | 红：删了旧 LINE 的专用身份 | 绿 |
| `test_unlink_order_locks_before_rows_and_external_before_commit`：命名锁 → 加锁读本站关联 → 删 → 摘关联 → 加锁读判 → 删身份 → 提交 → 放锁 | 红（顺序不同） | 绿 |
| `test_keycloak_failure_rolls_back_and_the_retry_finishes`：删身份失败 → 回「unavailable」、回滚后才放锁、关联行还在；再发一次 → 删掉身份、关联清空 | 绿（原来也是失败整笔回滚） | 绿（守住新顺序下仍可对账） |

另过：`pearnly_control.account.test_repool`、`test_identity_pools.TestPools`、`pearnly_core.account.test_line_login`（共 23 条）；`tests/test_teardown_members.py`（3 条）、`tests/test_member_identity_products.py`、`tests/test_companion_contract.py`。
`tests/test_release_member_identity.py` 只在一次性实验栈里跑（`PEARNLY_RELEASE_LAB=1`），没跑。

## 共用件影响清单（`check-impact.py --files <本次改动>`）

`member_service.py` 在两件共用件的文件表里，命中 2 个入口：

| 入口 | 结果 | 依据 / 没测到的原因 |
|---|---|---|
| control.issue-and-entry（控制面开户、repool、智能体入口） | 没测到 | 控制面没有本机实验栈；本次没改建号 / 改密 / 开户代码；repool 走到的 `move_members` 只有 `_release` 的判断换成加锁读，离线 `TestMoveMembers` 6 条与 test_repool 过 |
| shared.team.members（团队页建成员同步身份服务） | 没测到 | 同上；`create` / `reset_password` 没改，`TestMembers` / `TestMemberPools` 过 |

## 没做 / 没测到

- 两连接实验格没做：控制面没有本机实验栈（按任务书不用实验栈）。真实 MariaDB 上的锁行为（`for update` 走 `line_user_id` 索引只锁同一个 LINE、命名锁可重入）按 MariaDB 文档写，没在库上跑。
- `link_line` 先外部 `ensure_line_person` 再写行，提交失败时身份服务里多一个挂着 LINE 的专用身份、库里没行；站点重发会补行。站点若在那之后撤掉了绑定，这个身份不会被删（与本条之前一样，没改）。
- 管理通道 `purge_members` / `move_members` 仍是「先删行、再拿命名锁」的老顺序（判断已是加锁读，结果对；最坏是 20 秒超时），不在 B8 范围。
- 真机（LINE 登录三产品）没测，要排队。
