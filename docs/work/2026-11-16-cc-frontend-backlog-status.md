# CC 前端与交互欠账：11-16 现状重分类

- 任务：把 11-14 欠账总表（分支 `origin/pearnly/cc-frontend-audit-373405` 的 `docs/work/2026-11-14-cc-frontend-backlog.md`，下称「原表」）里欠着的 72 条（B01–B72）+ 2 条已合入未上线（U01、U02）+ 弹窗通知 18 处（P-1–P-11、C-1–C-7）按 11-16 现状逐条重核，找出**没有任何窗口在做**的部分并接着做。
- 分支 `pearnly/cc-frontend-backlog-status`，窗口 frontend-backlog（local_717dd931）。
- 基线：origin/main = **847fc020c**；线上服务端 = **72516d120**（`git merge-base --is-ancestor <提交> 72516d120` 判是否上线）；CC 桌面版正式版仍 `companion-v1.12.21`。
- 「在途」指已有分支 / 窗口在做；「归引导式」指按「引导式全覆盖总表-11-16」第 6 节分给了某条线、但还没见到对应代码；「没人管」= main 上没有、所有在途分支（`git diff --name-only origin/main...<分支>` 逐个看过 40+ 条 11-12 后的远端分支）都没碰相关文件、也没有窗口认领。
- 路径缩写同原表：`F/` = `frontend/assistant/src/`，`K/` = `apps/pearnly_core/pearnly_core/`，`C/` = `apps/pearnly_cowork/pearnly_cowork/`，`E/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`。

## 1. 数字

| 类 | B / U（74） | 弹窗通知（18） |
|---|---|---|
| 已上线 | 4 | — |
| 已在 main 未上线 | 9 | — |
| 在途（有分支 / 窗口） | 14 | 5 |
| 归引导式某一格 / 某条线 | 12 | 3 |
| 已被用户新决定取代 | 3 | 3 |
| 已满足（不改） | 1 | 1 |
| 口径待定 | 10 | — |
| **没人管** | **21** | **6** |

（11-16 晚重核后：B26 原表标错、其实 11-07 已上线；B72 逐张核对已满足；B27、B28、B35 改列口径待定。最初一版是 已上线 3 / 待定 7 / 没人管 26。）

## 2. 逐条（B01–B72、U01–U02）

