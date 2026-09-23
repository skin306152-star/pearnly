# 推送状态、防重与回执纯合同

固定来源 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。本包只有纯规则，不创建原生账本、队列或 RPC。当前来源/配置证据见 [运行基准](push-runtime-baseline.md)，第七批总记录见 [工作包](../2026-09-20-cowork-backend-migration.md)。

## 来源与落点

| 固定来源 | 新域入口 | 保持的行为 |
| --- | --- | --- |
| `services/erp/push_retry.py` 纯定义 | `push.retry` | 60/300/1800 秒、3次重试、用户数据错误不重试；Express排队哨兵为pending、manual不算成功；旧统计中的pending“非失败”不可作为成功证据 |
| `express_push/agent_store.py`、`agent_lease_store.py` | `push.lifecycle` | legacy pending领取、120秒租约、内部ERP来源拒绝、账套过滤、已确认绕过防重的任务失联转manual且不重领 |
| `shared_express_agent_queue.py` | `push.lifecycle` | managed generation/attempt条件、过期重领取推进attempt、waiting_lock推进attempt；旧legacy waiting_lock不推进。同一字段同时参与失败上限的旧行为保持 |
| `confirmed_push.py`、`confirmed_push_reservation.py` | `push.lifecycle`、`push.receipts.reservation_decision` | 成功→活跃→未知的预留优先级、固定target_intent；MRERP技术结果不明落manual；Express技术失败首次60秒、数据错误不调度；未知finalize先读持久结果 |
| `push_log_queries.py`、`push_dedup_store.py` | `push.receipts.current_receipts/legacy_success` | 展示先按History×endpoint折叠，再筛状态；防重独立查询历史成功，不能把最新失败当“未写过” |
| `external_ref.py` | `push.receipts` | dict/JSON/嵌套payload解包，MRERP/Express/通用单号派生；未迁DMS专属派生；派生字段不改变实际状态，不证明独立回读 |
| `express_push/prior_doc.py` | `push.receipts.prior_document` | 从关联History租户归属及权威成功回执读旧凭证号；NULL log tenant不能把有效历史凭据丢掉 |

## 明确差异和接入条件

1. 新纯查询把 `None`（读失败）与 `[]`（成功但无记录）分开；读失败抛 `push.receipt_lookup_unavailable`，不继承旧catch-all“无成功记录”的放行。PRN增加端点和账套匹配；旧同History跨账套最新凭证号不能用在另一本账里。服务必须先从不可变 Source/Batch 取原键并重新授权，纯函数不接受用户指定查询权限。
2. 时间由调用方提供带时区datetime，按UTC比较；不读墙钟。旧SQL `btrim`只去普通空格、`lower`不等于Python `casefold`。存储侧和允许列表输入侧分别保留来源规则，TAB和ß/SS已有拒绝测试；没有在此证明PostgreSQL不同collation与MariaDB所有Unicode比较完全一致，原生存储接入时需验证或显式固定规范。
3. `ack_matches`只是条件对照；实际组合入口 `owned_ack_decision`先处理legacy已成功的幂等早返回，返回 `transition=None`，晚来的失败不能撤销成功。managed陈旧attempt/generation拒绝。legacy同owner没有attempt防护这一来源缺口仍明确可见；纯规则不能替代原生持久锁/CAS和身份校验。
4. 源managed确认任务过期没有调用legacy manual sweep，可能停留pending但不可领取。当前生产managed端点为0，不能省略未来修复也不能凭此新增自动重投。legacy普通重领依赖Companion本地查重；没有真实客户端来源及写入验证就不能声称“不会重复建单”。
5. `recover_finalize_result`只消费同scope的持久回读；仍被租用/读失败/未知状态回manual，不重新建单。纯返回不代表已提交事务。重试是否实际调度、账号锁、attempt token、客户端ACK验签、历史状态镜像都未接原生消费者。
6. 新代码不输出日志、读环境或调用网络；纯模块依赖与墙钟/IO禁用由定向检查保护。没有新前端消息入口，不新增产品翻译或宣称语言验收。

## 验证

`tests/test_cowork_push_lifecycle.py` 29项覆盖排队不成功、ACK覆盖布尔、waiting_lock分支、失败耗尽、duplicate_confirmed的SQL真值、内部ERP边界、账套/端点/代次/attempt、零次计数、期限边界、固定目标、终态晚ACK、未知finalize、显示与防重分离、孤儿日志、同目标原凭证和查失败拒绝。`tests/test_cowork_push_purity.py` 2项静态边界检查适用于全部新targets/push纯包。

`tests/test_cowork_push_source_parity.py`绑定7个固定源文件SHA，在不导入旧应用的前提下执行原定义：229组分类、480组有效owned ACK的SQL参数状态效果、340组外单号派生、5组目标冻结、208组旧async发起层结果分类。数据库出口用记录参数的内存sink；这不验证真实SQL锁、令牌认证、native CAS或外部ERP写入。正常失败与成功都纳入，不靠只比较HTTP状态认一致。

候选环境使用上一批镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`、Python3.14.7；`docker run --rm --network none`只读挂任务树，选定的源文件正文从stdin传入后重新核对hash。Colima不共享主机 `/tmp`，第一次直接挂source目录因找不到文件退出1，未执行差分断言；改stdin后上述1262组比较通过。没有启动或改动任何Site、DB或Redis。

完整本批汇总及交付首尾复核写在工作包，不能把本页纯测试数量加总成真实业务验收。
