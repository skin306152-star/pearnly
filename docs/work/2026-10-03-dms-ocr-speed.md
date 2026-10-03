# DMS 身份证识别提速（2026-10-03）

用户：「处理 DMS 的 OCR 识别速度，也按 Cowork 那样处理方式，但不能影响 Cowork 现在的基础配置；看一次 OCR 时间花在哪里。」
分支 `codex/dms-ocr-speed`，基线 `origin/main` 8f195c7d；生产 DMS 镜像 `sha256:3332e232…`（源码 5240ce8b）。

## 时间花在哪里（生产 `pearnly.dms.ocr_timing`，9-27 只用 flash-lite 读一次之后的 7 条，ris / testdms）

| 步骤 | 中位数 | 范围 |
|---|---|---|
| 排队到开工（`ready_wait_ms`） | 0.37 秒 | 0.2–2.0 |
| 开工时再发"正在输入"（同步等 LINE） | 0.39 秒 | 0.3–0.5 |
| 从 LINE 下载照片 | 2.85 秒 | 2.5–3.6 |
| Gemini 读字 | 3.39 秒 | 2.1–10.3 |
| 余额闸、扣费、记录、选端点 | 0.39 秒 | 0.2–2.1 |
| 合计（`attempt_ms`） | 约 7.1 秒 | 5.9–14.3 |

- 下载慢的原因：服务器（新加坡 Hivelocity）到 `api-data.line.me`（147.92.191.80，日本）经 GTT 绕美国，TCP 往返约 330 毫秒；
  每次新建连接到第一个字节约 1.7 秒（TCP + TLS 1.2 两个来回），复用连接后同一请求 0.33 秒。`api.line.me` 在新加坡有 Akamai 节点，0.01 秒。
- 谷歌云新加坡（临时 Cloud Run 任务，测完删除）到同一地址：建连 0.08 秒、首字节 0.45 秒、复用 0.08 秒。
- 读字已无可省：调用记录 `usageMetadata` 无思考 token，每次约 1,700 输入 / 250 输出；`gemini-3.1-flash-lite` 只有全球入口。
  压缩 / 裁剪照片会改变结果，按「提速不能影响质量」不做。
- LINE 下载服务器空闲连接保持约 25–30 秒（生产容器实测：空闲 25 秒仍复用 0.33 秒，30 秒起重新握手 1.8 秒）。

## 不照搬 Cowork 上云的原因

Cowork 上 Cloud Run 解决的是服务器 CPU / 进程 / 排队；DMS 排队与 CPU 都很小。估算：云上已热时约 4.9 秒，冷启动（Cowork 10-01 实测约 2.8 秒）
时约 7.7 秒，比现在慢；DMS 用量零散，多数是冷启动，保持热要常驻（固定月费）；还要把 LINE 频道令牌交给云端。

## 改动

- 共用层只新增、不改原有行为（Cowork / ERP 不调用新入口）：
  - `pearnly_core.line.start_loading_background`：与 `start_loading` 同一请求，在短线程里发，返回 `wait()`；令牌在调用线程取好，线程里不碰站点与数据库。
  - `pearnly_core.line.download_message_content(..., session=None)`：可传调用方的会话；不传照旧每次新建。
- DMS：
  - `intake.flow.process_image`：开工时的"正在输入"与下载、识别同时进行；任务结束前（回复在 `line.batch` 结束时才发出）先 `wait()`，
    识别出错也等；动画请求最多等 3 秒。原来同步约 0.4 秒。
  - `line.out.download_image`：每个识别进程共用一个到 LINE 下载服务器的会话（连接池 8，与识别线程数一致；不收 Cookie，各租户只靠请求令牌）。
    复用的连接被关掉（连接错误）换新会话重下一次（只读 GET）；连不上（`ConnectTimeout`）不重下。旧会话不主动关，免得打断别的线程。
    25 秒内连续到来的图片省约 1.4 秒；零散使用时没有收益。
- 机房路由：用户 2026-10-03 已向 SSD Nodes 提交工单（附 mtr），请改走亚太线路；修好后下载预计约 0.7 秒。

## 验证

- `scripts/test-dms-line-runtime.py` 43 条（新增 6 条：后台动画同一请求 / 结果 / 无令牌不发，下载用调用方会话，DMS 共用会话与不收 Cookie，
  断连换新重下一次，连不上不重下，动画与识别同时进行且回复前落地、出错也等）：生产镜像一次性容器、断网、挂载本分支，通过。
- 生产镜像（`sha256:3332e232…`）挂载本分支五个 app 跑完整 `ci-candidate-checks.sh offline`：54 项全过（exit 0），其中 `test-dms-line-runtime.py` 43 条。
- `check-architecture.py` PASS；`check-dms-offline.py`（Python 3.14）20 项 PASS。
- `scripts/dms-line-lab-stale-work.py` 补新入口的替身；`scripts/sample-verify-line-flow-real.py` 下载替身原本不收 `max_bytes`，改为接受关键字参数。

## 未验 / 下一步

- 测试环境 testdms 实时源码挂上后，用户真实上传一张身份证，看计时日志里 `loading` ≈ 0、`download` 与原来对比。
- 随下一批攒批部署；不单独发版。
