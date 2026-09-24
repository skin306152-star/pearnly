# DMS 手动建缓存、交互读本地、提交查实时

## 当前工作包

用户已明确采纳并要求实施。取代本日五分钟全量同步及每日地址同步的触发方案；旧记录保留为历史验证，不代表当前方案。仅本机实现和隔离验证，不合并主线、不推送、不部署、不登录真实 DMS、不发 LINE、不调用付费 OCR。

- 主任务树：`/Users/skin/.codex/worktrees/dms-five-minute-snapshots/pearnly`，`codex/dms-five-minute-snapshots`，续接基线 `658b8399fbf5d8cfe35842807241f448223bcfb5`。
- 目标 main：`57039ca15595641932f053fd34c49d4e45dba964`，开工干净；共享 ADR-009 已在基线。此次是用户要求的新设计，不是从未核实的旧生产版本移植功能。
- 独立子树 `dms-manual-connect`、`dms-manual-conflicts`、`dms-manual-query`（对应 `codex/` 同名分支）均由上述基线开始，归本任务；完成后由主任务整合并逐项清理。其他 ERP/Cowork 工作树不动。

## 合同与归属

- App：`pearnly_dms`。snapshots 管本地完整资料与显式读取请求；adapter/client 管第三方有界读取；connections 管管理员手动入口；query 管本人查询；intake/booking/approval 管实时写入及差异确认。
- 连接页原生“读取并更新资料”：仅 DMS Admin/System Manager；固定本站主连接；可选择基础资料、客户、地址。状态查询没有外部 IO。未配置连接/权限不足服务端拒绝。
- LINE 菜单 1/2 预览、编辑、下拉只读本地；菜单 3/4 仍真实登录/改凭据；菜单 5 明确一次刷新当前本人查询，不后台扫描所有员工。无数据不能伪装零结果。
- 取消资料周期采集和订车后的全量销售采集，保留业务恢复任务。旧持久化队列入口保留无副作用兼容，不能凭旧任务重新启动采集。
- 成功客户写后只回填独立验证过的一条，不能冒充全库刷新；失败不能导致外部写入重试。
- 新建时实时遇到已有客户进入差异确认；编辑比较基线、用户意图与实时值，同字段冲突重新确认，未修改字段保留当前值。幂等恢复与业务碰撞分开。
- 后台读不抢占实际提交；账号互斥保留，分块释放账号。第三方人工登录及最后复核与提交之间的竞态无法由我方完全消除。
- 无自动得知第三方修改的能力，不宣称 webhook/增量同步；完整分页校验也不等于第三方原子快照。旧好包显示真实时间，身份/权限/凭据变化仍拒绝。

## 本轮实现与证据

本地任务集成先后包含 `4fe39ce` 连接页、`647a9d6` 交互回归、`c5f9c54` 客户实时冲突/写入证据、`aaef3d8` 手动请求/分组快照/旧调度退役、`747e795` 单条件查询、`fbad447` 原生查询回归和 `fb9b7bd` 删除旧周期文案。后续补充验证/修复在本记录末尾登记。

- 核对并使用既有任务实验室：context `colima-pearnly`，project `pearnly-dms-snapshot-lab`，Site `dms-snapshot-lab.localhost`；四个专用卷、internal 网络、无宿主端口、五 App/脚本来自本任务只读挂载。未动共享样板。
- 镜像固定 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，Frappe 16.33.0 / Python 3.14.7。真实 Site migrate 成功，新增私有 refresh_request；原生 scheduler 中旧读档任务不再启用，三个业务恢复任务保留。未启动 scheduler。
- 离线门禁：333 条业务断言 + 46 个纯采集测试通过；architecture 0 违规；翻译包一致性和前端词条覆盖通过。全仓 `check-i18n --strict` 仍失败：main 498 条、候选 487 条扫描发现，377 条已登记中文；未扩大忽略列表。属于已有全仓文案债，不伪报全绿。
- 原生存储 25 场景通过：完整发布、旧包保留、凭据换代、员工隔离、双连接并发、独立写穿不提交业务事务。旧测试从合并目录改为独立客户包，删必需 customers 字段仍拒绝；年龄改成软提示，显式 max_age 限制仍覆盖。
- 手动请求 11 场景通过：冷态不伪装空、GET 零派发、原生队列去重、RPC 回滚不丢已独立登记工作、派发失败可见、有限续读、前台忙需手动重试、旧终态任务重投无副作用、发布 token 行锁、认证拒绝跨组保护、完整性失败清半成品但保留成功代际。
- 原生队列 2 场景通过：真实 dms_sync burst 只消费 UUID 合成 now 任务，long 哨兵仍排队；旧入口无 token 不登录、不排队；恢复任务保留。
- 原生交互 15 场景通过：实际 Site lookup/editor/全部订车选项只读本地；菜单 5 经真实 manual.request、Redis job 序列化和 execute_job 后只发布当前条件；外部读使用假 transport，双连接撤权/换密码阻断发布，旧代际不变。
- 菜单 5 独立查询 40 场景、LINE 出口 11 场景、查询卡片 32 断言通过。连接 RPC 权限/元数据 12 场景、原生 JS 生命周期 4 组通过。
- 真实本地 Chromium/原生 Desk 实测：手动按钮、Dialog 选定基础资料而非全选、一次 POST 立即关闭且无长时间冻结、重复点击同请求 ID/一个队列任务、切换页面停止 GET 轮询。页面按原生 FieldGroup 两列显示状态/数量/实际时间，已截图检查。测试浏览器显式 locale=en-US，避免容器默认非 BCP47 locale 令上游 AltShortcutGroup 抛错；未改 Frappe/共享 UI。
- 一次浏览器测试 harness 在异常关闭浏览器后阻断 fixture cleanup，已按核实的 `EP-00130` / `snapshot-818d91eec48d4b50a3cb7787c567e1aa` 精确清主连接/快照，停用其两个人工测试账户；随后修复 harness 的 finally 并回读。无用户业务资料。

