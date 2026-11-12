# CC 回话语言：短泰文夹英文公司名、提问卡、被拒原因（审计第 5 批 E1–E3）

- 分支 `pearnly/cc-reply-language`，叠在 `pearnly/cc-conversation-ledger`（5e5c2a08a）之上：两支都改 `pearnly_core/assistant` 与
  Cowork 判定一带，那支排集成第一；它合入 main 后本支变基一次即可。
- 来源：A 线审计「CC 不认上下文」E 节（共享笔记 `A线-CC不认上下文-审计-11-12.md`），主会话 11-12 排为第 5 批。

## 改了什么

| 编号 | 现象 | 改法 |
|---|---|---|
| E1 | 「ลง SINCERE ICE」这类短泰文夹英文公司名：泰文字母比拉丁字母少，被判成英文，CC 用英文答（11-10 d3b2f9acf 的判法） | `contracts.message_language`：有两个以上泰文字母就判泰文，不再与拉丁字母比；假名、两个以上汉字仍先判（中文句夹泰文公司名仍是中文）；只有拉丁字母、两个词以上才判英文 |
| E2 | 提问卡题目、选项是英文：语言规则只管回复与过程短句 | 随每条消息带的 `LANGUAGE_RULE`、智能体说明 `definition.SYSTEM`、`ask_user` 工具说明都写明：卡片题目、标题、选项名与说明用本轮语言，数据里的名称（账套、公司、科目）照原样，工具给的英文理由要转述 |
| E3 | 工具被拒的原因是纯英文文本，不带 `reply_language`，CC 照抄英文 | `contracts.reply_language(text, language, is_error)`：不是 JSON 对象的结果也包成带标记的对象——出错放 `reason`（`status: failed`），其余放 `result`；英文或读不到语言仍原样。`toolrun._with_language` 把是否出错传下去（出卡前拒绝、执行结果两处） |

不做：E2c（建议科目理由是服务端英文句）——工具结果按设计是内部英文、带 `reply_language`，E2 的规则已要求转述；E2d（判定选项名为空，CC 自起）属第 4 批「提问一个入口」，已并入引导式第 1 期。
没改界面文案（只改给模型的说明），`check-copy` 新增 0、改动 0。

## 验证

- 单测：`tests/test_assistant.py` 新 3 条（`test_a_short_thai_line_with_an_english_company_name_is_thai`、
  `test_refusals_and_plain_text_results_also_carry_the_reply_language`、`test_question_cards_are_written_in_the_users_language`），
  旧代码红、新代码绿；原「纯文本原样回」的断言按 E3 改为英文才原样回。`test_assistant` 251 条通过。
- 本地闸门（照 development.yml）：225 步通过；其中 `test_flows_coverage` 一格在闸门里并发负载下返回 1（无输出），单独重跑整步通过。
- 候选检查（生产镜像 offline）：见下。
- 共用件连带回归：命中「CC 对话（assistant）」10 个入口（`check-impact.py --base origin/pearnly/cc-conversation-ledger`），见下表。

## 共用件入口实测

（21:00 后本机实验栈 `pearnly-aline7-lab` 补：`run.py --changed --base origin/pearnly/cc-conversation-ledger`）
