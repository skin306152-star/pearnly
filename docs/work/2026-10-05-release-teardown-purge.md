# 上线 02e0e235 部署工具：删租户时一并删 Cowork 原件桶（2026-10-05）

main 02e0e235，镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:5aab833df7a5e23c568d86a410fb4b4070b99f78be492c42ca8b28d62358ecb4`
（构建 run 37310904277、stage run 37311667622）。只同步部署工具，不激活产品：自 acc020b0（三产品当前在跑的镜像）以来，
可部署路径只改了 `infrastructure/production/site-step.py`（`purge_site_objects`）与它的测试，产品代码没变。用户 2026-10-05："为什么不部署" → 部署。

## 内容

- `drop_site`：先删该站点在 `pearnly_cowork_originals_bucket` 里 `<站点>/` 前缀下的全部对象（原件、预览，含所有版本，16 个并发），
  再删库。前缀为空 / 带斜杠 / 不像域名一律拒绝；404 视为已删，可整步重跑。原因：隐私政策承诺"停用或要求删除后 30 天内删除"，
  以前删租户只删站点和库，桶里的原件一直留着。ERP / DMS 不用云存储，原来随站点删干净。
- 测试：`scripts/production/test-site-step-purge.py`（已进 Development checks）；用临时前缀在真实桶实测 2 删 2 剩 0。
- 一次性清理（用户同意）：已不存在的 testcowork2 / testcowork3 残留原件约 848 MB、3180 份已删，桶里只剩 testcowork。

## 部署与回读（UTC 约 12:50）

- 一次 SSH：镜像 revision 02e0e235；无非生产容器；`docker cp` 部署源到 `/opt/pearnly/deploy/`；
  `check-deploy-image.py` 输出 "Deploy tooling matches this image"；部署工具里的 `site-step.py` 有 `purge_site_objects`。
- 三个产品容器未重启、未激活，仍跑 acc020b0 镜像；执行器从 `/opt/pearnly/deploy` 挂载 `site-step.py`，下一次删租户即用新逻辑。
- 之后的正常激活必须用这一版或更新的镜像（部署工具已与 5aab833d 对齐）。

## 待验收

- 下一次真实删租户：看步骤输出 `PURGED_OBJECTS <数量>`，再 `gcloud storage ls gs://pearnly-cowork-originals-sg/` 确认该站点前缀不在。
