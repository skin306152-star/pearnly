# 流程断言按列判断（11-02 主会话派）

分支 `pearnly/flow-by-product-columns`（基线 `origin/main` `81fa4837`）。只改流程脚本，不动产品代码。

## 起因

`erp.inventory.by-product` 的「by product has no warehouse column」约 1/8 误报：它把按物料那一行的整行文字拼起来，
找里面有没有仓库名。这次仓库叫「คลัง 1」，物料名「รวมคลัง 19fe85」的随机后缀以 1 开头，整行文字里就有「คลัง 1」，被判成有仓库列。

## 修法

| 流程 | 原来 | 现在 |
|---|---|---|
| `erp.inventory.by-product` | 行文字里找仓库名、找「 4 」、找低库存字样；手机宽度另靠行文字猜是不是宽屏 | 每行按格子的字段名（`data-fieldname`）取各格文字：按位置两行的 `warehouse_name` 正好是两个仓、按物料那一行没有 `warehouse_name` / `location` 两格、`qty_on_hand` 是「4」、`status` 是低库存；手机宽度收起的列文字照样读得到，两种宽度同一套判断。导出按表头列位置（`inventory.api.EXPORT_COLUMNS`）取格子：数量 4、仓库空。物料名改成「รวมยอด …」，不带「คลัง」 |
| `erp.products.import` | 提示文字拼起来找「3」「1」单个字符（编码 `IMP-…-1`、随机标记里的数字就能冒充） | 成功提示与跳过说明分开读，各取第一句里独立的整数，认 3 件、跳过 1 行 |
| `erp.warehouse.codes` | 「列表有仓库编号列」看整页文字有没有这个列名（开着的仓库弹窗自己就有这个标签，永远为真） | 看列表行里有没有 `data-fieldname="warehouse_code"` 的格子 |

## 同类排查

全仓 61 个流程模块查了「整段 / 整行文字里做子串判断」的写法（`in " ".join(...)`、`not in text`、`innerText.includes`）：

- 上表三处会误报或判不出，已改。
- 其余都是找这一轮随机生成、不会和别的字撞上的标记（单号、标题、`tag`）是否出现，或固定文案 / 字典键的判断，不改。

## 验证

- 实验栈 `pearnly-wh-lab`（本分支 `58305d80`）：`erp.inventory.by-product`、`erp.products.import`、`erp.warehouses.codes` 各两套数据 × 两种宽度，12/12 通过。
  手机宽度下按物料那一行读到的格子是 `product_name / code / barcode / unit / qty_on_hand（4）/ unit_cost / value / status（低库存）`，
  没有 `warehouse_name`、`location`；按位置两行的 `warehouse_name` 是两个仓；导出那一行数量 4、仓库空。
- 改动只在流程脚本与本记录：`--changed` 按产品文件挑流程，挑不到这三条，所以用 `--flow` 点名跑；不回写 `pearnly/flows` 状态
  （部署闸看的是 main 上全套，非产品路径改动沿用之前的全套结果）。
