# Cloudflare 代理与缓存（免费版）评估（2026-10-02）

压测窗口转用户口径：评估网页走 Cloudflare 代理与缓存，核实后再决定。**结论：暂不开（橙云），先修前置项；真实客户端 IP 已单独修（`2026-10-02-real-client-ip.md`）。**

## 现状（生产只读核实）

- DNS 已在 Cloudflare（NS `*.ns.cloudflare.com`），全部灰云直指 107.155.65.74；宿主机 nginx 终止 TLS（Let's Encrypt，HTTP-01 走 80 端口
  `/.well-known/acme-challenge/`）。租户站 `client_max_body_size 30m`、`proxy_read_timeout 120s`、WebSocket 已透传；gunicorn `--timeout=120`。
- 收益测算：宿主机 access.log——10-01 全天 126,435 次请求、172 MB，其中 `/assets` 273 次、20.9 MB（12%）；10-02 截至 13:05 `/assets` 64 次、2.4 MB（5%）。
  静态文件已有 `Cache-Control: max-age=31536000`（URL 带 `?v=` 内容哈希或 bundle 哈希），浏览器一年内不再请求。
  曼谷到源站 RTT 约 45 ms（连接 + TLS 约 0.10–0.15 s），到 Cloudflare 曼谷节点（colo=BKK）约 10 ms（0.02–0.03 s）：首次打开约省 0.1–0.3 s；
  不影响 API（p95 2 秒来自服务器端处理，见下一批「web CPU」项）。m7 压测期间 `/assets` 0 次（压测客户端只打 API）。

## 逐条核实

1. **超过 100 秒的同步请求（开代理后会变 524）**——都在请求里同步做，不是后台加轮询：
   - ERP 识别：`sales/api.py:90` `api_recognise_sales`、`purchasing/api.py:114` `api_recognise_purchase` 在请求里逐页读（最多 20 页，每页最多 4 次 Gemini + 1 次 Vision，单次最长 120 s），只有进度轮询。今天就会撞 gunicorn 120 s。
   - DMS：连接测试 `connections/api.py:88/179`（Playwright 登录，账号锁最长等 180 s）、`save_write_accounts` 逐个账号试登录；LIFF 编辑器
     `editor/browser_api.py` 草稿 / 保存经 `with_customer_detail` 可能实时登录 DMS 多次；LINE 回调里的去重（`line/out.py:126`）可能回落到实时读 DMS。
   - Cowork：`recon_center/sheets.py:509` `load`（以及 `save`、`presets.answer`）在没有存档时等 Redis 锁最长 120 s 再现算。
   - 无上限的同步导出：ERP 销售 / 采购导出、盘点导出（含照片缩放）、Cowork 银行 / 收入对账导出、控制面 `billing_www.export_records`；控制面 `logout_everywhere` 逐会话调 Keycloak（每次 15 s）。
   → 要开代理，先把这些改成「后台任务 + 轮询」或加上限。ERP 识别那一条与 Cloudflare 无关也该改（大 PDF 今天就会超时）。
2. **上传大小**：发票原件 Cowork 走 GCS 签名直传（`files/cloud.py:96`），没配桶时回落经服务器（上限 100 MiB，被宿主机 30m 先挡）；其他经服务器的上传都 ≤ 30 MB
   （对账文件 25 MB、工作附件 25 MB、付款凭证 10 MB、LINE 媒体 20 MB 等），在免费版 100 MB 以内。
3. **实时推送**：socket.io 走 WebSocket，Frappe 默认心跳 25 s（仓库无覆盖），在 100 s 空闲断开之内；前端 `product.js` 推送失败有定时轮询兜底。可用。
4. **回调与机器访问**：LINE 回调 HMAC 验签（`pearnly_core/line.py:98`）、Cloud Run 回调 Google ID token 验签（`ocr/remote/callback.py:62`）、小助手 Bearer 令牌、
   billing 走内网 `billing-web:8000` 不经 Cloudflare——都不依赖来源 IP，代理后照常。没有 IP 白名单；应用层没有自建按 IP 限流（用 Frappe 自带的，依赖真实 IP）。
   **开户会坏**：`provision-agent.py` `publish()` 要求租户域名解析到本机 IP，橙云后解析到 Cloudflare，新租户开通失败；要先改成「灰云开通、证书签好后再切橙云」或认 Cloudflare 网段。
   证书续期：只要 Cloudflare 不开「Always Use HTTPS」，80 端口的 HTTP-01 照常回源。
5. **缓存规则（将来开时）**：只缓存 `/assets/*`（带版本的 js / css / 字体 / 图片；`/assets/**/*.html` 不缓存）；`/api`、`/app`、`/socket.io`、`/private`、`/files`、预览跳转、HTML 一律绕过。
   私有文件源站回 `Cache-Control: private`，默认不会被缓存，仍显式加绕过。SSL 用 Full (strict)（源站 Let's Encrypt 有效）。
6. **验证**（将来开时）：先只开一个测试站，按「改脚本加载方式要逐产品点页」在浏览器点 ERP 收银台 POS、录入、对账、DMS，对比首屏；再跑小样本压测看 CPU 与带宽。

## 需要的 Cloudflare 权限（将来开时）

API token 只授 `pearnly.com` 这一个 zone：Zone:Read、DNS:Edit（切橙云 / 灰云）、Zone Settings:Edit（SSL 模式、Always Use HTTPS 关）、Cache Rules:Edit。
或由用户在面板按上面第 5 条操作。

## 建议顺序

1. 真实客户端 IP（已做，下一批上线）。
2. 把第 1 条里的同步长请求改成后台 + 轮询（ERP 识别优先，它今天就会超时），开户流程适配橙云。
3. 前置项都上线后，再按第 5、6 条只开一个测试站验证，确认收益后逐站开。按目前数据收益约 0.1–0.3 s 首屏，不急。
