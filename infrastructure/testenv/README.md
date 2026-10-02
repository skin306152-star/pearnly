# Windows 验证环境（testenv）

> 权威说明（流程、命令、边界、清理）见 [真实环境验证：Windows 测试环境](../../docs/development/test-environment.md)。
> 本文件只记设备侧的结构与差异。

在一台独立的 Windows 机器（WSL2 + Ubuntu 24.04，x86_64）上跑**与正式环境同一份镜像、同一份 compose**的验证栈，
产品代码用被测工作树的**实时源码**（用户 2026-09-27）：Mac 上改完 → `scripts/testenv.sh live <app>` 十几秒生效 →
用户试用没问题 → 合入 main、从 main 构建镜像部署正式服务器。

## 与正式环境的关系

- 栈定义 = [`production/compose.yml`](../production/compose.yml) **原样**，只叠加 [`override.yml`](override.yml)。
  正式 compose 改了，这里自动跟着改，不存在第二份需要同步的栈描述。
- 镜像 = 正式环境正在运行的同一个 digest（`ghcr.io/skin306152-star/pearnly/frappe@sha256:…`），
  不重新构建、不换标签。
- 架构 = amd64，与正式服务器一致（Mac 本机是 arm64，跑不了正式镜像）。

## 与正式环境有意不同的地方

| 项 | 正式 | 这里 | 原因 |
| --- | --- | --- | --- |
| 数据卷 | `external`、固定名 `pearnly-production_sites-*` | 每个产品一套栈，项目内卷 `pearnly-testenv-<app>_sites-*` | 不碰正式数据；测一个产品不碰另一个 |
| 产品代码 | 镜像里的 App | `/opt/pearnly-testenv/live/<app>/` 只读挂在 App 目录上（`live-<app>.yml`，`run.sh live` 生成） | 改了马上能试；`live-off` 回到纯镜像 |
| 域名 | `*.pearnly.com` + 正式 DNS | 空白站点 `*.test.localhost`；整站导入的测试租户仍用自己的 `test<app>.pearnly.com`，由 `lab-start` 把解析切到隧道、`lab-stop` 切回 | 测试租户域名不变，LINE / OIDC / 推送地址都不用改 |
| OCR 凭据 | 正式 Vertex 凭据 | 测试机 `/opt/pearnly/secrets/ocr.json` 自 2026-09-24 起是线上那份：经 LINE / 网页识别是**真实付费调用**，只用于人工验收，不跑自动调 OCR 的脚本 | 与线上同一读法验收 |
| 计费/身份 | 正式 Keycloak realm 与计费租户 | 测试租户登录走线上 Keycloak（同一批测试账号）；账本是整站导入的副本，扣费不回写线上。只有空白 `*.test.localhost` 站点才用本机 Keycloak（`--profile keycloak`） | 不写正式账本 |
| 端口暴露 | 127.0.0.1，由宿主 nginx 终结 TLS | 同样 127.0.0.1，经隧道或 SSH 隧道访问 | 保持同一种请求路径 |

## 仍然不能由它证明的事

内核细节、宿主机网络与 TLS 终结行为、正式并发与容量；没有公网入口时收不到 LINE 真实回调
（需要隧道指向测试租户自己的频道）。这些属于正式环境或真机验收范围。

## 用法

设备侧准备（一次性，在那台 Windows 上执行）：

```bash
# WSL 内，root
bash wsl-setup.sh
```

Mac 侧：

```bash
export PEARNLY_TESTENV_HOST=lenovo-office-lan
export PEARNLY_TESTENV_IMAGE=ghcr.io/skin306152-star/pearnly/frappe@sha256:…
export PEARNLY_TESTENV_SRC=<被测工作树>
bash scripts/testenv.sh lab-start cowork   # 起该产品的栈（先停别的栈）→ 首次导入测试租户 → 挂实时源码 → 切域名
bash scripts/testenv.sh live cowork        # 改完代码同步到测试机并生效
bash scripts/testenv.sh urls               # 打印 Mac 侧可直接打开的地址
bash scripts/testenv.sh logs dms-web
bash scripts/testenv.sh lab-stop cowork    # 域名切回线上 → 停栈，保留数据卷
bash scripts/testenv.sh reset              # 删卷重来（会连导入的测试租户数据一起清掉）
```

产品为 `dms|erp|cowork`，一次只跑一个；`up` / `sites` / `down` 等单步命令见权威说明的「命令速查」。

详见 [验证矩阵](../../docs/development/verification.md) 与本任务的 `docs/work/` 记录。
