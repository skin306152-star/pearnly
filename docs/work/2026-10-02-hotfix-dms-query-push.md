# 热修：DMS 菜单 5 查询读完推不出结果（2026-10-02）

分支 `codex/hotfix-dms-query-push`，基线 main c5092f48。压测窗口转用户：真实客户（ris.pearnly.com，LINE 操作员 0868892228_Korn）
曼谷 19:40、19:44 两次按交车日期查询，只收到「กำลังอ่าน…」，之后既没结果也没失败提示。按「线上坏了先止血」只修最小必要。

## 证据（生产只读，一次 SSH，未 exec 进数据库）

- dms-sync-worker：12:41:16Z、12:44:46Z 两个 `pearnly_dms.snapshots.manual.execute`（EP-00004，dataset `query.…`）读取都
  `Successfully completed`；紧接着都是 `DMS query result push failed`：`query/snapshot_sync.py:257 _push_result` →
  `query/query_flow.py:291 _run_records` → `KeyError: 'limit'`。ris 的 Error Log 同时段 0 条（只打了 logger.warning）。
- 原因：`_push_result` 用保存下来的条件当查询参数；那份条件是 `query_parameters` 规整过的业务条件，按设计不含每页条数 / 页码，
  `_run_records` 取 `params["limit"]` 就报错，外层 except 吞掉、不给操作员任何消息。
- 同时段 `adapter/parallel_reads.py:51`（`_ReadOnlyTransport` 没有 `_rc`）是另一件事：只读批量时并行读客户详情建不起来、自动退回
  逐个读，结果不受影响，只是慢；进 batch-1003。

## 修法（只改 `query/snapshot_sync.py`）

- 推结果用发起人**当前会话**里那份完整参数（含每页条数、页码，与 `_same_query_session` 比的是同一份）；会话已不是这组条件就不推（原设计）。
- 推不出去：记 Error Log「DMS query result push failed」，并给发起人推一句 `TXT_QUERY_FAILED`（现有术语表的「查询失败请重试」）。
- 不给客户补发 19:40 那两次（数据可能过时、占推播额度）；由用户通知客户重试。

## 验证

- `tests/test_dms_query_push.py`（候选离线道，生产镜像）：保存条件里没有 limit 也能推、推送报错时操作员收到失败提示并记 Error Log、
  会话换了条件不推——3 项通过；Development checks 同名 Python 步骤本地全过。

## 进 batch-1003（不进热修）

「นานจัง」等结果期间的自由文本掉进建档（`text_router.nudge`）、日期区间（DMS 交车日期只能按单日子串查，区间要明确提示）、
查询总时限与各失败出口都给终局回复（`manual.execute` 终态 failed、`_guard` 失败、RQ 超时、卡在 queued/running 超 15 分钟）、
计数接口 `ndt::` 当 0、`parallel_reads` 只读传输。
