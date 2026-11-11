# 2026-11-11 线上压测第二轮（写入格 + 40MB 并发上传）

状态（11-11 准备完毕）：脚本已备好、离线干跑通过，语料已就位，5 项前提主会话已定；**还没对生产发任何请求**，等主会话「部署完成、开始压测」。
第一轮（只读格）见 [2026-11-08-prod-stress-100.md](2026-11-08-prod-stress-100.md)「11-10 第一轮结果」。

窗口：线上压测第二轮（主会话 local_74a4f119 派）。只用生产测试租户 testcowork / testerp 和 e2e 账号（e2ecowork / e2eerp）；
写入只写测试账套；不绑 LINE、不碰真实操作员。

## 基线
- 代码：分支 `pearnly/stress-r2-c38409`，基于 origin/main 9ba528f45，带上第一轮分支 `pearnly/prod-stress-100`（脚本 + 第一轮记录，还没合入 main）变基后的 4 个提交。
- 今晚这批已在 main、和本轮有关的：P0「CC 只写对话选中公司的账套」（`pearnly_cowork/assistant/write_scope.py`、7b6a305b5）；ERP 对话收文件、ERP 代录收文件（cb66cbb44、a0caf85bf）；
  上传上限统一 40MB（ef46a95a0 等）；网页容器内存 Cowork 3072m / ERP 2560m（06bb2e600，生产还要改 runtime.env 才生效）；ERP 代录列出别的月份的单（0372c19c5）。
- 不在 main：一句话下令 `pearnly/cc-write-plan`（所以今晚代录仍是开始卡点「开始」）、本公司税号问一次 `cc-own-tax-once-2`、核账本 `cc-ledger-check-2`。
- 生产版本：开跑前由主会话通知部署完成，开跑时记 release 标记 / SHA。

