# Companion 受限 HTTP 认证与心跳入口（第十批）

本批将第九批的内部凭据校验接到**唯一机器入口**：`POST /api/method/pearnly_cowork.companion.heartbeat.report`。只接受 Authorization 中的 Bearer `exp_` 凭据；人类 cookie/API key、body/query token、API v1/v2 别名、`cmd` 分派和其它路径都不能获得机器身份。服务端报告与刷新回执使用新的原生 Connection/Credential；不导入旧 endpoint/token/issuer/root 数据，不解除八至九批历史全量配置漂移 HOLD。

没有新增客户端、推送 poll/ACK、外部运输或部署。`connected=true` 是已收到本次报告的合同值，不证明真实 Windows 二进制、设备与 ERP 写者可用，也不证明准备资料齐全。

## 当前源码与证据

- 工作树：`/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`；本批基线 `70383cdf5e8f45129997c8e773e0bcf3772422a9`。初始工作树干净；后续 root/其它代理各自修改的连接、报告、hooks/schema 与文档不归本分工。
- 主任务已复核第十批线上 source/runtime 与第九批交付证据一致后授权本地实现；固定旧 SHA 仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。详细 gate 见本任务主记录，不将固定旧源码的存在替代当轮线上证据。
- 框架来源是候选镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，Frappe 16.33.0。此次通过 `docker --context colima-pearnly run --rm --network none --read-only --memory 256m --cpus 1` 只读镜像内源码；没有挂 Site/数据库卷，没有启动 lab，没有读取凭据。
- 镜像内框架根：`/home/frappe/frappe-bench/apps/frappe/frappe/`。以下 SHA256 绑定本次实际读取的文件：

| 文件 | SHA256 |
| --- | --- |
| `app.py` | `1e910e76bcbf596dc708dbd4d8f682f6a6e37368971afcca5f4512efeaa845f9` |
| `auth.py` | `69e0a665d37a7ffe7b014db3d85de5443179491eea1a8e8262c8b8d6e818695b` |
| `__init__.py` | `f507694f573b42b1431489f5d74dc6f862bd271556152f91045329b2f99c5151` |
| `sessions.py` | `6bf965d99cd9dd03d9bd212d38e9f48724d6f649c4ce1d6ad491144c8ca2c894` |
| `handler.py` | `f8adf6b97e885f5208c0147eb4812911a1d2196276fe45d38a317f9f57a0fe99` |
| `api/__init__.py` | `c9fabae9d1026d9bb65a4ed028eb6878d10b8c3ed94071e32aa6ce52b8540543` |
| `api/v1.py` | `93996b93c3aa647df279cf0f14641262bacdf3d28aa84d4cdd03f1f409827abc` |
| `api/v2.py` | `c6f7519a26d51cbd294814e92e092c6bc8f6ce6703f4df2b5ef93b0f0875dde6` |
| `recorder.py` | `6549ec1f6107ec317b6128e09a0e47ede8b58e367f80a641d19e821e2875acc8` |

## 真实框架顺序与触发反例

