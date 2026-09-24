# Windows 验证环境（testenv）

> 权威说明（流程、命令、边界、清理）见 [真实环境验证：Windows 测试环境](../../docs/development/test-environment.md)。
> 本文件只记设备侧的结构与差异。

在一台独立的 Windows 机器（WSL2 + Ubuntu 24.04，x86_64）上跑**与正式环境同一份镜像、同一份 compose**的验证栈。
目的是：Mac 上改完 → 出镜像 → 在这台机器上用真实环境验一遍 → 没问题再把同一个 digest 部署到正式服务器。

## 与正式环境的关系

- 栈定义 = [`production/compose.yml`](../production/compose.yml) **原样**，只叠加 [`override.yml`](override.yml)。
  正式 compose 改了，这里自动跟着改，不存在第二份需要同步的栈描述。
- 镜像 = 正式环境正在运行的同一个 digest（`ghcr.io/skin306152-star/pearnly/frappe@sha256:…`），
  不重新构建、不换标签。
- 架构 = amd64，与正式服务器一致（Mac 本机是 arm64，跑不了正式镜像）。

## 与正式环境有意不同的地方

| 项 | 正式 | 这里 | 原因 |
| --- | --- | --- | --- |
| 数据卷 | `external`、固定名 `pearnly-production_sites-*` | 项目内卷 `pearnly-testenv_sites-*` | 不碰正式数据 |
| 域名 | `*.pearnly.com` + 正式 DNS | `*.test.localhost`，隧道另给公网名 | 不占用正式域名 |
| OCR 凭据 | 正式 Vertex 凭据 | 占位空凭据，测试站点不启用 OCR | 不产生付费调用 |
| 计费/身份 | 正式 Keycloak realm 与计费租户 | 本机 Keycloak 与测试租户 | 不写正式账本 |
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
bash scripts/testenv.sh up        # 拉镜像并起栈
bash scripts/testenv.sh sites     # 建/迁移测试租户站点
bash scripts/testenv.sh urls      # 打印 Mac 侧可直接打开的地址
bash scripts/testenv.sh logs dms-web
bash scripts/testenv.sh down      # 只停栈，保留数据卷
bash scripts/testenv.sh reset     # 删卷重来（会清掉这台机器的测试数据）
```

详见 [验证矩阵](../../docs/development/verification.md) 与本任务的 `docs/work/` 记录。
