# Express 远程操作与账套比对工具（开发 / 验收用，不进产品代码）

2026-10-25 验收窗口在测试机上远程操作服务器版 Express 录单时用的工具。
- 操作手册见 `docs/reference/express-operator-playbook.md`（截图在同名目录）；
- Express 原生写法见 `docs/work/2026-10-25-express-native-rules.md`。

**不含任何凭据**：
- Express 登录由人本人输密码；
- 脚本只借已登录用户（测试机上是 skin3）的交互会话跑，计划任务不存密码。

## 组成
| 文件 | 跑在哪 | 作用 |
|---|---|---|
| `xprun.sh` | 本机 | 把动作清单推到测试机，以会话用户执行 `express-ui.ps1`，打印日志、拉回截图到 `./xp/` |
| `run-as-session-user.ps1` | 测试机 | 用计划任务以已登录用户（Interactive、Limited）跑一次程序，等结束后删任务。**任务名每个使用方各用各的** |
| `express-ui.ps1` | 测试机（会话用户） | 按 `xp-actions.json` 操作 Express；每次按键 / 点击前核前台是 ExpressI，不是就中止 |
| `book-snapshot.ps1` | 测试机（会话用户） | 只读把整本账套拷到 `C:\PearnlyStress\_book_record\<Tag>`（共享读、不加锁） |
| `dbf_rawdiff.py` | 测试机 | 两份账套副本按 DBF 原始字节逐表比对（新增行 / 改动字段 / 删除标记），输出 JSON |
| `dbf_docrows.py` | 测试机 | 列某些单号在 STTRN / STCRD / GLJNL / GLJNLIT / ARTRN / APTRN / ISVAT 的行 + ISRUN 计数器 |
| `dbf_stock_balance.py` / `dbf_stock_history.py` | 测试机 | 某品的 STMAS / STLOC 结存；某品某日起的 STCRD 流水（含删除标记） |
| `dbf_isinfo.py` / `dbf_item_negallow.py` / `dbf_isprd.py` | 测试机 | 账套负库存设置；商品级 NEGALLOW 分布；会计期间 |
| `dbf_zero_cost_sales.py` | 测试机 | 列成本为 0 的销售出库行 |
| `express_set_isinfo.py` | 测试机（会话用户） | **会写账套**：改 ISINFO 字段，只用于获准的测试账套、且 Express 没开时；首选在 Express 设置画面里改 |
| `examples/*.json` | — | 动作清单样例（选公司行、赊销录一行、改库存设置、剪贴板贴泰文备注） |
| `pilot/` | 本机 + 测试机 | 10-26「CC 代录 Express」试点执行器（固定流程 + AI 兜底 + 读回核对 + 记账），见 `pilot/README.md` |

## 动作清单格式
```json
[{"do":"activate"},{"do":"wait","ms":400},
 {"do":"click","x":209,"y":189},{"do":"keys","text":"5001"},{"do":"keys","text":"{ENTER}"},
 {"do":"paste","text":"ทดสอบ"},{"do":"shot","name":"step1"},{"do":"windows"}]
```
- `keys`：.NET SendKeys 写法（`{ENTER}`、`{TAB}`、`{ESC}`、`%2` = Alt+2、`+{END}` = Shift+End、`{F7}`）。
- `paste`：泰文等非 ASCII，走剪贴板 + Shift+Insert。
- `launch`：只放行白名单里的 Express 程序（脚本里写死），别的拒绝。

## 规矩
- 只操作获准的测试账套。改的设置测完改回，并拷副本核对。
- 每批动作只做一小段，看截图再继续；看到 -70 先别点，查网络。
- 远程操作期间，在占用表登记；别人要动网络 / DNS / CC / 实验栈先通知。
- 截图可能含公司 / 客户名，收进仓库前只留对话框部分或打码。
