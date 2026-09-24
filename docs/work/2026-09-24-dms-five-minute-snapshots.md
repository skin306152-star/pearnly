# DMS 五分钟完整快照与本机验证

## 当前交接（2026-09-24）

用户已采纳：客户、车型/全部颜色、银行、销售等业务资料每 5 分钟后台同步；地址目录首次后台完整建立、之后每日完整更新。LINE 资料选择、客户预检、编辑与查询只读完整本地快照；最终外部写入仍实时复核并独立回读。

本批实现及本机独立容器验证，**不部署、不推送、不做真实 LINE/OCR 或 DMS 写入**。本批是新读取架构，不把旧迁移生产源码作为新增需求来源。第三方仅执行用户明确批准的 DMS-TEST 短时只读协议核对，已结束。

- 分支 `codex/dms-five-minute-snapshots`；工作树 `/Users/skin/.codex/worktrees/dms-five-minute-snapshots/pearnly`。
- 开工基线/集成目标 main 与开工远端 main：`57039ca15595641932f053fd34c49d4e45dba964`；已包含共享 ADR-009。开工主目录干净。
- 实现本地提交 `58ea02a583fcf1cfff3726801128a1571c136571`，提交钩子检查实际 staged 快照通过；本记录的收尾补充仅文档提交。尚未合回 main，不能说全仓已统一。
- 旧任务 `dms-line-full-flow-fix` 的未提交诊断、暂停实验室及测试附件原样保留；其它 ERP/Cowork 工作树、分支、数据未修改。

## 实现及旧依赖替换

设计合同见 [ADR-010](../adr/0010-dms-background-read-snapshots.md)。

| 路径 | 新读取方式与保留的业务边界 |
| --- | --- |
| 菜单 1 身份证＋电话、查重、候选/差异卡 | OCR 仍真实识别；电话后直接完整本地查重，不再进入 long 队列或登录 DMS；全身份证精确唯一匹配、不取第一条碰撞 |
| 菜单 1 编辑加载、地址级联、保存回卡 | 同一代业务快照＋独立每日地址；保存只改本地草稿/重算差异，发送新卡成功才换完 nonce；缺快照不是空客户 |
| 菜单 2 逐问、车型/颜色、付款银行、编辑保存 | 每步重查当前身份与完整代际，选项只读本地；未就绪时保留已录入客户，可继续订车，不重新 OCR 或重写客户 |
| 菜单 3 门户、4 凭据修改 | 实际登录/改凭据操作，不伪装缓存；原票据、权限及凭据同步保留。换密码会改变快照 scope |
| 菜单 5 记录、排序、状态、查询能力、排行 | 员工自己的原生完整快照，直接回卡；不借管理员范围、不用 Python 重算原生排序。默认本月预取，自定义区间登记后等待正常后台同步 |
| 最终客户创建/更新、订车确认 | 原实时身份/地址/主档/付款/顾问复核、账号及写者锁、nonce、幂等、独立回读、附件/回执恢复保留 |
| 成功客户写穿 | 原生完整表单独立回读后立即更新缓存；新客户 ID 纳入后续后台直读，不能拿旧 overlay 字段冒充新资料 |
| 成功订单刷新 | 仅请求本人销售快照后台重读；`cache_refresh_requested` 不代表刷新完成，旧 `cache_updated` 返回 false，不伪造销售行 |

`adapter.operations` 承接原实际登录执行体；booking/recovery/delivery、管理端 masters_cache 内部直接引用新入口，intake.service 保留显式薄导出供旧诊断脚本。旧 warm 队列路径指向新调度器，master_shared 只读新完整代际，旧 45/90 秒按需等待/冷抓退休。管理端顾问配置仍有明确实时复核；它不是 LINE 前台读取。

所属 App 为 DMS；新增 snapshots 域只负责原生本地存储、scope、派发；client 负责原生完整采集，adapter/query 负责外部读，intake/editor/booking/query 消费。纯 contracts.geo_matching 统一实时/缓存地址规则，不依赖框架或 IO。未修改 ERP/Cowork 产品页面或菜单。

## 同步与正确性保护

