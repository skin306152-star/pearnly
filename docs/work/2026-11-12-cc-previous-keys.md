# 换密钥后旧 CC 对话读不到：以前用过的密钥只读（2026-11-12）

主会话 local_40940693 批（A 为主、B 兜底），本窗口施工。分支 `pearnly/cc-previous-keys`，基线 origin/main `6275e4999`。

## 现象与证据

- 11-11 erpcowork 388 条 `anthropic.NotFoundError: Session not found`，全是 mrerp@outlook.co.th 的 6 个对话（11-08 19:29 – 11-09 19:30 建），
  都早于 11-10 22:00 全平台由 IwAA 换成 6QAA；换密钥后建的对话没有一条。代码路径：367 条整段重放读历史（`sessions._segment_events`），
  12 条接续新段取前文（`segment_for_turn`），7 条附件（`files._upload` → `segment_for_turn`）。前端同时段报「The conversation history hasn't loaded yet」。
- 11-12 本机只读核实（只读会话与第一条事件，没发消息、不花钱，密钥不落日志）：`sesn_016b6tjzCvxUkkewu5XSnbsc` 用 IwAA 读会话 200、读事件 200；
  用 6QAA 都 404。**远端会话跟着建它的那把密钥。**
- 影响范围：11-11 全天 404 只在 erpcowork（388）和 testcowork（2）；其他正式租户 0。各正式租户换密钥前建、之后还用过的对话数待线路恢复后补（本机出口 IP 到生产被上游挡）。

## 做法（A 为主、B 兜底）

- 站点配置 `pearnly_assistant_previous_api_keys`：以前用过的密钥列表（也认一个字符串）。没配 = 一切同以前（不多试、不多读）。
  登记进 `scripts/impact/product-map/config.toml`：`presence`、`secret = true`（部署前对比只看有没有、不取值）。
- `engine.ReadOnly`：以前密钥的适配器只放行 `status / events / files / read_file / metadata / gone`，其余（建会话、发消息、传文件、挂载、改名、
  归档、删除、刷新工具、实时流、总结、备资源）一律 `ReadOnlyKey`（报错只带调用名，不带密钥）。**硬约束，有测试。**
- `engine.reader(api, remote)`：这段远端会话用哪把读——当前密钥读得到用当前；读不到（引擎判「不存在」，`Adapter.gone`：Anthropic 400/404/410）
  按顺序试以前的；都读不到回 None。结果记缓存（读到 1 天、都读不到 10 分钟）；远端一时不通照旧抛出、不记。
- 整段重放：每段用读得到它的那把读；哪把都读不到的段换成一句 `Earlier messages in this conversation are unavailable.`（四语），不再整段失败。
- 增量补拉、运行状态、侧栏核对状态：同样按段选密钥；都读不到的段状态按空闲。
- 接着聊（发消息、传附件、`segment_for_turn`）：当前段是以前的密钥建的 → 同「到了计价界线」一样在**当前密钥**下开新段，前文用旧密钥读出接上
  （`carry_note`）；都读不到就开新段不带前文。旧段永远只读。
- 附件列表与取结果文件（`files.listing`、`files._stored`）：按段选密钥。

### 改到的函数（交 A 线避让）

- `assistant/engine.py`：新增 `PREVIOUS_KEYS`、`ReadOnlyKey`、`ReadOnly`、`previous_keys`、`previous_readers`、`reader`（其余没动）。
- `assistant/engines/base.py`、`engines/anthropic_managed_agents.py`：新增 `Adapter.gone`。
- `assistant/sessions.py`：新增常量 `HISTORY_UNAVAILABLE`、函数 `_remote_status`、`_remote_events`、`_readable`；改 `run_state`、`_operator_run`、`_check`、
  `segment_for_turn`、`events`、`_operator_events`、`_segment_events`（只换读取那几行）。
- `assistant/files.py`：新增 `_segment_files`；改 `listing`、`_stored`。
- 没动写账目标、工具执行、发送（`send` 只经 `segment_for_turn`）、标题（`titles.py` 只在还没标题时读第一段，旧对话都有标题）。

## 文案（新增 1 条，`check-copy.py --report`）

| 键 | 中文 | English | ไทย | 日本語 |
|---|---|---|---|---|
| pearnly_core·Earlier messages in this conversation are unavailable. | 此对话之前的消息无法显示。 | Earlier messages in this conversation are unavailable. | ไม่สามารถแสดงข้อความก่อนหน้าในการสนทนานี้ | この会話の以前のメッセージは表示できません。 |

