# pearnly.com 第一批泰文教程文章（2026-10-04）

分支 `codex/website-guides`，基线 `origin/main` `d379da4e`。用户同意“先写几篇试试”，目的：用回答常见问题的泰文文章接住非品牌搜索。

## 内容

| 文章 | 网址 | 引向 |
|---|---|---|
| 银行对账（含对账表示例，数字已核算） | `/guides/bank-reconciliation/` | Cowork |
| 进项税发票核对清单（86/4 条要素、常见错误、6 个月规则） | `/guides/purchase-tax-invoice/` | Cowork；税号校验、VAT 工具 |
| 预扣税（常用税率、按税前额计算示例、ภ.ง.ด.3/53、50 ทวิ） | `/guides/withholding-tax/` | Cowork；预扣税工具 |
| VAT 7%（公式、Excel、四舍五入、ภ.พ.30、1.8 百万登记门槛） | `/guides/vat-7-percent/` | ERP、Cowork；VAT 工具 |

- 文章只有泰文版：`website/guides/<slug>.html`（开头注释里 JSON 元数据 + 正文 HTML），`website/guides.py` 读取；构建器把它们当“只有泰文”的页面，不出多语 hreflang，语言切换跳到对应语言首页。
- 页面：目录页 `/guides/`、文章页（目录、正文、免责声明、相关工具、产品面板、其他文章）、泰文首页“บทความแนะนำ”、导航“บทความ”、泰文工具页与产品页底部“อ่านเพิ่มเติม”。schema.org：Article、CollectionPage、BreadcrumbList。
- 税务内容写法：期限类（ยื่นออนไลน์ขยาย 8 วัน、e-Withholding 优惠税率）都写成“以税务局最新公告为准”；每篇底部有一般信息免责声明。不写 Express。

## 验证

- `python3 tests/test_website.py` 8 项通过（新增：文章元数据齐全、引用的产品与工具存在、至少 3 个小节、不含 Express；仅泰文页的 hreflang 只含 th + x-default）。`node --test tests/test-website-tools.cjs`、`check-architecture.py` 通过。
- 本机预览：文章页、目录页 2×2、首页推荐区。

## 待用户确认后发布

需用户或会计同事核对的税务说法：进项发票要素与买方税号/分支要求；进项税最长 6 个月顺延的条件；预扣税常用税率与 1,000 泰铢以下不扣的说法；ภ.ง.ด.3/53 与 7 日 / 网上延 8 日；ภ.พ.30 15 日；VAT 登记门槛 1.8 百万、30 日内登记。