- 私有原生 `DMS Read Snapshot` DocType：无客户通用 CRUD/导出/分享权限，数据/完整代际/staging 分开；只有完整成功才原子替换。Redis 重启不会丢完整资料。
- 按 Site＋DMS 实际读取凭据隔离；员工销售额外按 Endpoint、owner、Operator 权限版本隔离。端点/用户/操作员启停及查询权每次检查。
- 客户按原生 count、精确完整分页、全详情和前后 ID 再核；销售按 count/page/count、原生排序/状态核对。异常、截断、重复、漂移拒绝发布，旧好包时间不刷新。
- 地址分批最多 300 父节点/90 秒，阶段持久化不对 LINE 可见；释放账号锁后由专用队列继续。完整才发布，日常每日更新；半成品超过 6 小时重建。
- 业务源数据年龄默认最多 30 分钟，地址最多 3 天；超过目标周期标滞后。已知认证/授权失败立即拒绝，临时故障保留旧好包。业务和地址分别显示真实采集时间，不以打开页面/编辑保存时间冒充刷新。
- 首次未同步明确待同步，不现场登录、不拿空数据放行；旧推送台账知道的客户号尚未进入本代时同样待同步，不能忽略后误标新客。自定义排行最多 32 个活跃条件、7 天过期；默认本月条件不占自定义限额。
- 两个真实并发故障已复现并修复：旧 RR 视图后加锁可导致业务事务丢失；并发登记可越额/锁升级失败。客户写穿与自定义条件登记改用独立短原生 DB 连接（READ COMMITTED、2 秒锁等待），只提交自身缓存，不提交/回滚调用方业务；作用域锁及登记 guard 保证权限及 32 条硬限额。
- DMS hooks 声明 directory/sales_query/geo 三种采集器，每 5 分钟原生 cron，DMS scheduler tick 60 秒。相同权威 scope 去重，员工销售不合并。独立 `dms_sync` worker/队列，不与 OCR/long 混队；原生 Scheduled Job Type 窄 mixin 把启动采集的 dispatcher 本身也放专道，其他定时任务的原生队列及行为不变，避免 dispatcher 仍被普通 worker 的长任务拖住。
- worker 的 Compose、开发挂载/重载、样板初始化、正式开户/启停/激活流程和容量校验都已接线，但本批没有应用到线上。
- 容量模型定向测试显示新增 worker 后，测试中的 12 GiB DMS＋ERP 组合会被容量闸拒绝，16 GiB 模型通过；这是测试模型，不是本轮生产服务器实测。上线前仍须按实际机器与产品组合核预算，不降低闸门。

## 证据分层

### 本地实现和自动验证

以下结果绑定本任务工作树的候选改动，提交前再次检查 staged 架构；不是旧线上版本的验收。

- 架构检查 0 违规，diff 检查通过。
- 零 IO 总门禁：client 114、booking-foundation 45、payments 42、readback 43、retry 61、quota 27；新增完整读取器 42，均通过。
- 新快照查询 31、消费者 18；旧 LINE runtime 受影响分组、intake queue 23、query output 11、真实 Site 旧查询会话 46 项通过。消费者验证两套时间、不多发收费消息、继续订车 reply token、严格完整写穿、实时写入拒绝、成功刷新请求语义、台账缺档不误报新客；真实查重仅一条结果 reply，不再另发 loading。
- 原生新快照测试：store 25、reads 15、sync 5、queue 3，共 48 项分批通过；关键更改后定向重跑，不冒称全部同刻一次执行。真实 MariaDB 双连接／Redis／原生 RQ，外部运输为合成替身，HTTP 尝试 0。
- 重点包括：失败保留旧代际、凭据变更/撤权拒绝、外部写成功不能因缓存失败逆转、业务事务不被偷提交/回滚、31→32 并发仅一个请求成功、303 个合成父节点两批经实际 RQ 完成、首次半树不可见、每日刷新跳过、专队列不消费 long。
- 原生 Scheduled Job Type 已实际登记唯一 */5、stopped=0，旧 warm 登记已不存在；真实原生函数验证 300 秒间隔、到点临界、跨日。真实 get_doc MRO 包含窄 mixin，原生 enqueue 投递 dispatcher 到 dms_sync/420 秒，正确 Site/JobType/队列回读后只清自己任务、未执行采集。其它 Cron 仍 default、Daily Long 仍 long。原生 scheduler 在本实验室保持暂停，不冒称已连续上线轮询。
- Frappe 全新 Site 安装 core+dms 通过；两次原生 migrate 建立 DocType/staging，实际 11 字段回读存在，4 个载荷字段均 Long Text。没有手工建表。
- 运行配置：activate 9、provision 11、runtime 2、capacity 11，Compose/脚本语法检查通过；队列目标 1、调用契约 3、客户身份 31、架构规则测试 17、真实 Frappe 入口 4、云端开户限制 17 通过。
- 前端锁定 Node 22.14.0：typecheck/build、翻译生成及漏键检查、编辑页单测 5 通过。真实 Chrome 本地合成 API 冒烟 6 场景通过，见 [页面验证](2026-09-24-dms-editor-snapshot-smoke.md)。不代表真实 LINE 浏览器/API 验收。
- 既有签名 webhook→真实 RQ→订车/回执/防重 10 项、独立恢复 8 项、booking flow 33 项通过，HTTP 为合成运输。新增成功用例实际生成本人 dms_sync 待处理任务，未触发外部读取、未把请求当完成；原生 Operator/Endpoint/凭据保存，不伪造业务权限。两个恢复用例曾被测试用户数量阈值挡住，仅合成 User fixture 创建时设原生 in_import 标记，未改产品限流，重跑 2/2 通过。
- 新门禁已接入 Development / Build workflow；native 测试用独立 snapshot-lab，精确 always 清理自身 project。**未触发实际 GitHub CI 或镜像发布。**

