# Cowork 识别提速下一批（2026-10-02，分支 codex/cowork-speed-next）

目标：用户经压测窗口（m7 之后）定的：不花钱的提速都做，攒成一批上线；100 人同时用时单张从上传到出结果的中位从 22–27 秒降到 12–15 秒。
基线：main aaba2585。交修复窗口进批次，过金标关①，一次部署。

## 改动

| 项 | 提交 | 内容 | 读法 / 结果 |
|---|---|---|---|
| ② 推送提前 | 02039da0 | 结果入库（已提交）就推「有结论」给等候页（`work.notify_result`），扣费放后面；以成功收尾时不再推第二次 | 不变 |
| ④ 列表计数索引 | 0cd13683 | Cowork OCR History 加 `workspace_client_index`，补丁 v0_0_12。用一次性库副本验证：计数从整表扫描 40 ms 降到只读索引 16 ms | 不变 |
| ③ 入库拆开计时 | 4645a1bc | persist 内部分步计时，计时行新增 `persist_prepare / lock / source / verify / names / duplicates / insert / bind / targets / save / commit / announce`；下一轮压测据此定怎么改 | 不变 |
| ① 派发循环变轻 | 本提交 | 见下文 | 不变 |

### ① 派发循环变轻

- **m7 实测**：识别进程里每个 RQ 线程先按原生 `execute_job` 完整进一次站点，即 init、连库、before_job、提交、after_job、destroy，到 `work.run` 才把这份交给线程。这一趟空转闲时 0.07 秒，高峰 0.75–0.81 秒（和二十几个识别线程抢解释器锁），每个进程只有一个 RQ 线程串行做，高峰时任务在队列里等 15–20 秒（p95）。
- **新做法**：`pearnly_core/execution/ocr_worker.py` 的 `OcrWorker` 是原生 `FrappeWorkerNoFork` 的子类，只换任务执行体。
  - 取到 `work.run` 后，`pool.relay` 不进站点，直接交给空闲线程；线程里照原生 `execute_job` 跑（自己的站点上下文、连接、钩子、死锁重试都不变）。
  - RQ 的登记、时限、心跳、结果保留照旧。
  - 等不到空闲线程时，照原样在 RQ 线程里跑，并算作识别线程，`work.run` 不再二次等位。
  - 其它任务、没开线程、fork 模式一律照原生跑。
- **启用条件**：`python -m pearnly_core.execution.worker --queue ocr --num-workers N`，不 fork 且 `PEARNLY_OCR_THREADS>1` 时选用。
  - `infrastructure/production/compose.yml` 和测试机 override 的 cowork-ocr-worker 改用这个入口。
  - DMS / ERP 识别容器不动。
  - `work.run` 里原来的 `handoff` 保留，给仍用 `bench worker-pool` 的容器用。
- **进程数 4→8**：修复窗口在 codex/cowork-ocr-procs（15cca214）做，8 进程 × 12 线程，内存按本窗口给的 m7 峰值核算。两项叠加，互不冲突。

## 验证（本地，未部署）

- 生产镜像 4e80514d 挂本分支源码，跑完整 `ci-candidate-checks.sh offline`：通过。新增 / 改动的测试都在里面：
  - test_pool：relay 交线程且 RQ 线程不进站点；其它任务照原生；等不到线程时就地跑；类只换执行体。
  - test_worker：ocr 队列选类的条件。
  - test_cowork_core_notifications、test_cowork_document_manifest 跟着 02039da0 / 4645a1bc 更新。
- 真 Redis + RQ 2.6.1 实验：5 份 `work.run` 每份 1–3 ms 交出，线程名 pearnly-ocr；RQ 线程里 0 次 `frappe.init`；其它方法照原生在主线程跑；6 份全部记完成，失败 0。
- 端到端：`python -m pearnly_core.execution.worker --queue ocr --num-workers 2 --burst` 起原生 WorkerPool，子进程用 OcrWorker，每份 1.5 ms 记完成。
- `check-architecture.py`、`check-production-capacity.py` 通过。

## 未做 / 待定

- ⑤ 误触发复核：等修复窗口的逐页金标结果，需要知道哪些复核前后值没变，再按类别收窄。
- ⑥ 逐页出结果、第一屏方案：只出方案。
- ⑦ 按时段选入口：数据不支持，不做。全天各时段都是 global 最好；US/EU 样本有选择偏差、429 偏高；只有约 1.5 天测试数据。
- 网页 p95 约 2 秒：和修复窗口一起查。
- 缺：测试租户承压、下一轮 100 人压测对比（排队等待、persist 分步）、部署。
