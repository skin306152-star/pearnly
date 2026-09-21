# Companion 领取与 ACK 的 HTTP 边界审查

日期：2026-09-21。范围：第十二批机器 RPC 的框架只读审查、独立 ACK 测试与实测问题记录；本批新增 40 项独立 WSGI 测试通过，不代表客户端验收。候选提交及其余整体验证由主任务总记录绑定。

任务树基线 `8abf444b6ee953d485cee9e5f7691e6a47244965`；开读时工作树无未提交改动。初始审查只新增本文件，后续授权另增本文末尾登记的 ACK 测试脚本，不修改认证、账本、运行环境或生产。审查期间其他代理可以继续在其所有权内工作，因此该开读状态不代表交付时整树无改动。

## 核实环境与证据

使用 Docker context `colima-pearnly` 中已存在的镜像：

`sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`

仅运行临时 `--rm --network none --read-only --memory 256m --cpus 1 --entrypoint /home/frappe/frappe-bench/env/bin/python` 读取镜像内指定源码；没有挂载 Site/数据库卷、启动 bench/数据库/Redis、读取配置凭据或访问网络。镜像中 `frappe.__version__` 源码为 `16.33.0`。下列框架相对路径均从 `/home/frappe/frappe-bench/apps/frappe/frappe/` 起算。

| 文件 | SHA-256 |
| --- | --- |
| `app.py` | `1e910e76bcbf596dc708dbd4d8f682f6a6e37368971afcca5f4512efeaa845f9` |
| `auth.py` | `69e0a665d37a7ffe7b014db3d85de5443179491eea1a8e8262c8b8d6e818695b` |
| `database/database.py` | `ce1199fe0e56f6534d4be591795c422166b7f0fdec4e423ab7060f36cdacfa00` |
| `utils/error.py` | `52028218c21c4e25592aaf5082ba1bfb41e020fd2460fe74ffb713f799dd07e2` |
| `utils/response.py` | `a375a9111b680422c09b66928e024ade622e5af32a107cd3bb90536bea804de9` |
| `__init__.py` | `f507694f573b42b1431489f5d74dc6f862bd271556152f91045329b2f99c5151` |
| `exceptions.py` | `4d2a8a9dede8c2b2fc291dafc4558913aced12625f70ee7fe7c7754eef6ecc53` |
| `recorder.py` | `6549ec1f6107ec317b6128e09a0e47ede8b58e367f80a641d19e821e2875acc8` |

## 1. 领取响应必须位于 durable commit 之后

框架的正常非流式响应顺序是：

1. `app.py:139–141` 执行 `init_request` 和 `validate_auth`。
2. `app.py:157–158` 进入 API，先构造 Python `Response`；此时尚未发送 HTTP headers/body。
3. `app.py:186–187` 执行 `sync_database()`；`458–466` 对 POST 执行 `db.commit(chain=True)`。
4. `app.py:194–203` 执行 after-request hooks、日志和 response 处理，然后返回 Response。
5. 镜像内 `werkzeug/wrappers/request.py:188–196` 先等待被包装函数返回，再调用 `resp(environ, start_response)`；因此正常路径不会把已构造的领取载荷提前发送出去。

不能把这个正常顺序扩写成“所有提交失败都有安全 JSON 错误”。`sync_database` 位于 try 的 `else`，其异常不会进入 `app.py:181–184` 的 `handle_exception` 和 rollback 分支。`finally` 仍会运行 after-request hooks，但随后异常会穿出 WSGI；Werkzeug 此处只把 `HTTPException` 转 Response，不处理任意数据库异常。最终服务进程如何生成错误响应需要实际 HTTP 故障注入证明。

数据库提交又有两段：`database/database.py:1185` 先运行 `before_commit`，`1187–1191` 发送 SQL COMMIT，`1193–1194` 清缓存并运行 `after_commit`。所以 `commit()` 抛错可能发生在 SQL 已经提交之后。此时再 rollback 只能作用于后续事务，不能证明先前 lease、Attempt、Delivery 与 `__Auth` 没有持久化。连接断开造成的 COMMIT 确认丢失同样不能当作“未发生”。

本批 HTTP 接入的最小约束：

