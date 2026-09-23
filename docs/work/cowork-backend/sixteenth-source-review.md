# 第十六批：旧 Companion 运输适配与 Windows 来源

基线 `c9bee02d35c4e025e75c1ac0ed2d51f212d2b47c`，工作树 `cowork-backend-migration/pearnly`。所属 App `pearnly_cowork`、域 `companion`；新增可选 WSGI 入口，数据仍归既有原生 Connection/Credential/Delivery/Push Record。只有运输层单向调用原生应用，无新业务域、DocType、框架hook、队列或依赖方向。未改 DMS、前端、默认部署或现用 Windows profile。

用户已授权必要的 Windows / 第三方ERP测试凭据及测试账套联调。本包只读 Windows 和 TEST，未读取明文测试凭据、未写ERP；这不是因授权缺失暂停，也不撤销下一包的测试授权。

## 1. 当前生产来源

开工与交付均重新采集 Cloud Run / GitHub / 固定 Git 源码；首尾 source compare PASS。

- 旧后端完整源码 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。
- Web / Worker 各100%为 `pearnly-web-ec2e5c957e6b-s3` / `pearnly-worker-ec2e5c957e6b-s3`；镜像 `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。
- 交付采集时间 `2026-09-21T10:06:46.366275+00:00`。固定源缓存 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；旧服务端 `routes/erp_agent.py` 的协议承接仍沿第十二/十三批已经核对的原生入口。
- 第十五交付→本批开工、本批首尾，变化仅在已采投影的 Web/Worker `database.identity.endpoints.configuration_digest`。其它已采身份/环境/observations相等，但不能由hash还原变化键，不能认定只是心跳。
- **完整配置迁入HOLD仍在。** 不反复采样挑选稳定hash，不重做上批失败的v1以替代全配置门槛；本包只承接固定业务源码协议，不迁旧owner/路径/凭据/状态。

证据 `evidence/*sixteenth-{start,delivery}.json` 及验证清单保留原始门槛结果。

## 2. 实际安装的客户端来源

Windows EXE `C:\Program Files\Pearnly Companion\companion.exe` SHA256：

`0be7e908c3ce38d080f88b57f6eb8bd59f51d0315c3da5ac58dd775b75be448a`

只读复制该文件到本任务证据缓存；用 Python3.11.16 解析 PyInstaller CArchive/PYZ，没有启动EXE、没有执行内嵌代码。内嵌Python311、版本1.1.77；54个 `src.companion` 模块及 `launcher_companion` 共55个，均与固定 Git 提交 `33af0fd5e381e87802c95655dc35e7709eb2a233` 的编译结果相符。递归只去除构建路径 `co_filename`，保留字节码、带类型常量、参数/变量/名称、行表、异常表、flags等，逐模块源码hash入证。

可复跑审计器 `scripts/cowork-companion-binary-source.py` 要求完整commit、预期EXE SHA256和匹配CPython minor，运行时先校验再解析；输出 `evidence/companion-sixteenth-binary-source.json`。这证明本包涉及的Python业务代码对应关系，**不证明签名构建链、原生CDX辅助程序或第三方依赖身份**，不是完整ERP写者验收。

固定 `src/companion/queue_client.py` SHA256为 `6ee2a943fbe8b847ec3c7db34b53ddd41d30fccdde20cd088823d5c3be5572f4`。其三个旧URL、请求字段、顶层JSON读取、IncomingTask和ACK bool返回均由本次真实WSGI测试覆盖；没有改旧仓源码。

## 3. 新落点与保持的合同

| 来源 / 调用方 | 新落点 | 保持的行为 |
| --- | --- | --- |
| QueueClient.health → `/api/erp/agent/heartbeat` | `companion.legacy_wire` → 原生 `heartbeat.report` | 旧请求体，成功顶层JSON，仍走机器报告权限与原子采集 |
| QueueClient.poll → `/api/erp/agent/lease` | 同层 → 原生 `queue_api.lease` | jobs/master_refresh字段不改、空队列None、冻结payload与UUID句柄；prepared不被轮询激活 |
| QueueClient._ack → `/api/erp/agent/ack` | 同层 → 原生 `queue_api.acknowledge` | 成功/失败回执原合同、历史凭据窄权限、幂等持久化 |
| 原生RPC、Desk及其它WSGI请求 | 直接交原生应用 | 原URL、方法、`message`封套、Cookie和框架响应不经过解包 |

可选入口 `pearnly_cowork.companion.wsgi:application`，默认基础设施仍使用 `frappe.app.application`。适配器只映射精确POST路径；把请求交原生应用一次，消费并关闭原生响应、完成既有提交/清理后才解包成功字典。没有网络转发、自动重试、自动配对、旧token转换或actor推断。

既有 auth_hooks、原生新凭据/issuer IP、总闸、当前公司/角色/连接授予、会话隔离、请求cmd/sid拒绝、历史ACK权限均保持。响应不设置浏览器Cookie、no-store；原生非200状态/内容保持。200但不是合法成功message字典或含异常/非JSON数值时返回固定502，不能让旧客户端将封套错误看作空队列或ACK成功。原生RPC仍保留其原封套；没有引入返回旧全量配置或凭据的接口。

## 4. Windows只读结果

实测现用两个profile均是 `\\Accserver\d$\ACCOUNT\70EXP\TEST`，root为70EXP，dbf、未暂停；本包没有改变profile或停止现用Companion。PID9056是15244的子进程，不能据两个进程推断两个业务写者。

- skin3交互身份可读取该TEST目录及 ISINFO/ARTRN/APTRN/GLJNL/STMAS/ISVAT 文件元信息。它与SSH服务身份的可访问性不同。
- ISINFO为Visual FoxPro DBF（version48），只读打开、cp874解码；公司名非空，13位税号与旧项目已记录TEST税号相同，证据只保存匹配布尔值及末四位0289，没有输出完整公司资料。
- 初期临时任务无输出，退出码0没有算成功；调整本任务电池条件及脚本启动方式后取得结果，未单独证明唯一失败原因。第一轮自写DBF探测未在字段结束标记0x0D处停下，误报identity_fields_missing；修正只读探测器后字段末位置2262与记录长2262相等并读到身份。原失败证据保留，未改ERP/Companion业务代码。
- 临时任务 `PearnlyCoworkReadOnly-20260921-16` 及 `C:\Users\Public\PearnlyCoworkReadOnly-20260921-16` 均已删除并回读不存在；未调用或修改旧 `PearnlyRestoreCompanionConfig`。

详见 `windows-sixteenth-readonly.json`（含首轮身份解析失败）和 `windows-sixteenth-identity.json`（修正后成功及最终清理）。两次结果都是只读证据，不是ERP写入或独立业务回读验收。

## 5. 定向验证及限制

专属 `colima-pearnly` / `pearnly-cowork-backend-lab`，既有 `cowork-backend-ack.localhost`。本树只读挂载、Internal网络、无发布端口/外部凭据/worker/scheduler，镜像保持 `d5c8749…`，Frappe16.33.0/Python3.14.7。

- 4个真实Site用例整次通过：旧客户端心跳→prepared空轮询→领取→重复成功ACK；撤销后拒领但接受历史ACK；原鉴权与原生封套；提交后响应失败保留已租任务且不重发。
- 3个无Site用例通过：精确POST与其它请求旁路、非法成功封套、错误不重试且关闭响应。
- hash绑定旧QueueClient，AST只去除文件logger import，真实httpx处理请求/响应，其余代码未改。pending使用已有合成内部激活夹具，本包没有重验人工确认全链（第十三批已验证），更不是模拟用户批准真实ERP写入。
- 仅为测试安装 httpx0.28.1/httpcore1.0.9/h110.16.0/anyio4.15.1到本任务容器，测试结束卸载并删除复制wheel/client；没有改产品依赖和镜像。bench/db/redis已停止，数据保留下一批。
- 架构0违规；无schema/术语/前端变更，未重跑安装/迁移、i18n、DMS或全量测试。7个用例不证明Windows网络接线、原生CDX写入或跨系统物理写者互斥。

## 6. 下一步与保留

下一包先复采实际来源。必要测试ERP写入授权继续有效；新平台→Windows尚未实际接通，须先使用独立测试连接/凭据与明确TEST目标建立有界接线，保证不会领取旧生产队列或引入第二写者，再做一个测试单据及独立ERP回读。已核对Python业务来源不替代CDX辅助程序和写入依赖核验。当前没有默认监听入口、没有Windows profile切换，不能宣称可直接给客户使用。

完整config迁入仍HOLD；managed公开运输、MRERP、其余对账/盘点、LINE/WeKan、其它文件出口及旧状态迁入继续待迁。准备/人工确认及ACK本地联动已有合同保持，通知仍held无消费者。

本任务工作树/本地分支未合并、未推送，保留继续迁移；容器已停止，Site/卷/合成数据保留。本地固定源码、EXE、审计脚本输入和日志 `/tmp/pearnly-cowork-migration-20260920/` 保留为证据，下包复核；不把缓存或历史hash当未来线上事实。收尾仅清本批可再生缓存、临时依赖和Windows探测资源。
