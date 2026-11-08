# 采购凭证两处缺陷（11-08）

- 主会话 local_e395dace 11-08 交办（PDF 并发 500 评估「另外发现」），与 PDF 并发修法同一分支、分开提交。
  分支 `pearnly/frosty-faraday-cdfc2b`，基线 `2ead204d5`。实验栈 `pearnly-pdflock-lab`（生产镜像 `sha256:1560bb95…`，源码本工作树）。
- 证据原文在共享笔记 `local_e395dace…/PDF并发500-截图与日志-11-08/日志/`。

## 1. 几张采购单同时出凭证死锁 500

- 现象：实验栈每轮 12 张不同的单同时点「补开收据」，12 份里 2–11 份 500（`QueryDeadlockError`），有没有 PDF 并发修法都一样。
- 根因：`purchasing/output.voucher` 在一笔事务里锁单、出 PDF、插 File、`doc.save()`。框架保存子表先按 parent 删旧行，两笔事务各拿到
  `tabERP Purchase Attachment` 的 `parent` 索引末尾（supremum）的 X 间隙锁，再各等对方的插入意向锁（`SHOW ENGINE INNODB STATUS` 原文：
  两边都在 `INSERT INTO tabERP Purchase Attachment`，`lock_mode X insert intention waiting`）。最新的几张单的附件都落在索引末尾，
  所以真实场景是两个人同时给最近的采购单出凭证。
- 修法：
  - 出 PDF 挪到事务外（只读：锁之前核状态、看是否已挂），结束读快照；
  - 挂附件那一步（重新锁单、重新核状态、先看这份是不是已经挂上、插 File、挂行、保存）经共用层 `pearnly_core.transaction.conflicts.retried`：
    撞上 1213 / 1020 整笔回滚、退避后重来，最多 4 次。
- 重来幂等：
  - 附件：每次重来都在锁里先看这一种凭证是否已挂，已挂就直接返回那一份（同一张单同一份同时点两次，后到的拿到先挂上的同一个文件）；
  - 文件：被回滚那次新写到磁盘的 PDF 由框架 File 的回滚回调删掉（锁定版 `core/doctype/file/file.py` `save_file_on_filesystem` 登记
    `after_rollback.add(self.on_rollback)`，`on_rollback` 对新文件 `_delete_file_on_disk`；`db.rollback()` 跑 `after_rollback`）；
  - 单据号：凭证不取号、不改单据号，`doc.save()` 只多一行附件。
- 用尽 4 次（几乎不可能）照旧报错，不加新文案。
- 测试：
  - 新流程 `erp.purchase.voucher-concurrent`（`scripts/flows/erp_purchase_voucher_concurrent.py`）：8 张单 × 两种凭证 + 其中 2 张的补开收据再点一次，
    18 个请求同时发；页面上全部成功、重复点拿到同一个文件；库里每张单每种正好一份、File 在且属于这张单、磁盘上文件在、
    没有多余 File 行和多余的 `<单号>-*.pdf`、单据号与状态不变。
  - 修前（只装了 PDF 并发修法）：新老两套数据都不通过（`QueryDeadlockError` 500）。修后：连跑 4 遍新老两套全过；
    InnoDB 最近一次死锁时间落在绿色运行期间（死锁仍在发生，被整笔重来接住）。
  - `tests/test_erp_purchase_voucher.py::AttachIsRetried`：只有挂附件那一步经整笔重来、出 PDF 在重来之外且只出一次、每次重来都重新锁单核状态查已挂。
- 共用件：`transaction` 的新调用方，登记入口 `erp.purchase.voucher`（`scripts/impact/shared-parts.toml`）。

## 2. 凭证抬头公司地址为空时印出「None」

- 现象：实验栈公司没填地址，补开收据 / 预扣税凭证抬头第三行印出「None」（截图 `voucher-pdf-before-after.png`）。
- 根因：`purchasing/voucher.html` 用 `company.get('address', '')`，主档里这一栏是 `None`（有键、值为空），默认值不起作用；税号同理。
- 修法：抬头三行（公司名、税号、地址）只印有值的，按行用 `<br>` 隔开；不加任何文案。
- 测试：`tests/test_erp_purchase_voucher.py::Header`（生产镜像里渲染真模板）：地址、税号为空时抬头只有公司名、没有「None」；
  三项都有时逐行印出并转义。修前红（印出 `None`），修后绿。