| # | 一句话 | 现状 | 证据 |
|---|---|---|---|
| B01 | 引导式本体（业务菜单、引导卡、合一个组件） | 在途 | 引擎已在 main：`K/assistant/guide/run.py`、`declaration.py`；前端 `guide-card.tsx` 在 `cc-guided-phase1`（列车 F）、`cc-guided-wire`（A1 local_38ec22a7）、`cc-guided-ui-run` / `-ui-review`（A2 local_1f7ac454） |
| B02 | 代录开始卡并进引导卡 | 归引导式 A4（guided-erp local_f0882fd0，分支 6） | 总表 6.2 A4 行 |
| B03 | 引导卡失效置灰 | 在途 A2 / A1 | `origin/pearnly/cc-guided-ui-review` 的 `guide-run-card.tsx`、`lib/guide-run.ts` 有 expired；`cc-guided-wire` 的 `guide/run.py` 有失效 |
| B04 | 代录任务 7 天没动作 / 同票进新任务就失效 | **没人管**（服务端 + 代录卡，非纯前端） | main `K/express_operator/tasks.py`、`F/components/tool-uis/card-phase.ts` 无 expired；旧分支 `cc-card-expiry` caf75dc65 落后 300+ 提交 |
| B05 | 写入计划 / 写入类允许卡 120 分钟失效 | 归引导式 A1（引导运行失效 S6 取代写入计划这条路） | `cc-guided-wire` `guide/run.py`；main `write_plan.py` 仍无过期判断 |
| B06 | A8 新对方、A10 新物料两题 | 归引导式 A3（guided-four-questions local_d99fabda）/ G 组 CC 桌面版小表 | 总表 6.1 末「随单新建属采购 / 销售格内的题」 |
| B07 | 复述别把刚答的说成「之前记下的」 | 归引导式 A1 | 第 1 期放行一页已知问题，无修复提交 |
| B08 | 识别后复核卡（就地改、标红不拦、分页） | 在途 A2 | 服务端复核在 main（`guide/run.py`）；前端 `guide-review.tsx` 在 `cc-guided-ui-review` 336d991c9 |
| B09 | 复核与开始前的题一次办完 | 在途 A2 | 同上（`guide-run-card.tsx` 一张卡含题 + 复核） |
| B10 | 每批一张结果卡 | 在途 A2 | `cc-guided-ui-review` `guide-run-card.tsx` 结果态 |
| B11 | 一张票到哪都同一套状态词 | 归引导式 A1 / A2（结果卡状态词） | main 无 `ticket_state` |
| B12 | 一张票一个身份、先查账本再问 | **没人管**（服务端写账规则） | `cc-ticket-identity-3` e8bb74653 未进 main 且冲突；main 只有 `ledger_tickets.check_present` |
| B13 | 都没税号时问一次「哪边是本公司」 | **没人管**（服务端） | main 无 `write_precheck/own_party.py`；`cc-own-tax-once-2` 冲突停着 |
| B14 | ERP 收款引导卡 | 在途 | 服务端在 main：`E/collection/guide.py`；前端接线随 A1 / A2 |
| B15 | 收款写成后对话里补结果 | 归引导式（收款格结果卡，A1） | `E/collection/push.py` TELL 未变 |
| B16 | 收款复述只念账套名 | 归引导式（收款格：卡上显示 `target.option_name`） | `E/collection/guide.py:169,180` |
| B17 | ERP 直写收单 + 复核 + 结果 | 在途 guided-erp | 总表组 B |
| B18 | Cowork 其余功能都有引导 | 在途 C1（P3 窗口）/ C2（guided-cowork-2 local_3b58bd71） | `cc-guided-readback/recon/vat/pos/wht/stocktake/work/...` |
| B19 | 删除类加允许 / 拒绝卡 | 已在 main 未上线 | 9ac8a8f5f（8 个删除 / 结束 / 立即运行工具都带 `approval`） |
| B20 | 立即运行先复述再跑 | 已在 main 未上线（允许卡 + 事实行）；引导形态归 C2 定时任务格 | 9ac8a8f5f、1244881a3 |
| B21 | 已放行推送可撤回 | 在途 `cowork-withdraw-cc` b47b9059a（备用 A local_f020820b） | 网页撤回 `C/push/native/service.py:1119 withdraw_pending` 已在 main |
| B22 | 回复不带账套内部编号；无名回退成位置 | 已在 main 未上线 | e3c1387e4、aeae687fd |
| B23 | 只收当月时说清原因 | 已在 main 未上线 | 290bc368b（`write_plan.py` OTHER_MONTHS_NOTE） |
| B24 | 提问卡浮在输入框上方 | 归引导式 A2（**未见实现，已请 A2 确认是否在范围**） | `cc-guided-ui-review` `approval-sheet.tsx:5,26` 仍写 ask_user 不浮 |
| B25 | 单选自动翻页、编号、窄屏翻页行 | 归引导式 A2（同上待确认） | `cc-guided-ui-review` `ask-card.tsx` 无自动翻页 |
| B26 | 对话标题约 3 秒出 | 已上线（11-16 晚重核：原表标错） | 61c2b812d（11-07）：第一句发出后马上后台概括，只看这一句、不等回复（`K/assistant/titles.py` 文件头） |
| B27 | 已发消息能重试 | 口径待定（11-16 晚重核） | 引擎会话只能追加、不能截断或分叉，「重试」只能做成再发一遍同一句（记录里会有两条）；`thread.tsx:817` 原决定「做不到原样替换就不摆」。见第 4 节 |
| B28 | 侧栏搜正文 + 摘录高亮 | 口径待定（11-16 晚重核） | 正文在引擎会话里，本站不存，要服务端另建正文索引（新增存储与每条消息写入）；审计 #8 原写「待定：先估成本」。见第 4 节 |
| B29 | 快捷键一组 + 快捷键表 | **没人管** | 现有只有 ⌘B、Ctrl+,、切模式、Ctrl+数字、代录 Ctrl+N |
| B30 | 打开对话用灰色骨架占位 | **没人管** | `F/assistant.tsx:207-214` ThreadLoading 仍是吉祥物 + 文字 |
| B31 | 两个同名公司组头补「· 分店」 | **没人管** | `sidebar-group.tsx` 无分店区分 |
| B32 | 附件真实上传进度 | **没人管** | `F/lib/managed-agents/attachments.ts:123` `progress: 0.3` 写死 |
| B33 | PDF 传完方块换第一页小图 | **没人管** | `F/components/assistant-ui/attachment.tsx:143-150` 方块只画图片；第一页小图只在发出后的卡（`:301`） |
| B34 | 上传失败点方块重试 | **没人管** | `attachment.tsx:186-208` 出错只有原因 + × |
| B35 | 输入框正常方块换标准 Attachment | 口径待定（11-16 晚） | 保留项原话「可见改版（缩略图会从大方块变成小图标位）」，与 B33「同 Claude 大方块显示 PDF 第一页」冲突；`attachment.tsx` 仍是 `.cc-compose-tile` |
| B36 | 代录选中就上传、发送才识别 | 已在 main 未上线 | df09e780d、4a7056a08 |
| B37 | 存量手写控件换标准组件 | 进行中无专人：各窗口改到哪换到哪（基线 337→332）；公司选择窗、MR.ERP 连接两处直接用 radix Dialog **没人管** | `scripts/check-frontend-kit.py` PASS 332；`F/components/company-picker.tsx:18`、`mrerp-connect.tsx:13` |
| B38 | 「记住的选择」收起写「代码 名称」 | **没人管**（11-14 用户已定：代码 + 名称，放不下名称截断、代码完整） | 记忆 cc-operator-decisions-2026-11-14 第 4 条；`F/components/company-cards.tsx:663-673` 仍只写代码 |
| B39 | 题目选项四语、加枚举检查 | **没人管** | `tests/` 无枚举 × 词条检查 |
| B40 | 转圈 60 秒 / 连点上报 | **没人管**（低） | `F/lib/caught.ts` 只报异常 |
| B41 | 认出乱用模式主动引导 | 归引导式（各格自己的卡） | 记忆 cc-all-guided-no-chat |
| B42 | 每日已写 / 跳过 / 可疑清单给会计 | 口径待定 | 见第 4 节 |
| B43 | 代录卡头部状态行 + 离线灰态 | 在途 A2 | `cc-guided-ui-review` 新增 `guide-operator.tsx` |
| B44 | 代录暂停原因所有设备看得到 | 在途 CC 桌面版接手 local_27c53845（⑥ interrupted 卡、A5） | 共享笔记「CC桌面版1.12.23-剩余四件-更新做法-11-16」二.3 |
| B45 | 代录「指定时刻开始」 | 已被取代 | 用户 11-14 第 6 条「下班后再开始不做」（cc-operator-decisions-2026-11-14） |
| B46 | 实时画面 /「查看」 | 已被取代 | 同上第 5 条「不做网页实时画面，人自己切过去看」 |
| B47 | 代录「需要你」发通知 | 归引导式（`attention.call` 归 A1，代录出口归 A5） | `K/assistant/attention.py:66 call` 在 main 未上线、尚无调用方 |
| B48 | MR.ERP 代录登录按钮 | 在途 MR.ERP 线（local_3e3bec90） | `K/express_operator/contracts.py:74` 开关仍关 |
| B49 | MR.ERP 代录开关打开 | 在途 MR.ERP 线（外部等真站点菜单） | 同上 |
| B50 | ERP 浏览器里也有「连接」栏，按产品声明不写死 | **没人管** | `F/components/settings.tsx:114`、`F/lib/desktop.ts:54` 仍 `product === "cowork"` |
| B51 | ERP 右栏「记住的选择」卡 + forget | **没人管** | `E/masters/cc_projects.py` SECTIONS 无 choices；`cc-guided-erp-company` 也没碰 |
| B52 | ERP 对话交代「某费用记某科目」按公司记住 | 口径待定 | 原文档写「另议」 |
| B53 | ERP 代录不写死 Express、补 MR.ERP 出题源 | 在途 MR.ERP 线 / A4 | 总表组 F |
| B54 | 代录遇不认识的弹框留给人、回报半截单号 | 已在 main 未上线（CC 桌面版待发） | 887844eb9、e450c1b22（服务端已上线），合并 59b182eed |
| B55 | 位置能只选几本账套 | 已在 main 未上线（CC 桌面版待发） | `companion/tests/test_place_pick_books.py` |
| B56 | 换位置后不再上报范围外账套 | 已在 main 未上线（CC 桌面版待发） | 同一合并 59b182eed |
| B57 | Express 窗口超出屏幕自动缩放 | 已在 main 未上线（CC 桌面版待发） | 314d61ae2、`companion/tests/test_operator_fit.py` |
| B58 | CC 桌面版更新：托盘进度、断点续传、停滞重连 | **没人管**（`companion/`，建议交 CC 桌面版窗口） | `companion/src/companion/auto_update.py` 无进度 / Range |
| B59 | 安装包改从 R2 下载 | 口径待定（等用户建令牌） | `prod-speed-2-5237c8` 8d69c624b |
| B60 | ERP「CC 桌面版未连接」改「未连接电脑」 | **没人管**（改已有文案，清单先给主会话） | `E/i18n/messages.tsv:1365`、`E/public/desk/company-connect.js:11,81`、`E/express/push.py:857,878`、`E/masters/cc_service.py:423,446` |
| B61 | 只剩一个位置时「×」标准禁用样式 | **没人管** | `F/globals.css:1146-1153` `.cc-row-btn` 无 `:disabled` |
| B62 | 窗口拿到焦点也核对对话列表 | **没人管** | `F/lib/managed-agents/list-sync.ts:16,249` 只听 visibilitychange |
| B63 | ERP 版 CC 补齐网页每个动作 | 归引导式（总表 D 组 + 第 2 节范围外逐条依据） | 总表第 5、6 节 |
| B64 | CC 不自己读票据、一律交识别工具 | **没人管**（模型指令） | main `K/assistant/definition.py` 无此段；`cc-no-self-read` 冲突 |
| B65 | 「一句话下令」与「点写入」措辞 | 已被取代 | 用户 11-14 引导式为主（cc-all-guided-no-chat） |
| B66 | 公司主档后续（批量、拖放、合一家、参考文件、合并申报） | 口径待定 | 见第 4 节 |
| B67 | 归不上公司的单在侧栏出「无公司」行 | 已上线 | 7a90890b5 |
| B68 | 截 Claude 桌面 Code 页核上传卡 | 口径待定 | 要用户在场或给截图 |
| B69 | 会话栏分界、桌面标题栏 40 | 口径待定（低） | 无用户确认记录 |
| B70 | 选项卡一行供应商名别连写两遍 | **没人管**（要实测） | 取决于服务端 item.label |
| B71 | 代录中途要选的单挪最后、其余照录 | 口径待定 | 11-14 第 1 条改成中途框带进对话 |
| B72 | 图标所有尺寸用带胡须原版 | 已满足（11-16 晚逐张核对） | 网页 icon-32/192/512、maskable、apple-touch、favicon 16/32/48，CC 桌面版 `companion/assets/cc.ico` 16–256 都是带胡须原版 |
| U01 | ERP 收款列全部、可撤回同事的 | 已上线 | d0190e4e0、a724dc175 |
| U02 | 「没有覆盖此日期的账套」写出期间 | 已上线 | 91e700a45 |

