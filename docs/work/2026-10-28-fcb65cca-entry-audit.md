# fcb65cca 逐入口业务流程实测（10-28，暂停中）

> 主会话派：把 08bfb419 → fcb65cca（202 个提交）上线的改动逐入口用真实业务流程实测。10-28 用户指示额度快用完，除扫码热修外原地暂停。
> 分支 `pearnly/fcb65cca-entry-audit`（基于 main ccf056c2），只有实验室脚本与本记录，不改产品代码。

## 已发现的问题

暂无确认的产品缺陷。（盘点录入一组用例失败是我自己的用例辅助函数判错——弹窗是 fixed 定位，`offsetParent` 为空被当成不可见；修正后还没重跑。）

## 进度

- 改动清单：已按提交归类（商品码 / 内部编码 / 撞码与候选、按台管理、仓库库位、入出库附件 / 作废 / 打印、盘点与手机盘点、采购混合税率与现金含税、销售、收银小票逐行税 / 退货 / 日汇总、公司页、团队页、扫码、外壳路由与页头白名单、打印第一二批、无权限检查的接口收口、Express / CC 桌面版回写）。
  Express / CC 桌面版回写类（stock entry OU/JU/TK、日汇总 HS、退货 SR、负库存、STLOC 等）要真 Express 和 CC 桌面版，本地实验栈测不了，记「没测到」。
- 实验栈 `pearnly-flows-lab`（`~/pearnly-devtools/flows-lab/compose.yml`，线上同一镜像 `6fe735e7` = main fcb65cca，不挂产品源码，端口 18791）：
  - `erp-new.flows.localhost`：像 polymer 的新开户（一家公司、7 个仓、无期初；商品分别只靠编码 / 条码 / 二维码内容 / 内部编码 / Express 编码认，另有按台管理的车）；
  - `erp-old.flows.localhost`：像 mrerp 的老数据（两仓有存量、3 台车在库、采购发票、销售单、收银开班卖退交班、进行中盘点）；
  - `control.flows.localhost`：本地计费控制面。站点收尾与岗位授权走正式开户同一套函数。
  - 已停（数据卷保留），续用：`docker compose -p pearnly-flows-lab -f compose.yml up -d` 后容器里 `bench serve --port 8000 --noreload`。
- 已跑：
  | 入口 | 数据集 | 结果 |
  | --- | --- | --- |
  | 入库（整页）明细行扫码：二维码内容 / 编码 / 内部编码 / Express 编码 / EAN 条码 → 认出商品 → 提交过账 → 该仓账面 +4 | new | 5 种全部通过 |
  | 桌面盘点「录入盘点」键盘输码 / 摄像头扫码 × 5 种码 | new / old | 待重跑（用例辅助函数已修） |
- 用例与工具（`scripts/regression/flows/`）：`lab.py`（建站与岗位）、`seed.py`（两套数据）、`sid.py`、`code_video.py`（二维码 / EAN 假摄像头视频）、
  `steps.mjs`（无头 Chrome 步骤驱动，含手机宽度与假摄像头）、`run.py`（跑用例、占位替换、变体）、`cases/`（入库扫码、盘点录入）。

## 暂停点与下一步

1. 重跑 `cases/count_record.json`（两套数据），再做手机盘点页 `www/stocktake.html`（只记录现象，不碰 `stocktake-entry.js`）。
2. 依次：出库扫码与按台选车、采购入库与收货单打印、销售与打印、收银台卖 / 退与小票、仓库库位与停用、商品撞码与候选、公司页、团队页。
3. 每个入口写进上表；完了把用例交验收接进 CI，删实验栈。