显示方式：整段重放里在读不到的那段的位置放一条 CC 消息（`agent.message`，编号 `pearnly_gap_<段>`），不改前端；不用提醒卡片（提醒卡片会带「打开通知」按钮）。

## 验证（提交 826c9171d，基线 6275e4999）

- 先红后绿：`tests/test_assistant.py` 新增 `PreviousKeyTests` 7 个（只读硬约束与配置列表、按段选密钥与缓存、整段 / 增量 / 状态用旧密钥读、
  都读不到时一句话不报错、接着聊在当前密钥下开新段并带前文、附件按段取、配置键登记）。放在旧代码上 7 个全红；只换上新 `engine.py` 时会话与附件那几个
  红在 `Session not found`（即线上现象）。改后全绿，`test_assistant.py` 全量 244 个过（4 个旧测试的 `files.engine` 替身补了 `reader`，断言没动）。
- 本地全闸门（`gate.sh`，同 development.yml）：223 项全过，GATE_EXIT 0。
- 候选离线（生产镜像 `b3adf474`，`cand-local.sh offline`）：108 组，LANE EXIT 0。
- 文案：`check-copy.py` 新增 1、拦截 0；`--report` 见上表。`i18n-build.py` 已重生成四语翻译包。
- 回归实验室（挂本分支，`run.py --migrate --jobs 3`）受影响流程 7 个 20 格全过（430 秒）：`cc.chat`、`cc.operator`、`cc.operator.replay`、
  `cc.sidebar-modes`、`cowork.cc.sidebar-modes`、`cc.attach-delete`、`cowork.cc.attach-delete`。实验栈没配以前的密钥，这一遍验的是「没配 = 同以前」。
- 「换密钥后」的行为（两把密钥）只在单元测试里用替身验过；线上效果看部署步骤第 3 步。

### 共用件连带回归（`check-impact.py`：命中 `assistant`，11 个入口）

| 入口 | 结果 |
|---|---|
| `cc.assistant` | 通过：`cc.chat`、`cc.attach-delete`、`cc.sidebar-modes`（新建对话、发消息、附件、切会话、侧栏） |
| `shared.assistant` | 通过：同上，对话页外壳打开、整段重放 |
| `cc.operator.thread` | 通过：`cc.operator`、`cc.operator.replay` |
| `cowork.assistant` | 部分通过：`cowork.cc.sidebar-modes`、`cowork.cc.attach-delete`；CC 推送 / 待处理工具没测到（本次没改工具，只改读历史与附件） |
| `shared.exports` | 部分通过：CC 附件下载（`cc.attach-delete` 读回附件内容）；账本导出没测到（没改导出） |
| `control.assistant` | 没测到：实验栈不走 fatcc.com 入口；本次没改登录与入口 |
| `erp.cc.companies` | 没测到：ERP 版 CC 公司工具不在受影响流程里；本次没改工具与公司页 |
| `erp.cc.chat-push` | 没测到：同上，没改直写推送 |
| `erp.cc.stocktake` | 没测到：同上，没改盘点工具 |
| `cowork.cc-chat.push` | 没测到：没改推送 |
| `cowork.cc.troubleshooting` | 没测到：没改待处理工具 |

## 部署步骤（随热修部署，主会话批；部署时刻另定）

1. 照常 `deploy.sh`（先 `--dry-run`）。
2. 部署回读通过后，一次 SSH 写各站配置：`ssh pearnly 'sudo bash -s' < ~/pearnly-devtools/release-1111/set-previous-keys.sh`。
   值取各站 11-10 换密钥时留的 `site_config.json.bak-iwaa-20261110-*` 里的旧密钥，只经文件读写、不打印；幂等；没有备份的站（换密钥后才建）跳过。
   生效以第 3 步实际打开旧对话为准；没生效就重建该产品 web 容器（照 activate-product.sh 清单，带 frontend）。
3. 回读（只核「已配置」与条数，不打印值）：各站 `pearnly_assistant_previous_api_keys` 有、条数 1；erpcowork 用 mrerp@outlook.co.th 那 6 个对话之一只读打开整段，
   历史出来、Error Log 不再有 Session not found。接着发一句会走 6QAA 开新段（花一次），交主会话定是否做。
4. 回退：删各站的 `pearnly_assistant_previous_api_keys` 即回到现在的行为（不用重部署）。
