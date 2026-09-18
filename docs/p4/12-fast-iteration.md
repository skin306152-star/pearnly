# 改完多快能验：迭代回路

日期：2026-09-18。

## 为什么会有这篇

用户的原话：**"每次部署很慢，我无法接受。我是想你改完我能快速验证真实环境的状态。"**

所以目标不是"再搭一套环境"，而是把**改动到可验证**的时间压到几秒。

## 慢在哪（实测，不是估计）

| 环节 | 原来 | 现在 |
|---|---|---|
| 改后端一行 Python | 重建镜像 **25 秒** + 重建容器 ~20 秒 + 迁移 = **约 50 秒** | **2 秒**（`dev-reload.sh`） |
| 改前端一行 | 同样要重建镜像（约 50 秒） | **4 秒**（`npm run build`，连重启都不用） |
| 真要重建镜像时 | — | 镜像 **4.08GB** —— 放到服务器就是要传这个体积，所以"每次部署都重建"根本不可行 |

## 做法

### 1. 源码挂载（`infrastructure/compose/04-dev-mounts.yml`）

app 代码原本是 `COPY` 进镜像的。这一层把 5 个自有 App 的**内层包目录**挂进容器
（只读），于是改完代码容器直接看到新文件，不用重建镜像。

两个细节是刻意的：

- **只挂 `apps/<app>/<app>`，不挂整个 `apps/<app>`**：`pip install -e` 的
  `<app>.egg-info` 在项目根，整目录挂上去会把它盖掉，`importlib.metadata` 查版本就失败。
- **只读**：容器不需要往源码里写。Python 写不了 `__pycache__` 会静默跳过，不影响运行。

生产镜像仍然打进同一份代码，**不依赖挂载**（`04-dev-mounts.yml` 只在样板里加载）。

### 2. `scripts/dev-reload.sh` —— 一条命令让改动生效

顺序不能换，三步各治一个坑：

1. `bench migrate`（可选）—— 改了 DocType / 字段这类**结构**才需要；
2. `bench clear-cache` —— hooks 与 DocType 元数据缓存在 Redis，**容器重建不会清**
   （表现是"代码改了、行为没变"，这个坑踩过多次）；
3. 重启该组的 web/worker/scheduler —— gunicorn 是 `--preload`，不重启就一直跑旧代码。

实际用时（本机）：

| 命令 | 用时 |
|---|---|
| `dev-reload.sh dms --sites qd65956.localhost --no-cache` | **2 秒** |
| `dev-reload.sh dms --sites qd65956.localhost` | 3 秒 |
| `dev-reload.sh dms`（整组 3 个站点） | 5 秒 |
| `dev-reload.sh all`（7 个站点 + 全部组） | 19 秒 |

### 3. 顺手修掉的一个 10 秒

实测发现：**`dms-scheduler` 单独重启要 10 秒**，而 web/worker 都是 0 秒。
原因是 `bench schedule` 不处理 `SIGTERM`，Docker 会等满默认 10 秒宽限期再 `SIGKILL`；
而 `docker compose restart a b c` 是**顺序**执行的，于是整组重启被这一个拖到 10 秒以上。

修法：给四个 scheduler 加 `stop_grace_period: 2s`（调度器只往 Redis 塞任务，
被强杀不丢业务数据）。整组重启从 11 秒降到 2 秒。

### 4. `scripts/stack.sh` —— 唯一入口

整套栈是 4 个 compose 文件（+ 这层开发覆盖），**必须一起带上**才算完整。
以前靠各人记 `-f`，漏一个就出现"数据库怎么没了"这类问题。

```bash
scripts/stack.sh up|down|ps|restart <服务…>|logs <服务>|config
```

## 这套怎么支撑"验真实功能"

验的是**真站点、真登录、真数据**（`scripts/sample-verify-*.mjs` 都是真浏览器走完整
OIDC 登录之后点界面），不是单测替代品。所以改完 → 2 秒生效 → 跑对应验证脚本，
一轮"改 + 验"在几十秒内。

现有的真实验证：

| 脚本 | 项数 | 覆盖 |
|---|---|---|
| `sample-verify-frontend-slice.mjs` | 37 | 产品壳 + 采购录入 + 录入工作台 + 账号菜单改密 |
| `sample-verify-dms-roster.mjs` | 25 | DMS 侧栏形态 + 操作员增删改启停 + 绑定码 |
| `sample-verify-tenant-landing.mjs` | 19 | 登录后落到工作台、裸域名、不误伤 /desk |
| `sample-verify-login-theme.mjs` | 36 | 登录页几何/语言/默认泰语 |
| `sample-verify-dms-web.mjs` | 8 | 网页端没有录入入口（含直接调接口 403） |
| `sample-verify-feature-gate.mjs` | — | 买了能用 / 没买 403 |

## 还没到"像生产"的地方（明写）

这套解决的是**迭代速度**，不是**生产形态**。离生产还差：

1. 入口没有反向代理（现在 gunicorn 自己供静态；生产是 Cloudflare → Nginx → Frappe）；
2. 站点是 `*.localhost` + http，Keycloak 为此放宽成 `sslRequired=none`（生产必须 `external`）；
3. 端口都绑在 `127.0.0.1`，**别的设备访问不到**（手机、LINE 回调都不行）；
4. 前端产物靠挂载，生产要打进镜像。

这四条一旦要补，**顺序是先补 1+2+3**：因为 Host 头、`X-Forwarded-*`、secure cookie、
WebSocket 升级、上传大小这些坑只在"反代 + HTTPS"这一层才暴露 —— 没有这层，
换到任何机器都只是把同一个本机形态搬个家。

## 放到另一台机器 / 服务器上怎么保持快

同一个覆盖层直接可用，**快的原理不变**（代码挂载 → 不重建镜像）：

- 传代码：`rsync` 只传改动的几个 App 目录（几百 KB～几 MB），不是 4GB 镜像；
- 生效：在远端跑同一个 `scripts/dev-reload.sh <组>`；
- 只有依赖变化或要出正式镜像时才重建。

唯一要换的是 docker 目标：Mac 上给它一个远程 context
（`docker context create <名字> --docker "host=ssh://用户@主机"`），命令后面加
`--context <名字>`（本脚本读 `PEARNLY_CTX` 环境变量，不用改代码）。

⚠️ 远端那台**要先装好 Docker 并把这份仓库放上去**（首次），之后每次迭代都是上面那几步。
这段还没在有服务器的环境上实测过，等有机器再验。
