# LINE 菜单同步：错误日志不再带频道令牌，假用户 ID 不再每 10 分钟报错（2026-10-03）

用户：「把 2、3 做一下，令牌不用换」（2 = testdms 菜单同步反复报「userId 无效」，3 = 错误日志里存了 LINE 频道令牌）。
分支 `codex/line-menu-token`，基线 `origin/main` 7a495884。

## 现象（生产只读核对）

- 带 `Bearer ` 的 Error Log：testdms 120 条（9-27 起）、**ris 10 条**（9-22 至 9-29），其余四站 0；全部来自 `pearnly_dms.menu.rich_menu.sync`。
  ris 与 testdms 早期的是 `api.line.me` 解析失败 / 读超时；testdms 10-03 起 115 条是 `Udeploy9eba…`（5240ce8b 部署回读时
  Administrator 建的临时绑定，没删）每 10 分钟（`schedule_menu_reconciliation`）同步一次被 LINE 回 400「userId 无效」。
- 原因：Frappe 记错误时用 `traceback_with_variables` 打印每层变量，只按变量名 / 字典键名含 password、token、key 等词打码。
  `token` 变量被打码，但 `headers = {'Authorization': 'Bearer …'}` 的键名不在名单里；网络错误时 requests 内部的
  `api.py:kwargs`、`sessions.py:headers` 两层也带着请求头。

## 改动

- `pearnly_core.line.request`：调 LINE 的统一出口。带令牌的请求头不放进任何变量；requests 异常换成同类、`from None` 的新异常
  （类型与原因文字不变，原异常链连同 requests 内部各层不进错误日志；该库也不打印异常链）。`headers` 只放额外头。
- DMS `menu.rich_menu`、Cowork `line.menu`、ERP `integrations.line_menu` 三份同样写法的 `_request` / `_has_image` 改走它
  （Cowork / ERP 目前没有泄露记录，但一出网络错误就会同样泄露）。
- DMS 菜单同步：LINE 对 `user/…` 回 400 且原因写明 `'userId'` 时抛 `UserRejected`，同步只记一条普通日志并把这个 ID 记成
  `rejected`（缓存一天），一天内跳过；其他 400（例如菜单 ID 不对）照样报错。
- 未改：`operators.line_account.public_account`（自己捕获网络错误再 `frappe.throw`，最后一层只有被打码的 `token`）；
  `pearnly_core.line` 其余发送函数都在本层捕获异常、只记文字。令牌不换（用户定）。

## 验证

- `scripts/test-dms-line-runtime.py` 46 条（新增 3 条：共用出口网络错误保留类型且错误记录无令牌、菜单 500 / 超时 / 查图
  都无令牌且上传仍带图片类型、userId 无效暂停一天不重试而其他 400 照报）；新测试用 Frappe 自己的 `get_traceback(with_context=True)`
  生成错误记录。同一测试跑线上旧代码失败，错误记录里出现 `headers = {'Authorization': 'Bearer …'}`，与生产一致。
- `tests/test_dms_entrypoints.py` 4 条（菜单发布替身改为替换 `requests.request`，查图也走它）。
- `check-architecture.py` PASS；`check-dms-offline.py`（Python 3.14）20 项 PASS；生产镜像 6d04fe4a 挂载本分支跑完整
  `ci-candidate-checks.sh offline`：54 项全过（exit 0），其中 `test-dms-line-runtime.py` 46 条。

## 部署后

已上线并清理，见 [上线记录](2026-10-03-release-1d690052.md)：两站带令牌的 Error Log 删后为 0，临时绑定已删，删时触发的同步没有再写错误日志。
