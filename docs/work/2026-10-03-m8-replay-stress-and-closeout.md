# m8 回放压测与压测窗口收工（2026-10-03）

压测窗口（本记录作者）收工；后续已知问题整批部署由修复窗口直接向用户汇报。

## m8：batch-1003（main 4fb91663）上线后的 100 人回放压测

- 时间：19:03–19:37 UTC（曼谷 02:03–02:37）。102 个模拟用户、每人 10 分钟、3 个测试租户；发票从回放原件池（1044 份真实件）取件原样上传；对账 pLite 5 套。
- 回放模式（`scripts/ocr-replay.py`，normal 档：中位 5.8 秒、p95 19.7 秒）只覆盖发票识别；对账单识别入口被拒（预期），4 套对账失败、1 套电子账单完成。识别费 0。
- 发票侧（客户端口径，每 20 秒查一次，与 m7 同口径，n=385→408）：上传→出结果 p50 36→21 秒，p90 68→38，p95 73→43，最大 108→62。
- 服务器侧（修复窗口采样）：ready p50 0.63 / p95 1.9 秒（m7 p95 20.9）；persist p95 1.94 秒；同时忙线程 p95 2、最多 4；redis-cache 淘汰 0、未命中 5.7%；负载峰值 17.5；ocr-worker memory.peak 1467 MiB / 5.25G；0 个 5xx。
- 接口 p50/p95：workbench 0.34/1.19，detail 0.20/0.80，submit 0.68/2.16，finish_upload 0.69/1.90，/app 0.12/0.46。
- 两处"变慢"均非产品问题：boot 是压测脚本未登录被转到 Keycloak 登录页；preview 是回放不生成预览图、现画。
- 结论：按用户"没有明确瓶颈就收工"，不跑 300 人。

## 费用核对

- 9/29–10/2 谷歌账单 ≈ ฿5,900（已入账 5,076），全部为测试：对账回归 2 轮全量、识别压测约 14 轮、准确率评测 14 轮，约 2 万页 / 8.5 万次调用，平均每页约 ฿0.30。合成压测票税号不合法走最贵复核路径，每页是真实件的 2.2 倍。
- 每页真实成本按账单单价×真实件逐页调用重算（见用户桌面成本 PDF）：电子 PDF 0.094、照片 3.8 0.147（2027 0.275）、银行 0.356（0.678）、总账 0.372（0.708）、税报页 0.400（0.766）、税报发票页 0.084。
- 10-02 晚起付费识别测试全部停止；回放压测工具上线后压测零费用。

## 收工清理（本窗口）

已删除：
- 生产：/tmp/pearnly-{m1..m8,q3..q6,h3,load,load-src,run,t3,e2e,prof,meas,eval}、/root/pearnly-corpus（1.3G 真实语料）、/root/pearnly-stress（压测用户清单与密钥）；容器 pearnly-m1..m8-recon、m8-mu、m8-locks（含其匿名卷）。
- 三个测试租户各 34 个压测用户 load-uNNN@pearnly.local：已停用并清除接口密钥（记录保留，识别历史归属不变）。
- 本机：工作树 agent-a3d3b0db1b66af844、agent-a5eaf72b45c8f0b82；本地与远端分支 codex/dms-menu-manual、codex/dms-recording-fixes（内容已随 batch-1003 进 main，按 patch 核对 0 差异）；scratchpad 密钥目录；桌面 HEIC 测试样张。
- 回放已关（三个租户 pearnly_ocr_replay 键已删）。

保留（登记）：
- /root/pearnly-replay-pool（624M）与 cowork sites 卷内 pearnly-ocr-replay/：以后零费用压测用；不再回放时由修复窗口删除。
- /tmp/pearnly-replay（switch.sh）、/tmp/pearnly-cand*：派发窗口 / 修复窗口的工具目录，归它们。
- testcowork2 / testcowork3 压测租户：修复窗口本批部署完成后按开户工具处理并报用户。
- 生产 docker 悬空匿名卷 542 个、frappe 镜像 27 个：历史一次性容器与部署留下，按仓库规则不做全局清理；下次 foundation 维护时由部署窗口按清单处理。
- Google：Cloud Run cowork-ocr minScale 0；无 Scheduler、Compute、Batch、Vertex 端点；Cloud Tasks 4 队列正常。Document AI 有两个 OCR 处理器（asia-southeast1 09-02 生产用；us 09-30 延迟评测建的，无闲置费用），us 这个是否删除由识别窗口确认。
