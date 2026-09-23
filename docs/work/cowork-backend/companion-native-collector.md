# Companion 原生目录收集与刷新

本批属于 `pearnly_cowork`，从新原生 Connection/新 Credential 接收旧线上固定源码的报告规则。实现位置为 `companion.heartbeat/http_auth` → `connections.reports/collector` → `connections.snapshots/refresh/state`；连接域不依赖机器鉴权域。无订单拉取、ACK、ERP 外部写入、原客户数据导入、Windows 切换或前端。

## 入口及数据责任

公开机器入口只有 `POST /api/method/pearnly_cowork.companion.heartbeat.report`。认证和原生请求隔离见[HTTP桥](companion-http-auth.md)，输入逐键净化和固定源码差分见[报告合同](companion-report-scope.md)。人工 configure/detail/targets/request_refresh/refresh_status 继续使用现有 Cowork 角色、购买功能、公司/客户范围及明确连接授权。没有新增产品导航。

Connection 新增隐藏只读 report_json/report_received_at/report_credential/report_generation，由已鉴权服务器写者维护。原始缓存保留旧连接级语义，独立的不可变 Snapshot 按目录范围存储。报告只合并五个所选账套字段及六个科目码；服务器身份、权限、凭据和其他管理配置均不受 body 控制。method 只允许由原管理配置入口设置，心跳不写入，默认 rpa；它不代表本批已接 RPA。

配置实际变化才增长 record_revision，并清除当前目录权威、使旧请求按 ERR_CONNECTION_CHANGED 失败。同值重复上报只更新观察时间；相同目录内容复用 Snapshot。Snapshot 保存服务器观测时间，客户端时钟不参与权威判定。客户端 complete、mappings、supplier_profiles、bank_index 不进入 preparation，目录 fresh 不能解除推送准备资料缺失。

未上报保留旧原始缓存；显式空 catalog 清商品/客户，但不清缺席的原始 reported_accounts；同一次规范目录投影的 accounts 按旧源规则可为空。空账套报告保留上一份有效 Snapshot 并记录 ERR_ACCOUNT_SET_EMPTY。offline 只更新旧离线哨兵和接收审计，不关闭人工 enabled，不发布目录或领取刷新。错误回读保留 error_code，重新请求清掉旧错误显示。

## 刷新状态与协议适配

原请求行仍是人工 API 的 request_id。机器 master_refresh.request_id 为每次领取新生成的随机句柄，绑定当前 Connection/revision/binding_generation、Credential ID/generation、范围及 900 秒到期时间；人端不暴露它。客户端沿原字段回显，无新增必填字段，但尚未验证真实 Windows 客户端兼容，不能将字段形状相同写成已验收。

- requested 的新申请合并同一行并更新请求时间；leased 时新申请将旧行标为 failed/ERR_REFRESH_SUPERSEDED，创建新请求。
- 领取严格使用本次 body.account_set 或 body.account_dir。缺席时允许其他待处理账套；展示 response.account_set 的配置回落值不能用来筛选任务。
- 同一连接最多一个活跃机器租约，优先 endpoint，再当前选择，再申请时间。Connection 先锁，Credential 复验后再锁 Refresh；旧 RR 快照冲突只在无外部副作用的入口进行至多三次完整事务重试。
- 普通心跳可以发布目录，但不能完成请求；当前租约成功回执才完成匹配范围。错误回执可发布独立有效组件，匹配任务仍失败；陈旧、跨范围、过期或上一代凭据的回执在任何报告变更前拒绝。
- 到期或完成时若 requested_at > started_at，则重新排队并领取新句柄，旧回执不可复用。凭据轮换后的新心跳使旧租约失败；旧 token 本身由凭据层拒绝。
- 有效回执同时带配置变更时，新配置和目录可以落库，但旧 revision 请求明确失败，不将旧请求重绑定到新配置。

相比旧 PG 多次独立提交，本平台将一次报告、目录与租约合为本地原子事务。陈旧完成请求导致整次报告拒绝；边界错误使用可翻译稳定码。两者是保护明确的协议适配，不计入纯报告零差分结论。真正提交 ERP 仍由此前独立推送账本控制，不在此处注册消费者。

## 本地验证环境

使用锁定镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，Docker context `colima-pearnly`、project `pearnly-cowork-backend-lab`。独立内部网络、无公开端口、无外部凭据、worker/scheduler；源码只读挂载。本轮实查旧专用容器和命名卷已不存在，因此从 `70383cdf5e8f45129997c8e773e0bcf3772422a9` 的只读 git archive 在新独立卷重建旧 schema/合成记录，再换本任务源码升级，不声称原旧卷或此前439条记录延续。

`cowork-companion-upgrade-lab-verify.py before/after` 只接受有标记的专有 Site，保存原字段集合和每表计数/哈希，不输出记录内容。重建后的14组表共18条业务记录（含5条凭据、5个连接、1个目录、1个刷新）升级与重复 migrate 原字段逐值摘要一致；其余表为空，不能借此声称已验证那些表的历史数据。另全新 `cowork-backend-heartbeat.localhost` 安装通过。

真实数据库用例见 `scripts/cowork-companion-collector-lab-verify.py`：18项，包括两个独立进程在已有 RR 快照后调用真实 WSGI，只能领取一个任务。HTTP和权限异常详见对应脚本及HTTP记录。基准证据、最终测试总数、限制和提交状态以[工作记录](../2026-09-20-cowork-backend-migration.md)为准。
