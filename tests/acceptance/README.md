# 验收登记

规范见 [验收测试标准](../../docs/development/acceptance-testing.md)。入口只在产品地图（`scripts/impact/product-map/`）定义，这里全部用地图行 id。

| 文件 | 作用 |
| --- | --- |
| `rows.toml` | 地图行的验收属性（`effect`、`upload`、`card`、`shared_doc`、`line`、`na`）与两个开关（`[policy]`）。第四期并回地图行本身 |
| `registry.toml` | L3 真环境场景。L2 场景就是流程用例（`tests/flows/registry.toml`，可选 `class`、`misuse`、`chain`） |
| `cc_talk.toml` | CC 对话的乱用种子库：十类「用户怎么乱」与八条底线；场景用 `talk = [代号]` 引用 |
| `chains.toml` | 业务链：一条链经过哪些地图行 |
| `evidence.toml` | 每条 L3 场景最近一次通过，`scripts/acceptance/run.py --record` 写，不手改 |
| `owed_baseline.txt` | 存量欠的场景（`行 id|欠项`），只许减少 |

检查 `python3 scripts/check-acceptance.py`（CI 跑）；总览 `--report`；跑场景 `python3 scripts/acceptance/run.py --scenario <id> --dry-run`。
占用锁 `scripts/acceptance/locks.py`；验收账套快照 / 还原 / 对比 `scripts/acceptance/ledger.py`。
