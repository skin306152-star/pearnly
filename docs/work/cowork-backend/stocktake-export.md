# 盘点 scan 模式 XLSX 导出

本模块把已授权、锁定且完整的盘点快照写成 XLSX 字节。只迁旧 scan 模式的泰文汇总和位置明细，不新增 XLSX 导入、删除、照片、LINE 或库存记账。目录采集的第九批运行配置漂移仍由[工作包记录](../2026-09-20-cowork-backend-migration.md)处理，本模块不解除该 HOLD。

## 来源及独立性

- 新树本段开始基线：`e23b17afa0e881d2c89de7fe930c17d26358c3f5`。
- root 已核对第九批旧源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；本段按明确授权独立迁移不依赖 ERP 运行配置的盘点导出。
- 固定源码本机路径：`/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。下列相对路径及哈希是可重建的来源，不能用当前旧仓 HEAD 替代。

| 来源 | SHA256 |
| --- | --- |
| `services/stocktake/reports.py` | `2051f30c8a073326724f5f2d506d6856d3d38f7058a035d8b15afef5da47db72` |
| `services/stocktake/photo_report.py` | `ea230dccf13abff1075e92e4a13615fea6834d2b926ebd3153b49366dc39e456` |
| `routes/stocktake_routes.py` | `99ab5f699fa2c14e532d0180acd39b7c9623aa9d323d2eba4821677f6c087dad` |

来源 route `export:168` 先按 `recon.export` 权限调用 `store.detail(export=True)`，再按 count_mode 分流：scan 调 `reports.workbook(task)`，legacy 调另一份 `excel.workbook(items)`。`store.detail:90` 在 task 锁内读取全部 items/entries；照片分支 `photos.export:105` 读取本地数据库里的图片内容，不访问 ERP endpoint 或网络。原 writer 只依赖传入数据、Decimal、UTC、openpyxl 与 BytesIO；无照片时真实 `photo_report.attach` 立即返回。

因此本段无照片字节 writer 不依赖目录版本、ERP endpoint 动态配置、Companion、外部采集器或外部 IO，可以与仍 HOLD 的 collector 分开交付。完整旧照片能力与 legacy 分支没有随本段迁移。

## 落点与调用合同

实现：[`stocktake/export.py`](../../../apps/pearnly_cowork/pearnly_cowork/stocktake/export.py)。

```python
from pearnly_cowork.stocktake.export import workbook