## 3. 弹窗与通知（18 处）

| # | 一句话 | 现状 | 证据 |
|---|---|---|---|
| P-1 | 直写结果、晚到的结论 | 在途：结果卡 A2；执行电脑托盘去掉「请在网页中处理」归 CC 桌面版 ⑨ | `cc-guided-ui-review`；用户 11-16 晚同意删半句 |
| P-2 | ERP 收款写成 / 失败 | 归引导式（收款格结果卡） | = B15 |
| P-3 | ERP 写账中途要人选 | 已被取代（网页提交的单）+ 归 guided-erp（CC 发起的） | user-decisions-2026-11-16 第 6 条「网页提交的单不进 CC 对话，中途要人选只在单据页上问」 |
| P-4 | 代录没登记的框 | 在途 CC 桌面版接手 ③（A5） | 共享笔记 1.12.23 剩余四件 二.1 |
| P-5 | 代录被打断暂停 | 在途 同上 ⑥ | 同上 二.3 |
| P-6 | CC 开的对账结果回对话 | 归引导式 C1（N2 结果卡带文件） | 总表第 4 节 N2 |
| P-7 | 定时任务提醒、简报、立即运行结果 | 已被取代（后台结果留原处只叫人）；立即运行落点归 C2 定时任务格 | user-decisions-2026-11-16 第 6 条 |
| P-8 | 待处理日报、「需要处理」框 | 已被取代（同上）；待处理格归 C2 | 同上 |
| P-9 | 别的对话的提醒被带进当前对话 | **没人管** | `K/assistant/notices.py:147-156 uncarried` 仍带 `CARRY_WINDOW` 内别的对话 |
| P-10 | 提醒点开时 CC 自开新对话 / 弹选公司窗 | **没人管** | `F/lib/nav.ts` pending / company / companies 分支未改 |
| P-11 | 已在对话里只换形态（允许卡、写入、状态行、附件原因） | 在途 A2（允许卡新版式已在 main 9ba87547a / 8e1376be6 未上线）；附件方块那项 = B35 没人管 | — |
| C-1 | 回复完成通知只看整窗焦点 | **没人管** | `F/lib/desktop.ts:113-119` 仍 `document.hasFocus()` |
| C-2 | 停下等人不叫人 | 归引导式（`attention.call` A1 / 代录 A5） | = B47 |
| C-3 | 本站提醒桌面版一律弹 | **没人管**（页面一半；托盘一半在 ⑨） | `F/lib/desktop.ts:121-129` |
| C-4 | 推送正看着也照推 | 服务端判据已在 main 未上线（9e9e2bf7c，`attention.watching`、`push.send_notice` 发前判）；**页面上报「在看哪段 + 可见」与接口没人管** | main 无调用 `attention.seen` 的接口，前端无上报 |
| C-5 | CC 桌面版托盘照弹 | 在途 CC 桌面版 ⑨（桥 `attention_seen`、读站点 muted） | 共享笔记 1.12.23 剩余四件 二.4 |
| C-6 | 侧栏圆点 / 托盘灯 | 已满足（保留作状态） | 原表 8.3 |
| C-7 | 通知开关与账户静音 | 桌面一半在途 ⑨；**网页一半没人管**（通知读 `User.mute_sounds`） | `F/`、`K/assistant/` 零命中 mute_sounds |

