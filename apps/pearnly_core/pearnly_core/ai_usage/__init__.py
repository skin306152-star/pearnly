"""Anthropic 用量与费用账本（2026-11-11 主会话审定，`docs/work/2026-11-11-cc-usage-cost-评估.md`）。

站点里每一笔调 Anthropic 的花费都记进本站的 `Pearnly AI Usage`，按用途分：CC 对话（`cc_chat`）、侧栏标题概括（`cc_title`）、
代录 AI 接手（`operator`）、Claude 识别（`ocr`）。评测 / 回放直接打甲骨文、不经过站点，不进这本账，所以
「Console 总额 − 全部租户账本合计」就是评测与测试脚本的花费。

- `prices`：价目表（纯数据，Decimal）。
- `contracts`：用量的规整与「当天增量」的算法（纯函数，不依赖 Frappe）。
- `ledger`：写账（只插入 / 覆盖本站的行，出错只记日志、不打断业务）。
- `report`：按日 / 用途 / 产品 / 用户 / 模型汇总（`bench execute`，没有页面）。
"""