content: bytes = workbook(snapshot)
```

snapshot 必须来自服务端 `stocktake.service.report(task_id)` 的完整快照，最小结构：

- `count_mode="scan"`、`items`、`entries`、`entry_total`，且 entry_total 等于 entries 长度；防止误把只含前 100 条的详情预览当作完整导出。
- item：id、product_code、product_name、barcode、unit、warehouse、location、book_qty、actual_qty。
- entry：item_id、product_code、product_name、barcode、unit、warehouse、location、quantity、voided、counted_by_name、counted_at、updated_by_name、updated_at。
- 数量可为原来源 Decimal 或新平台准确十进制字符串；日期为 datetime 或 ISO 字符串。照片只能缺席或为空。

writer 不接受文件路径，不读数据库，不依赖 Frappe，不产生持久文件，不改变输入。权限和 task 锁归调用服务。既有 `summarize_item`/`compare_location`/`quantity` 为数量与比较规则的唯一复用入口，不另外重算 active entries 合计来覆盖 item.actual_qty。

root 接线（单独所有权）：`service.export` 调用 `contract(workbook, report(task_id))`，GET `stocktake.api.export` 返回附件下载、记录 access log 并复用现有私有响应 no-store hook。报告服务持有当前权限和 task 锁，完整 entries 不分页；writer 不能单独替代这些授权检查。

## 保持的布局与行为

- 固定两个 sheet：`สรุปผลตรวจนับ`（15 列）、`รายละเอียดการนับ`（17 列）。完整泰文列顺序与源定义一致；没有按界面语言改列名。
- 汇总保留商品码、名称、条码/QR、单位、账面数、实盘数、差异、盘点结果、账面与实际仓库/位置及比较。
- 明细保留所有 entries，包括作废项、盘点人、修改人和 UTC 时间；作废项标注不计入总量，不参与汇总实际位置，不做有效项的位置高亮。
- 0 是已盘点，None 是未盘点；正负差异和未指定账面位置沿源泰文状态。实际仓库/位置去重后排序，空位置显示 `(ไม่ระบุ)`。
- 保留源行顺序、`A2` 冻结、全范围筛选、表头高度 26、常规列宽 25、名称列宽 36、表头色 `EAF0FF`、差异色 `FFF2CC`、细边框、换行和顶部对齐。
- Decimal 超过 15 位有效数字按源写为文本，避免 Excel 数值精度损失。普通数量保留数字类型；所有用户字符串显式写为字符串，公式状文本、前导零和 QR 不被执行或改写。
- 两表末尾 `ดูรูปภาพ` 列保留为空，以保持无照片源布局；不生成图片 sheet、图片或下载链接。

## 明确适配及拒绝边界

| 情况 | 行为 |
| --- | --- |
| 新平台数量字符串 | 用既有 quantity 转 Decimal，再按源规则写单元格；不经 float 运算 |
| Frappe naive UTC 时间 | 明确附 UTC 后序列化；来源 PostgreSQL aware 时间转换到 UTC 后保持相同文本，不使用运行主机时区猜测 |
| service.report 中已有 summary | writer 根据同快照 items/entries 复用纯计算合同，忽略预先投影的 summary；不改变 item 持久总量 |
| 非 scan | `stocktake.legacy_task`，不把旧 legacy 任务伪装为 scan |
| 非空 photos | `stocktake.photos_not_supported`，不默默漏图片后声称完整导出 |
| entry_total 缺席或不匹配 | 新码 `stocktake.export_incomplete`；由 root 添加四语友好文本 |
| 无效数量、日期或跨 item 孤立 entry | 稳定合同错误，不能返回部分文件 |

新的日期类型适配、完整快照检查及明确拒绝不属于“旧 writer 零变化”。值与样式差分只针对授权的无照片 scan 有效输入域；照片、legacy、未授权数据不进入等价声称。

## 差分与定向验证

[`test_cowork_stocktake_export_source_parity.py`](../../../tests/test_cowork_stocktake_export_source_parity.py) 对 reports.py 和 photo_report.py 完整文本做上述 SHA256 核验后，实际执行其定义。只从 AST 删除 reports 中指向旧包的 photo_report import，并绑定同哈希来源的真实 attach 函数；空 photos 走其源代码早退。没有 mock 旧 writer，没有用新代码代替旧规则，也不导入旧服务应用。

差分包含 80 组固定随机快照和 4 组零值/六位小数/数量上限边界，比较单元格值、数据类型、字体、填色、边框、对齐、数字格式、保护、超链接、列宽、行高、筛选、冻结、合并区和无图片属性。ZIP 时间戳等非业务容器元数据不要求逐字节一致。

支持 `--source <固定源码根>` 和 `--source-stdin`。后者接收 JSON 对象，键是 HASHES 中的来源相对路径，值是完整源码文本；同样校验哈希，适合 root 在隔离候选镜像中通过 stdin 投递源码。

```sh
PYTHONPATH=apps/pearnly_cowork:tests python3 -m unittest test_cowork_stocktake_export -v
PYTHONPATH=apps/pearnly_cowork:tests python3 tests/test_cowork_stocktake_export_source_parity.py --source /path/to/fixed-source
```

本代理本机实跑结果：

| 检查 | 环境和结果 |
| --- | --- |
| writer 定向单测 | Python 3.14.7，openpyxl 3.1.5；10 项通过 |
| 固定源真实 writer 差分 | 84 工作簿、17,524 单元格，值/类型/样式差异 0 |
| stdin 源码输入方式 | 独立验证 CLI 输入与哈希流程，通过；同一 84 样本，不重复计数 |
| 架构检查 | `python3 scripts/check-architecture.py`：PASS，0 violations |
| 空白检查 | `git diff --check` 通过；新增文件另查尾空白 |

本机使用 `/opt/homebrew/bin/python3.14`，只读复用已安装 Codex runtime 中的 openpyxl 3.1.5 纯 Python 包，通过 `OPENPYXL_LXML=False` 使用标准 XML 路径；没有安装依赖或改运行环境。锁定候选镜像同样固定 openpyxl 3.1.5，root 的候选环境与原生 Site 结果须另列，不以本机包路径混称候选镜像验收。

[`test_cowork_stocktake_export.py`](../../../tests/test_cowork_stocktake_export.py) 还覆盖 0 与未盘点、作废明细、持久总量不被重算、位置排序、超过 Excel 精度的文本、公式状用户字符串、aware/naive/ISO UTC 日期、输入不变、空报表和错误边界。

API/service 接线已做只读审查，未见阻断：使用完整锁定 report、稳定合同错误映射、附件 MIME、access log 与 no-store 标记。真实 Site/WSGI 的 101 条以上、作废/关闭、撤权、权益撤销和 legacy 拒绝场景由 root 执行并记在工作包正本；本代理未运行 lab。本段没有部署、没有外部业务结果，也没有 Excel 客户端人工视觉验收。
