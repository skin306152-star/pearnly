# 真实环境验证：Windows 测试环境

**结论先行**：需要"真实环境验证"的改动，先在这台隔离局域网里的 Windows 上验，通过了再合并、推送、部署。
它跑的是**线上正在用的同一份镜像**，验证站点用**测试租户**，不占用正式服务器资源。

## 它是什么

- 一台隔离局域网里的 Windows 机器，跑 WSL2 + Ubuntu 24.04（x86_64），与正式服务器同架构。
  Mac 是 arm64，跑不了线上镜像，所以这台机器是唯一能"用线上镜像真跑一遍"的本地环境。
- 栈定义**只有一份**：[`production/compose.yml`](../../infrastructure/production/compose.yml)
  原样 + [`testenv/override.yml`](../../infrastructure/testenv/override.yml)（只换数据卷名、挂测试脚本）。
  线上 compose 改了，这里自动跟着改，不存在第二份需要同步的栈描述。
- 站点：三个产品的空白站点（`dms|erp|cowork.test.localhost`），以及两个**整站导入的测试租户**
  `testdms.pearnly.com`、`testerp.pearnly.com`（数据库、文件、`site_config.json` 与线上一致，
  包括 `encryption_key` 与各渠道凭据）。
- 操作入口：[`scripts/testenv.sh`](../../scripts/testenv.sh)（Mac 侧）、
  [`infrastructure/testenv/`](../../infrastructure/testenv/README.md)（设备侧）。

## 每次开发的流程

1. Mac 上改代码并跑本地快循环（语法、单测、前端 build、`scripts/check-architecture.py`）。
2. 推分支 → 手动触发 `build-production.yml`，在 CI 跑完强制检查与隔离 lab，产出镜像并记下 **digest**。
3. 在测试环境起这一版：
   ```bash
   export PEARNLY_TESTENV_IMAGE=ghcr.io/skin306152-star/pearnly/frappe@sha256:…
   bash scripts/testenv.sh up
   ```
4. 按下面的"一键切换"用测试租户做真实验证。
5. 通过 → 合并推送，再用**同一个 digest** 部署；不通过 → 在测试环境里改，线上从没被动过。

## 一键切换（把测试租户整体切到测试环境）

```bash
bash scripts/testenv.sh lab-start   # 线上导出两个测试租户 → 取回 → 导入测试环境 → 域名切过去
bash scripts/testenv.sh lab-stop    # 域名切回线上（测试环境数据保留）
bash scripts/testenv.sh lab-start dms   # 只动 DMS：只导出/导入 testdms 并只切它的域名
bash scripts/testenv.sh lab-stop  dms   # 只把 testdms 切回线上，ERP 域名不动
```

**按产品切换**：`lab-start` / `lab-stop` 可以只带要测的产品名（`dms`、`erp`，不带=两个都动）。
只切一个产品时，另一个产品的域名完全不动：它仍走通配记录、仍由线上服务。
底层是 [`testenv-cloudflare.sh`](../../scripts/testenv-cloudflare.sh) 的 `switch-lab` / `switch-prod`
（同样接受产品名，并可 `status [dms] [erp]` 只读看某一个）。隧道本身同时带两个域名，
所以"切哪个产品"只是"给哪个域名建/删自己的 CNAME"，不需要改隧道配置。
注意 DMS 与 ERP 共用同一个测试租户数据导出通道，只切一个产品时不会顺带刷新另一个产品的数据。

- 切换改的是 Cloudflare 解析：这两个名字平时走通配记录 `*.pearnly.com → 线上 IP`；
  切过去时建两条 `CNAME → 隧道`，切回来时删除，自动回落到通配记录。
- **域名不变**，所以 LINE webhook、OIDC 登录、第三方 ERP 推送地址都不需要改：
  登录仍走线上 Keycloak 与同一批测试账号。
- 影响范围只有这两个测试租户；其它租户完全不受影响。
  切换期间这两个租户由测试机提供服务，线上容器照常在跑，只是没有流量。
- 明细开关（排查时用）：`bash scripts/testenv-cloudflare.sh status|switch-lab|switch-prod`。

## 命令速查

| 命令 | 用途 |
| --- | --- |
| `bash scripts/testenv.sh setup` | 设备侧一次性准备（WSL 内装 docker / nginx / cloudflared） |
| `bash scripts/testenv.sh up` | 拉指定 digest 并起整栈（含建/迁移站点） |
| `bash scripts/testenv.sh sites dms erp cowork` | 只建/迁移空白站点 |
| `bash scripts/testenv.sh import <组> <站点>` | 导入某个测试租户（数据来源于线上导出） |
| `bash scripts/testenv.sh migrate <组> <站点>` | 发版后升级某个站点 |
| `bash scripts/testenv.sh check` | 读回每个站点的产品、已装 App、版本、数据量 |
| `bash scripts/testenv.sh smoke` | 入口冒烟（按 Host 头访问各站点） |
| `bash scripts/testenv.sh urls` | 打印 Mac 侧端口转发与可访问地址 |
| `bash scripts/testenv.sh logs <服务>` / `down` / `reset` | 看日志 / 停栈（保留数据）/ 删测试环境数据卷 |
| `bash scripts/testenv.sh lab-start [dms\|erp]` / `lab-stop [dms\|erp]` | 见上"一键切换"；可只切一个产品 |

## 凭据与边界

- Cloudflare API token 与隧道运行令牌只存在**本机** `~/.config/pearnly-testenv/`（权限 600），
  设备侧令牌在测试机 `/opt/pearnly-testenv/`；都不进仓库、不写进文档。
- 测试环境不接正式账本、不启用付费 OCR（占位空凭据）、LINE 用测试租户自己的频道。
- 测试机上另有一个**空的** Keycloak（仅本地），不代表线上身份；身份以线上为准。

## 它证明不了什么

- WSL2 的内核/网络栈与正式服务器不同；宿主 TLS 终结、生产并发与容量不能在这里验。
- 真机验收必须在切换状态下由人执行：手机 HTTPS 摄像头、真实 LINE 消息送达、
  真实第三方 ERP 落单。健康检查、HTTP 200、页面能打开都不算业务验收。
- 切换期间这两个域名的 certbot 自动续期会跳过（切回后恢复）；证书本身是免费的自动续期。

## 出问题怎么查

```bash
# 隧道服务与日志（设备侧）
ssh lenovo-office-lan 'wsl -d Ubuntu-24.04 -u root -- systemctl status pearnly-tunnel'
bash scripts/testenv.sh logs dms-frontend        # 看请求是否真的落在测试机
bash scripts/testenv-cloudflare.sh status        # 看解析当前指向谁
```

- 页面打不开但端口能通：确认 WSL 是 `networkingMode=mirrored`（`C:\Users\codexops\.wslconfig`）。
- 隧道 530/502：多半是隧道刚起或后端未就绪，重试并看 `dms-frontend` 访问日志。
- Mac 想绕过域名直连：`bash scripts/testenv.sh urls`。

## 归属与清理

- Cloudflare：命名隧道 `pearnly-testenv`（id `875e14d3-ac1b-4ff5-860e-2533f09dbd5a`），
  DNS 记录由脚本建删，不手工改。
- 设备：systemd 单元 `pearnly-tunnel`、目录 `/opt/pearnly-testenv/`、WSL 发行版 `Ubuntu-24.04`。
- 不再需要这套环境时：`lab-stop` → 删隧道与记录 → 停用并删除设备单元与目录，并在
  [本任务记录](../work/2026-09-24-windows-testenv.md) 里登记清理结果。
