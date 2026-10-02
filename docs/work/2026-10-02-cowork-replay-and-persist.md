# 回放压测工具 + 识别结果入库提速（2026-10-02，分支 codex/cowork-replay）

来源：用户要求剩下的产品问题一次性解决（经压测窗口），分给本窗口两项，都是零费用。做完交修复窗口集成。
基线：codex/cowork-speed-next f4842e60（入库分步计时在这个分支上）。

## 1）回放压测工具

**目的**：压测时不调谷歌、不交 Cloud Run，交回以前真识别过的同一份原件的结果，交回前按实测时延等待。排队、入库、扣费（扣测试额度）、推送、网页都照常跑。

**开关**：只能在测试租户由站点配置打开。
- 用法：`scripts/ocr-replay.py --site <测试租户> on [--profile normal|peak] [--median 秒] [--p95 秒]`，`off`、`status` 同理。
- 配置写进 `pearnly_ocr_replay`，各进程最多 60 秒内生效。
- 时延档：
  - normal：中位 5.8 秒、p95 19.7 秒（m7 实测的 Cloud Run 段）；
  - peak：p95 40 秒；
  - 时延按对数正态分布抽取，上限 300 秒。

**正式站拒绝，分三层**：
1. 运行时：站点名必须匹配 `pearnly_core.execution.replay.TEST_SITES`（testcowork/testerp/testdms 系列和本地实验室）。不匹配就当没开，照常真识别，并每小时记一条错误日志。
2. 架构检查（`check_ocr_replay`）：`TEST_SITES` 不许放宽；开关键只许出现在回放模块、开关脚本和测试里，写进生产配置、部署脚本或产品代码一律拦下。
3. 开着时付费调用一律拒绝（`replay.Refused`）：本机 `provider.post` 和交给 Cloud Run 的 `forward._call` 都会拒绝。没做回放的入口在压测时直接失败，不会悄悄花钱。

**怎么接进流程**：在 `workflow._recognize` 里，回放代替 Cloud Run 和本机付费读（`pearnly_cowork/ocr/execution/replay.py`）。
- 第一次进来：
  - 找结果的顺序：本租户以前真识别过的同内容、同页数的识别单 → 共享回放库。
  - 找到后记检查点（状态 external，与交给云端时同样按“可能已付费”处理排队时限）。
  - 抛 `Deferred("replay_waiting", 时延)`，识别线程立刻空出来，到点由共用层调度放回队列。
- 到点再进来：交回存下的结果和证据，证据末尾加上 `{"replay": 来源}`。之后的校验、复核、入库、扣费、推送都和真识别相同。
- 找不到结果：按 `ocr.replay_missing` 失败，不扣费，不编结果。
- 开关中途关掉：已经开始回放的那几份照样回放完，不会转去真识别。

**和真识别的差别（如实记录）**：
- 没有 Cloud Tasks 回调打到网页的那一下：到点由调度放回队列，不经过 `ocr.remote.callback`。
- 多页文件的进度只在最后一页推一次。
- 预览图不在识别时生成（Cloud Run 没调用）。
- 浏览器直传的原件仍会调一次 Cloud Run 的原件检查（不调谷歌、不付识别费）。

**语料池**（`scripts/ocr-replay-corpus.py`，从测试租户的真实件导出，跳过 stress_ / mu_ / ms_ 开头的合成票和回放出来的单）：
- 2026-10-02 从 testcowork 导出：1044 份，其中图片 548、PDF 496，多页 178（最多 27 页）。testcowork2/3 只有合成票。
- 共享回放库：cowork sites 卷下的 `pearnly-ocr-replay/<sha256>-<页数>.json`，共 1044 个文件、5.7MB，各测试租户共用。
- 原件池：生产机 `/root/pearnly-replay-pool/<sha256>.<扩展名>`，共 624MB，附 `manifest.jsonl`（sha256、文件、类型、页数、字节数），不带原文件名，也不带识别内容。
- 导出时在一次性容器里只读业务库，写的是共享回放库和原件池（`/tmp/pearnly-replay/run.sh`）。

**验证（本地）**：
- 生产镜像 4e80514d 挂本分支源码，完整 offline 闸门通过（47 项）。新增：
  - `pearnly_core.execution.test_replay`：测试租户 / 正式站判断、时延分布（2 万次抽样，中位 5.8±0.25、p95 19.7±1.2）、付费调用拒绝；
  - `tests/test_cowork_ocr_replay.py`：等待与到点交回、只认真件、共享库、找不到时失败、开关关掉后照样回放完、`_recognize` 不走 Cloud Run 也不在本机读。
- 架构守卫测试 20 项通过。
- 还缺：在测试租户实际开回放跑一轮小压测（需要部署这个分支，或在测试机上挂实时源码）。

## 2）识别结果入库慢（中位 2.6 秒、慢的 8 秒）

（分析与修法待补）