1. `app.init_request:214-245` 先建 Site/DB、`make_form_dict`，随后 `HTTPRequest`，最后才 `before_request`。`HTTPRequest:33-51` 先恢复会话、再 CSRF；`LoginManager.__init__:130-143` 会因 `cmd=login` 先进入交互登录。**单独 auth_hooks 太晚，不能保证 cmd=login 不创建会话。**
2. `auth.validate_auth:629-652` 按 OAuth → API key → auth_hooks 执行。最后只有 session 仍为 Guest 才拒绝两段 Authorization；已有浏览器 session 可能令错误 Bearer 被忽略。认证桥不能写成“已登录就跳过”，必须检查所有机器候选，包括错误路径、错误格式、已有 cookie。
3. `app.application:146-158` 对真值 `form_dict.cmd` 的分派优先于 URL。`POST` 精确心跳 URL + JSON `{"cmd":"frappe.auth.get_logged_user"}` 会先调用另一个函数；不能只检查 request.path。Session 构造还会在 `sessions.py:222-224` 提前 pop `form_dict.sid`，所以必须检查原始 query/form/JSON，不能只看已处理 form_dict。
4. `api/__init__.py:91-100` 同时注册 `/api` 与 `/api/v1`，允许末尾斜线。`api/v1.py:35-41` 进一步把 method 的首个 `/` 后内容丢弃，因此 `...report/ignored` 也能进入同一个函数。`api/v2.py:274-286` 有另一路 RPC。所有这些别名均被机器入口拒绝。
5. `handler.execute_cmd:65-86` 会先做 override_whitelisted_methods、Server Script 映射，再取实际方法。桥在授予请求身份之前拒绝对目标 METHOD 的 override 或 API Server Script 映射，避免身份落到另一个执行器。
6. `frappe.set_user:382-395` 会原地修改 local.session 的 user/sid、清空 session.data 和 form_dict。原 local.session 别名指向 `Session.data`；`app.sync_database:470-472` 又会在响应后执行 `session.update`。直接 set_user 会污染原 Session/Guest 缓存，且丢掉报告正文。
7. Core 已有 before_request 可能先对 `/`、`/login` 重定向，早于 Cowork 守卫。机器响应 after_request 把这类提前 3xx 变为 401 并清 Location；不修改 Core 的普通人类登录流程。
8. Recorder 启用时，`recorder.py:345-346` 保存全部请求头/参数；`auth.py:682` 会在 auth_hooks 前用完整 Bearer 查询 OAuth Token。Recorder 的 SQL 和最终 Redis dump (`410-417`) 可能留下 token/目录正文。no-store 或 Cookie 清理对此无效，必须按请求停止录制。

上述为固定框架源码中的确定调用顺序。可复现 WSGI 场景已写入本批脚本，实际 WSGI 执行见文末，安装、迁移和总交付证据由主任务统一记录。

## 已实现结构

所属 App：`pearnly_cowork`。入口和编排仅在 `companion`；权威凭据仍为第九批 Credential，权威报告/目录/刷新属于 `connections`。依赖为 `companion.http_auth → companion.credentials`，`companion.heartbeat → credentials + connections.reports/collector`；credentials 不反向导入 HTTP。

| 文件或 hook | 责任 |
| --- | --- |
| `companion.http_auth.before_login` | 在原生登录验证和会话创建前，拒绝机器路径或 exp_ 认证头触发的交互登录 |
| `companion.http_auth.before_request` | 严格 POST/精确路径/canonical Bearer、拒任意 sid cookie、raw cmd/sid、非 Guest session、认证来源覆盖；JSON 必须对象或空正文；冻结本请求私有封套后移除本请求 Authorization |
| `companion.http_auth.authenticate_request`（auth_hooks） | 一次消费绑定同 request 的私有封套、重查边界与实际分派；内部 authenticate；新建独立 request-local session 后 set_user(issuer)，保留原报告正文的深拷贝和私有对象能力 |
| `companion.http_auth.current_request` | handler 再验证私有能力、同请求对象、同 Site、路径/方法和当前 issuer；只返回上下文与正文拷贝 |
| `companion.http_auth.after_request` | 只对机器候选清待发及已发 Set-Cookie、提前 redirect、no-store/no-cache/nosniff；错误路径同样覆盖 |
| `companion.heartbeat.report` | 唯一 POST 白名单机器函数；从私有能力取正文，当前身份复验、Connection 锁、纯 plan_report、collector，同一事务 |

机器认证成功前，将 `frappe.local.session` 从原 Session.data 脱离；成功后 `frappe.local.session_obj=None`，不会被 sync_database 注册 session.update。CookieManager 的待发 Cookie/删除列表均清空，既不创建登录 Cookie，也不通过删除 Cookie 强制用户浏览器退出。最终 local 数据由框架每次 `frappe.init(force=True)` / destroy 清理，不跨请求复用身份。

对机器候选调用原生 `@frappe.recorder.do_not_record` 的窄函数；before_login/before_request/auth_hook/after_request 都覆盖，清本请求 Recorder 并恢复 SQL 包装，不更改 Recorder 全局设置。普通 API 和人类登录不受影响。没有打印或持久化 Authorization 明文。

