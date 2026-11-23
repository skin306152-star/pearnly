# 没有复核的表单引导答完出确定卡（11-23）

## 评估（按需求影响评估模板，精简）

- 现象：readback / recon / vat 这类不写账的表单引导没有 `review`（声明闸门：写账的表单必须有复核，不写账的可以没有）。服务端答完最后一题停在
  `status=asking`、`card.questions` 空、`card.ready=true`（实验栈实测回读）；前端 `guide-run-card.tsx` 的 `Asking` 没有题时只画「已定 n 项」，
  确定钮只在复核卡上 → 页面点不到「确定」，走不到结果。`cowork.cc.guide.recon-follow` 原来直调 `guide_confirm` 接口绕过。
- 修法（主会话 11-23 定）：按类修在共用前端。`status=asking`、无题、`ready=true`、不是 documents 源时出确定卡 `GuideReady`：已定各项一行一项
  （点一行回到那一步，`guide_reopen`），脚上复用复核卡的 `decideButtons`（「拒绝」= 取消、主按钮交 `guide_confirm`）。不给这三个声明补复核。
- 主按钮文字：这类不写账，叫「确定」（新增词条 `confirm`，四语），不叫「写入」。
- 同类：所有没有复核的表单引导一起生效（现有 readback、recon、vat）；documents 源与有复核的表单不变。
- 服务端：不改（`run.confirm` 对没有复核的表单本来就在 `asking` + ready 时收）。
- 入口与共用件：CC 对话页的引导卡（Cowork、ERP 同一组件）；`check-impact` 没命中登记的共用件；覆盖闸列出的受影响流程见下。
- 已知限制：确定卡只列后台定的步骤（`card.decided`），刚在问卷里答过的题不再重复一行（服务端此刻不给已答题的显示名）。
- 文案：新增 1 条（`confirm`：确定 / Confirm / ยืนยัน / 確定），`check-copy` 拦截 0。

## 验证（实验栈 pearnly-pageflow-lab，本分支源码，变基到 main 4ceef0788 后带迁移重跑：8 条 17/17 格通过）

| 流程 | 结果 |
|---|---|
| `cowork.cc.guide.readback`（新，真页面菜单 → 月份 → 确定卡 → 结果） | 电脑 / 手机 通过 |
| `cowork.cc.guide.recon-follow`（改：页面上点确定卡「确定」、结果卡点追问选项「提交」，加手机宽度） | 电脑 / 手机 通过 |
| `erp.cc.guide.collection`（有复核的表单，按钮回归） | 电脑 / 手机 通过 |
| `cowork.cc.guide`（第 1 期引导卡） | 电脑 / 手机 通过 |
| `erp.cc.guide.reopen` | 电脑 通过 |
| `cc.chat`（new / old）、`erp.cc.guide.stop`、`erp.cc.typed-collection`（覆盖闸列出） | 电脑 / 手机 通过 |

总表：回读 · Cowork 直写 · 本地、对账 · Cowork 直写 · 本地 两格 half → done（叠在第 1 批之上，`--counts`：main e8b2246f4 上 ✅1 ◐30 ○10 ✗4 → 第 1 批 ✅4 ◐27 → 本条 ✅6 ◐25 ○10 ✗4）。增值税声明没有结果步骤，不是完整引导，仍 half。