- `companion/transport.py:69–108` 的领取及 Delivery 创建继续由同一事务承接，内部方法不 commit；公开队列边界在任何 payload 暴露前完成持久提交。
- 若选择在 handler 内显式 commit，可把第一次提交失败转为固定、无敏感上下文的错误；但框架仍会再做一次 `sync_database` 提交，不能省略这次失败场景的验证，也不修改框架去跳过它。
- 领取提交失败后不自动重跑 lease，不创建第二 attempt，不发送先前生成的 jobs/payload。若原事务已经落盘，保留原 attempt 和锁，由现有过期 unknown/manual 路径收敛；响应丢失不构成安全重发依据。
- `QueryDeadlockError` 的有限重试只能包围尚未提交、没有外部输出的完整本地事务，重新鉴权和读取。不能把所有 `commit()` 异常套入同一个重试循环。
- 单纯返回 `{ok: false}` 或设置一个错误 HTTP 状态不会触发框架 rollback：`sync_database` 依据请求方法/commit flag，而非响应码。需要回滚的失败应走实际异常/明确事务回滚路径。

主任务后续在 `companion/queue_api.py::_run` 实施了分段边界：提交前仅对本地死锁最多三次回滚重验；显式 commit 单独捕获异常并返回无快照 503，不重跑 service。若死锁后的显式 rollback 自身失败，也终止重试并转为 `ack_rollback`/`lease_rollback` 的安全错误；不能继续使用未确认回滚的事务。

框架第二次提交的限制仍保留，不通过修改 Frappe 消除。主脚本的 `test_outer_framework_commit_failure_does_not_expose_success` 在 handler 提交成功后让第二次 `Database.commit` 抛错，期望 WSGI 调用抛出异常而非返回成功 payload，同时验证首次提交的 lease 仍存在、后续 poll 不重发。它验证“没有成功响应”和持久状态，不证明外层服务/代理会生成固定 503 JSON 或完整 no-store headers。此场景与 handler 内 before_commit 失败、after_commit 已落盘失败必须分别记录。

## 2. ACK 权限与原发行人的 IP 限制

现有 `receipt_auth.py:44–65` 按 Delivery 冻结关系锁 Connection→历史 Credential，刻意不检查 dispatch 总闸、当前 active credential、发行人角色/授予、连接 enabled/config revision。`74–91` 只允许匹配历史 secret 的这一张 Delivery，并在写入前重验。这是接收已经授权尝试的回执，不授予重新领取或其他 RPC 权限。

开读基线的 `ReceiptContext` 没有 issuer 字段（`receipt_auth.py:18–31`）；主任务后续已从锁定的历史 Credential 增加该字段。公开 ACK 认证桥不能为了复用 heartbeat 而调用 `credentials.authenticate()`：后者 `credentials.py:238–250` 会检查总闸和当前发行人权限，导致关闸/撤权之后无法收回真实结果。也不能从请求正文取得 issuer，或改用 Guest/Administrator 绕过原发行人的限制。

本批采用从已经锁定并与 Delivery 匹配的历史 Credential 取得原 `issuer`，只绑定到本次精确 ACK 请求的私有上下文。解除 `session_obj` 关联后，由 Guest 调用 `frappe.set_user(issuer)`，继续原生 `validate_auth` 流程：

- `auth.py:633–640` 读取 Authorization、执行 OAuth/API key、再执行自定义 hooks；机器明文仍须由 before-request 私有 envelope 交接并移除请求头，不进入前两条通用认证路径。
- `auth.py:651–652` 在 Guest→非 Guest 后自动调用 `validate_ip_address(issuer)`，不得省略或改用一个无限制的替代账户。
- `auth.py:476–501` 只读取 User 的 IP 限制及系统原有 2FA bypass 规则，不检查 `User.enabled`；`core/doctype/user/user.py:1420–1424` 也仅解析 `restrict_ip`。因此 disabled issuer 可以仅收其历史 ACK，同时保留其原 IP 规则。
- `frappe.__init__.py:382–395` 的 `set_user` 只改 request-local session/cache/form 字段，不做交互登录，也不检查 enabled。是否能调用专用 ACK 必须由不可伪造的请求上下文控制；不得让这份已撤权身份通过普通 RPC、DocType REST、覆盖方法或 API v2 别名。
- 原发行人 User 不存在/无法确定时应拒绝，不能降级到无 IP 限制的身份。IP 来源保持框架 `auth.py:62–73` 的既有代理/remote-address 行为；本批不修改反向代理信任配置。

“允许撤权后的 ACK”不会绕过每张 Delivery 的 Site/Connection/credential generation/attempt/fence 绑定。新 token 不能 ACK 旧 generation 的 Delivery；历史 token 不能用于新领取、heartbeat 或 ACK 其他人的 Delivery。

## 3. 错误快照与明文 token

