# DMS LINE 语言跟随手机（2026-09-27）

## 授权与范围

- 用户 2026-09-27："把 dms 这边的 line 语言做成和 erp 一样，跟随用户设备语言"。
- 工作树 `/Users/skin/.codex/worktrees/dms-line-language/pearnly`，分支 `codex/dms-line-language`，**叠在** `codex/dms-fast-sync`（`d167b94`）之上：两件事都等同一轮 Windows 测试机试用，按顺序合并。
- 不改 DMS 业务判断；只改"用什么语言说"、命令词识别、图文菜单图片与编辑页语言来源。

## 做法（与 ERP 同一个来源：`pearnly_core.line.user_language`，问 LINE profile，缓存一小时）

| 部分 | 内容 |
| --- | --- |
| 收件人语言 | `pearnly_dms.i18n.line_language(user)` 上下文 + `line_lang()`；块内卡片、错误话术、Frappe `_()` 都用这个人的语言，块外仍是泰文（与改动前一致）。不是四种之一或问不到 → 泰文 |
| 入口 | webhook 每个事件（`line/handlers.handle`）、后台任务（`line/out.run_guarded`）、订车回执补发（`booking/delivery`）、超时重试卡（`booking/claim_recovery`）、查询结果推送（`query/snapshot_sync._push_result`）；审批发给审批人/申请人的卡片与通知按**收件人自己**的语言生成 |
| 写死泰文 | 17 处 `lang="th"` / `["th"]` / `.get("th")` 改为当前收件人语言；`qa_cards` / `qa_payment_cards` 里导入时就算好的 23 个标签按钮 + 拼接文案、`cards.FIELD_LABELS_TH` 改为用到时现取（外部 `模块.名字` 写法不变） |
| 翻译 | 此前 259 条 LINE 卡片文案只有泰文（th 605 行、其余 344 行）；补齐英/中/日并入 DMS `messages.tsv`，四个语言包现各 887 行。泰文原文经 Frappe 读取器逐条比对 0 条变化（2 行只是换行写法变成转义） |
| 命令词 | 菜单 / 重新开始 / 问候 / 现金 / 解绑 增加中、英、日写法，**整句相同**才算（`line/commands`、`booking_qa._CASH_WORDS`、`handlers.UNBIND_WORDS`）；绑定帮助里示范的解绑词按语言给 |
| 图文菜单 | 发布 泰/英/中/日 × 基础/带查询 共 8 套；按用户手机语言挂载，换语言后下一次发消息重挂（`rich_menu.ensure`）。泰文用设计原图不变；其余语言由 `scripts/render-dms-line-menus.py` 以原图为底只重画文字区生成（字体为 Frappe 镜像自带的开源字体）。聊天栏文字由"欢迎语截 14 字"改为短词（泰文「เมนู」）。只发布过旧的两套时照旧挂那两套 |
| 编辑页 | `browser_auth` 返回的语言改为 LINE 手机语言（问不到退回账号语言）；`EditorPage` 采用入口页放在会话里的这个语言，避免"聊天是中文、编辑页是泰文" |

## 验证（本机，候选源码）

- 新增 `scripts/test-dms-line-language.py` 10 条（锁定 Frappe 16.33.0 镜像、无网络），已加入 `ci-candidate-checks.sh`：上下文进出与嵌套、LINE 语言与回落、卡片与拼接文案随语言、四语键与占位符全对齐、命令词与误判、菜单标签与旧发布回落、8 张图存在。
- 架构 0 违规；离线套件（额度 46/46）、调用契约、客户身份、队列目标、架构守卫、Desk 契约通过；`i18n-build --check`、前端 i18n 严格检查通过；前端 `npm ci && npm run build`（Node 22.14.0 容器）与编辑页快照 6/6、native-desk 通过。
- 镜像内：line-runtime 37、session-reuse 18、intake-queue 23、query-output 11、query-snapshots 40、snapshot-consumers 25、teardown 3。
- flow lab：line-usage-redis 5、native-flow 10、native-recovery 8、booking-flow 33/33、entry-e2e 12/12、query-flow 46/46。snapshot lab：store 27、reads 17、manual 13、queue 2。
- 按新行为调整的测试：`test-dms-native-recovery` 放行只读的 LINE profile 查询（回执按收件人语言），单独记录、不计入消息流量；快照测试共用基类把手机语言固定为泰文（与快照读写无关）；额度测试按新写法比对入口。
- `check-i18n --strict`：本批新增的中文日志已改英文；新增的中日文**命令词**（菜单、重新开始、你好、您好、現金、现金、連携解除、選單、やり直し）是识别用户输入的词表，必须是原文，保留。

## 上线前要做 / 限制

- 部署后需要**重新发布图文菜单**（`rich_menu.publish()`，写 LINE 官方号，需用户同意）才会出现多语言菜单；不发布时一切照旧挂泰文菜单。
- 泰文以外的译文由本 Agent 撰写，业务用语（例如"登记名义""配车"）请懂中文/日文的人在测试机上过目。
- 未做真机验收：需要在测试机上把手机 LINE 语言分别切到中文/英文走一遍菜单 1、2、5。