一次入口检查因实验室未挂 docs 而未执行到断言；改用候选源码完整只读挂载、同锁定镜像且 `--network none` 后 4/4 通过，不以缺依赖当通过。

### 第三方只读协议

见 [DMS-TEST 协议记录](2026-09-24-dms-snapshot-protocol.md)。客户 count=40、页末空串、51 个完整原生控件；员工销售 count=32，零状态明确 count=0。地址仅有限样本和真实级联 JS，没有猜批量接口。账号锁前后释放，无遗留请求。

本轮没有执行真实全客户详情发布/全国地址完整建树；sale02 没有原生排行权限，未借管理员绕过。相应真实完整性/耗时及有权限排行仍待后续验收。

### 尚未执行

部署、真实 LINE 新版业务测试、OCR 速度改善/付费 OCR、真实客户/订单写入及用户验收均未执行。轮询不保证第三方任意规模/故障下每 5 分钟必定完成；首次全国树和容量需要正式应用前观察。第三方无事务 revision，有界扫描不等于第三方数据库原子快照。

## 实验室与复跑

- Docker context：`colima-pearnly`；Compose project：`pearnly-dms-snapshot-lab`。
- Site：`dms-snapshot-lab.localhost`；镜像：`sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，本机 arm64 Frappe 16.33.0，MariaDB 11.8、Redis 6.2；不是生产 amd64 候选镜像验收。
- 独占 sites/db/redis-data/logs 四卷、internal 网络、无 host 端口、无生产凭据；5 App 与 scripts/tests 只读挂自本工作树。bench 只 sleep，无常驻 worker/scheduler，pause_scheduler=1。
- 队列测试只用本任务 burst worker；合成快照/marker/活跃操作员/自建 queued job 已按 UUID 清理或停用；原生安装/迁移留下的 long 3 个索引任务及 default 8 个安装任务不清空冒领。最终 dms_sync 为空，native sweep 测试任务不存在。
- 本地页面冒烟两个自建服务及 Chrome 标签已关闭。

在本树、只操作上述专属项目复跑：

```bash
docker --context colima-pearnly compose -p pearnly-dms-snapshot-lab -f infrastructure/compose/dms-snapshot-lab.yml up -d --wait db redis bench
docker --context colima-pearnly exec -w /home/frappe/frappe-bench pearnly-dms-snapshot-lab-bench-1 env/bin/python /workspace/scripts/test-dms-snapshot-store-native.py
```

其余三套将末尾脚本依次换为 `test-dms-snapshot-reads-native.py`、`test-dms-snapshot-sync-native.py`、`test-dms-snapshot-queue-native.py`，不并行争用同 Site。新空卷先运行 `dms-snapshot-lab-init.py`。不要拿此命令指向共享样板/生产。

## 收尾与保留

本批按“仅本地验证、先不部署”交接保留，不擅自合并、推送或改变线上。

- 目标本地主线与真实远端 main 经 `git ls-remote` 回读均为 `57039ca15595641932f053fd34c49d4e45dba964`；主目录仍干净。本任务远端分支不存在，未创建 PR、未推送、未部署。
- 本地实现提交 `58ea02a583fcf1cfff3726801128a1571c136571`，作者沿用当前环境与前序主线实际身份。随后仅提交本次交接文档；不重复跑未改业务测试。
- 已正常停止并回读 exited：`pearnly-dms-snapshot-lab-bench-1`、`pearnly-dms-snapshot-lab-db-1`、`pearnly-dms-snapshot-lab-redis-1`；自建预览服务/标签和 burst worker 已结束。未停止任何共享容器。
- 保留四个本任务卷：`pearnly-dms-snapshot-lab_sites`、`pearnly-dms-snapshot-lab_db`、`pearnly-dms-snapshot-lab_redis-data`、`pearnly-dms-snapshot-lab_logs`；包含原生安装状态及停用合成审计，供下一轮验收复跑。没有删除卷或业务资料。
- 保留本任务工作树、`codex/dms-five-minute-snapshots` 本地分支及 internal 网络/已停容器：成果尚未集成，且容器仍绑定此源码目录，不能删目录断挂载。本任务远端分支不存在，无远端分支清理。
- 保留本树 ignored `frontend/node_modules/`、`frontend/auto-imports.d.ts`、`frontend/components.d.ts`、`apps/pearnly_core/pearnly_core/public/pearnly-app/`：均为本次锁定构建/本地预览所需可再生资料，不提交。未触碰共享依赖。
- 所有保留项归属当前快照任务；下一次复核为用户确认本地成果并决定集成或下一轮测试时。满足集成保存、无源码挂载/运行占用后再逐项清理，不长期以“另一个窗口”为保留理由。其它任务工作树及旧任务资料不在本次清理范围。

下一步只在用户明确安排后：按真实目标资源核对容量、生成正式候选镜像、初始化并观察真实全量客户/全国地址快照，然后跑真实 LINE 菜单业务及独立 DMS 回读；这些不是本批本机验证的已完成项。
