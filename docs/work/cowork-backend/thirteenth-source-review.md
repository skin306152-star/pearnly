# 第十三批：准备资料与人工确认发送

开工新树 `b94fe1c4527a012ced4b12cbae8e2bd7e41609f5`。2026-09-21 实采 source/runtime 与第十二批交付比较通过；来源仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。首采证据在 `evidence/*thirteenth-start.json`。旧配置导入 HOLD 不解除。

本包只在 Cowork 实现服务器读取资料及人工确认，不改 DMS/Core/前端，不接外部写入、不部署。

| 固定来源 | 新归属 | 合同 |
| --- | --- | --- |
| `services/erp/mappings_store.py:133-163,235-254,417-432` | `masters.push_inputs` / Cowork ERP Mapping | 科目按租户，客户按客户 ID；推送仅取 express 与本票客户。空结果与查询失败有不同采集状态。 |
| `services/purchase/supplier_posting.py:28-44,102-155` | `masters.push_inputs` / Cowork Supplier Posting Profile | 公司+卖方税号，user_rule 优先于 correction，缺档案可继续；不把买方税号当卖方。 |
| `services/erp/express_push/bank_evidence.py:28-67`、`services/recon/bank_recon_v1_store.py:447-460` | `reconciliation.bank_evidence` / Cowork Bank Evidence | 原连接发行人+票面日期±7天，Decimal 文本；是已付的可选佐证，读取失败不凭空作已付。只迁消费所需存储，不声称整个银行对账/导入已完成。 |
| `routes/erp_agent.py`、`express_push/agent_reporting.py` | `connections.collector/preparation` | 原生机器认证后写目录及每类缓存的账套/root/凭据来源；浏览器无 complete 或事实上传入口，普通心跳不能代替本地业务表查询。 |
| `confirmed_push.py`、`confirmed_push_reservation.py` | `companion.submission` → `push.application` → `push.native` | 原操作人显式确认，当前权限与冻结版本、目标、凭据代次复核，幂等 pending；poll 不激活。 |

补正旧交接：供应商和银行在旧预检中是可选佐证，映射查询异常也被旧 DAL 退空。采集器保留这些回退行为并冻结状态为 unavailable，而非伪称“查到空”。数据库死锁/事务故障必须抛出，不能在已失效事务中继续。账套/root、当前授权、目标证据及报告归属失败仍拒绝。新原生数据不是旧配置/状态迁入，旧数据导入另做。

依赖：masters 与 reconciliation 拥有各自数据；connections 读取其窄接口并提供资料证据；push 读取 connections；companion 作为人/机器运输编排层调用 credentials 和 push，不让 push/connections 反向依赖 companion。公开人工确认放 `companion.submission.confirm`，内部账本不直接 whitelist。

验证限定本条链：固定源同输入的资料投影/载荷差分；隔离 Site 的实际采集→prepare→confirm→lease、撤权/错代次/过期目录、幂等/并发、晚失败回滚；schema 变化仅增加必要的新装/升级/重复升级与原数据摘要保持。保留既有其它批次证据，不全仓重跑业务测试。

## 实际入口与有意边界

- `masters.api.save_erp_mapping(kind,mapping_key,erp_code,expected_revision=0)`：配置本 Site 科目映射或原生 Client 映射；需 `master_data` + 当前 Cowork Admin。稳定自然键、版本和同值重放；空 code 停用映射效果，不删除审计行。公开入口固定 express，未声称 MRERP 映射已接。
- `masters.api.save_supplier_profile(workspace,seller_tax_id,default_payment,default_item_type)`：明确 user_rule。内部 correction 不能覆盖 user_rule；此批未接自动纠错学习。付款 cash/credit、项目 goods/expense 与固定源相同。
- `push.application.api.prepare`：现在读原生数据表和经机器认证、明确上报目标的缓存来源，不再消费 Snapshot 的旧 `preparation.complete`。Client ID 从授权 History 推导，票面 `client_id` 不能改映射。销项不查供应商档案。返回 `preparation_states` 和 `has_reported_credential`，不返回资料、令牌或冻结载荷；后者仅是准备时的报告事实，不承诺凭据仍有效。
- `companion.submission.confirm(name,expected_revision,request_key)`：POST、原操作人、当前 intake 权限/范围/连接授予、当前凭据及发行人的权限。调用后只进入 pending，Frappe 提交事务后可由原有 queue RPC 领取。无页面、菜单、消费者或外部动作新增。
- `reconciliation.bank_evidence.record` 仅内部、绑定当前登录人、自然来源键幂等；无普通 RPC 上传银行事实。银行导入/对账完整任务尚未迁移，不把这个消费表宣传成完整对账。

