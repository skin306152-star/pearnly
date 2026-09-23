# Cowork 盘点原生后端

工作树：`/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`；本轮基线 `f409bce560df11f78ee5fc7908e71129c4e37a36`。父任务已再次核对旧生产来源仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，固定源码位于 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。最终生产来源复核由父任务统一记录；本工作包不访问生产。

本批只落地本地后端，不接前端、不部署、不发送 LINE、不调用 OCR/ERP、不写库存账。盘点属于 `pearnly_cowork.stocktake`；持久数据是当前 Cowork Site 内的原生 DocType，不直接读写旧 PostgreSQL。原导入、数量和报告计算纯合同继续使用前批已核对版本。

## 来源与新落点

| 固定来源 | 新落点 | 保持的行为 |
| --- | --- | --- |
| `services/stocktake/excel.py` 已解码行校验 | `stocktake/contracts.py`、`native_contract.py` | 六类表头、文本编号/条码、公式拒绝、每商品一次、编号冲突、最多 10,000 行、六位 Decimal、负账面数允许 |
| `services/stocktake/store.py:create` | `stocktake/service.py:create` | 请求 UUID 为任务 ID；原始商品快照只创建一次；重复同请求返回同 ID，不覆盖内容 |
| `store.py:listing/detail` | `service.py:records/detail` | 公司范围、账面/实盘分离、未盘与零分离、差异数量；详情最多前 100 条扫描，独立明细分页 |
| `entries.py:write/_replay/_audit/_total` | `service.py:write` | 任务串行锁；创建版本 0；修改版本加一；operation UUID + 完整规范请求 + 操作者回执；作废保留审计；未作废明细求和；累计超限整笔回滚 |
| `store.py:close` | `service.py:close` | 关闭幂等；关闭后禁止新操作，相同已成功 operation 重放仍返回原回执；关闭不写库存账 |
| `reports.py:workbook` 的计算部分 | `service.py:report` + `calculations.py` | 锁定一致快照、商品差异/地点比较、全部扫描与作废明细，输出 JSON；不是 XLSX/照片导出 |
| `services/stocktake/access.py` | `stocktake/access.py` | 当前已启用用户、购买功能、角色和公司范围由服务端校验；旧身份/RLS 改为原生 Site + User Permission |

本批明确的接口边界：`create` 接收 JSON `header` 与 `rows`，每个单元格为 `{value, data_type}`，再次调用原行校验。`source_format=decoded_rows_v1` 的摘要是**规范化行快照**的 SHA-256，不能当作旧 XLSX 文件字节摘要；旧文件请求 ID、文件导入和历史数据迁移均未开放。这是新后端入口的内容幂等合同，不是对旧上传接口兼容完成的声明。调用方不能用 `data_type=s` 给数值编号冒充原文本编号。

## 原生结构与入口

四个 DocType 都在 `pearnly_cowork/doctype/cowork_stocktake_*/`：

- `Cowork Stocktake Task`：公司、名称、导入摘要/格式、scan/legacy、active/closed、创建与关闭操作者/UTC 时间。
- `Cowork Stocktake Item`：任务内不可改的商品与账面快照、实盘合计、版本和最近盘点操作者/UTC 时间。
- `Cowork Stocktake Entry`：任务/商品、实际库位、精确数量、作废标记、版本、原创建与最近修改操作者/UTC 时间。
- `Cowork Stocktake Operation`：不可修改的 operation 请求、前后状态、操作者、entry 和返回版本。

数量字段存规范 Decimal 字符串，避免 Frappe Float 转换；实盘空值是未盘，`0.000000` 是真实零。全部计算用 `Decimal`，原生列表数量比较以 `DECIMAL(20,6)` 进行。用户不能通过 DocType 通用写入声明来源、改数量、改版本或伪造审计。

RPC 前缀为 `pearnly_cowork.stocktake.api`：

| 方法 | 输入 | 输出/限制 |
| --- | --- | --- |
| `create` POST | workspace, title, request_id, header, rows | `{id}`；只创建 scan |
| `records` | workspace, start=0, limit=50 | `{tasks}`；页长最多 100，每任务带 total/count/differences（字段为 `total/counted/differences`） |
| `detail` | task_id | 账面快照、合计、前 100 条 scan、总明细数、有效地点 |
| `entries` | task_id, start=0, limit=100 | `{entries,total}`；包括已作废明细 |
| `add_entry` POST | task_id, item_id, request_id, quantity, warehouse, location="" | `{ok,entry_id,version}`；初始版本 0；非空 photos 明确拒绝 |
| `edit_entry` POST | task_id, entry_id, request_id, quantity, warehouse, version, location="", voided=false | 同回执；拒绝过期版本；允许作废/恢复并保留历史；非空 photos 明确拒绝 |
| `close` POST | task_id | `{ok:true}`；重复关闭不改关闭人/时间 |
| `report` | task_id | scan 全量 JSON 和 `summary`；不是外部库存过账 |

本批不公开 legacy 的直接改实盘 API，不把 legacy 当 scan。已有 legacy 任务可详情读取/关闭，scan 新增、修改和本批 scan JSON 报告返回 `stocktake.legacy_task`。生产旧任务数据的导入尚未迁移，测试中只用标记清楚的合成旧任务验证隔离。

