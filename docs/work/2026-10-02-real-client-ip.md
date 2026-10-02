# 真实客户端 IP（2026-10-02，下一批）

分支 `codex/real-client-ip`，基线 main c5092f48。来源：评估 Cloudflare 代理时核实发现，压测窗口转用户口径「不管上不上 Cloudflare 都单独修」。

## 现象（生产只读核实）

- 各租户库 `tabActivity Log.ip_address` 全是 `172.18.0.x`（Docker `pearnly-production_runtime` 网关）。
- 链路：宿主机 nginx（TLS）→ `127.0.0.1:818x` → Docker 转发 → 产品 frontend 容器 nginx → gunicorn。frontend 的
  `set_real_ip_from` 用镜像默认 `127.0.0.1`，而连进来的是 Docker 网关，于是 `$remote_addr` 停在网关，`@webserver` 再把它写进
  `X-Forwarded-For`；Frappe（`frappe/auth.py:64`）取 `X-Forwarded-For` 第一个值作 `request_ip`。
- 结果：所有人同一个 IP——按 IP 的限流一人超限拖累所有人、防暴力破解失效、登录与会话记录（`pearnly_control/sessions.py:77`）没有意义。
- 另外宿主机租户配置用 `$proxy_add_x_forwarded_for`，会把客户端自带的 `X-Forwarded-For` 接着往下传（若中间层信任它就能伪造）。

## 改动

- `provision-agent.py` 租户模板：`X-Real-IP $remote_addr`、`X-Forwarded-For $remote_addr`（用本机看到的来源覆盖，不接客户端自带的）。
- `frappe.conf.template`（三个产品 frontend 共用）：`set_real_ip_from 127.0.0.1` 与 `172.16.0.0/12`（Docker 网段），`real_ip_header X-Real-IP`；
  `/socket.io` 也改为 `X-Forwarded-For $remote_addr`。
- `scripts/production/refresh-tenant-nginx.py`：按当前模板重写已开通站点的宿主机配置（只改内容不同的，`nginx -t` 不过全部还原）；
  `activate-product.sh` 每次激活末尾跑一次，失败只告警。
- `test-provision-agent.py`：模板只传本机来源、frontend 只信任本机与 Docker 网段的 `X-Real-IP`；刷新脚本只改在用站点。

## 以后开 Cloudflare 时

宿主机 nginx 的 http 段加 Cloudflare 官方网段的 `set_real_ip_from` 与 `real_ip_header CF-Connecting-IP`，`$remote_addr` 即为真实访客，
上面的模板不用再改。另见 Cloudflare 评估记录。

## 上线后回读

登录一次，Activity Log 记的是真实公网 IP（不是 172.18.0.x）；`frappe.local.request_ip` 在两个不同来源下不同（限流键按 IP 区分）。
`auth.pearnly.com`（Keycloak）的宿主机配置本来就是 `X-Forwarded-For $remote_addr`，不在本次范围。