准备资料的身份包括账套/root、凭据 ID/代次/发行人、各缓存来源、资料内容摘要、查询范围和曼谷日期。空心跳不能把配置中的默认账套冒充本机报告，切换账套不能重新给旧缓存贴标签。普通心跳既不能上传 mappings/profile/bank，也不能声明采集完成。

确认和领取都复查冻结资料、票据/连接版本与目标；换代凭据不能领取旧代确认的数据。已领取的历史 ACK 仍走原回执鉴权，本包未改 ACK 语义。没有报告凭据时可以生成仅供审阅的 prepared，但不能公开确认；旧内部 activate 仍是非 whitelist 的测试/可信适配 seam，不是产品授权入口。

新增的谨慎拒绝：资料或公司税号已变、跨曼谷日、精确账套刷新待定、报告 offline/归属变更时不得直接发送旧准备。未发送 prepared 可通过既有 cancel_prepared 取消，再使用新请求键重新准备；原幂等键始终回读旧记录，不静默重建载荷。数据读取的死锁/断连/未知异常向外抛出，只有已知缺表/列保持旧可选资料回退，并记录 unavailable；不是所有错误都假装空集合。这些限制保护冻结确认，不能据此声称和旧不安全重试路径完全相同。

## 验证与真实结果

绑定候选文件 SHA256 的完整清单见 `evidence/thirteenth-local-validation.json`。

- 17 项新增真实 Site / 人类 WSGI：实际原生采集→prepare→confirm→lease、原载荷不变、重复确认/领取后回读、凭据轮换、原actor及权限、无归属报告、精确目录 pending、跨日、票据修改、映射资料变化、客户 ID、供应商/银行隔离、失败回滚与两个带旧 RR 快照进程的同键确认。最后归属修复只定向复跑受影响两项，8MiB 数据门只复跑成功链，不重复累计。
- 18 项既有准备/PRN应用回归、18 项既有目录收集回归通过；共 **53 个不同 Site 用例**。没有执行 755 项全量离线、DMS、完整 transport/ACK HTTP 套件或前端构建。
- 8 组同输入来源比较：将实际原生采集得到的 mapping/profile/bank/context 喂给 SHA/hash 固定的旧预检与当前预检；进/销项 × 空资料/科目映射/供应商档案/银行佐证，完整预检及载荷相同。只规范化 Frappe 公司 ID 到双方相同的旧整数形状。旧 DAL 真实生产查询没有执行，隔离与范围由新 Site 测试独立验证；不是旧数据迁入证明。
- 新 Site `cowork-backend-preparation.localhost` 完成原生安装和 migrate，17 项新增测试在此通过；既有 push Site 升级、重复 migrate 成功。v3 摘要验证业务表、凭据、用户、权限/默认设置保持不变。Frappe sync 重建 20 个已安装 Page 的 Has Role 子行（40 个旧/新行身份），逐项核对 parent/type/field/role 多重集合不变，用户角色行未改；不以物理子行 ID 固定作为权限保持断言。
- 验证过程的失败：两处 `_` 局部赋值遮蔽翻译函数导致拒绝分支 TypeError，已修；字段名 payload_version 被测试误当完整 payload，改为检查实际禁止键；新 Site fixture 缺 Push Log 测试角色、测试授权用错 actor、AST差分加载器的模块元数据均已修。第一份升级摘要在合成测试新增行后回读，不用它作为通过证据；第二份揭示上述 Page 子行重建，按具体来源和角色语义完成 v3 校验，没有改业务数据或跳过失败迁移。
- 新增 16 词、Cowork 每语 491 条，真实 Frappe 四语 64 次查询通过；术语表、架构0违规、AST/diff/共享暂存钩子通过。strict i18n 仍是 **147项/43文件/135文案**，与第十二批逐条去行号比较新增0/减少0，不声称全仓全绿。
- 交付前 source/runtime 再采并与开工比较通过：source `2026-09-21T08:34:31.715517+00:00`；runtime精确时间见 JSON。仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，settings_unchanged=true、observations_changed=false。旧第八→九批 HOLD 未解除。

没有 CI、部署、真实 ERP/OCR/扣费、Windows、前端或用户验收；本地合成领取不是外部成功。下一包是 ACK 的 History/库存主档/唯一通知事件联动（仅持久化，不实际发通知），其后才补其它迁移缺口。
