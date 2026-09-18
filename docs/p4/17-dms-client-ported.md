# DMS 客户端已搬地基（浏览器 + 登录 + 会话锁）

日期：2026-09-18。承接 [16-legacy-credentials-found.md](16-legacy-credentials-found.md)：
凭据有了，这一步把"能真的操作 DMS"的地基搬过来。

## 一、搬了什么

老产品 DMS 那坨一共约 2800 行，分两层。**先搬地基，不搬业务** ——
地基不对的话，业务代码写得再多也是白写。

| 我们这边 | 老产品对应 | 作用 |
|---|---|---|
| `pearnly_core/browser.py` | `services/erp/_browser.py` | 浏览器会话：启动参数、泰文区域、`sdpt()` 补丁、弹窗留档、凭据脱敏、截图 |
| `pearnly_core/session_lock.py` | `services/erp/session_lock.py` | 同一账号的会话串行（见第五节：换成了 Redis 锁） |
| `pearnly_dms/dms_transport.py` | `mrerp_dms_adapter.py` 里的 `PlaywrightTransport` | 把"已登录的浏览器"包成 HTTP 管道 |
| `pearnly_dms/dms_adapter.py` | `mrerp_dms_adapter.py` | 登录、会话归属、第二套凭据 |

**分层是照老产品的设计**（不是我们发明的）：老产品的业务客户端
（`mrerp_dms_client.py`）**一句话都不提 Playwright**，它只认一个注入的 transport。
这样业务逻辑能用假 transport 单测，浏览器那层坏了也不影响业务逻辑的测试。
所以下一批搬业务（查重、建档、建单）时，它们会直接复用这里的 transport。

浏览器这层放**共用层**而不是 DMS 层，是因为老产品的注释写得很清楚：
"DMS 会话由真实浏览器登录持有"。Cowork / ERP 以后接外部系统同样是这个形态，
谁用谁 import，不重复造。

## 二、Playwright 进镜像（这一步是前提）

镜像里加了两样东西（`infrastructure/images/frappe.Dockerfile` 末尾）：

· `playwright==1.60.0` —— **版本对齐老产品 `requirements.lock.txt`**，不自己挑版本；
· chromium（含系统依赖）→ 装到 `/opt/ms-playwright`。

两个必须钉住的点：

1. **浏览器不能落 `/root/.cache`**：容器是以 `frappe`(1000) 跑的，读不到 root 的家目录。
   所以显式设 `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`，让"安装"和"运行时"看同一个路径。
   （老产品是 root 跑、用默认路径，这段不能照抄。）
2. `--with-deps` 装系统库要 root，所以 Dockerfile 里临时 `USER root` 再切回 `frappe`。

验证：在容器里以 `frappe` 身份起 chromium、渲染泰文，成功（chromium 148）。

## 三、踩到的坑（三个症状，其实是同一个原因）

第一次跑验证时看到的现象很吓人，而且**互不相干**：

1. 脚本**静默死掉**，连缓冲的输出都没有；
2. Playwright 的 node 驱动报 `EPIPE`（管道断了）；
3. Redis 里留下一把死锁，后面 15 分钟谁都进不来。

真相：**`dms-bench` 容器的内存上限是 256MB，chromium 被 OOM 杀了**（`OOMKilled=true`）。
进程被杀 → `finally` 没机会跑 → 锁不释放 → 输出缓冲丢了。三个症状一个原因。

实测数字：一 次 DMS 登录（含 chromium）**内存峰值 340MB**（cgroup `memory.peak`），
所以 256MB 必死、1GB 有充足余量。

**这不是测试环境的小毛病 —— 生产同一个坑**：老产品把 Playwright 跑在服务 web 进程里，
我们的 `dms-web` 也要跑（连接向导的"测试连接"就是一个 web 请求）。
所以 DMS 组的 `dms-bench` / `dms-web` / `dms-worker` 三个上限都提到了 **1g**，
并在 compose 里写清了原因。以后 Cowork / ERP 接外部系统，照这三行加。

### 另一个坑：别用 `bench console` 跑 Playwright

在 IPython（`bench console`）里启动 Playwright，会**把 Python 进程打死** ——
表现同样是"输出全丢 + node 报 EPIPE + 锁不释放"，但和内存无关。

而且它比真实路径更不真实：**生产上跑 DMS 的是 web 请求和后台任务，都不是 IPython**。
所以验证脚本改成独立 python 进程（自己 `frappe.init/connect`），
这也让它更接近将来真实的调用方式。

## 四、验证结果（从我们自己的容器里，真连真系统）

`scripts/sample-verify-dms-login.sh dms qd65956.localhost` —— **7 项全过，连跑 3 次都过**：

| 验的是什么 | 结果 |
|---|---|
| 会话锁**真的互斥**（同账号第二把锁必须拿不到） | 通过 |
| 真登进 DMS（离开 `index.php`） | 落点 `home/home.php` |
| 没被当成"重复登录" | 无弹窗 |
| 会话真的建立（有 cookie） | `PHPSESSID` |
| 截到登录后的图（证据） | [evidence/dms-login-from-container-20260918.png](evidence/dms-login-from-container-20260918.png) |
| 跑完锁自动释放 | 0 个残留 |

连跑 3 次是刻意的：**这个失败是间歇性的**（内存够不够决定生死），一次成功说明不了问题。

## 五、会话锁为什么换成 Redis

老产品用 Postgres 的 `pg_advisory_xact_lock`；我们这边是 MariaDB + Redis，
所以换成 `frappe.cache().lock`（Redis 锁），表达的是同一件事。换的理由：

· 锁在整个栈里共享（同一站点的 web/worker/scheduler 看到同一把锁）；
· 进程崩了锁按 TTL 自动过期，不会永久卡死（等同于老产品说的"断连自动释放"）。

**策略默认从严**：拿不到锁**直接报错，不放行**。老产品云上也是拒绝执行、
只在本机降级放行 —— 我们整个栈都跑在容器里，相当于它说的"云"。
真要临时放开，显式设 `PEARNLY_ALLOW_LOCKLESS=1`，且会写 warning 日志。

TTL 定 600 秒（可用 `PEARNLY_SESSION_LOCK_TTL` 覆盖），取的是一个折中：
正常会话远用不到，被杀之后的代价也还能忍。

**报错会说清是"活会话"还是"死锁"** —— 这一条是踩坑换来的：
现在的提示是"锁还剩 615s（像是上一个进程被杀留下的死锁；确认没人跑的话可以删掉这个 key）"。
两种情况的处理方式完全相反，报错里看不出来就只能干等。

配套工具：`scripts/dms-lock.sh status|clear` —— 默认只**看**，要清必须显式说 `clear`。

## 六、还没搬的（下一批）

| 能力 | 老产品 | 状态 |
|---|---|---|
| 客户查重 / 读客户 / 搜索 | `mrerp_dms_client_intake.py`（375 行） | ❌ |
| 建单 / 附件 / 主数据 | `mrerp_dms_client_ops.py`（494 行） | ❌ |
| 表单字段与地址映射 | `mrerp_dms_client_forms.py`（176 行） | ❌ |
| 查重建档编排 | `erp_dms_intake.py`（318 行） | ❌ |
| 推送编排 | `erp_dms_push.py`（405 行） | ❌ |
| 主数据缓存 | `dms_masters_cache.py`、`dms_advisor.py` | ❌ |

搬完这些才能跑"识别 → 查重 → 建档 → 推送"的完整链路。