`app.py:448–449` 在 HTTP ≥500 或 developer mode 下调用 `log_error_snapshot`。`utils/error.py:20–25,168–170` 用 `isinstance` 排除 `AuthenticationError` 及其子类，但不排除普通 `PermissionError`/`ValidationError`。`utils/error.py:68` 默认使用 `frappe.get_traceback(with_context=True)`，错误记录还会进入 deferred insert（`95–101`）。响应 `disable_traceback` 不等于阻止此持久化快照。

已有 `http_auth.py:103–121,169–184` 在私有 envelope/认证边界使用无快照异常；`140–166` 定义保留 403/503 状态的 AuthenticationError 子类，并只记录固定事件、阶段和异常类型。新增 queue 入口需要维持这一约束。

ACK 还有另一份秘密：`transport.py:118–122` 将 `delivery_store.secret()` 解密出的 attempt token 传给 `push.native.service.ack`；`service.py:502–512,542` 的拒绝或意外异常可以把该参数留在 traceback locals。仅清 Authorization 不能保护这份密钥。新增 HTTP ACK 边界必须把确定的业务拒绝转为保留适当 4xx 的无快照异常，未知本地错误转为固定 503，且不记录原异常字符串、traceback、body、payload 或 headers。内部服务继续保留可区分的异常和事务语义，不在内部假称成功。

真实 WSGI 测试另外发现了**响应消息列表泄漏**：`frappe.throw(secret, ValidationError/PermissionError)` 在抛异常前会把消息加入 `frappe.local.message_log`。仅改抛 AuthenticationError 子类、设置 disable_traceback 和 no-store，仍会让原消息进入 v1 响应 `_server_messages`。本批两个 throw 子场景均真实复现；这与 Error Log/Recorder 是不同的通道，之前 bare `raise` 测试通过不能证明此处安全。

主任务已在 `http_auth.py::_private_error` 统一清空 `frappe.local.message_log` 并移除 `frappe.flags.error_message`，再返回固定的私有错误；`_deny`、`reject_request`、`_deny_permission`、`raise_busy` 共用该入口。这是产品机器错误边界的局部处理，不清普通请求消息。对应原测试保留实际解密 canary、真实 `frappe.throw`、整个响应及 `_server_messages` 断言，没有改弱断言；修复专项和最终 ACK 全套均已通过。

后续还验证了**重试后成功的消息残留**：第一次真实 ACK 已执行，再以 `frappe.throw(secret, QueryDeadlockError)` 触发回滚，第二次正常成功。仅清错误终态不足以保护这个 200 分支。主任务抽出 `clear_private_messages()`，在 queue 本地回滚后、成功 commit 后以及 auth-hook 死锁回滚后调用。新增 ACK 用例验证第一次 message_log 确实含 canary、第二次调用前 message_log/error_message 已清、最终成功 DTO 和 `_server_messages` 不含秘密，且账本仅一次成功；另一个 auth-hook 用例以三个子场景覆盖 heartbeat/lease/ack。两项均通过，后者按一个独立测试计数。

Recorder 是独立路径：`recorder.py:434–440` 的原生 `do_not_record` 清理并移除本请求 Recorder。现有桥在机器候选、成功、拒绝与 after-request 均局部调用；增加 lease/ACK 后，必须把这两个入口及其禁止的别名纳入机器候选。不得只给成功 handler 包装，也不全局关闭普通请求录制。

原生 IP 检查发生在自定义 hook 返回后。此时 envelope 必须已销毁、Authorization 已移除、私有对象 repr 不含 token。IP 拒绝的原生 AuthenticationError 无快照；IP 读取的意外 500 仍可能走框架诊断，因此要验证其中没有原 bearer/attempt token，不能宣称整个框架都使用了产品的安全 503 包装。

## 4. no-store 与会话边界

当前 `files/api.py:50–54` 的私有响应标记设置 `Cache-Control: no-store`、`Pragma: no-cache`、`X-Content-Type-Options: nosniff`。`http_auth.py:253–271` 再对机器候选清 CookieManager、删除 Set-Cookie，并把 3xx 改为不带 Location 的 401。应复用并扩大精确机器候选覆盖范围，不改变普通请求。

框架 `app.py:194–211` 的 after-request 在成功及受处理异常后都执行；`process_response` 在其后才调用 CookieManager.flush（`310–313`），所以仅删除 Response 上的 Set-Cookie 不够，还需要清 manager 中的待写/待删 cookie。现有 session detachment（`http_auth.py:224–231`）也须保留，防止 `sync_database` 在 `470–472` 注册 session.update。

