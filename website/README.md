# pearnly.com 官网

纯静态站：`build.py` 用标准库把 `content.py` 的文案渲染成 HTML，样式一个 `static/style.css`，站点交互一个 `static/site.js`（点阵背景随鼠标起伏、滚动渐入、首页“单据→数据”演示、视频播放），免费工具一个 `static/tools.js`（只在浏览器里算），文件类工具（图片转 PDF、合并 / 拆分 PDF）一个 `static/files.js`（文件只在浏览器里处理）。都不发请求，系统开了“减少动态效果”时全部静止。不用框架、不进业务容器，也不是产品 Web（产品 Web 仍是原生 Desk，见 ADR-005/007）。

- 泰文在 `/`，英文 `/en/`，中文 `/zh/`；每页自带 canonical、三语 hreflang、Open Graph 与 schema.org 结构化数据，另生成 `sitemap.xml`、`robots.txt`、`404.html`。
- 文案只写产品已有的能力，不写准确率、价格或客户数；改文案只改 `content.py`。
- 视觉：暖白底 + 点阵，界面黑白为主，不用品牌紫；色彩交给首页主视觉（`story.py`：和下面产品演示一样的窗口外框里一间手绘办公室，镜头在小单身后、越过肩膀看到桌面和屏幕，窗下沙发上睡着一只猫，“过去”一张张手动录到天黑，“现在”手机拍一拍、数据飞进屏幕里的系统，循环播放；全是内联 SVG，每一帧由 `site.js` 的 `story()` 按时间算出来，减少动态效果时停在最后一帧；共用的颜色、手写字体在 `poster.py`）。字体 IBM Plex Sans Thai（正文）与 Source Serif 4（字标），手写字 Sriracha（泰/拉丁按字符集分片）与 Long Cang（中文只取用到的字），经 Google Fonts。
- 每个产品页只放一段总览片：视频在 Cloudflare R2 桶 `pearnly-media`（`media.pearnly.com`，与 DMS LINE 手册视频同处，免费额度内），封面在 `static/posters/`。换片：`npx wrangler r2 object put pearnly-media/<文件名> --file <本地文件> --content-type video/mp4 --remote`，再把 `content.py` 里 `OVERVIEW` 的 `v` 改成新文件 sha256 前 10 位（绕过 CDN 旧缓存）。

唯一会联网的工具是「税号查公司」（`/tools/tax-id/`）：浏览器请求本站 `/api/rd/vat`、`/api/rd/tin`（只收 13 位数字），由 `infrastructure/production/landing.conf` 里的 nginx 拼好固定的 SOAP 请求转给泰国税务局公开接口（匿名账号，和 ERP 的 `masters/rd_contracts.py` 同一份请求），每个 IP 每分钟限 20 次；改这两段配置要用 `publish-website.sh --nginx` 发布。

第三方库（只在用到它的工具页加载，随官网发布，页面不向外部发请求；文件名带版本号）：

| 文件 | 来源 | 许可 | 用在 |
|---|---|---|---|
| `static/vendor/pdf-lib-1.17.1.min.js` | npm `pdf-lib@1.17.1` 的 `dist/pdf-lib.min.js`，integrity `sha512-V/mpyJAoTsN4cnP31vc0wfNA1+p20evqqnap0KLoRUN0Yk/p3wN52DOEsL4oBFcLdb76hlpKPtzJIgo67j/XLw==` | MIT（`static/vendor/pdf-lib-LICENSE.md`） | 图片转 PDF、合并 / 拆分 PDF（`static/files.js`） |
| `static/vendor/qrcode-generator-1.4.4.js` | npm `qrcode-generator@1.4.4` 的 `qrcode.js`，integrity `sha512-HM7yY8O2ilqhmULxGMpcHSF1EhJJ9yBj8gvDEuZ6M+KGJ0YY2hKpnXvRD+hZPLrDVck3ExIGhmPtSdcjC+guuw==` | MIT（文件头） | PromptPay 收款二维码 |
| `static/vendor/pdfjs-6.3.289/` | npm `pdfjs-dist@6.3.289` 的 `legacy/build/pdf.min.mjs`、`pdf.worker.min.mjs`（改名为 `.js`，内容不变）和 `cmaps/`，integrity `sha512-ZHjSVpDa3D6izMq8/04lvkhkATUmL9px6ChPaXc1k6nU2Mrhlg1/7F0bdUqCwUjw3NsPTfPZsMDUU6ZIcRaeQw==` | Apache-2.0（`static/vendor/pdfjs-6.3.289/LICENSE`） | PDF ⇄ Excel 转换：读 PDF 文字（`static/sheets.js`） |
| `static/vendor/exceljs-4.4.0.min.js` | npm `exceljs@4.4.0` 的 `dist/exceljs.min.js`，integrity `sha512-XctvKaEMaj1Ii9oDOqbW/6e1gXknSY4g/aLCDicOXqBE4M0nRWkUu0PTp++UPNzoFY12BNHMfs/VadKIS6llvg==` | MIT（`static/vendor/exceljs-LICENSE`） | PDF ⇄ Excel 转换：写 / 读 xlsx（`static/sheets.js`） |

换版本：`npm pack <包>@<版本>`，核对 integrity 与 npm 一致、库里没有网络请求，再按新版本号改文件名和 `build.py` 的 `SCRIPTS`。

本地预览：

```bash
python3 website/build.py && python3 -m http.server 8765 --directory website/dist
```

检查：`python3 tests/test_website.py`（标签、链接、sitemap）与 `node --test tests/test-website-tools.cjs`（工具计算）。

发布（只从 `main` 发布，一次 SSH，上一版保留在 `/var/www/pearnly-landing.prev`）：

```bash
bash scripts/publish-website.sh              # 只换网页
bash scripts/publish-website.sh --nginx      # 同时安装 infrastructure/production/landing.conf
bash scripts/publish-website.sh --rollback   # 网页换回上一版
```