## 4. 口径待定（要定什么）

| # | 要定的事 | 建议 |
|---|---|---|
| B42 | 每日「已写 / 跳过 / 可疑」清单给会计还做不做 | 不做：11-16 定后台结果留原处只叫人，结果卡已在对话里；没有实测问题撑着、只加成本 |
| B52 | ERP 对话里「某费用记到某科目」按公司记住要不要（Cowork 已有） | 做，同 Cowork（CC = 网页功能 + 代录） |
| B59 | 安装包改 R2 | 等用户建 R2 令牌与 3 个 GitHub 密钥；不建就保持现状 |
| B66 | 公司主档后续五项是否被公司接入重做取代 | 问公司接入线 local_3709c091；未取代的按用户需要再排 |
| B68 | 截 Claude 桌面 Code 页核上传卡样子 | 要用户在场授权电脑控制，或用户给两张截图；没有就按现有研究文档做 |
| B69 | 会话栏分界、标题栏 40 | 按现状，不再问 |
| B27 | 引擎会话不能截断，「重试」只能是再发一遍同一句（记录里留两条） | 维持原决定不摆（`thread.tsx:817`）；要的话做成「再发一遍」 |
| B28 | 搜正文要本站另存正文、建索引（新增存储与写入） | 先不做；会计找对话靠公司分组 + 标题，真有人反映再估 |
| B35 | 输入框附件方块换标准卡（小图标位） vs 同 Claude 大方块 | 保留 Claude 大方块，B35 划掉（失败卡已是标准卡） |
| B71 | 代录要选的单挪最后、其余照录 | 视为被 11-14 第 1 条（中途框带进对话）取代 |

