# 工作包：CC 桌面版打开白屏、改连租户两边断开、询问框被判卡死

来源：`~/Downloads/pearnly-cc-video/功能分集/问题清单-给修复窗口.md` 第 9、10 条与「附」（CC 教程录制，10-20）。

## 目标和范围

- 目标：三条各自核实 → 找根因 → 修 → 回归测试（先失败后通过）→ 真机能验的上测试机验。
- 本次不做：清单其余条目；服务端心跳整份名单的提速（见「剩余问题」）；合入 main、发布 CC 桌面版、部署。
- 范围状态：待审查（分支已推、两套 CI 绿）。收尾状态：交接保留（分支等开发窗口合入）。
- 分支 `pearnly/cc-desktop-runtime-fixes`，基线 `241b9ef8`（origin/main），修复提交 `3687bfc4`；工作树为本代理的临时工作树。
- 未改 `companion/src/companion/version.py`（测试包仍报 1.9.1）。

## 第 9 条：打开 CC 窗口先白屏

- 核实：测试机 1.9.1 正式版，托盘重开窗口三轮截屏（每轮 4 秒、约 110 帧）：窗口出现后的前 0.3–0.4 秒内容区是单一的纯白 `#ffffff`
  （测试机的 CC 入口是 `testai.pearnly.com`，lab 没开、Cloudflare 很快回 502 页，所以白的时间比录制时短；录制时对话页要走网络，
  白 1–2 秒）。本机离屏复现：页面没画出来时整块 `#ffffff`。
- 根因：网页内核（`QWebEnginePage`）默认底色是白色；窗口（`window_main` 拉起的窗口进程）一建好就显示，页面第一帧之前露出来的就是这块白。
  对话页外壳 `assistant.html` 自己的启动画面是 `#fcfcfb / #151515`，只是要等到网络回来才画得出来。
- 修法（`gui_tomo.py`）：建页面时 `page.setBackgroundColor(APP_BG[亮/暗])`，系统主题变了（`_fit_frame`）一起换。`APP_BG` 就是
  globals.css 的 `--cc-bg-app`（`tests/test_cc_ccs.py` 已比对），与外壳启动画面同色，页面第一帧接上看不出切换。不加文案、不加骨架。
- 验证：
  - 回归测试 `test_the_cc_window_shows_the_chat_background_not_white_before_the_page_paints`（真窗口子进程，本地慢服务器 2 秒才回页面，
    前 1.25 秒逐帧取像素；另切深色核对）：旧代码 `#ffffff`，新代码 `#fcfcfb` / 深色 `#151515`。
  - 测试机真机（CI 打的测试包 `3687bfc4`，sha256 `3d7ae011…`）：三轮托盘重开，页面画出前的整块颜色全部是 `#fcfcfb`，纯白占比 0；
    旧版同一做法三轮都是 `#ffffff`。逐帧统计与截图：`~/pearnly-devtools/cc-runtime-fixes/frames/{old,new}/`（`summary.json`、`r*-*.png`）。
- 未验证：Windows 深色主题下的真机效果（不改测试机系统设置；只有本地测试）；冷启动（托盘也没开时双击图标）走的是同一个窗口进程，没单独截。

## 第 10 条：改连另一家公司时第一次配对超时、两边都断

- 核实（测试机日志 + 生产 nginx 计时日志，只读）：
  - 10:56:49 UTC 同一秒：ccdemo `companion.api.register`（对话页要新密钥）与 testcowork `device_api.leave`（吊销旧密钥）；10:56:51 起
    testcowork 全部 401。
  - 10:58:05 ccdemo `heartbeat.report` **499 rt=10.211**（CC 10 秒读超时先断开）→ 「配对失败」；同一时刻同一连接上另一台电脑的
    `lease` 也 499 / 8.2 秒、`next_request` 10.7 秒（被这次心跳锁住连接行）。
  - 11:01 用户点重试：register 两次（exists → rekey created），11:02:06 `heartbeat.report` **200 rt=9.081**（差一点又超时）；
    11:02:17 第一次例行整份心跳又 499 / 10.28 秒，之后只报「没变」的心跳 0.05 秒左右。
