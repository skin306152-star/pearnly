# 识别检查点按行存（2026-10-01）

分支 `codex/ocr-checkpoint-rows`，基线 main 29838385。LINE 窗口与修复窗口定由压测窗口做（数据库内存的根治）。

## 原因

- 生产 MariaDB 两次被内核按内存杀掉：22:36（上限 2g，mariadbd anon-rss 2.08G）、00:49（上限 3g，2.54G；直接触发是对容器
  docker exec 的客户端）。启动后固定约 1.41G（缓存池 1G 等），多出的是连接级内存。
- `work.checkpoint` 每次调用都把这份 Work 的全部检查点 JSON 整块重写进 `tabPearnly OCR Work.checkpoints`，多页文件几 MB；
  对账等识别时（`document_ocr.wait`、`invoice_ocr.wait`）每 5 秒把整块 JSON 读回来找结果、数调用。

## 改动

- 新 DocType `Pearnly OCR Checkpoint`（Core）：每次调用一行，`name = sha256(work \0 key)`，`work` 建索引；写入用
  `INSERT … ON DUPLICATE KEY UPDATE`，同一调用再写（started → done / rejected）覆盖本行。
- `work.checkpoint(key, state, body)` 签名与语义不变（LINE 窗口的 Cloud Run 通过同一接口切换存储位置）；每次运行仍只读一次。
- 新读接口：`stored_checkpoints(name)`、`saved_checkpoint(name, key)`（按主键）、`done_calls(names)`（按行计数）。
  `run()` 判残留 started、`has_completed_call` 改用它们。
- 旧格式兼容：Work 上的整块 JSON（部署前开跑的任务）读时作底、新行覆盖，计数不重复；不需要停机迁移数据。
  `prune_checkpoints` 另删 7 天前成功 / 取消的任务的行；失败 / 结果不明的保留作证据（同原规则）。
- Cowork 对账：`document_ocr.wait` 取结果、`completed_calls`，`invoice_ocr.wait` 数调用、取结果，改用上面的接口。
- 实验室脚本 `dms-line-lab-ocr-roundtrip`、`dms-line-lab-queue-load`、`dms-line-lab-dispatch-recovery`、`ocr-multipage-stress`
  改用 `stored_checkpoints`。

## 验证

- 离线（生产镜像一次性容器、断网）：`pearnly_core.execution` 31 条；`test_cowork_recon_scanned_docs` 118 条；
  `test_cowork_provider_runtime` 19 条，通过。
- 测试环境真实数据库（独立进程加载本分支代码，不切换测试环境源码；新表只加不改，合成工作单用完删除）：15 项全过，含建表与
  `work` 索引、覆盖不新增行、不再写整块 JSON、旧格式作底与覆盖、计数不重复、清理范围、对账读取接口。

## 待办

- 生产镜像完整 `ci-candidate-checks offline`；合入前告诉 LINE 窗口和修复窗口；部署走原生迁移（新表），每一步用户同意。
- 部署后看 MariaDB 峰值内存（目标不超过上限 70%）。
