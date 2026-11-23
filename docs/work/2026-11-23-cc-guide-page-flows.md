# CC 引导真页面流程（11-23）

目标：给声明完整的引导补「真页面点卡，从题到结果」的流程，登记到 `scripts/impact/cc-coverage.toml` 的 `link.page_flows`，让格子从 ◐ 升 ✅。

## 第 1 批（本分支）

| 流程 | 格 | 页面上点了什么 | 库里核了什么 |
|---|---|---|---|
| `cowork.cc.guide.purchase` | 采购 · Cowork 直写 · 本地 Express | 题卡（付款方式）→ 复核卡（一张、合计 107）→「立即录入」→ 结果卡已交出 | 运行 done、同意 `guide:<运行>:<版本>`、推送目标与推送记录都在本对话账套 T.8 |
| `erp.cc.guide.operator-purchase` | 采购 · ERP 代录 · 本地 Express | 代录对话题卡（付款方式）→ 复核 → 确定 → 结果卡交进代录 | 运行 done、一份写入计划且同意相同、票进代录任务一次 |
| `erp.cc.guide.operator-sales` | 销售 · ERP 代录 · 本地 Express | 同上（销售） | 同上 |

- 实验栈 `pearnly-pageflow-lab`，电脑 / 手机两格全过（`run.py --flow …`）。
- 限制：实验栈没有识别进程，「交文件 → 识别」那段不在流程里；单据用 documents 源的公开入口 `run.start(doc_ids=…, at="asking")` 交进来（同 `erp.cc.guide.documents-edit`）。没有真 CC 桌面版来领，结果停在「已交出 / 进行中」。
- `--counts`（变基到 main 4ceef0788 后，引导车 3–6 已在）：前 ✅1 ◐30 ○10 ✗4 → 后 ✅4 ◐27 ○10 ✗4（等 MR.ERP 10、等云端 24、等公司接入 1、等 ERP 直写采购 / 销售 1 不变）。

## 查出、没修（已报主会话，等定）

1. **没有复核步骤的表单引导页面走不到结果**（挡住回读、对账、增值税升 ✅）：readback / recon / vat 声明都没有 `review`。服务端答完最后一题停在 `status=asking`、`card.questions` 空、`card.ready=true`（实验栈实测回读）；前端 `guide-run-card.tsx` 的 `Asking` 没有题时只画「已定 n 项」，确定钮只在复核卡（`guide-review.tsx` `decideButtons`）上。`cowork.cc.guide.recon-follow` 是直接调 `guide_confirm` 接口绕过的。
2. **Cowork 销售：写账判定与推送对付款方式不一致**（挡住 Cowork 直写销售）：销售票在账套里没记付款方式时，`write_precheck.api.card` / `documents.card_for` 判「可写」、题卡不出付款；复核后「立即录入」，推送记 `choice_needed:payment`，结果「已跳过」。采购同样情形会问付款。

修好后：Cowork 销售流程（`cowork_cc_guide_docs_lib.py` 已按业务参数化）与 `pearnly/cc-guided-flow-markers` 里的读回、对账、增值税、POS、预扣税流程（旧卡片标记，要按现在的标记改写）再补。
