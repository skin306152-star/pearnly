# 三租户压测：开始对账报 1020、直传偶发"没收到文件"（2026-10-01）

压测窗口报（生产 main `2bbcb4ee`，testcowork 1–3 同时各 35 套对账 + 100 张直传发票）：105 套里 24 套在 `recon_center.jobs.start`
报 `QueryDeadlockError(1020, "Record has changed since last read in table 'tabCowork Source File'")`；同期锁等待峰值 39 个事务、最长 27 秒。
分支 `codex/source-lock-scope`，基线 main `12ab7a05`。

## 1020 根因

- Cowork 原件保护（`files/guard.py` 的 `mutation` → `files/access.py` 的 `protected_sources(include_hash=True)`）在每次写任何 File 时都跑
  （新 File 约 5 次：before_insert 两次、before_validate、validate、save_file 前后）。对账存文件、页图、缩略图都走这里。
- 它是一条 `SELECT DISTINCT … FROM tabCowork Source File s LEFT JOIN tabFile … WHERE 多个 OR … FOR UPDATE`：执行计划 type=ALL，
  整张原件表逐行加锁（连带 File 行）。数据库 MariaDB 11.8.9、`innodb_snapshot_isolation=1`：本事务已经读过（有快照）后，
  只要扫到同租户发票直传改过的原件行就报 1020；没报的就排队等锁。
- 本机样板复现：一个事务先读，另一个事务改一行原件并提交，再对一个无关的新文件跑这条查询 → 1020。

## 改法

- `protected_sources` 拆成几条按索引的精确查找：File 侧（`file_url`、`content_hash`）不加锁读出文件名，原件侧（`stored_file`、
  `storage_url`、`name`、`native_content_hash`）只锁命中的行。判什么算原件不变；不带 `include_hash` 的读照旧不加锁、不看哈希。
- 补丁 `pearnly_cowork.patches.v0_0_10_source_lookup_indexes`（原生迁移）：
  - `tabFile.content_hash`：原生 `search_index` 属性设置器 + 索引（以后 File 迁移不会把它当未知索引删掉）；
  - `tabCowork Source File.storage_url` 是长文本，Frappe 既不建也不删这类索引，加 255 字符前缀索引。
- `recon_center.api.start` 遇 `QueryDeadlockError` 回滚重试 3 次（与 `files.api.upload` / `finish_upload` 一致；入队是最后一步，之前失败不会重复排任务）。

## 直传"没收到文件"（417，约 1%）

- `files.service.finish_direct` 有两处报同一句话：Cloud Run 读不到对象（`original_missing`），或读到的文件名 / 扩展名 / 大小 / 类型与声明不一致。
  只看代码分不出是哪一种。这次先在日志里分开记（原因代码 + 对不上的字段名，不记文件名和内容，`pearnly_cowork.files.service` 的 WARNING），
  下一轮压测据此定位，再修根因。

## 验证

- 本机样板（MariaDB 11.8.9）：现有 62 个 File 新旧两版判定完全一致（加锁 / 不加锁两种）；上面的复现场景旧版 1020，新版（带补丁的两个索引）不报。
  临时加的索引已删、改过的 `modified` 已恢复。
- `tests/test_cowork_source_lock_scope.py`（加进候选离线道）：查询只有精确 IN、只锁原件表、判定与原来一致、补丁内容、开始对账重试；
  `tests/test_cowork_direct_upload.py` 新增两种"没收到"分开记；`test_cowork_recon_scanned_docs.py`（118）、`test_cowork_document_manifest.py` 通过。
- 还缺：分支 CI、生产镜像候选离线道（这次没改 pearnly_core）、补丁在真实站点迁移（测试机）、部署后压测窗口同场景复测（开始对账失败数、锁等待峰值）。

## 第 3 项（上传 / 页图 / 原件在并发下慢）

- 待核实，不在本分支。核对方向：`finish_upload` 同步等 Cloud Run 读原件、`page` 同步等 Cloud Run 出图，等待期间释放了数据库连接（`pool.release_db`），
  但 gunicorn 线程（2 进程 × 4 线程 / 容器）一直被占着；300 路同时上传时，只做 302 的 `original` 也要排队。另外 1020 修好后锁等待会少，要先复测再定。
