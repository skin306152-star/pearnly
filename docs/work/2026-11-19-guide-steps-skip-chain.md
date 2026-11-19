# 引导引擎：条件依赖不适用的步骤也不适用（11-19）

分支 `pearnly/guide-steps-skip-chain`，基线 main 1a91b178b。主会话 11-19 定单拆，引擎负责人 local_38ec22a7 审语义。

## 现象
一步的 `when` 依赖的那一步本身不适用时（定时任务选「启用」：「去向」只在新建 / 修改时问），挂在它上面的步骤（现赊、抵扣、科目、部门、存货；
挂在「提醒」上的提醒时刻）一直算「没轮到」，卡片永远到不了可确定。与 1266ea15f（条件已判出不成立时不再等 after）同一类。

## 改法
`pearnly_core/assistant/guide/steps.py` 新加 `_ruled_out_keys`：照声明顺序，自己的条件已判出不成立，或条件依赖的那一步已不适用，都算不适用（一路传下去）。
`blocked`、`to_ask`、`final_values` 都排除这些步骤；`after` / `when` 点名它们的不再被挡。依赖的那一步真没值（判不出）时照旧算没轮到。

## 验证
- 先红后绿：`tests/test_cc_guide_steps.py` 新的一条在旧 `steps.py` 上失败、新代码 25 条全过。
- 共用件连带回归（`check-impact`，命中「CC 对话（assistant）」13 个入口）：
  - offline 入口 erp.cc.chat-push、erp.cc.stocktake、erp.cc.collection、cc.operator.start-card：全闸门里的对应测试通过（见提交说明的闸门结果）。
  - auto 入口 erp.page.collections：没测到（原因：只改引导步骤的判定；收款页不经过引导引擎）。
  - manual 入口 control.assistant、shared.assistant、cowork.assistant、cc.assistant、cc.operator.thread、erp.cc.companies、cowork.cc-chat.push、
    cowork.cc.troubleshooting：没测到（原因：引导引擎还没接进对话页（接线分支未进 main），这些入口不调 `guide.steps`；引导各业务由
    test_cc_guide_run / registry / card / menu / tool、收款、读回、对账、单据源、写账判定引导的离线测试守住，全部通过）。
