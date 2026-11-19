# 引导卡页面接口约定（11-19，服务端 Cowork 引导第二线 ↔ 前端 A2 local_1f7ac454）

服务端分支 `pearnly/cc-guide-page-endpoints`（叠在 `pearnly/cc-guided-wire` 8d74ec0b6 上）；前端照 A2 分支 `pearnly/cc-guided-ui-review`
的 `frontend/assistant/src/lib/guide-run.ts`。**字段名以这份为准，两边改动都先改这里。** 每个写接口都回整个 view（同 `guide_view`），
不收的回 `outcome: "refused"` 与 `refused: {键: {code, text}}`（text 是本页语言的一句；没有译文为空串）。版本不对回 `outcome: "changed"`。

| # | 页面调用 | 服务端 | 形状 |
|---|---|---|---|
| 1 | 题面 `zero` | `run._question` | 声明 `"zero": True` 的金额多选题带 `zero: true`；没有不带 |
| 2 | 题面 `total` | `run._split_total` | 分摊题（input `split`）带 `total`：它前面最近一道金额多选题此刻答的各行之和，两位小数字符串；那一道没答不带 |
| 3 | `guide_search(run_name, key, txt, start, page_length)` GET | `run.search` | 任何选项步骤（choice / multi / amounts / split）都给，不只声明了 search 的；不带 txt 给第一页 |
| 4 | `guide_reopen(run_name, step, version)` POST | `run.reopen` | 表单业务。那一步要此刻适用且有值（答过的或后台定的）；回 asking、版本 +1，那一题带 `value` = 原值（不放 preselected），依赖它的后面各步作废；后台定的那一步改的时候照常问。没值 / 不是这次引导的步骤 → refused（码 `not offered`）。按单据的业务不走这里 |
| 5 | `guide_edit(run_name, doc_id, field, value, version)` POST | `run.edit` | 按单据：票面一格（`doc_id` = 单据号，产品写回）。表单：复核表一行的一格（`doc_id` = 行码），声明点名 `edit(ctx, draft, row, field, value)`，只在复核中；回新草稿则复核卡照新草稿出、版本 +1；不收回 `refused: {格: {code, text}}`，产品给了 `refused_text: {格: 一句}` 原样带上 |
| 6 | `guide_follow(run_name, answers, version)` POST | `run.follow` | 结果卡上的追问（N1b）；answers 是 JSON 字符串 `{子题 key: 值}` |
| 7 | `guide_confirm(run_name, version, seen?)` POST | `run.confirm` | 按单据的业务带复核卡上的 `review.seen`；与此刻不一致（票在别处改过）不办，回 changed 与刷新后的复核卡。表单业务不带 |

另：`card.questions[].query`（打字开卡预填搜索框）在收款开卡分支 `pearnly/cc-collection-typed-guide`，见 `docs/work/2026-11-19-cc-typed-collection-opens-card.md`。

## 存储
`Pearnly Assistant Guide Run` 加只读字段 `reopened`（Long Text，`{步骤: 原值}`），原生迁移。

## 测试
`tests/test_cc_guide_run.py`：PageFieldTests、ReopenTests、SearchAnyOptionStepTests、FormEditTests；`tests/test_cc_guide_registry.py`：表单可写 edit 但要有 review；
`tests/test_cc_guide_run_card.py`：PageEndpointTests（每个页面调用都有接口、方法、参数名、交给引擎同名函数）。除 SearchAnyOptionStepTests（wire 上已支持，守住）外都先红后绿。