## 权限、事务与调用方向

`api → service → documents/access + 纯 contracts/calculations/native_contract`。`access → access.scope.contains/grants/administrator` 只复用公司授权基础；`access → core.shell.entitlements` 只读本站购买功能。没有反向依赖、外部执行器或外部管理权限扩展。

所有 API 读写要求 `pearnly_product=cowork`、`stocktake` entitlement、用户 enabled、`Cowork Stocktake` 或 `Cowork Admin`，以及有效 workspace 范围。管理员只能在本站业务授权范围内操作盘点，没有推送/真实 LINE/OCR 执行能力。

父任务统一登记的 hook 签名：

- 四个 DocType `has_permission`：`pearnly_cowork.stocktake.access.has_permission(doc, ptype=None, user=None, **kwargs)`。
- 查询条件分别为 `.task_query(user=None)`、`.item_query(user=None)`、`.entry_query(user=None)`、`.operation_query(user=None)`。
- `DocShare.before_validate` 与 `DocShare.validate` 增加 `.reject_share(doc, method=None)`；拒绝新增/修改盘点分享。读取 hook 在拒绝时抛权限异常，列表若发现旧个人/全员分享会拒绝，避免 Frappe 的 OR 分享回退越权。

DocType controller 的 `before_insert`、`before_validate`、`validate` 只接受内部对象 capability；客户端提交 `flags.stocktake_write=true` 或 `ignore_validate` 不能代替 Python 对象身份。通用 insert/save、删除、改名拒绝；operation 不允许修改。专用服务每次先检查权限，再获取任务 `FOR UPDATE`；item/entry/operation 和报告用 current read，避免 MariaDB REPEATABLE READ 中权限读取先建立的旧快照导致丢合计。写入、回执、合计和版本属于同一事务；校验/后续持久化异常使用 savepoint 回滚整个操作。

实测 MariaDB 仍可在等待任务锁后的 current read 返回 1020 快照冲突。专用 RPC 对有锁的读写最多执行三次完整本地事务，始终保持原 request/operation ID；每次冲突先完整回滚，再重读权限和权威记录。第三次仍冲突则报错，不能伪造成功。内部编排应调用 `service`，其事务冲突直接上抛，不能借 RPC 的整事务重试覆盖编排的其他工作。没有外部执行，回执和业务写入在同一数据库事务内。

## 验证记录

2026-09-21，本工作树未提交代码：

- Python 3.14：`PYTHONPATH=apps/pearnly_cowork /opt/homebrew/bin/python3.14 -m unittest discover -s tests -p 'test_cowork_stocktake*contract*.py' -v`，21 项通过。
- `python3 scripts/check-architecture.py`：PASS，0 违规。
- `git diff --check`：通过。
- 隔离容器实际挂载本工作树只读 App/scripts；Compose project 为 `pearnly-cowork-backend-lab`，context 为 `colima-pearnly`。父任务统一迁移三个专用 Site 后授权运行，未接管其他 DMS/样板容器。
- `scripts/cowork-stocktake-lab-verify.py`：14 项原生测试通过，exit 0（首轮发现 1020 并发问题，修复后通过；错误断言同步检查对应固定词条译文）。覆盖创建与重放、精度/零/作废、版本与来源冲突、关闭重放、累计溢出回滚、后置写入失败回滚、通用写入及 token/ignore_validate 伪造、跨公司/购买功能/角色/停用/撤权、DocShare、legacy 隔离、两进程同 operation/不同 entry 竞争、两个 Site 隔离。
- `--http-smoke`：真实 WSGI 创建→计数→关闭→完整报告通过；原生 REST PUT 伪造改实盘返回 417，回读原合计不变；匿名读取与 GET 发起关闭返回 403，exit 0。输出中的 ValidationError/PermissionError 栈属于这些预期拒绝断言。

执行命令（已存在的专用 lab，不启动/迁移）：

```bash
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench /home/frappe/frappe-bench/env/bin/python /workspace/scripts/cowork-stocktake-lab-verify.py
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench /home/frappe/frappe-bench/env/bin/python /workspace/scripts/cowork-stocktake-lab-verify.py --http-smoke
```

原生脚本仅接受 `PEARNLY_COWORK_BACKEND_LAB=1`、`sites/.cowork-backend-lab` 标记、四个白名单 lab Site（默认A，跨Site始终选另一个）、`pearnly_backend_lab` 与 `db_host=db`。写入的是合成用户/公司及少量已提交的并发测试记录，普通测试事务回滚；不自动清库。本工作包未启停容器或执行 migrate（安装/升级/恢复证据由父任务统一记录），未执行 CI、部署、外部业务和用户验收。前端、XLSX 字节导入/导出、照片、LINE、legacy 直接盘点、任务删除、历史数据回灌仍未实现。

容量限制：10,000 商品仍按原导入合同接收，但原生创建当前逐条 Document.insert，最大批量耗时尚未测；不能据小型业务回归宣称已通过上限容量验收。若达到请求时限，后续应设计持久导入作业及完整快照发布边界，不能跳过校验或把半份导入暴露给读者。
