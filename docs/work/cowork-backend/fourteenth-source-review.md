# 第十四批：ACK 的本地业务联动

本批基线 `c1e57cf0a91f593cfc25637a2e3fcd4ade77f870`，仍在 `codex/cowork-backend-migration`。只实现和验证本地 Cowork 后端，不改 DMS、Core、前端，不部署、不推送、不调用真实 ERP/LINE/WeKan/OCR。

## 来源与范围

开工和交付各重新采集 Cloud Run/GitHub 身份及既有只读运行配置投影，文件为 `evidence/*fourteenth-{start,delivery}.json`。固定源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，Web/Worker 100% 对应 `pearnly-web-ec2e5c957e6b-s3` / `pearnly-worker-ec2e5c957e6b-s3`，镜像 digest `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。时间、canonical hash、首尾比较和本地源码 hash 在验证清单登记。

首次 source 采集使用了错误的 GitHub 仓库参数，拒绝且未生成证据；从上一批原证据回读正确仓库 `skin306152-star/pearnly-app` 后重新采集通过。首次 runtime 命令使用的系统 Python 缺 dotenv/psycopg2，未生成证据；只读复用旧仓 `venv/bin/python` 依赖后通过，没有安装依赖、修改旧仓或降低采集器检查。

| 固定旧源 | 保留行为 | 新落点 |
| --- | --- | --- |
| `services/erp/express_push/agent_store.py:309-479` | success/manual 镜像到 History；waiting_lock 和可重试失败保持 pending；成功重放不重复副作用 | `push.native.service` → `history.push_state` |
| 同文件 `_bump_created_stock_masters` | 仅 legacy 成功时，逐行 `mode=stock_item` 且 created 真值增加计数；不把 direct_account 或仅 meta.created_masters 视为真实新库存 | `push.native.completion.created_stock_count` |
| `services/erp/express_push/agent_reporting.py:355-395` | 增加库存指纹，解除下一张销售单仍读旧零计数的情况；新机器扫描可覆盖指纹 | `connections.stock_effects` |
| `services/erp/shared_express_agent_queue.py:280-435` | managed 终态同样镜像历史；该分支没有库存计数副作用 | 同一 native ACK 编排；managed count=0，未新增 managed 公开运输 |
| `routes/erp_agent.py:353-370`、`services/erp/line_push_notification.py` | 首次成功给出通知机会；仅 LINE 来源消费，普通 Web 不通知 | 私有 `Cowork Push Notification`，唯一 push_record，仅 cowork_line 成功保存 held 事件 |

上述五个源码文件均与固定 Git 对象的 SHA-256 比对过；源文件缓存不是独立版本权威。源计数 helper 的八组同输入对比只执行 hash 绑定的 AST，不导入旧生产 DAL，不冒称真实 ERP 验证。

## App、数据、权限与入口

全部归属 `pearnly_cowork`。依赖为 `push.native` → `history.push_state` / `connections.stock_effects`，既有方向不反转；通知持久化归 push 域，无 LINE 域、外部 SDK、队列消费者或 scheduler。

| 入口 | 用途与配置/角色 | 去向和证据 |
| --- | --- | --- |
| 既有 `companion.submission.confirm` POST | 原 actor、当前 Cowork 权限与准备证据；只有人工确认选择 History 最近推送 | prepared→pending 与 History 状态同事务；不发消息 |
| 既有 `companion.queue_api.acknowledge` POST | 原历史机器凭据、Delivery/Attempt/fence；保留撤权/停闸后的窄 ACK 能力 | 首次 accepted ACK → 原账本、历史投影、同来源库存缓存、通知事件；6 个既有 WSGI 定向用例 |
| 既有 History detail / records | 原公司/客户范围和角色 | 新增 last_push_status / last_pushed_at / last_push_record；不暴露通知内容或运输载荷 |
| 既有 review workbook export POST | 原 History 读取权限 | 只接服务器 History 状态；票面伪造 push_status 被过滤；真实工作簿验证固定泰语状态 |
| `Cowork Push Notification` | 无产品资源 API、无菜单、无客户角色访问 | 通用读/列举/改写/分享/删除拒绝；内部身份令牌写入、push_record 唯一；没有发件消费者 |

新增 History 三个只读投影字段、Push Record 的 completion_json、一个原生通知 DocType；不改业务表底座、共享鉴权或运行镜像。History 状态为最近一次人工确认选择的推送，不是所有连接的汇总。其他连接的迟到成功保存在其原 Record，不能覆盖用户后来选定的那次推送。

通知保存原 actor、公司/客户、History、Connection、账套、冻结票号和金额、单号及成功时间；没有 LINE ID 或凭据。`held` 明确表示尚无发送接入，不是“已送达”。未来发送端必须重新验证 LINE 绑定与当前接收人，不能把原 actor 字段当直接发送授权。

## 一致性与有意收紧

- ACK 已持有 Connection→History→writer→Record/Attempt 锁。三个副作用在同一个保存点内，无独立 commit；任一步失败，回执/状态/计数/事件/锁释放全部回滚。旧 legacy 吞掉镜像错误、库存另开事务的故障行为不搬入；这是原生账本一致性要求，正常业务结果保持。
- 成功重放先通过原 Attempt 凭据校验，再直接返回首个回执，不重复计数或事件。两独立 DB 进程建立旧 RR 快照后同时 ACK，最终一份事件、一次计数，一条返回幂等结果。
- History 使用专用窄更新，不更新 pages、record_version、edit_count、fields_edited_at、人工完成者或 modified；人工编辑不能伪造状态字段。
- 重试中的 pending 不重复更新历史时间；unknown 仍投影 manual 并保留写锁。迟到的可信结果可结束 unknown；客户端声明 unknown 不能解锁。
- 库存增量只应用到原准备证据所绑定的账号、root、报告 credential/generation/issuer，且 catalog_fingerprint 具有相同独立来源。切换来源记录 `target_changed`，没有可用画像记录 `unavailable`；回执成功仍保存，但绝不改另一账号的缓存。原凭据撤销本身不妨碍接收原授权尝试的事实。
- 计数仍是来自机器 ACK 的本地库存画像，不是本平台做了库存入账，也不是独立 ERP 回读。新扫描仍是缓存权威，后来的 ACK 重放不会再把计数加回去。
- 不补造升级前已有成功记录的通知或历史镜像；旧数据/旧账号配置迁入 HOLD 未解除。新字段默认空，已有源证据和记录保持。

## 验证结果与限制

- 13 个新增定向 Site 用例逐项通过；6 个既有 ACK HTTP 用例通过，共19个独立用例，不累加复跑次数。覆盖成功/重复/晚失败、失败耗尽、waiting_lock→needs_review、unknown→晚成功、撤权与新报告凭据、缺来源、新扫描覆盖、后续连接选择、编辑后回执、权限/分享、两进程并发、故障原子回滚、工作簿真实输出。
- 首轮10项有4失败：夹具错误使用 `stock`，旧线协议实际是 `stock_item`。修正夹具后只复跑这4项及新增2项，通过；没有把生产计数规则放宽为别名。
- 新装 Site 运行13项时12项通过，导出项发现本批事实装配漏了 `state` 层，状态显示 `-`；修正结构后输出已为旧协议泰语 `สำเร็จ`，另修正夹具期望英文的问题；只复跑该导出项通过。此前失败日志保留，不伪装整次首跑通过。
- 现有 `cowork-backend-preparation.localhost` 原生升级：23组既有表/投影（含凭据和权限）保持。原 Frappe Page roles 的40个 child row ID 重建按既有严格规则核验，精确角色集合不变、User roles 不变。
- 新 `cowork-backend-ack.localhost` 完整安装/迁移通过。重复 migrate 前后8组 ACK 相关数据/凭据/角色投影完全一致，包含并发测试已提交的 held 事件。
- 八组固定源码库存计数对比通过，managed无增量。不是全套功能等价证明。
- 新增6词×4语言，共24次原生翻译查询通过；严格 i18n 仍失败于旧147项/43文件/135文案，归一化对比新增0、减少0。
- 架构、语法、JSON、diff及暂存快照检查的最终状态见验证清单。没有运行755项全量离线、24项完整ACK HTTP、DMS、前端构建、CI或外部业务验收。

下一包优先接 **复核表导出的 ERP 单号、逐行主档动作及回执事实读取**，沿原账本授权，不从票面字段推断、不新增真实外写。仍未完成真实 Windows 接线/客户端身份、旧URL适配、managed公开运输、MRERP、完整对账/盘点、LINE/WeKan、其他文件出口与旧状态迁入。通知事件存在不等于通知功能完成。
