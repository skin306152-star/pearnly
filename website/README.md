# pearnly.com 官网

纯静态站：`build.py` 用标准库把 `content.py` 的文案渲染成 HTML，样式一个 `static/style.css`，免费工具一个 `static/tools.js`（只在浏览器里算，不发请求）。不用框架、不进业务容器，也不是产品 Web（产品 Web 仍是原生 Desk，见 ADR-005/007）。

- 泰文在 `/`，英文 `/en/`，中文 `/zh/`；每页自带 canonical、三语 hreflang、Open Graph 与 schema.org 结构化数据，另生成 `sitemap.xml`、`robots.txt`、`404.html`。
- 文案只写产品已有的能力，不写准确率、价格或客户数；改文案只改 `content.py`。
- 教程视频放在 `media.pearnly.com`（Cloudflare R2）。上传后在 `content.py` 的 `VIDEOS` 里登记，就会出现在对应产品页。

本地预览：

```bash
python3 website/build.py && python3 -m http.server 8765 --directory website/dist
```

检查：`python3 tests/test_website.py`（标签、链接、sitemap）与 `node --test tests/test-website-tools.cjs`（工具计算）。

发布（只从 `main` 发布，一次 SSH，上一版保留在 `/var/www/pearnly-landing.prev`）：

```bash
bash scripts/publish-website.sh            # 只换网页
bash scripts/publish-website.sh --nginx    # 同时安装 infrastructure/production/landing.conf
bash scripts/publish-website.sh --rollback # 换回上一版
```
