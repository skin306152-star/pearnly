# Cowork 发票识别搬到 Cloud Run（2026-10-01）

- 分支 `codex/ocr-cloud-run`，基线 `origin/main` b5858198（含检查点按行存、受理去全站锁）。决定见 [ADR-015](../adr/0015-ocr-on-cloud-run.md)。
- 用户目标：几百人同时传几千份，只等识别本身的时间，不排队，不明显加成本。用户同意：谷歌云按量小额费用；识别结果（不含原图）
  在新加坡存储桶放 7 天自动删除；不开任何定时任务 / 常驻实例（「不要再 run 开任何定时任务循环烧钱」）。
- 浏览器直传谷歌存储（原件存到谷歌）没做：那是原件存放位置的变化，需用户另行决定；先实测上传在我们服务器上的耗时。

## 已建的谷歌云资源（项目 pearnly）

| 资源 | 设置 |
|---|---|
| 镜像仓库 `asia-southeast1/pearnly`（Docker） | 清理策略：只保留最近 2 版 |
| 存储桶 `gs://pearnly-ocr-checkpoints-sg` | 新加坡、统一访问控制、禁止公开、对象 7 天自动删除 |
| 服务账号 `pearnly-ocr-sa` | 新增：该桶 `roles/storage.objectUser`；Cloud Run 服务 `roles/run.invoker`。没有新增模型 / OCR 权限 |
| Cloud Run `cowork-ocr` | 新加坡；2 vCPU / 4 GiB；并发 8；最少 0 / 最多 20；600 秒；启动加速；不公开（未授权请求 403） |

URL：`https://cowork-ocr-h6qxkkwz4a-as.a.run.app`。更新用 `scripts/cloud-run/deploy-cowork-ocr.sh`（只部署已提交代码）。

## 实测（合成发票，直接调 Cloud Run，真实谷歌；客户语料未离开测试机）

- 单张：字段全对、未标复核；云内 9.9–12.8 秒；4 次调用（Gemini 3.1-flash-lite ×3、Vision ×1），与本机同一调用顺序。
- 40 张同时：40 / 40、40 / 40、39 / 40；5 个实例；云内单张中位数 10.6 秒、P90 13.7 秒、最长 23.2 秒。
- 每张 CPU：中位数 0.23 秒、最长 0.57 秒（生产服务器上约 2 秒：每次调用前的权限查询、深拷贝、证书库解析与更慢的 CPU）。
- 慢的在谷歌：全球入口 2 次 429 立即转美国入口；其中一次美国入口 60 秒无回应 → 结果不明（与本机同一规则，未自动重付费）。
- 冷启动：服务空闲 16 分钟缩到 0 后第一个请求 2.92 秒（之后 0.13 秒），即冷启动约 2.8 秒；40 张同时发时新开实例的首次调用最晚 5.4–7.6 秒（含从开发机上传）。
- 测试共约 120 张合成发票，谷歌识别费估算约 30 泰铢。

## 验证

- `tests/test_cowork_remote_ocr.py` 15 条：本机 / Cloud Run 结果与计费证据一致、续跑不重复付费、限流转入口 / 交回、断线判不明且不重放、
  同一调用只能一个实例发出、租约、请求校验、流式回复必有结果行。
- `tests/test_cowork_remote_forward.py` 6 条：开关、名额、令牌失败暂缓、断线 / 5xx 暂缓、结果按本机方式抛出。
- 两者已加入 `ci-candidate-checks.sh offline`；`check-architecture.py` 通过；生产镜像候选离线检查：（结果待填）。

## 未完成 / 下一步

1. 生产接入：新镜像部署后，先在 testcowork 打开开关实跑几十页（真实上传→Cloud Run→落库→扣费→推送），再决定其他租户。需用户同意、夜间。
2. 服务器名额：搬到云上后每份只占一个名额（服务器几乎不占 CPU），`pearnly_ocr_budget` 的全站 / 租户并发需按谷歌额度重定。
3. 对账文档（Document AI 区域额度在转发时拿名额）、ERP / DMS（`pearnly_core.ocr.json_from_images`）后续阶段。
4. 每租户 / 全站每日页数上限（花费封顶）随对账阶段加。

## 第二批：服务器等回复不占进程 / 连接（分支 `codex/ocr-remote-wait`）

用户（2026-10-01）：「全部开所有 app，所有 ocr 入口」「一次授权」（每批做完夜间部署并开开关，出问题关开关回退）。

- `pool.release_db()`：转发等 Cloud Run 回复前提交并断开连接；`pool.local_slot()`：本机识别每进程同时最多 `PEARNLY_OCR_LOCAL_SLOTS`
  份（默认 1），Cowork 发票本机分支、对账销项税核查、对账文档的本机读都套上；没开多线程时不起作用。
- `PEARNLY_OCR_THREAD_MB`（compose 三个识别容器，默认 150）：容量检查与按内存回收的每线程余量都读它。
- 上线目标值（Cowork）：识别 4 进程 × 24 线程、每线程 30MB、上限 8g（容量检查已验证放得下）；同时最多 96 份在等 Cloud Run。