- 根因：
  - (b) 两边断：**客户端顺序**。`desktop_settings.pair(switch=True)` 先对每个原成员 `device_api.leave`（吊销）并删成员，再去配对新公司；
    新的没配上，旧的已经没了。服务端发新配对码不碰别家租户的密钥。
  - (a) 读超时：**服务端处理慢 + 客户端超时短**。不是冷连接、DNS、TLS（nginx `urt` 9.9 秒，全在应用里）。这台电脑头一回把 679 份账套
    整份报给新连接时，`collector.ingest → pool.apply_report` 逐行建账套登记与可达行（每行一次 Frappe `insert`，上千次），持着连接行锁
    处理约 9–10 秒；CC 所有请求都是 10 秒读超时，配对那一下没有重试。
- 修法：
  - 客户端 `desktop_settings.pair`：改连别家时先记下原成员的站点与密钥，**新连接配对成功后**才吊销（发到原站点）并删原成员、重启取任务线程；
    配对没成，原来那家照旧连着、什么都不动。改连时不再按端点号去认原成员。
  - 客户端 `pairing.pair`：配对那次整份心跳遇到超时、网络断、`RemoteProtocolError`、502/503/504 自动再试（共 3 次，间隔 2、5 秒）；
    401/403/404 不重试。
  - 客户端 `queue_client.health`：带整份账套名单的心跳读超时 60 秒（`FULL_REPORT_TIMEOUT_S`），只报在线的心跳与领取、回报仍 10 秒；
    看门狗阈值 180 秒，不受影响。
  - 对话页（`frontend/assistant`，随服务端镜像发）：`needsKey` 认「本机成员是别家公司的」也要新密钥（第一次没配上、再点时服务端回
    exists，旧逻辑会当成好着、不配对）；设置「连接」里改连那一行在「正在连接…」「连接失败 + 重试」时照样显示（原来只在 other_site 时显示，
    新顺序下配对失败时旧成员还在，失败原因会看不到）。只用现有文案（`connecting` / `linkFailed` / `retry` / `switchTo`），
    `check-copy` 新增 0、改动 0。
- **发布顺序：先部署服务端（带这次的对话页），再发 CC 桌面版**。新对话页 + 旧 CC：旧 CC 失败后没有成员，`needsKey` 本来就是真，行为同前；
  新 CC + 旧对话页：第一次就配上（多数情况，现在有重试与 60 秒）没区别，但若三次都没配上，再点「改连」时旧对话页会因 exists 停在原地
  （原来那家仍连着，不会两边都断）。服务端接口未改，旧客户端照常。
- 验证：
  - `test_one_computer_one_company_another_company_needs_an_explicit_switch`（改写）：配对失败 → 原成员还在、没发吊销、不重启线程；
    配对成功 → 新成员存下之后才吊销、吊销发到原站点 `testcowork`、原成员删掉。旧代码失败。
  - `test_a_read_timeout_on_the_pairing_report_is_retried_by_itself` / `test_pairing_gives_up_after_the_retries_and_does_not_retry_a_refused_key` /
    `test_a_report_with_the_whole_account_list_waits_longer_than_a_plain_heartbeat`：旧代码失败。
  - `tests/test_assistant_ui.py::MultiDesktopBehaviourTests::test_switching_company_keeps_asking_for_a_key_while_the_old_company_is_still_connected`
    （真跑对话页 TS）：旧 `desktop.ts` 失败；对话页 `tsc` + `vite build` 通过。
- 未验证：真机 / lab 上的「改连租户」全流程。要两个 Cowork 站点：测试环境一个产品栈只有一个测试租户（testcowork），另一个只能用
  ccdemo（录制用演示租户，连着录制笔记本）或真实租户，按任务书不碰；生产上也不试。服务端心跳的耗时没有在 lab 复测。

## 附：询问框等人点时看门狗判卡死

- 核实：测试机日志 19:47:31 `侦测到 poll loop 卡死 190s → 自愈重启 worker`，紧接着 `Companion 任务状态异常 … 'IV6909-0001'`；
  19:50:31 再一次（180 秒）；19:50:47 Express 关掉后这张写成。