`process_response` 的 `app.py:307–308` 会最后合并 `frappe.local.response_headers`，因此验证必须看最终 WSGI headers，不能只检查 hook 内的字典。框架异常在 sync/WSGI 外层逃逸时，不能以 hook 曾执行证明最终外层响应一定有这些 header。

## 5. 建议定向验证矩阵

以下使用本任务隔离 Site、假外部出口、真实 `Client(application)`/WSGI 和合成 token；真实执行由主任务串行完成，本文作者没有自行启动 Site 或并发执行。`buffered=True` 或显式 close 响应，使 Recorder dump/after-response 真正执行。

| 场景 | 必须验证 |
| --- | --- |
| 正常 lease | HTTP bytes 输出前另一数据库连接可读到 Record leased、Attempt、Delivery 与加密 `__Auth`；回包只有规定的非秘密 log_id/payload |
| Delivery/Password 插入失败 | Record/Attempt/锁/Delivery/`__Auth` 同事务回滚；无 jobs、payload、明文 secret 出现在响应/日志 |
| 提交前失败 | 在 SQL COMMIT 前注入失败；不重领、不返回原 payload；实际 DB 无新增持久 claim |
| 提交后回调失败 | SQL 已提交后 before-return/after_commit 注入异常；原 attempt 仍唯一、第二 poll 不重发；原锁保留，过期按 unknown/manual |
| 框架最后一次 sync commit 失败 | 显式 handler 提交成功后，在 `sync_database` 的提交处注入；确保没有成功载荷泄漏，并记录实际 WSGI 失败形态，不能只测 handler |
| 响应丢失 | 持久提交后客户端不接受 body；重复 poll 不创建第二 attempt；按真实已提交状态恢复 |
| ACK 身份变化 | 依次撤销 token、移除发行人角色/范围、禁用 issuer、禁用连接、关闭总闸，旧 Delivery 的正确回执仍受理；同身份 lease/heartbeat 拒绝 |
| ACK IP | 限制原 issuer IP：匹配地址允许，不匹配由原生 AuthenticationError 拒绝；disabled issuer 仍执行同一 IP 检查；body 伪造 issuer 无效 |
| ACK fencing/replay | 新 token→旧 Delivery、其他 Delivery、错误 fence/secret 拒绝；完全相同回执幂等，晚回执不回退 success |
| 快照 canary | developer mode 下分别注入 PermissionError、ValidationError、未知 ACK 异常和 IP 读取异常；同时搜索响应、Error Log/deferred cache、logger 捕获和 Recorder 中的 bearer 与解密 attempt token |
| 路由/会话负例 | GET、API v1/v2 别名、cmd/login、sid query/body、有效 cookie、API key 混合、method override/server script 均拒绝，不创建/更新 Sessions/Redis session |
| 最终 headers | 成功、空队列、4xx、503、坏 JSON、3xx 拒绝均检查最终 no-store/no-cache/nosniff，无 Set-Cookie/Location |
| 普通请求阳性对照 | 普通 ping/浏览器请求不被机器 guard 接管，原 session 与 Recorder 功能保持 |

## 审查结论与交付边界

锁定框架支持“先提交、后发送”的正常机器 RPC，但提交异常、提交后回调失败和 ACK 秘密快照需要在新增 HTTP 边界明确处理和验证。原 issuer disabled 与 ACK-only 并不天然冲突；可以保留原生 IP 限制，无需创建替代用户或恢复旧权限。

已完成：固定镜像源码核对、应用调用链审查、独立 ACK 测试脚本和故障记录。主任务已实现 lease/ACK HTTP 装配，本批新增 40 项独立真实 WSGI 测试全部通过，计数与证据见下表。客户端/网络代理验收、部署和外部 ERP 验收未执行。本文不把第十一批内部 transport 测试替代为第十二批 HTTP 通过证据。

## 独立 ACK WSGI 脚本

后续限定授权新增 `scripts/cowork-companion-queue-ack-http-lab-verify.py`，通过 `runpy.run_path` 只复用主脚本 `cowork-companion-queue-http-lab-verify.py` 的 fixture 方法，不继承其测试类或重复运行父测试。每项使用独立合成身份、Workspace、Connection、History 和 Credential；执行过程会提交本地测试记录，Site 配置与 Recorder 开关在结束时恢复，必须与共用该 Site 的测试串行运行。