合法 canonical Bearer 在 before_request 验证一次后，token/body 放在 `repr=False` 的私有 `_PendingRequest`，绑定原 request 对象与不可伪造 authority；随后仅删除这个 request.environ 的 `HTTP_AUTHORIZATION`。因此框架 validate_auth 的 `authorization_header` 是空值，不会进入 OAuth/API key 查询。auth hook 只消费冻结封套，在 finally 清除；身份改变后，原生 `validate_ip_address(issuer)` 仍执行。普通请求没有走此分支，原认证头和流程均保留。

实际故障注入已确认旧的“只包 custom auth hook”不足：原始 bearer 可从 `validate_auth.authorization_header` 进入 Frappe 的 with-context Error Log。框架 Error Log 排除 AuthenticationError 子类，但 developer_mode 仍会记录普通 PermissionError。现机器初次鉴权/交接意外错误与本地冲突耗尽用专用 AuthenticationError 子类保留 HTTP 503，机器鉴权权限拒绝保留 HTTP 403；只写固定事件码、阶段和异常类型，不写异常原文或 traceback。交接本身也覆盖合法深 JSON 导致 deepcopy RecursionError 的路径。原生 IP 校验的普通未知错误继续记录诊断，真实快照断言验证其中没有机器 token；没有全局停用错误日志，也没有吞掉普通报告异常。

## 写入与刷新装配

`report(**ignored)` 忽略路由传入参数来选择身份和正文，使用 hook 绑定的拷贝。每次尝试先 `credentials.revalidate`，按 Connection→Credential 的既有顺序取得当前 token generation、issuer/角色/范围/显式连接授予、启用和总闸；再读取同一 Connection 当前行。旧 token 在 hook 后被轮换或发行人撤权时，写入必须拒绝。

纯报告输入：

```python
previous = {**model.parse(doc.get('report_json'), {}), **model.parse(doc.config_json, {})}
plan = plan_report(body, previous, datetime.now(timezone.utc))
collector.ingest(doc, plan, credential_id=context.credential_id,
                 token_generation=context.token_generation, issuer=context.issuer)
```

任何异常在保存点回滚；已知 ReportError/ConnectionError/ProjectionContractError 用稳定 code 翻译后拒绝。初次 credentials.authenticate 与报告写入分别只有本地 QueryDeadlockError 可完整 rollback 后最多三次重试，每次重验当前授权且不保留失败尝试的身份；初次鉴权的当前行锁可能遇到会话阶段已建立的 RR 快照，不能只在 handler 内重试。耗尽返回稳定 companion.report_busy / HTTP 503；权限拒绝不重试。没有服务 commit、外部 HTTP、ERP 派发或“报告成功即入账”。

刷新机器线协议保留 `master_refresh.request_id` 及原回显字段；request_id 的值在新平台代表每次领取的随机 lease handle，绑定当前 credential/generation/revision/expiry，不等于人类 Refresh ID。重复、过期、轮换和错误回报由 connections.collector/refresh 处理。其业务规则与真实本地验证由主任务记录；这里仅验证 HTTP 身份接线与一条真实报告→目录→人类刷新→机器领取/回报链。

## 明确协议适配与未迁边界

- 使用新原生 Connection 和新发行凭据，响应采用原生 RPC 的 `message` 封套。不是旧 URL/裸 JSON 的兼容代理。
- 只接受 JSON object 或空正文；非法 JSON、JSON null/array/scalar、非 JSON 非空正文拒绝。旧路由曾把无效/非对象 JSON 当空对象；此处为已登记的新平台协议收紧，没有声称容错完全等价。
- 空正文 `{}` 仍可用；报告字段为原顶层字段，不额外要求 wrapper。身份、Site、issuer、连接只取认证上下文；正文不能改归属。
- 明确拒绝 sid（含 Guest cookie）、raw cmd/sid、API v1/v2 alias、尾斜线及多余 suffix。没有关闭全站 CSRF、IP 限制或普通会话验证。
- 不实现机器 poll/ACK，不把旧裸 log_id 转成当前 attempt；客户端 EXE/source/release 映射、是否兼容原生 URL/封套、新 lease handle、真实目录采集与 ERP 业务效果仍未验证。

## 验证方法与状态

离线：

```sh
PYTHONPATH=apps/pearnly_cowork:tests python3 -m unittest test_cowork_companion_http_auth -v
python3 scripts/check-architecture.py
```