## 5. 没人管的清单与做的顺序（本窗口接）

按「用户最容易碰到」排，一类一条分支。估时按机器耗时（写码 + 闸门）。

| 批 | 分支 | 条目 | 估时 |
|---|---|---|---|
| 1 上传 | `pearnly/cc-upload-tiles` | B34 失败点方块重试、B32 真实上传进度、B35 方块换标准 Attachment、B33 PDF 第一页小图进方块 | 3–4 h |
| 2 小界面 | `pearnly/cc-small-ui` | B30 骨架占位、B38 记住的选择「代码 名称」、B61 × 禁用样式、B62 焦点核对、B31 同名公司组头分店 | 2–3 h |
| 3 叫人判据前端 | `pearnly/cc-attention-page` | C-4 页面上报在看哪段 + 接口、C-1 回复完成按这段对话、C-3 本站提醒按这段对话、C-7 读账户静音、P-10 提醒点开不自开新对话、P-9 别的对话提醒不带进来 | 4–5 h（与 A1 `attention.call`、CC 桌面版 ⑨ 对接口） |
| 4 ERP 对齐 | `pearnly/cc-erp-parity` | B50 连接栏按声明、B51 ERP 记住的选择卡 | 3 h |
| 5 外壳 | `pearnly/cc-shell-keys` | B29 快捷键 + 快捷键表、B27 重试（先查引擎能否截断，做不到标「没依据 / 做不到」）、B26 标题提速、B28 正文搜索（先估成本） | 6–8 h |
| 6 收尾 | 各自小分支 | B37 公司选择窗 / MR.ERP 连接换标准 Dialog、B39 四语枚举检查、B40 卡顿上报、B60 ERP 旧文案（清单先给主会话）、B64 不自读票据、B70 / B72 核对 | 4–5 h |
| 交主会话派 | — | B04 代录任务失效、B12 一票一身份、B13 本公司税号问一次（都是服务端写账规则）；B58 CC 桌面版更新进度（`companion/`，建议 CC 桌面版窗口） | — |

