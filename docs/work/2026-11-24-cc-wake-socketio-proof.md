# CC 桌面版 socket.io 唤醒：实验栈证明（轮询降量第二步）

> 只是证明，不交合入；是否施工由主会话定。第一步（1.12.25 空闲降量）已进 main（c6fd4d177），评估与验收见
> [2026-11-24-cc-desktop-poll-traffic-评估.md](2026-11-24-cc-desktop-poll-traffic-评估.md)。
> 环境：本机独立实验栈（`MACLAB_STACK=pearnly-ccwake`，ERP，与线上同一 Frappe 16.33.0 镜像），测完连卷删除。
> 证明脚本 `scripts/cc-wake-socketio-lab-proof.py`（只连实验栈、不写数据）；原始输出在主会话共享笔记 `cc-poll-lab-1124/step2/`。

## 结论


| 要证明的 | 结果 |
| --- | --- |
| 握手能过 | 能过：访客身份（`Cookie: sid=Guest`）连站点命名空间，再 `task_subscribe` 一个由机器令牌派生的房间号。直接带机器令牌（`Authorization: Bearer pcc_…`）握手现在**被拒**（`44 … Unauthorized`）：Frappe 回调的 `frappe.realtime.get_user_info` 是 GET，不在我们机器鉴权钩子的名单里 |
| 服务端推「去领」CC 能收到 | 10/10 收到，延迟 6–20 毫秒（redis `events` 频道发布 → CC 收到）；另一个房间的连接 0 次收到 |
| 断线退避重连 | 停 socket.io 60 秒：断开后 16 秒、55 秒各试一次（第二次等到 502），恢复后 5.9 秒重新加入房间 |

## 鉴权建议：两条都不选，用「访客 + 派生房间」

- Frappe 16 的 `task_subscribe` 不查权限，任何连上的人都能进任意 `task_progress:<id>` 房间；服务端 `frappe.publish_realtime(room="task_progress:<id>")` 直接推。
- 房间号 = `cc-wake-` + sha256("pearnly-cc-wake|" + sha256(令牌))。库里存的就是 sha256(令牌)（`contracts.token_digest`），服务端不用新存东西；
  别人没有令牌也没有库，算不出 256 位的房间号。推的内容只有「去领」，不带任何单据数据，领什么仍由 `lease` 按现有鉴权判。
- 好处：不改机器鉴权钩子（`http_auth` 是安全边界，加 GET 名单或短票据接口都要动它），不进签发人的 user 房间（不会推到他的浏览器标签页）。
- 风险：依赖 Frappe「task 房间不查权限」这一行为，升级 Frappe 时要回归；施工时加契约测试钉住、实验栈冒烟。

## 依赖

只加 `websocket-client` 1.9.2（PyPI 2026-08-31 发布，wheel 94 KB，Apache-2.0，零依赖）；engine.io v4 / socket.io v5 的帧自己写约 150 行（握手、`2`/`3` 心跳、`40` 连命名空间、`42` 事件）。
不用 `python-socketio`：要再带 python-engineio、bidict、simple-websocket、wsproto，同步客户端还要 requests 一串。

## 要定的 / 注意

- 实验栈里 socket.io 回调站点用的是 Origin（被前端 nginx 改成 `http://站点` 无端口），容器里访问不到本机端口；我加了一个 80→8080 的临时转发容器才通。
  生产 Origin 是 `https://租户域名`，网页现在就在用 socket.io（日志里有 `GET /socket.io/ 200`），不受影响；施工后在测试租户真域名上复验一次。
- 往 redis `events` 频道发一条坏 JSON 会把 Frappe 的 socket.io 进程打崩重启（测试时我自己发错了一次）；只有 Python 端 `publish_realtime` 往里发，正常不会发生。
- socket.io 连接本身不算 Cowork 的「在场」：按需请求兜底仍要 30 秒（办公室约 29 次 / 分）；想再降要让服务端把 socket.io 在线也记成在场，属施工范围。

## 做到哪、下一步

- 已做：三项证明（握手、定向推送、断线退避重连）、鉴权方案、依赖选型。
- 没做：服务端推送点（`request_wake.ring` 两处 + 新单入队 / 重发 / 代录指令 / 整份重扫）、CC 侧正式实现与能力标记、
  socket.io 在线算不算 Cowork 在场（不算就维持按需请求 30 秒）、测试租户真域名复验、新旧混跑与老办公机 CPU 采样。
- 下一步：主会话定是否施工；施工先在服务端加心跳回复能力标记 `wake: "socketio"` 与推送点（旧 CC 不受影响），再做 CC 1.13
  （看到标记且握手成功才把领单兜底放到 60 秒），依赖锁 `websocket-client==1.9.2`。