本分工已执行 13 项纯 HTTP 边界测试通过；Python 语法检查通过。纯测试 AST 提取本文件中的无 IO 边界函数，不使用伪 Frappe 模块；实际框架行为交给 Site 测试。当前 http_auth/heartbeat/collector 依赖组合架构检查通过（0 violations），主任务另绑定全候选检查。

真实 Site 脚本为 `scripts/cowork-companion-heartbeat-lab-verify.py`。它复用既有独立 Site 守卫，只允许指定实验 Site 和 `.cowork-backend-lab` 标记；无网络调用、不启动/迁移环境。26 项真实 WSGI 用例覆盖：

- 原 JSON 报告→本站目录、human refresh→机器领取→回报。
- Session 数量/Guest Redis 前后不变、Session.update 不被调用、真实已有 cookie/session 不被改写。
- 精确路由、API/CRUD/v1/v2/GET/HEAD/OPTIONS/网站 redirect 拒绝；raw cmd/sid；cmd=login 在 password authenticate 之前拒绝。
- 人类 API key、cookie、body token、错误头格式无机器能力；后续请求不继承上次上下文。
- hook 后 role/rotation 故障注入，写前复验仍拒绝；override/Server Script 不继承机器身份。
- 报告写入后已知错误整单回滚，只有本地 DB 冲突重试。
- 真实 Recorder 启用时，机器成功/拒绝/坏 JSON 不产生条目，普通 ping 继续生成条目；保留/恢复全局配置。
- 初次鉴权冲突一次成功、三次耗尽、重试期间撤角色拒绝；报告冲突三次耗尽；不重试权限拒绝。
- 初次鉴权与 set_user 注入含合成 token 的异常，安全 503 且实际原生 log_error 调用为零。
- 私有交接后 OAuth/API key 解析不调用，Guest 会话恢复及 issuer 原生 IP 校验继续调用；issuer IP 500 保留原生诊断，但 with-context 快照和响应均无 token。
- 总闸关闭加 developer_mode 的真实权限拒绝返回 403，无原始鉴权快照；550 层合法 JSON 实际触发深拷贝失败，安全 503，无原生快照。

脚本会提交带合成标签的本地用户、连接、凭据和报告数据；HTTP 所需测试 Site 配置暂改后恢复，Recorder 只检查本测试新产生的条目，不清全局缓存或既有记录。主任务发现旧 lab 三容器/卷已不存在，本批将重建基线并升级；不得声称旧物理卷延续。实际 Site 测试、安装/迁移与交付 gate 结果由主任务统一记录。


### 最终 HTTP 本地执行证据

主任务授权本分工在已启动的专用 `cowork-backend-heartbeat.localhost` 执行；执行前核对 context `colima-pearnly`、project `pearnly-cowork-backend-lab`、候选镜像 d5c874…、本任务树只读挂载、独立 sites 卷、internal 网络无网关和无发布端口。

```sh
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T \
  -e PEARNLY_COWORK_TEST_SITE=cowork-backend-heartbeat.localhost \
  bench env/bin/python /workspace/scripts/cowork-companion-heartbeat-lab-verify.py
```

最终 **26/26 通过，1.105 秒，退出码 0**；完整实验日志 `/tmp/cowork-tenth-http-final26.log`，SHA256 `3a5cd741fce87f3997047958550994f2f9f0f21a2e6dac8a98a442d864d38fe9`，明文凭据模式匹配数为 0。包含真实 Recorder 启用/停录对照与原 Sessions/Redis 会话保持，全部使用合成数据。首跑暴露两项夹具适配：frappe.app 必须在 connect 之后导入；Sessions 是框架特殊表，按 sid 参数化查询而非 DocType 默认 name/creation。修复后保留原行为断言，先定向复验会话用例，随后补认证并发与日志边界，再完成全 26 项最终确认；没有修改框架或隐藏失败。

本分工没有启停环境、迁移 Site、操作其它任务的数据或提交代码。运行完成已释放 heartbeat Site 给主任务；本地 HTTP 通过仍不代表真实 Companion 客户端、外部 ERP 或生产部署验收。
