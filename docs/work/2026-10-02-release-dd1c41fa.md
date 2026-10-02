# 统一上线 dd1c41fa（2026-10-02）

用户要求当天白天上线（生产上暂无真实客户，照常进维护）。集成分支 `codex/batch-2026-10-02`，41 个提交，快进合入 `main`。

## 改动

| 来源分支 | 内容 |
| --- | --- |
| codex/cowork-run-timing（识别窗口，含 01432ed9） | 识别排队 / 墙钟 / CPU 计时日志、Vision 资源不足按限流、大图首等、Cloud Run 装 pypdf、站点级 `OCR_DIRECT_MODEL` |
| codex/wht-total | 预扣税单据的合计改回扣前含税总额（`2026-10-02-wht-total.md`） |
| codex/recon-cost-estimate | 对账成本估算：采用重读时第一遍与重读各计一次 |
| codex/eval-tool | 评测工具（金标打分、`--count-missed`、`l1` 取原文、银行重放指标） |
| codex/seller-name-item-unreadable | 按税号定一个对方名称、不把编码 / 单位当品名（新表 Cowork Name Memory） |
| codex/erp-line-card-no-review-box | ERP 识别后不再显示检查原因 |
| codex/ocr-review-closure | 佛历差十年重读、入库定案（税号补校验位、单号定案）、录入页不再弹疑似重复 |
| codex/pdf-text-cache | 对账 PDF 文字层按内容只取一次 |
| codex/non-invoice-titles | 票头判非票据、原文无分店字样清空猜的 00000、识别改动上线闸门（`verification.md`） |
| codex/eval7b-prompt | 直读模型为 3.8 时补「水电费账单 / 请款单算采购凭证」（`2026-10-02-direct-model-3.8.md`） |

集成时解了 4 处交界冲突（`history/storage.py`、`ci-candidate-checks.sh`、`engine/runner.py` 两处），两边逻辑都保留。

## 五道关

1. 本地 Development checks（按 `development.yml` 逐步）全过；GitHub Development checks（dd1c41fa）success。
2. 生产镜像完整候选离线道：识别窗口在生产镜像 8f82d5d3 上挂 dd1c41fa 代码跑 `ci-candidate-checks offline`，41 步全过；构建流程自带的候选检查也通过。
3. 闸 ①（`verification.md`「识别改动上线闸门」）：Cloud Run 标签修订版 batch / batch38 真跑两批金标，站点侧后处理用整批代码零费用重放，`gold --count-missed`，对照同批文件生产基线：

| | 印了的读错 | 字段总错 | 漏掉的真票 | 非票据入账 |
| --- | --- | --- | --- | --- |
| batch（生产读法 + 本批，两批金标全部形态） | 8.64% → 7.93% | 11.2% → 10.3% | 8 → 8 | 205 → 156 |
| batch38（+ 直读 3.8，扫描件和照片） | 10.7% → 4.8% | 13.5% → 5.8% | 7 → 3 | 170 → 126 |

4. 闸 ②（测试租户 100 人）：上线前用标签跑过一次（testcowork 指向 batch38，03:10–03:23Z）：发票 417 张全入账、0 错误，testcowork e2e p50 / p95 42.5 / 65.5 秒（上一轮 44 / 150）。页面响应三站一起变慢（list p95 1.3 → 2–3 秒），同时数据库 CPU 69% → 140%，记为数据库容量问题，另行处理（缓冲池可配置：`codex/mariadb-buffer-pool`）。上线后 testcowork 开 3.8（08:05Z）再跑 m4（08:07:40–08:20:37Z）作最终确认：发票 384 张全入账、0 错误，15 套对账全部完成，e2e p95 58–77 秒（上线前 90–150），页面不退步（boot p95 1.4–1.5 秒）。**闸 ② 通过。**
5. 监控：部署前后快照 `/opt/pearnly/state/snapshot-{before,after}-dd1c41fa.txt`。

## 部署与回读（UTC）

- 镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:4e80514d3f57aba4b2b877f2f5261698e959794ebf8cb2c5255b947a6f616191`（rev dd1c41fa），
  构建 run 36979553109、stage run 36980063606。
- 激活：ERP 07:44:37、Cowork 07:48:45、DMS 07:57:57，08:02:11 完成，均「activated with verified image」。
- Cloud Run（识别窗口）：cowork-ocr-00050-7x9，07:38:44 起 100% 流量。按修订版名切换：「最新就绪」当时是带 3.8 评测开关的 00054，不能用 `--to-latest`。
- 回读：24 个产品容器全是新镜像；三个识别进程 NOFORK=1；八站 maintenance=0、近 1 小时 Error Log 0、ping 200；
  Max_used_connections 126 / 210、当前 4–6；MariaDB 2.6G / 4G；Cowork Name Memory 表已建。
- 回退目标：cowork `…@sha256:8f82d5d3…`（rev 10a4f07d），erp / dms `…@sha256:9c5607eb…`（rev c14ab8a6），用 `activate-product.sh` 按产品切回。

## 上线后：数据库内存（08:24Z，在线生效，不重启）

按 m4 期间各容器峰值重新分配上限，总量不变、不放宽「上限之和 ≤ 1.3 倍物理内存」：erp-ocr-worker 8g → 7g（保底 6600M）、
cowork-ocr-worker 7g → 5g（保底 4560M，峰值 1.1G）、MariaDB 4g → 8g、缓冲池 1G → 4G。`runtime.env` 同步（改前副本
`/opt/pearnly/state/runtime.env.before-db8g`），`docker update` 改上限，一次性客户端容器 `SET GLOBAL innodb_buffer_pool_size`。
缓冲池参数化随 main 84bf893c；下次 activate-foundation 时数据库按 8g / 4G 重建，在那之前数据库重启会回到 1G。
事后核对：热数据约 1GB，1G 缓冲池本来就装得下，扩容只多出空闲页。m5（08:27–08:38）变慢经压测窗口查明是谷歌 3.8 入口变慢，与此无关。

## 遗留

- testcowork 已设 `OCR_DIRECT_MODEL=gemini-3.8-flash`；其他站点逐站打开待定（识别窗口先加「3.8 入口都失败时降级到 flash-lite」）。
- 对账本地拼平（21–40 行 / 页一档）零费用分析在继续：卡点是密页纵向配数和日期写法，不是列位置。
