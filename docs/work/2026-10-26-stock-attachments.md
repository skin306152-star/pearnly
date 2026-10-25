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
- 实验栈（10-26 02:46–02:52，挂 38cf8258 之后的工作树，REFRESH 导入，演示公司不接 Express）：建入库单带 2 个附件 → 2；已提交再加 1 → 3；
  已提交删一个被拒；别人上传的文件被拒；加到 10 个、第 11 个报「Up to 10 attachments」；作废后再加被拒、原 10 个照能列出。
  盘点照片：手机 base64 存上（实验栈没配原件层，走本机兜底）、别人的文件地址被拒、本人刚上传的挂上。
- 修了一个原有问题：手机盘点照片的本机兜底原来用 `frappe.utils.file_manager.save_file`，Frappe 16 会把写好的图片按文本读回去再去 EXIF，
  图片一律报 TypeError 存不上（实验栈复现）。线上 LINE 盘点拍照大概率一直存不上；改成带字节建 File（同 `recognition.originals` 的兜底）。
- 没测到：网页上传框的实际点选 / 拍照（要登录浏览器，交验收）；原件层直传（实验栈没配原件层，走的是同一套 `erp-originals.js`，采购 / 销售在用）。

## 运维待办（主会话 10-26）

- 甲骨文剩余空间纳入外部守望（`/opt/pearnly-watch`）的阈值提醒：附件按偏高口径一家重度公司约 2.2 GB / 月，记进运维待办，不挡本需求。
