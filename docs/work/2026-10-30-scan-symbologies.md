# 扫码码制一次覆盖全 + GS1 解析（2026-10-30）

分支 `pearnly/scan-data-matrix`，基线 origin/main `00736f59`。主线冻结期间只推分支、不合入。

## 起因与范围

用户发来眼镜框吊牌照片：方形二维码、L 形边框、下面印「2856」——Data Matrix。共用读码器
`apps/pearnly_core/pearnly_core/public/scan/reader.js` 只认 QR 和八种一维码，扫不出来。主会话 10-30 定按类一次覆盖常见码制：

- 二维：QR、Data Matrix、PDF417、Aztec；
- 一维：EAN-13 / EAN-8、UPC-A / UPC-E、Code 128、Code 39、Code 93、ITF、Codabar、GS1 DataBar（含扩展型）；
- GS1 解析：Data Matrix、DataBar、GS1-128 的内容是 (01)GTIN + (17)/(10)/(21) 这类串，解出 GTIN 按商品条码认，14 位 GTIN 对上 13 位 EAN；
  序列号给按台管理认台，批号、有效期不用；认不出才走就地新建。
- 不能拖慢现在的扫码；每个扫码入口逐个码制用假摄像头验，原有 QR 和条码一起回归。

## 打包的 WASM 读码器不用换

`public/vendor/barcode-detector/`（ponyfill + `zxing_reader.wasm`，zxing-wasm 3.1.3）本来就带 Data Matrix、PDF417、Aztec、Code 93、
DataBar（Omni / Stacked / Limited / Expanded）的解码器，码制名 `data_matrix`、`pdf417`、`aztec`、`code_93`、`databar`、
`databar_expanded`、`databar_limited` 都认。文件不动，哈希照旧（见那个目录的 README）。

## 实测：每帧耗时与误读

测法：`~/pearnly-devtools/scan-symbols/`（一次性工具，不进仓库）——bwip-js 4.5.1 画 17 张测试码（Code 93 用 zxing-cpp 2.3 画：
bwip-js 画的 Code 93 各家读码器连 zxing-cpp 官方包都读不出），测速页逐张解码，原生 BarcodeDetector 与 WASM 各跑一遍；
真实镜头模式开手机后置镜头 1080p，每组读 15 帧（画面是办公桌，没有码——就是扫码时绝大多数帧的样子）。

**三星 A14（SM-A146P，低端安卓，Chrome 156），真实镜头 1080×1920，每帧中位数**

| 读码 | 原来那组（9 种） | 全部码制 | 只二维（整幅） | 只二维（半分辨率） | 只 DataBar |
|---|---|---|---|---|---|
| 原生 | 47–48 ms | 66–69 ms | — | — | 原生不支持 |
| WASM | 113–129 ms | 285–341 ms | 263–268 ms | 88 ms | 75–90 ms |

结论：原生多读三种二维只多约 20 ms（原生在主线程外读）；WASM 全开要 2.5–3 倍、在主线程上读，会拖慢页面。二维码在画面里都很大，
缩到一半分辨率读，四种二维（含 GS1 Data Matrix）在测试图上全部照读，耗时降到三分之一；一维码与 DataBar Expanded 缩半会读丢，保持整幅。

**iPhone**：没有真机。iOS 模拟器（iPhone 17e，iOS 18.7 Safari / WebKit，没有原生 BarcodeDetector）上 WASM 全开读出 16/17
（缺的 Code 93 是那时还在用 bwip-js 的图，换图后无头 Chrome 的 WASM 读得出），半分辨率二维照读。模拟器跑在 Mac 的 CPU 上，
**耗时不代表真 iPhone**；真 iPhone 的每帧耗时没测到，需要真机。

**误读**：17 张图在原生与 WASM、整幅与半分辨率下，没有一张被读成别的码制、没有读出别的内容（UPC-A 被读成前面补 0 的 EAN-13
是标准行为，下面的 GTIN 对齐处理）。原生与 WASM 给的原文有差别：原生 GS1-128 前面带码制标识 `]C1`、GS1 Data Matrix 前面带 FNC1（GS 字符），
WASM 都不带；WASM 把 UPC-E 读成展开后的 13 位——都在 GS1 / GTIN 的统一处理里对齐。

## 改法

### 读码器分几路（`reader.js`）

- 主读：一维 + QR（含 Code 93），每帧都跑，**耗时与原来一样**。用原生的条件照旧是「原来那九种它都能读」；原生还能读的二维也并进主读
  （安卓 Chrome 上只多约 20 ms）；原生读不了的新码制（如 Code 93、DataBar）交给补读，不因此整个退到 WASM。