## 主会话 11-11 已定
1. 选 A：测试机 CC 位置与数据根临时只留 `C:\PearnlyExpress\70EXP`（两种成员都给），默认账套 cowork = T9SINCER、ERP = T9GIZ；`\\accserver` 上任何账套都不写。
   做法：先备份 companion.json（原哈希 7D2EE159，备份在 `C:\PearnlyStress\_备份\cc-config-2026-11-11-stress\`）；改之前登记占用表，确认没人在用；
   以 skin3 普通权限 `--background` 重启 CC。压完换回备份，核哈希。
   脚本：`~/pearnly-devtools/test-window/round12/stress-r2/cc_cfg.py`（show / stress / pause / restore）和 `cc_restart.ps1`（停 CC → 改 → 一次性任务拉起 → 删任务 → 核进程）。
2. S8a 跑前暂停 testcowork 档案。服务端万一放行，就把排队的请求删掉、恢复档案，并立即停下报主会话：这算 P0 缺陷。
3. ERP 写入同意：恢复 testerp 档案，写 C 盘 T9GIZ（GIZE 进项税票）；压完把档案改回暂停、位置还原。
4. 「一个月超过 50 张」的修复已在 db294b485 上线。一句话下令（cc-write-plan）在今晚第 11 车：代录与直写都改成 CC 复述 → 点「录吧」，脚本已按这个改，旧开始卡留作兜底。
5. 40MB 并发：5 → 10 → 15 份。网页容器新内存值随今晚 runtime.env 生效；开跑前再读一次确认。

## 语料（11-11 从测试机只读复制，放本会话 scratchpad，压完删）
| 文件 | 来源编号 | 页 | 用在 |
|---|---|---|---|
| bigc-jun-soi / bigc-jun-plab / bigc-jul-soi / bigc-jul-plab | 6901887 / 6901888 / 6901907 / 6901908 | 27 / 15 / 23 / 25 | S8 |
| void-jun-plab / void-jun-soi / void-jul-plab（作废票） | 6901891 / 6901892 / 6901914 | 5 / 1 / 6 | S8 |
| bigc-aug-soi / bigc-aug-plab（8 月共 53 张） | 6900294 / 6900295 | 25 / 28 | S9（S0a 用前者） |
| car-sale（卖车税票） | 6900943 | 1 | S8a |
| inv-jul-p1 / inv-jul-p2（电子税票） | 6901909 / 6901910 | 15 / 17 | S9off / S9m |
| inv-jun-p1-10（电子税票前 10 页） | 6901899 | 10 | S10 Cowork 三路 |
| mix-1 承包商发票（30.6MB）/ mix-2 电费 4 张 / mix-3 报价单 + 销货单照片 3 张 | 6900270 / 6900843–45、6900848 / 6900396、6901893–95 | 8 / 4 / 4 | MIX |
| giz-feb（GIZE 2 月进项）/ giz-mar-10（3 月照片 10 张） | 6901164–72 / 6901173–82 | 14 / 10 | S9e / S10 ERP 三路 |
各格用的文件互不重叠，后一格不会只撞上前一格已经写过的票。S8 本身含作废票，S10 是三路同时写同一批票，专门看重复。

## 开跑顺序（收到「部署完成、开始压测」后）
1. 一次只读 SSH：核网页容器新上限、ERP 识别线程内存、memory.events 基数、swap（命令照 2026-11-11-web-container-memory.md 4.3）。
2. S0a、S0b（只读，CC 不用动）。
3. 40MB 并发：先起采样（一次 SSH，600 秒），再跑 Cowork 5 / 10 / 15，然后 ERP 5 / 10 / 15。
4. 登记占用表 → 测试机 CC 改成 A，同时暂停 testcowork 档案 → S8a。
5. 恢复 testcowork 档案 → S8 → S9 → S9off（停 / 起 CC 各登记一次）→ MIX → S9m → S9e → S10。
6. 写入格做完通知 C 线读 U0 日志；测试机 CC 换回备份并核哈希；释放占用表。

## 清单与预估（美元）
「上限」是脚本开跑前的最坏估算：识别按每页 0.04，加本格模型费上限。「预计」按第一轮实数折算。本轮硬上限 32（`--budget 32`），本月台账累计到 120 自动不开新格（`--month-cap 120`）。

| 顺序 | 格 | 做什么 | 前提 | 预计 | 上限 |
|---|---|---|---|---|---|
| 1 | S0a / S0b | 第 0 格复验（同第一轮，选第一轮那家、只到开始卡 / 只识别不录） | 部署完成 | 约 2 | 2.6 |
| 2 | UPc / UPe | 40MB 并发上传：Cowork、ERP 各按 5 → 10 → 15 份同时放附件、不发消息 | 部署完成；生产采样一次 SSH | 0 | 0 |
| 3 | S8a | P0 实证：直写说录进 Express，再要求改写进别家账套（`PEARNLY_STRESS_OTHER_BOOK`），看服务端拒不拒、CC 怎么说 | 第 1、2 项 | 约 1 | 1.4 |
| 4 | S8 | 直写真推送：7 份 102 页，按推荐答完提问直到写完，写后回读 | 第 1 项；测试机 CC 在线 | 约 6–7 | 7.6 |
| 5 | S9 | 代录全量：8 月 53 张（超过 50 张）→ 开始前一次问完 → 开始 → 问进度、跳过、停止 | 同上；第 4 项 | 约 4–5 | 5.1 |
| 6 | S9off | 代录中电脑离线：开始后停测试机后台 CC，约 5 分钟后再起 | 同上；重启 CC 要批 | 约 1 | 1.4 |
| 7 | MIX | 混合：采购和销售混在一起、多个对方、新对方、带物料行 | 同上；别家语料拷到本机 | 约 3 | 3.7 |
| 8 | S9m | 代录（Cowork 手机，中文）25 页 | 同上 | 约 2 | 3.0 |
| 9 | S9e | 代录（ERP 电脑，中文）15 页 | 第 3 项 | 约 1.5 | 2.6 |
| 10 | S10 | 并发写：两租户各 3 路同时直写同一份 10 页（同一张票三路同时写，看会不会重复写） | 同 S8 + S9e | 约 4–5 | 8.4 |
| | | 合计 | | **约 25–29** | 36（硬上限 32 会挡住排在最后的格） |

- 本月台账现约 63.6，跑完约 89–93，没到 120。
- 写入格都要加 `--allow-tomorrow`。写哪家用 `PEARNLY_STRESS_WRITE_COMPANY` / `PEARNLY_STRESS_ERP_COMPANY` 给，没给就跳过。
- `~/pearnly-devtools/e2e-runs/stress-spent.json`（第一轮 19.159）开跑前归档成 `stress-spent-round1.json` 再清零。

## 脚本（本轮改动）
- `scripts/e2e/stress.py`：
  - 加第二轮写入格。
  - `auto`：替用户走一步：提问卡选第一项（推荐）再点下一步、批准条点允许、代录开始卡点「开始」；每一步点了什么都记进结果的 `acted`。
  - `probe_real`：P0 实证格见到别家账套不停，记下服务端怎么回。
  - 推送工具也认 `erp_push_documents`；真账套特征也查 `connection` / `target`。
  - 本月总闸 `--month-cap`；`--list` 列出每格前提。
- `scripts/e2e/stress_upload.py`：
  - 合成 PDF：40 页噪点图，40,809,031 字节，低于 41,943,040 的上限，pypdf 能读出 40 页；没有任何票面信息。
  - 每个产品登录一次。N 页都就绪后同时放附件，记每份的秒数、状态码，并核服务端会话文件的大小（丢件）。
  - 传完走正式删除接口删对话，并读回。
- `scripts/e2e/stress_upload_sampler.sh`：生产上只读采样，用一次 SSH 长连接，到时自己退出。每 2 秒记 cowork-web / erp-web 的 cgroup 匿名内存、`memory.events` 的 max 与 oom_kill、整机 swap；首尾各记一次 docker stats 和 free。
  用法：`ssh pearnly 'bash -s' -- 600 < scripts/e2e/stress_upload_sampler.sh > samples.tsv`。
- 测试：`tests/test_e2e_stress.py`（15 条，加写入格答卡点开始的假页面一条），`tests/test_e2e_stress_upload.py`（6 条，假页面同时传 3 份、认出丢件、删除）。两个文件都登记在 Development checks。
  本机 `STRESS_BROWSER_TEST=1` 时真 Chromium 全过；另用真 39MB 文件对假页面同时传 15 份，15/15 通过。

## 40MB 并发上传怎么判
- 每波记：p50 / 最长秒数；状态码（413 / 502 / 504 / 超时）；丢件（200 但服务端会话文件大小对不上）；页面报错。
- 生产采样：cowork-web / erp-web 匿名内存峰值占上限的比例；`memory.events max` 的增量（撞上限次数）；oom_kill；swap 增量。
- 过：15 份同时传全部成功、没有丢件、oom_kill 0、max 增量为 0 或个位数、swap 不涨。
  不过时按 C 线建议，再定「每进程同时处理大文件份数」限流（11-11 主会话：有实测问题再做）。

## 结果

（开跑后逐格填。）

## 收尾要做
- 测试租户：本轮建的对话与识别记录用正式接口删，读回为 0。上传格的对话由脚本当场删并读回。
- 本机：SINCERE 与混合语料、合成 PDF、pypdf 临时环境都在会话 scratchpad，压完删并读回。
- 测试机：语料临时目录、计划任务删掉；CC 配置按备份换回并核哈希；释放占用表。
- 写入格写完通知 C 线（local_0764c7f8）读 U0 日志（`pearnly.assistant.upload`）。
