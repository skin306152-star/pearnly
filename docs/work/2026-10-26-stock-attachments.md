# 库存单附图片 / 拍照（第一部分）

基线 main `d8d42e6f`，分支 `pearnly/stock-attachments`。评估：`docs/work/2026-10-25-stock-attachments-vehicle-评估.md` 第一部分；
主会话 10-26 审定：LINE 这次不做；已提交的单只能加、不能删；每张单最多 10 个；盘点照片改成同一套（压缩 + 原件层）。

## 做了什么

- 入库单、出库单（`ERP Stock Entry`，全部用途）加附件区：官方上传框（选图、PDF、拍照，多选），照片先压缩（`p.shrink_photo`），
  传到统一原件层（`recognition.originals` 加方向 `stock_in` / `stock_out` / `stock_count`，各要 `stock.in` / `stock.out` / `count.write`），
  本机只留登记 File。弹窗与整页新建都有；新建时选的先挂在页面上，提交时一起挂到单（建单前先核，挂不上不建单）；
  已提交的单选了就挂上、只能加不能删；草稿可删；作废的单不再加、原有附件照留照能看。
- 挂接（`inventory/attachments.py`）：原生 `attached_to_doctype / attached_to_name`，不建子表；只挂本人刚上传、私有、没挂过别处的 File；
  每张单最多 10 个（服务端拦，文案「最多 {0} 个附件」）；读写都跟着单据（读这张单 / 这个方向的开单能力与这个仓）。
- 盘点照片同一套：电脑端上传框改走原件层 + 压缩；手机 LINE 盘点页先在浏览器里压缩（转正、长边 2400、JPEG 0.92）再传，服务端交原件层存
  （没配原件层的站点照旧存本机），单张上限 1 MB 改 4 MB（压完远小于它）。顺带补上：桌面端按地址挂照片原来不核是谁上传的，
  现在只认本人刚上传、私有、没挂过的 File。
- 不写 Express、不识别、不扣费；CC 载荷不变。

## 用途 / 入口 / 配置

| 入口 | 去向 | 需要 |
| --- | --- | --- |
| 入库 / 出库 → 新建（弹窗、整页）→ 附件区「Attach」 | 选图 / 拍照 / PDF，提交时挂到单 | 入库或出库岗位（`stock.in` / `stock.out`）、这个仓 |
| 打开已提交的单 → 附件区 | 点文件名打开；「Attach」再加 | 同上 |
| 库存盘点 → 录入 → 照片；LINE 盘点页拍照 | 挂到这条盘点记录（最多 5 张） | `count.write` |

## 验证

- 单元：`tests/test_erp_stock_attachments.py`（本人上传才能挂、别人的 / 公开的 / 已挂过的拒、方向能力、10 个上限与建单前核、已提交不能删、作废不再加）。
- 登记：`scripts/impact/shared-parts.toml` 新入口 `erp.stock-in.attach`、`erp.stock-out.attach`；`erp.stock-count.upload` 补盘点照片的新口径与文件。
- 实验栈与手机实测见下（施工中补）。

## 运维待办（主会话 10-26）

- 甲骨文剩余空间纳入外部守望（`/opt/pearnly-watch`）的阈值提醒：附件按偏高口径一家重度公司约 2.2 GB / 月，记进运维待办，不挡本需求。
