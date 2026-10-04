# pearnly.com 官网

纯静态站：`build.py` 用标准库把 `content.py` 的文案渲染成 HTML，样式一个 `static/style.css`，站点交互一个 `static/site.js`（点阵背景随鼠标起伏、滚动渐入、首页“单据→数据”演示、视频播放），免费工具一个 `static/tools.js`（只在浏览器里算）。都不发请求，系统开了“减少动态效果”时全部静止。不用框架、不进业务容器，也不是产品 Web（产品 Web 仍是原生 Desk，见 ADR-005/007）。

- 泰文在 `/`，英文 `/en/`，中文 `/zh/`；每页自带 canonical、三语 hreflang、Open Graph 与 schema.org 结构化数据，另生成 `sitemap.xml`、`robots.txt`、`404.html`。
- 文案只写产品已有的能力，不写准确率、价格或客户数；改文案只改 `content.py`。
- 视觉：暖白底 + 点阵，黑白为主，不用品牌紫；字体 IBM Plex Sans Thai（正文）与 Source Serif 4（字标），经 Google Fonts。
- 每个产品页只放一段总览片：视频文件放 `website/media/`（不进仓库，见 `content.py` 的 `OVERVIEW`），封面在 `static/posters/`，线上地址 `/media/…`。

本地预览：

```bash
python3 website/build.py --preview && python3 -m http.server 8765 --directory website/dist
```

检查：`python3 tests/test_website.py`（标签、链接、sitemap）与 `node --test tests/test-website-tools.cjs`（工具计算）。

发布（只从 `main` 发布，一次 SSH，上一版保留在 `/var/www/pearnly-landing.prev`）：

```bash
bash scripts/publish-website.sh                  # 只换网页
bash scripts/publish-website.sh --nginx --media  # 同时安装 landing.conf、上传总览视频
bash scripts/publish-website.sh --rollback       # 网页换回上一版
```
