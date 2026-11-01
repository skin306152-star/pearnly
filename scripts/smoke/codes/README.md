# 扫码冒烟与流程用例的码图

`make_barcode_video.py` 把这些图居中放进假摄像头画面（8 位灰度 PNG，只画码本身、带白边）。图由 bwip-js 4.5.1 画出、转成灰度存进来（10-30）。

| 文件 | 码制 | 内容 |
| --- | --- | --- |
| `data-matrix.png` | Data Matrix | `2856`（用户发来的眼镜框吊牌那种：纯数字短码） |
| `data-matrix-gs1.png` | GS1 Data Matrix | `(01)08850001000019(17)271231(10)LOT7(21)SN12345`：GTIN 是两套数据里条码商品 `8850001000019` |
| `data-matrix-gs1-unknown.png` | GS1 Data Matrix | `(01)08850001999993(21)SN777`：系统里没有这个 GTIN（走就地新建） |
| `qr-tis-url.png` | QR | `https://appdb.tisi.go.th/Q/i.php?d=PEARNLYFLOW0001`：泰国工业标准认证（TIS）码那种网址，不是商品码（`erp.stocktake.web-code`，11-01） |