首批定义 **20 项**：成功/重复/晚失败、waiting_lock 后新 fence 拒旧 409、明确失败耗尽与冲突、过期 unknown 保锁及晚回执、轮换/撤销凭据、disabled issuer 的原生 IP 拒绝与允许、角色撤权、禁用公司/连接、总闸关闭、hook→write 的 Credential/Delivery 重验、History 范围拒绝、ACK 局部死锁重试/重验/耗尽、developer mode 下秘密不进 Error Log/Recorder，以及 SQL 提交前与 after_commit 回调失败的不同持久结果。

秘密诊断使用实际解密的合成 attempt token 作为 canary，验证其确实到达原生 ACK 参数后再注入异常；不输出该值或 bearer。Recorder 包含普通 ping 阳性对照。提交故障挂原生 before_commit/after_commit 回调，不替换 Database.commit 签名、不自动重试提交。

运行入口（由主任务在已核实的隔离 lab 中统一执行）：

```bash
PEARNLY_COWORK_TEST_SITE="<主任务已创建的隔离 Site>" \
  env/bin/python /workspace/scripts/cowork-companion-queue-ack-http-lab-verify.py
```

Site 名必须使用主任务实际创建且带 `pearnly_backend_lab` 标记的测试 Site；此处不是开户/启动指令。支持重复 `--test test_方法名` 定向复跑。初次脚本交付时仅 AST、架构检查和 diff 格式检查通过，20 项真实 Site 测试交由主任务运行；最终结果由主任务绑定候选版本记录。

后续增加 3 项且不修改已交付的原 20 个测试函数：

- 已解密并调用原生 ACK 后抛死锁，只让 `queue_api._run` 的首次显式 rollback 失败；断言停止重试、安全 503、秘密不进入响应/快照、最终框架回滚后记录不漂移。
- 使用真正的 `frappe.throw(secret, ValidationError/PermissionError)`，检查 `_server_messages` 与整个响应。它覆盖 `message_log` 已写入秘密、只换异常类仍可能泄漏的通道，不以 bare `raise` 替代。
- ACK 的私有请求上下文不能调用 lease/heartbeat 的上下文读取或公开函数；正确 ACK 仍完成。

在上述 23 项后追加成功重试消息清理用例，ACK 脚本最终共 **24 项**。首轮原 20 项中 19 通过，关闸项受公共夹具未清 `get_site_config` 60 秒进程缓存影响；主任务在 `change_config`/tearDown 使用官方 `frappe.config.clear_site_config_cache()` 后，关闸和两项 developer mode 诊断测试重新通过。这是测试夹具修复，不是关闸授权逻辑放宽。

过程中另有一次名为 `twelfth-queue-ack-http-final.log` 的 23 项运行出现 4 个 setup 错误：专有 lab 反复创建合成用户积累了联系人辅助任务，触发 `QueueOverloaded`，不能记录成业务断言通过。主任务报告该辅助队列累计约 650 项，仅清理本 Site 内已核实对应 `cw-*@example.invalid` 的 454 个合成联系人任务；公共 fixture 之后仅在合成用户/凭据准备阶段使用局部 `in_install` 使 Contact 同步创建，HTTP 测试请求仍走原生流程。没有清业务队列、改派发保护或删减断言。最终完整 ACK 日志以下面的 `complete` 文件为准，不因旧日志文件名含 `final` 而忽略其失败。

## 最终新增 WSGI 结果

主任务提供结果，本文已核对下列日志的 `Ran … / OK` 汇总；日志位于本机 `/tmp/pearnly-cowork-migration-20260920/`，未纳入业务源码或对外发布。

| 独立测试组 | 结果 | 证据 |
| --- | --- | --- |
| Queue HTTP 主组 | 15/15 PASS，6.098 秒 | `twelfth-queue-http-final.log` |
| Auth 成功重试清消息，含 heartbeat/lease/ack 三子场景 | 1/1 PASS，0.507 秒 | `twelfth-auth-retry-messages.log` |
| ACK HTTP 完整组 | 24/24 PASS，7.772 秒 | `twelfth-queue-ack-http-complete.log` |
| **新增独立 WSGI 测试合计** | **40/40 PASS** | 主组 16 + ACK 24 |

真实 throw 泄漏修复专项 1/1、关闸/开发模式重验、其他分段复跑均属于这些测试的重复运行，不额外累计。上述通过证明本地 Frappe WSGI、数据库事务、私有权限和故障处理；没有调用 Windows 客户端或外部 ERP，也不证明网络代理最终错误页面、真机运行或部署完成。
