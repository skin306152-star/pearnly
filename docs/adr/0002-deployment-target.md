# ADR-002 目标机与外部依赖保留范围

状态：已选定（用户确认 2026-09-17）。

## 决策

1. 目标机候选：**Contabo 新加坡 6 vCPU / 12GB / 200GB，月付**，服务器预算人民币 150 元内。
2. **不选付费备份附加包**；异地副本沿用已有 Google Cloud Storage。
3. OCR 与必要对象存储**保留在 Google**（Document AI + GCS），费用单列。
4. 开发完成后再租一个月测试；验收通过才切换。切换前现有服务维持。
5. 已关闭的独立 ERP 云项目**不复用、不计费，也不纳入自动清理**。
6. 旧 Cloud Run 发布规范对旧线上继续有效；**禁止把旧 Vultr 脚本改 IP 凑用**，新部署规范在 P8.05 完成。

## 关键约束

- 12GB 不是已验证的容量结论。P1.08 只证明"样板未发现阻断"，**不能当目标机性能结论**；真机结论在 P7。
- 本机开发环境是 arm64，目标机是 x86_64，两者资源数字不可互推。
- 容量口径**不含控制 Site**（见 ADR-004）。
- 容量报告按 T0—T3 档位，保留未通过项与实际开放上限；不得测试后改阈值凑通过。

## 已观测到的现状（2026-09-17，只读）

- Cloud Run：`pearnly-web`、`pearnly-worker`，同镜像 `sha256:224f0f80…`，修订 `4f1cbab9f740-s3`。
- 定时：`pearnly-dms-master-refresh`（每分钟）、`pearnly-background-recovery`（每 5 分钟）；队列 `pearnly-background` 限速 1/s；Job `pearnly-schema`。
- 边缘：Cloudflare Worker `pearnly-cloud-run`，最后部署 2026-09-14，比应用旧三天，两侧版本不联动。
- 常驻 VM `pearnly-work`（WeKan）**仍在运行**，有公网 IP，列 P8 退役。
- 旧 ERP 项目关而未拆，且**是本机 gcloud 的默认 project**，脚本必须显式指定项目。