## 6. 限制

- 「归引导式 / 在途」只说明有窗口认领或有分支，不等于做完；进 main 前不算交付。B24、B25 在 A2 分支上没见到实现，已请 A2 确认。
- 「已在 main 未上线」只说明代码在 main；线上仍 72516d120。
- 没逐行比对旧分支内容，只按文件与提交说明判断。

## 7. 第二件进度（11-16 晚）

| 批 | 分支 | 条目 | 状态 |
|---|---|---|---|
| 1 上传 | `pearnly/cc-upload-tiles` ed8445c09 | B32 B34 B33 | 本地全闸门过、CI success；已交验收窗口 |
| 2 小界面 | `pearnly/cc-small-ui` | B30 B38 B61 B62 B31 | 闸门复跑中 |
| 5 外壳（先做快捷键） | `pearnly/cc-shell-keys` 018473e2c | B29 | 相关测试过、已推，CI 待查 |
| 核对 | — | B72 | **已满足**：网页 icon-32/192/512、maskable、apple-touch、favicon、CC 桌面版 cc.ico 16–256 都是带胡须原版（都由 `src/brand/cc-icon.svg` 出图） |
| 改列口径待定 | — | B35 | 和 B33 冲突（大方块 vs 小图标位），已问主会话 |

### B60 改已有文案清单（待主会话 / 用户同意后施工）

依据：user-decisions-2026-10-18「未连接电脑文案」，Cowork 已用共用层词条 `intake.agent.state.missing`。ERP 现在仍写「CC 桌面版未连接」。

| 出现处 | 现在（中 / 泰 / 日 / 英） | 改成（同 Cowork 那一条） |
|---|---|---|
| ERP 待处理与单据页原因 `express/pending.py:73`、`public/desk/transactions.js:137` | CC 桌面版未连接 / CC เดสก์ท็อปไม่ได้เชื่อมต่อ / CC デスクトップが接続されていません / CC Desktop not connected | 未连接电脑 / ยังไม่ได้เชื่อมต่อคอมพิวเตอร์ / パソコン未接続 / No computer connected |
| ERP 公司页「同步 Express」没电脑 `public/desk/company.js:291`、`public/desk/company-connect.js:166`（词条 `company.syncExpress.offline`） | 同上 | 同上 |
| ERP 收款引导读账套时 `collection/guide.py:43` | 同上 | 同上 |
| ERP 收款 LINE 回复 `integrations/line_collection.py:461` | 同上 | 同上 |
