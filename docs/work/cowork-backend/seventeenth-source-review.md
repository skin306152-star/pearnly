# 第十七批：实际 Windows 采集、确认与 TEST 单笔回读

## 结果与边界

2026-09-21，在独立原生 Site `cowork-backend-ack.localhost` 创建新连接和新凭据，由 Windows skin3 身份执行固定旧 Companion 源码，完成真实 HTTPS 资料回报、公开人工确认、唯一写者交接、一次成功 TEST 写入和独立回读。不是部署，也没有把已安装 EXE 的现用 profile 改连新平台。

TEST 单据 `RR690921-001`，发票引用 `CW17-2ebbb740df`，含税 107、VAT 7，3 行分录借贷各 107；APTRN1 索引命中。另回读 1 条非库存明细及服务主档 `PN00060`（STKTYP=3），库存影响字段为零。单据和主档保留，不自动删除测试业务数据。成功原生记录 `CWP-e8b04880f110e5237597e482994115648d7f76ff5f7e6b93`，一次 attempt、fence=1、无残留 writer holder。

原始票据是**明确标记的合成识别结果**；公司、现有供应商、科目及目录来自实际 TEST 只读采集。没有调用付费 OCR，没有将手工录入的低置信度改成高置信度。公开手工创建仍因 `low_confidence_band` 拒绝推送，其记录保留。此结果只证明该合成输入后的真实发送/ERP 链路，不证明识别质量、所有采购/销售/库存场景或用户验收。

## 来源和有意差异

开工与最终交付均重新读取线上身份。后端 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；Web/Worker 各 100% `pearnly-*-ec2e5c957e6b-s3`，digest `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。最终采集 `2026-09-21T11:06:43.880484+00:00`，首尾 source PASS。旧源只读目录仍为 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。

完整配置导入继续 HOLD：首尾已采投影仅 Web/Worker 的 `database.identity.endpoints.configuration_digest` 有业务投影差异；采集时间和绑定证据 hash 变化属于元数据。不能由摘要反推字段或将其归因心跳。本批新测试连接不导入旧生产 owner、token、路径状态或队列。中途修复前快照保留为 `*-seventeenth-before-wire-fix.json`。

固定 Companion 源码 `33af0fd5e381e87802c95655dc35e7709eb2a233`。前批已证明安装 EXE 的 55 个 Python 模块匹配，本批首尾 EXE hash 不变；本批另证明安装 CDX helper 与固定源码的二进制 hash 一致。使用 Windows 现有 Python 3.11.9、dbf/httpx/PySide6/pywin32/pywinauto，没有安装依赖。

| 来源 / 合同 | 本仓落点 | 行为 / 差异 |
| --- | --- | --- |
| `selected_account.py`、`line_target_projection.py` 的归一身份，Companion `worker._task_settings` 的 SCCOMP 原始目录，`purchase_adapter.validate_purchase_payload` 的精确白名单 | `push/express/account_rules.wire_account_set`、`preflight.preflight_express` | 原源码会把默认/投影选择变成小写，而现用 Companion 再取 SCCOMP 原始路径进行区分大小写比较。真实首单在写盘前失败。**有意修复**：只有目录和账套归一身份相同，才用服务端选定目录的原始拼写作为载荷 account_set；身份、授权、锁和来源纯选择模块不变。采购、销售共用此处。 |
| 旧 heartbeat/lease/ack 路径、QueueClient 顶层 JSON、原生公开 prepare/confirm | `scripts/cowork-windows-lab-relay.py` | 仅指定内网 Site 和三条机器 POST；固定 LAN 地址、对端、TLS 证书。每次通过原生 WSGI 鉴权/事务。不是正式服务器，没有默认启用。 |
| 原 Companion `single_instance`、GUI `_quit`、`route_task`、备份/回滚、CDX reindex | `scripts/cowork-windows-test-client.py` | 临时源码进程使用相同会话和原 mutex 名。托盘“退出”合作停止原写者，拿到真实 mutex 才领取单笔，拒绝 fail-open；忙碌则停止，完整走旧写入与 ACK。finally 恢复原 EXE / APPDATA；不加载、改写或解密现用 profile。 |

本批没有改来源仓库、默认 WSGI 部署入口、schema、后台消费者、DMS、前端或共享身份层。`push.express` 保持纯计算，未加框架/IO 依赖。未重新跑整个来源对比；不能将这项有意修复称为原输入输出完全相同。

## 实际失败与恢复

1. SSH 禁止端口转发，未改 sshd。改成临时 Mac `192.168.0.93:43917` HTTPS，仅接受 Windows `192.168.0.94`，Windows 固定证书校验；Docker Internal 网络与无发布端口保持。
2. 准备但未确认时 Windows poll 返回 0。初次报告脚本误写最高载荷版本 3，改成从固定源支持集读取真实值 1 后，旧准备证据被 confirm 以 HTTP 417 拒绝。脚本当时未将 RPC 错误设为非零退出，后续空领取仍执行了合作交接，但未调用 ERP writer、没有 attempt。已修正脚本 stdout/退出码，取消旧准备后重新准备；没有降低证据比对。
3. 首次实际领取的载荷被 `PAYLOAD_INVALID / ACCOUNT_SET_NOT_ALLOWED` 拒绝。固定源码表明此门位于 DBF 打开/备份/写入前，ACK written=false；另一次只读回查该引用为 0 张。公开 history 保存提升版本，旧 pending 经正常 poll 隔离为 manual，未改其冻结载荷。
4. 新准备、新人工确认后才执行修正载荷。两次旧本地防重标记的归档分别以“无领取/无写入”和“明确写前拒绝+独立零单据”证明为前提；不是重试不确定结果。成功后未重发。
5. 原 Companion 每次均恢复；首尾 profile SHA256 `96336c13b45bac266d87d5072ca30e4d8fb03148139721156b0bc70fd4ca7d71`。最终仍为同会话父子进程，不能把两条进程当双写。

## 检查和清理

23 个受影响 preflight 用例通过（新增 2 个，覆盖 UNC/本地目录、采购/销售及不同账户不能替换）；脚本语法、错误平台和不受限监听拒绝通过；架构 0 违规。真实 Windows 操作及独立回读见 [集成证据](evidence/windows-seventeenth-integration.json)，[验证清单](evidence/seventeenth-local-validation.json)。没有全量回归、DMS、前端、CI、schema 安装/迁移或用户验收。

Windows 专属计划任务和 Public 文件夹已删除并回读不存在；原 profile、密码文件、EXE 和其它任务未动。临时机器凭据撤销，两个联调用户停用，API key/secret 清除，Site config 按原字节恢复；原生审计记录保留。Mac TLS 入口关闭，四个私有 JSON、TLS 私钥/证书和临时 bundle 删除；没有保留明文测试 token。原固定来源缓存及脱敏日志留作复核，下一批/迁移结束复核用途。

独立 lab bench/db/redis 已停止，保留本任务数据库、Site、命名卷和既有匿名卷。bench 的 sleep 进程停止退出 137，OOMKilled=false；业务操作和凭据回读均在停止前完成。工作树及本地分支未合并、未推送，属于继续迁移的交接保留；下一批开工再次核来源与资源归属。
