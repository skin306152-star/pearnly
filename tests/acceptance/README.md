# 验收登记

规范见 [验收测试标准](../../docs/development/acceptance-testing.md)。入口只在产品地图（`scripts/impact/product-map/`）定义，这里全部用地图行 id。

| 文件 | 作用 |
| --- | --- |
| `rows.toml` | 地图行的验收属性（`effect`、`upload`、`card`、`shared_doc`、`line`、`na`）与两个开关（`[policy]`）。第四期并回地图行本身 |
| `unmapped.toml` | 还没进产品地图的高风险入口（写外部账本 / 动钱 / 删数据 / 对外发消息），过渡登记：当作地图行算欠账，第四期搬进产品地图后删 |
| `registry.toml` | L3 真环境场景。L2 场景就是流程用例（`tests/flows/registry.toml`，可选 `class`、`misuse`、`chain`） |
| `cc_talk.toml` | CC 对话的乱用种子库：十类「用户怎么乱」与八条底线；场景用 `talk = [代号]` 引用 |
| `cc_talk_cases.toml` | 乱用的参数化用例骨架（动作序列 + 硬断言 + 说法类断言）；对话驱动接好后逐格登记成场景 |
| `inventory.toml` | 11-12 全产品入口盘点快照，只给第一版一屏总览用；第四期入口全部进产品地图后删除 |
| `chains.toml` | 业务链：一条链经过哪些地图行 |
| `evidence.toml` | 每条 L3 场景最近一次通过，`scripts/acceptance/run.py --record` 写，不手改 |
| `owed_baseline.txt` | 存量欠的场景（`行 id|欠项`），只许减少 |

检查 `python3 scripts/check-acceptance.py`（CI 跑）；总览 `--report`；跑场景 `python3 scripts/acceptance/run.py --scenario <id> --dry-run`。
占用锁 `scripts/acceptance/locks.py`；验收账套快照 / 还原 / 对比 `scripts/acceptance/ledger.py`。