- 补读（WASM，后台加载，加载好之前不跑、加载不了不影响主读）：只在主读这一帧什么都没读到、手上也没有待确认的码时，轮流跑一路——
  二维（原生不支持时，半分辨率）；DataBar 与原生读不了的一维（整幅）。
- 读到码的那一路下一帧先跑、接着确认（照旧连续两帧同一个码才算），所以原来那些码的确认节奏不变；补读读到的码同样两帧确认。
- 类别：二维码（QR、Data Matrix、PDF417、Aztec）都归 `qr`（存二维码内容那一列）；一维与 DataBar 的各种写法（WASM 报 `databar_omni` 等）归 `barcode`。

### GS1 与 GTIN（`products/gs1.py`，纯规则）

- `clean`：去码制标识（`]C1`、`]d2`……）、开头的 FNC1；`parse`：解应用标识串（定长的直接接、变长的以 GS 或结尾截，人手输的 `(01)…` 写法也认），
  只把「`01` + 校验位对的 14 位开头」或括号写法当成 GS1，普通编码、URL、13 位 EAN 不会被当成 GS1；
- `gtin_forms`：一个 GTIN 的全部写法（14 / 13 / 12 位、UPC-E ↔ UPC-A），只认校验位对的数字串。
- 接入：认码 `products.codes.candidates`（扫码、盘点、收银、入库、选择框共用）原文对所有码、GTIN 写法对条码与售卖单位条码；
  按台认号 `inventory.serials.lookup` 用 GS1 里的序列号（21）；盘点就地新建 `count.create_product_here` 遇到 GS1 码条码记 13 位 GTIN
  （首位不是 0 的记 14 位），不存那串原文，下次扫同一件不管带什么批号、有效期都认得出。

### 测试与假摄像头

- `tests/test-scan-symbologies.cjs`：分类；主读原生 + 两帧确认、中间不插补读；补读读到 DataBar 并由同一路确认；没有原生时二维半分辨率补读；
  空闲帧轮流补读；原生少了原来九种之一照旧整个走 WASM。
- `tests/test_products_gs1.py`（纯规则，开发检查）、`tests/test_erp_gs1_codes.py`（认码与认台，候选镜像检查）。
- `scripts/smoke/make_barcode_video.py` 能把 `scripts/smoke/codes/` 里的灰度码图放进假摄像头画面（标准库解 PNG），流程用例用它。
- 流程 `erp.stocktake.gs1`：输 GS1 Data Matrix 原文按条码认出、系统没有的 GTIN 当场新建（条码记 GTIN），手机宽度另用假摄像头读 GS1 Data Matrix。

## 码制 × 入口（假摄像头，自有实验栈挂本分支源码）

每格：照页面的点法打开扫码，假摄像头喂那种码制的画面（480×360，`~/pearnly-devtools/scan-symbols/matrix.py`），读码器交给入口的原文、码制、类别都记下；
GS1 码核对 GTIN，其余核对原文（UPC / EAN 按 GTIN 写法对齐）。两套数据的老站点、各入口的岗位账号（收银先开班），无头 Chrome。

| 码制 | ERP 盘点 | ERP 盘点（手机） | LINE 盘点页 | 入库 | 出库 | 销售 | 采购 | 收银 | 新建物料 | Cowork 盘点 |
|---|---|---|---|---|---|---|---|---|---|---|
| qr_code | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| data_matrix | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| data_matrix_gs1 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| pdf417 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| aztec | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ean_13 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ean_8 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓¹ | ✓ |
| upc_a | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| upc_e | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| code_128 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| code_128_gs1 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| code_39 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| code_93 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| itf | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| codabar | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| databar | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| databar_expanded | ✓ | ✓ | ✓¹ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

170 格全过（¹ 第一轮这格 15 秒内没读出，单格复跑读出：LINE 盘点页 × DataBar Expanded、新建物料 × EAN-8，判为偶发）。
结果 `~/pearnly-devtools/scan-symbols/matrix-full.json`、`matrix-retry.json`。

## 连带回归清单

（跑完填）

## 没测到 / 限制

- 真 iPhone 的每帧耗时（只有模拟器的正确性）；真手机对着实物吊牌扫（假摄像头画面是规整的打印图）。
- LINE 里 LIFF 打开的盘点页：实验室走本站点已登录账号自己的绑定那条回落路径，页面与读码器同一份，LIFF 外壳没测到。