本轮没有真实第三方读写、LINE 消息、付费 OCR、生产构建/部署或真机业务验收。无需把旧真机测试当成本候选通过。

## 状态与下一步

本地实现和本轮验证完成，交接保留，未上线。最后业务补包 `8a3883f`、`e62b7e9` 已整合；业务内容固定在 `c2ca3f47dd1956c6d46be37274cc45c91596b323`（后续仅记录更正）。最终组合 consumer 19/19、客户流程 15/15 通过；纯客户 client 三方比较 11 场景、旧身份 31 场景、旧 intake 23 场景亦有对应包验证。

客户派发原生 7/7：真实 MariaDB/Redis 和两个独立进程，稳定同源/CID/旧新证号共同拦截未知重放；同请求已证实成功可恢复；失败前不留已派发证据。审批派发前重检当前绑定/管理员/认领，失去 claim 时不能写入或发送旧回执。旧 RR 与跨连接 claim 变更触发 MariaDB 1020 后安全拒绝，回滚后证实新认领未被覆盖，**不宣称旧 RR 一定成功锁读**。

原生主连接测试 fixture、CW marker、审批记录、快照均已精确清除；本轮 migrate/一次失败 harness 清理新增的 6 个原生 housekeeping job 按已核实 ID 删除，未消费历史队列。最终队列回到既有 default=8、long=3、dms_sync=0；合成账户停用审计保留于隔离实验室，无用户数据。

### Git 与资源收尾

- main 与远端实际 `refs/heads/main` 均为 `57039ca15595641932f053fd34c49d4e45dba964`，主目录干净且未改。当前仅本机授权，因此未合回 main、未推送、未部署，也未新建 PR。四个本任务远端分支均不存在。
- 三个子包先 cherry-pick 到本任务集成树，核对内容等价（原生查询脚本保留父树独立 seed 修改），再以 ancestry-only merge 登记已整合来源。已正常非强制删除 `dms-manual-connect`、`dms-manual-query`、`dms-manual-conflicts` 三个工作树及对应 `codex/` 分支；删除前核 tracked/untracked/ignored、符号链接、进程及所有容器挂载，均无残留或占用。
- 保留主任务树 `/Users/skin/.codex/worktrees/dms-five-minute-snapshots/pearnly` / `codex/dms-five-minute-snapshots`：用户尚未授权本方案合并/推送/部署，成果唯一整合位置；下一次开始本方案部署前复核 main 增量及授权。
- 保留该树旧批生成的 `frontend/node_modules/`、`frontend/auto-imports.d.ts`、`frontend/components.d.ts`、`apps/pearnly_core/pearnly_core/public/pearnly-app/`：本任务候选编辑器构建和后续复验所需，未沿链接删除共享依赖；正式候选构建/主任务合并时复核再生清理。
- 本轮测试专用的 browser serve 和三个 `pearnly-dms-snapshot-lab-{bench,db,redis}-1` 容器收尾停止；保留该任务 sites/db/redis-data/logs 四卷及原有固定镜像，供新方案后续本机复验。解除条件：用户确认不再复验或授权对应实验室收工；不得因停止容器删除数据卷。其他 ERP/Cowork/runtime 未动。
- 本轮一次性无网络容器已自动删除，三个临时子树无容器引用。已查看的宿主浏览器截图 `/tmp/pearnly-dms-manual-evidence.8vuTtH` 移入废纸篓，可恢复；复验脚本保留，容器内截图随测试容器生命周期保留。

后续若要上线：先取得授权并合并当前主线增量，制作固定候选与备份/恢复方案，再部署/migrate 和启用专用 worker；之后由管理员首次手动建缓存，做真实 LINE 菜单 1–5 与第三方 DMS 对账。此段是下一步，不代表已执行或已获生产授权。