- 根因：`PollWorker._ask_busy` 在取任务线程里每 0.2 秒等一次人的回答，期间不盖存活时标；看门狗（阈值 max(90, 3×心跳)=180 秒）把等人
  当成卡死、重启线程。旧线程停下时 `_ask_busy` 回「abort」，这张单按出错报一次；新线程领到同一张又弹一次框（框还叠着）。
- 修法（`gui_tray_workers.py`）：等人回答的循环里照常 `self._live.tick()`。只有在这个循环里（线程活着、在等人）才盖；卡在写盘、网络上
  照旧不盖，自愈不变。
- 验证：
  - `test_waiting_for_someone_to_answer_the_busy_prompt_is_not_a_stall`（假时钟走 5 分钟，询问在另一线程等）：旧代码 240 秒判卡死，新代码不判；
    答完后不盖时标照样判卡死。
  - 测试机真机（Python 3.11 + PySide6 6.11.1，真 Qt 事件循环、`PollWorker._ask_busy` 在 QThread 里等、托盘同款原生询问框、
    同款 15 秒巡检，阈值 180 秒，200 秒后脚本点「重试」）：旧源码（main）在 180.6 秒、195.6 秒两次判卡死；新源码 0 次。
    脚本 `~/pearnly-devtools/cc-runtime-fixes/busy_prompt_watchdog.py`，结果 `frames/watchdog-{old,new}.log`。
- 未验证：Express 真开着同一份账套、服务端真派单的全流程（要造一张推到测试机 C 盘账套的单，本次没做）。

## 验证汇总

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 完成 | `3687bfc4` |
| 本地验证 | 通过 | companion 全量 1020 passed / 42 skipped（Windows 专属）；Development checks 全部 run 步骤 109 项本地通过；对话页 build 通过；7 个新回归测试（companion 6 个、对话页 1 个）在旧代码上全部失败 |
| CI | 通过 | Development checks 39441626789、Companion checks 39441626830（含打包与启动冒烟）均 success |
| 部署 | 未执行 | 按任务书不合入、不发布、不部署 |
| 外部业务结果 | 未执行 | 改连租户全流程、Express 占用全流程见上 |
| 真机 | 部分通过 | 白屏、看门狗在测试机通过；改连租户未做 |

## 测试机使用与恢复

- 10-20 21:50–22:07 持锁 `~/pearnly-devtools/locks/testpc.lock`（已删）。
- 装测试包前备份 `companion.json`（hash `849382E4…`）与 `HKCU\Software\Pearnly`；用 CI 测试包（`3d7ae011…`）量白屏，之后以 skin3 普通权限
  装回官网 1.9.1（`2923082a…`，与 latest.json 一致，exe 为 10-19 21:58 正式版）。装回后 `companion.json` 哈希与备份一致、注册表与备份 0 差异，
  托盘 `--background` + 窗口进程照原样开着；测试包期间心跳正常（101 份，Accserver 夜间下线照旧）。
- 测试机上本任务的临时目录 `C:\PearnlyStress\cc-runtime-fixes`、备份 `C:\PearnlyStress\_备份\cc-config-2026-10-20-runtime-fixes`（配置未变）、
  临时计划任务 `PearnlyCCRt*` 均已删除；`PearnlyRec2Agent`、`PearnlyRestoreCompanionConfig` 不是本任务的，未动。

## 剩余问题与下一步

- 服务端：整份账套名单的心跳在「新电脑 / 新密钥第一次报」时逐行 `insert`，679 份约 9–10 秒，并持着连接行锁挡住同连接其他电脑的领取
  （生产 10:58、11:02 lease 8.2 秒 / 499）。客户端已改成等得住，但锁等待仍在；建议另开工作包把 `pool.apply_report` 的新登记 / 可达行改成批量写
  （要过多台电脑的认定规则与锁顺序，需 lab 验证）。
- 合入：开发窗口按「主线集成」合入 main；发布顺序先服务端（对话页）后 CC 桌面版。
