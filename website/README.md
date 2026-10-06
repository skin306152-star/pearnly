# pearnly.com 官网

纯静态站：`build.py` 用标准库把 `content.py` 的文案渲染成 HTML，样式一个 `static/style.css`，站点交互一个 `static/site.js`（点阵背景随鼠标起伏、滚动渐入、首页“单据→数据”演示、视频播放），免费工具一个 `static/tools.js`（只在浏览器里算）。都不发请求，系统开了“减少动态效果”时全部静止。不用框架、不进业务容器，也不是产品 Web（产品 Web 仍是原生 Desk，见 ADR-005/007）。

- 泰文在 `/`，英文 `/en/`，中文 `/zh/`；每页自带 canonical、三语 hreflang、Open Graph 与 schema.org 结构化数据，另生成 `sitemap.xml`、`robots.txt`、`404.html`。
- 文案只写产品已有的能力，不写准确率、价格或客户数；改文案只改 `content.py`。
- 视觉：暖白底 + 点阵，界面黑白为主，不用品牌紫；色彩交给首页主视觉（`poster.py`：彩铅插画的书桌放在液态玻璃卡片里，往下滑时小单站在画上捏着左下角往右上拉、边退边掀开（翻起的纸背用和画面同色的渐变与柔和投影做出真实感）、露出底下整理好的账，全是内联 SVG，逐帧由 `site.js` 的 `peel()` 按同一套公式重画）。字体 IBM Plex Sans Thai（正文）与 Source Serif 4（字标），手写字 Sriracha（泰/拉丁按字符集分片）与 Long Cang（中文只取用到的字），经 Google Fonts。
- 每个产品页只放一段总览片：视频在 Cloudflare R2 桶 `pearnly-media`（`media.pearnly.com`，与 DMS LINE 手册视频同处，免费额度内），封面在 `static/posters/`。换片：`npx wrangler r2 object put pearnly-media/<文件名> --file <本地文件> --content-type video/mp4 --remote`，再把 `content.py` 里 `OVERVIEW` 的 `v` 改成新文件 sha256 前 10 位（绕过 CDN 旧缓存）。

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
